import Foundation
import Testing
@testable import PresenceCore

final class Stub: Evaluator, @unchecked Sendable {
    let can: (Bool, Int?)
    let answer: (Bool, Int?)?   // nil: the reply never comes
    private let lock = NSLock()
    private var _evaluated = 0, _invalidated = 0, _reason = ""
    init(can: (Bool, Int?) = (true, nil), answer: (Bool, Int?)?) { self.can = can; self.answer = answer }
    var evaluated: Int { lock.withLock { _evaluated } }
    var invalidated: Int { lock.withLock { _invalidated } }
    var reason: String { lock.withLock { _reason } }
    func canEvaluate() -> (Bool, Int?) { can }
    func evaluate(_ reason: String, _ reply: @escaping @Sendable (Bool, Int?) -> Void) {
        lock.withLock { _evaluated += 1; _reason = reason }
        if let answer { DispatchQueue.global().async { reply(answer.0, answer.1) } }
    }
    func invalidate() { lock.withLock { _invalidated += 1 } }
}

@Test func approve() {
    let stub = Stub(answer: (true, nil))
    #expect(confirm(action: "migrate", summary: "Move ingest", evaluator: stub) == (.approved, nil))
    #expect(stub.reason == "AgentMesh migrate: Move ingest")
}

@Test func deny() {
    #expect(confirm(action: "migrate", summary: "x", evaluator: Stub(answer: (false, -2))) == (.denied, -2))
}

@Test func unavailable() {
    let stub = Stub(can: (false, -5), answer: (true, nil))
    #expect(confirm(action: "migrate", summary: "x", evaluator: stub) == (.unavailable, -5))
    #expect(stub.evaluated == 0)
    #expect(available(stub) == (.unavailable, -5))
    #expect(available(Stub(answer: nil)) == (.approved, nil))
}

@Test func timeout() {
    // On a thread with its own deadline, so a confirm that ignores its timeout fails here instead of hanging.
    let stub = Stub(answer: nil), finished = DispatchSemaphore(value: 0), result = Reply()
    Thread {
        let (verdict, _) = confirm(action: "migrate", summary: "x", evaluator: stub, timeout: 0.2)
        result.set(verdict == .timedOut, nil); finished.signal()
    }.start()
    #expect(finished.wait(timeout: .now() + 5) == .success)
    #expect(result.get().0)
    #expect(stub.invalidated == 1)
}

@Test(arguments: ["apply", "", "MIGRATE"]) func badAction(action: String) {
    let stub = Stub(answer: (true, nil))
    #expect(confirm(action: action, summary: "x", evaluator: stub) == (.usage, nil))
    #expect(stub.evaluated == 0)
}

@Test func badSummary() {
    let stub = Stub(answer: (true, nil))
    #expect(confirm(action: "migrate", summary: "", evaluator: stub) == (.usage, nil))
    #expect(confirm(action: "migrate", summary: String(repeating: "a", count: 4097), evaluator: stub) == (.usage, nil))
    #expect(stub.evaluated == 0)
}

@Test func reasonIsTruncated() {
    let stub = Stub(answer: (true, nil))
    _ = confirm(action: "migrate", summary: String(repeating: "b", count: 1000), evaluator: stub)
    #expect(stub.reason == "AgentMesh migrate: " + String(repeating: "b", count: 400))
}

@Test func parsing() {
    #expect(parse(["available"]) == .available)
    #expect(parse(["confirm", "--action", "migrate"]) == .confirm("migrate"))
    #expect(parse([]) == nil)
    #expect(parse(["confirm", "--action", "apply"]) == nil)
    #expect(parse(["confirm", "--action", "migrate", "extra"]) == nil)
    #expect(parse(["available", "--action", "migrate"]) == nil)
}

@Test func summaryLimit() throws {
    func read(_ bytes: Int) throws -> String? {
        let pipe = Pipe()
        pipe.fileHandleForWriting.write(Data(repeating: 0x61, count: bytes)); try pipe.fileHandleForWriting.close()
        return readSummary(pipe.fileHandleForReading)
    }
    #expect(try read(4096)?.count == 4096)
    #expect(try read(4097) == nil)
    #expect(try read(0) == "")
}

@Test func output() {
    #expect(line(.approved, nil) == #"{"la_error":null,"result":"approved"}"#)
    #expect(line(.denied, -2) == #"{"la_error":-2,"result":"denied"}"#)
    #expect(line(.approved, nil, probe: true) == #"{"la_error":null,"result":"available"}"#)
    #expect(line(.unavailable, -5, probe: true) == #"{"la_error":-5,"result":"unavailable"}"#)
}
