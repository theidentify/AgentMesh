// A plain client of the supervisor's control.sock: no authority of its own. Presence for unit.backend is proved by the
// supervisor's own helper, never by this app.
import Foundation

public enum Arg: Encodable, Sendable, Equatable {
    case string(String), int(Int)
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let v): try c.encode(v)
        case .int(let v): try c.encode(v)
        }
    }
}

public enum ControlError: Error, Equatable, Sendable {
    case unreachable            // no socket, or nobody listening
    case timedOut
    case protocolError(String)  // a frame or JSON the app cannot read
    case server(code: String, message: String)
}

struct Request: Encodable {
    let v = 1
    let id: String
    let op: String
    let args: [String: Arg]
}

struct ErrorBody: Decodable { let code: String; let message: String? }

struct Envelope<R: Decodable>: Decodable {
    let id: String?
    let ok: Bool
    let result: R?
    let error: ErrorBody?
}

public struct ControlClient: Sendable {
    /// unit.backend and mapping.set wait for the supervisor's presence decision (up to 90 s there).
    public static let moveTimeout: TimeInterval = 100
    public static let defaultTimeout: TimeInterval = 10
    public static let presenceOps: Set<String> = ["unit.backend", "mapping.set"]

    public let socketPath: String

    public init(socketPath: String = ControlClient.defaultSocket()) { self.socketPath = socketPath }

    public static func defaultSocket() -> String {
        let home = ProcessInfo.processInfo.environment["AGENTMESH_HOME"]
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".agentmesh").path
        return home + "/run/control.sock"
    }

    public func call<R: Decodable>(_ op: String, _ args: [String: Arg] = [:], as type: R.Type,
                                   timeout: TimeInterval? = nil) throws(ControlError) -> R {
        let id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let body: Data
        do { body = try JSONEncoder().encode(Request(id: id, op: op, args: args)) }
        catch { throw .protocolError("encode") }
        let reply = try exchange(body, timeout: timeout ?? (Self.presenceOps.contains(op) ? Self.moveTimeout : Self.defaultTimeout))
        let envelope: Envelope<R>
        do { envelope = try decoder().decode(Envelope<R>.self, from: reply) }
        catch { throw .protocolError("decode") }
        guard envelope.id == nil || envelope.id == id else { throw .protocolError("id") }
        if !envelope.ok { throw .server(code: envelope.error?.code ?? "failed", message: envelope.error?.message ?? "") }
        guard let result = envelope.result else { throw .protocolError("result") }
        return result
    }

    public func status() throws(ControlError) -> Status { try call("status", as: Status.self) }
    public func inventory() throws(ControlError) -> Inventory { try call("keys.inventory", as: Inventory.self) }

    func exchange(_ body: Data, timeout: TimeInterval) throws(ControlError) -> Data {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw .unreachable }
        defer { close(fd) }
        var on: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
        var tv = timeval(tv_sec: Int(timeout), tv_usec: Int32((timeout - timeout.rounded(.down)) * 1_000_000))
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let path = Array(socketPath.utf8)
        guard path.count < MemoryLayout.size(ofValue: address.sun_path) else { throw .unreachable }
        withUnsafeMutableBytes(of: &address.sun_path) { raw in
            raw.copyBytes(from: path); raw[path.count] = 0
        }
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard connected == 0 else { throw .unreachable }
        do {
            try Framing.write(fd, body)
            return try Framing.read(fd)
        } catch FrameError.timedOut { throw .timedOut }
        catch FrameError.invalidLength { throw .protocolError("frame") }
        catch FrameError.closed { throw .protocolError("closed") }
        catch { throw .protocolError("io") }
    }
}

/// Readable text for the codes the supervisor returns (ops.py, moves.py, migrate.py).
public func describe(_ error: ControlError) -> String {
    switch error {
    case .unreachable: return "The AgentMesh supervisor is not running."
    case .timedOut: return "The supervisor did not answer in time."
    case .protocolError(let what): return "Unreadable answer from the supervisor (\(what))."
    case .server(let code, _):
        switch code {
        case "rate_limited": return "Too many requests. Wait a few minutes and try again."
        case "busy": return "Another move is in progress."
        case "presence_required": return "Not approved: Touch ID or password was cancelled or failed."
        case "presence_unavailable":
            return "Touch ID is not available to the supervisor. Use `agentmesh-log control adopt` in Terminal."
        case "maintenance": return "Maintenance mode is on."
        case "failed": return "The action failed. Check the audit log."
        case "unknown_op": return "This supervisor does not support that action yet."
        case "bad_args": return "The supervisor refused the request."
        case "unknown_move": return "That move is no longer known (the supervisor may have restarted)."
        case "not_native_capable": return "This unit cannot run under the supervisor."
        case "already_native": return "Already running under the supervisor."
        case "not_native": return "Not running under the supervisor."
        case "unsupported_plist": return "The unit's launchd plist could not be read."
        case "launchctl_failed": return "launchctl refused the change."
        case "not_pending": return "That project already has a scope (or is not waiting for one)."
        default: return "Refused: \(code)."
        }
    }
}
