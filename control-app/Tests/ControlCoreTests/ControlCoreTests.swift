import Foundation
import Testing
@testable import ControlCore

// MARK: framing

func pair() -> (Int32, Int32) {
    var fds: [Int32] = [0, 0]
    precondition(socketpair(AF_UNIX, SOCK_STREAM, 0, &fds) == 0)
    return (fds[0], fds[1])
}

@Test func frameRoundTrip() throws {
    let (a, b) = pair(); defer { close(a); close(b) }
    let body = Data(#"{"ok":true}"#.utf8)
    try Framing.write(a, body)
    #expect(try Framing.read(b) == body)
}

@Test func frameHeaderIsBigEndian() throws {
    #expect(try Framing.encode(Data([1, 2, 3])).prefix(4) == Data([0, 0, 0, 3]))
}

@Test(arguments: [UInt32(0), UInt32(maxFrameBytes + 1), UInt32.max]) func frameLengthRefused(size: UInt32) {
    let header = withUnsafeBytes(of: size.bigEndian) { Data($0) }
    #expect(throws: FrameError.invalidLength(size)) { try Framing.length(header) }
}

@Test func emptyBodyRefused() {
    #expect(throws: FrameError.invalidLength(0)) { try Framing.encode(Data()) }
}

@Test func shortReadIsClosed() throws {
    let (a, b) = pair(); defer { close(b) }
    let partial: [UInt8] = [0, 0, 0, 9, 1, 2]
    _ = Darwin.write(a, partial, partial.count); close(a)
    #expect(throws: FrameError.closed) { try Framing.read(b) }
}

// MARK: client against a fake supervisor

final class FakeServer: @unchecked Sendable {
    let path: String
    let fd: Int32
    private let lock = NSLock()
    private var _request: [String: Any]?
    var request: [String: Any]? { lock.withLock { _request } }

    /// reply: given the request id, the raw bytes to frame back; nil sends nothing (a hung supervisor).
    init(reply: @escaping @Sendable (String) -> Data?) {
        path = "/tmp/amc-\(UUID().uuidString.prefix(8)).sock"
        unlink(path)
        fd = socket(AF_UNIX, SOCK_STREAM, 0)
        var address = sockaddr_un(); address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutableBytes(of: &address.sun_path) { raw in
            let bytes = Array(path.utf8); raw.copyBytes(from: bytes); raw[bytes.count] = 0
        }
        _ = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        listen(fd, 1)
        let listener = fd
        Thread { [self] in
            let conn = accept(listener, nil, nil)
            guard conn >= 0 else { return }
            defer { close(conn) }
            guard let data = try? Framing.read(conn),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            lock.withLock { _request = object }
            if let bytes = reply(object["id"] as? String ?? "") {
                if bytes.isEmpty { return }
                try? Framing.write(conn, bytes)
            } else { Thread.sleep(forTimeInterval: 2) }
        }.start()
    }

    deinit { close(fd); unlink(path) }
}

func json(_ text: String) -> Data { Data(text.utf8) }

@Test func requestShapeAndResult() throws {
    let server = FakeServer { id in json(#"{"id":"\#(id)","ok":true,"result":{"accepted":true,"move_id":"m1"}}"#) }
    let result = try ControlClient(socketPath: server.path).call(
        "unit.backend", ["unit": .string("ingest"), "to": .string("native")], as: MoveAccepted.self)
    #expect(result == MoveAccepted(accepted: true, moveId: "m1"))
    let request = try #require(server.request)
    #expect(Set(request.keys) == ["v", "id", "op", "args"])
    #expect(request["v"] as? Int == 1 && request["op"] as? String == "unit.backend")
    #expect((request["id"] as? String)?.count == 32)
    #expect(request["args"] as? [String: String] == ["unit": "ingest", "to": "native"])
}

@Test func serverErrorCode() {
    let server = FakeServer { id in json(#"{"id":"\#(id)","ok":false,"error":{"code":"rate_limited","message":""}}"#) }
    #expect(throws: ControlError.server(code: "rate_limited", message: "")) {
        try ControlClient(socketPath: server.path).call("unit.start", ["unit": .string("ingest")], as: JSONValue.self)
    }
}

@Test func mismatchedIdRefused() {
    let server = FakeServer { _ in json(#"{"id":"someone-else","ok":true,"result":{}}"#) }
    #expect(throws: ControlError.protocolError("id")) {
        try ControlClient(socketPath: server.path).call("status", as: JSONValue.self)
    }
}

@Test func badJSONRefused() {
    let server = FakeServer { _ in json("not json") }
    #expect(throws: ControlError.protocolError("decode")) {
        try ControlClient(socketPath: server.path).call("status", as: JSONValue.self)
    }
}

@Test func closedWithoutReply() {
    let server = FakeServer { _ in Data() }
    #expect(throws: ControlError.protocolError("closed")) {
        try ControlClient(socketPath: server.path).call("status", as: JSONValue.self)
    }
}

@Test func hungSupervisorTimesOut() {
    let server = FakeServer { _ in nil }
    #expect(throws: ControlError.timedOut) {
        try ControlClient(socketPath: server.path).call("status", as: JSONValue.self, timeout: 0.3)
    }
}

@Test func noSupervisor() {
    #expect(throws: ControlError.unreachable) {
        try ControlClient(socketPath: "/tmp/amc-missing-\(UUID().uuidString.prefix(8)).sock").status()
    }
}

@Test func moveTimeoutCoversDecision() {
    #expect(ControlClient.moveTimeout > 90)
}

// MARK: models and the shared fixture

func fixture() throws -> Data {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()
    return try Data(contentsOf: root.appendingPathComponent("profile-log/tests/control/fixtures/status.json"))
}

@Test func fixtureDecodes() throws {
    let envelope = try decoder().decode(Envelope<Status>.self, from: fixture())
    let status = try #require(envelope.result)
    #expect(status.units.map(\.name) == ["ingest", "summary", "retention", "catch-up", "broker", "bridge", "insight", "insight-tunnel"])
    let catchUp = try #require(status.units.first { $0.name == "catch-up" })
    #expect(catchUp.backend == "native" && catchUp.move == Move(moveId: String(repeating: "0", count: 32), to: "launchd", state: "moving"))
    #expect(status.units.first { $0.name == "retention" }?.health == .missing)
    #expect(status.units.first { $0.name == "ingest" }?.job?.ok == true)
    #expect(status.units.first { $0.name == "broker" }?.pid == 4242)
    #expect(status.maintenance == Maintenance(on: false, reason: nil, since: nil) && status.quota.calls == 0)
}

@Test func unknownHealthDecodes() throws {
    let row = #"{"name":"x","kind":"job","backend":"native","health":"exploded","pid":null,"runtime_s":null,"last_exit":null,"job":null,"move":null}"#
    #expect(try decoder().decode(UnitRow.self, from: json(row)).health == .unknown("exploded"))
}

@Test func auditRowDecodes() throws {
    let row = #"{"args":{"unit":"ingest","limit":3},"caller_exe":"/usr/bin/agent","caller_pid":7,"op":"unit.start","outcome":"ok","ts":1793154609936}"#
    let audit = try decoder().decode(AuditRow.self, from: json(row))
    #expect(audit.op == "unit.start" && audit.callerPid == 7 && audit.args?["limit"]?.description == "3")
}

@Test func describedCodes() {
    #expect(describe(.server(code: "presence_unavailable", message: "")).contains("agentmesh-log control adopt"))
    #expect(describe(.server(code: "something_new", message: "")) == "Refused: something_new.")
    #expect(describe(.unreachable).contains("not running"))
}

// MARK: live, opt-in: AMC_LIVE=1 ./test.sh --filter liveStatus (read-only status on this Mac's supervisor)

@Test(.enabled(if: ProcessInfo.processInfo.environment["AMC_LIVE"] == "1")) func liveStatus() throws {
    let status = try ControlClient().status()
    #expect(!status.units.isEmpty)
}

@Test(.enabled(if: ProcessInfo.processInfo.environment["AMC_LIVE"] == "1")) func liveUnitsOfferMove() throws {
    let client = ControlClient()
    let infos = try client.call("units", as: [UnitInfo].self)
    let row = try #require(try client.status().units.first { $0.name == "catch-up" })
    let info = infos.first { $0.name == "catch-up" }
    #expect(info?.nativeOk == true)
    #expect(actions(for: row, info: info).contains(.move(to: row.backend == "native" ? "launchd" : "native")))
}
