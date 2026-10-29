// Touch ID, or the Mac password, evaluated in a child process of the AgentMesh supervisor
// (LAPolicyDeviceOwnerAuthentication: the owner chose password fallback on 2026-10-29).
// The exit code is the verdict, and anything but 0 is a refusal. The summary arrives on stdin, never argv.
import Foundation
import LocalAuthentication

public enum Verdict: Int32, Sendable {
    case approved = 0, denied = 1, unavailable = 2, timedOut = 3, usage = 64
    public var name: String {
        switch self {
        case .approved: "approved"
        case .denied: "denied"
        case .unavailable: "unavailable"
        case .timedOut: "timeout"
        case .usage: "usage"
        }
    }
}

public protocol Evaluator: Sendable {
    func canEvaluate() -> (Bool, Int?)
    func evaluate(_ reason: String, _ reply: @escaping @Sendable (Bool, Int?) -> Void)
    func invalidate()
}

/// Actions that may reach this sheet. Kept narrower than presence.ACTIONS: only backend moves use it in M3a.
public let actions: Set<String> = ["migrate"]
public let maxSummaryBytes = 4096
public let reasonPrefixChars = 400

final class Reply: @unchecked Sendable {
    private let lock = NSLock()
    private var value: (Bool, Int?) = (false, nil)
    func set(_ ok: Bool, _ code: Int?) { lock.lock(); value = (ok, code); lock.unlock() }
    func get() -> (Bool, Int?) { lock.lock(); defer { lock.unlock() }; return value }
}

public func available(_ evaluator: Evaluator) -> (Verdict, Int?) {
    let (ok, code) = evaluator.canEvaluate()
    return ok ? (.approved, nil) : (.unavailable, code)
}

public func confirm(action: String, summary: String, evaluator: Evaluator, timeout: TimeInterval = 60) -> (Verdict, Int?) {
    guard actions.contains(action), !summary.isEmpty, summary.utf8.count <= maxSummaryBytes else { return (.usage, nil) }
    let (can, code) = evaluator.canEvaluate()
    guard can else { return (.unavailable, code) }
    let done = DispatchSemaphore(value: 0)
    let reply = Reply()
    evaluator.evaluate("AgentMesh \(action): \(summary.prefix(reasonPrefixChars))") { ok, code in
        reply.set(ok, code); done.signal()
    }
    guard done.wait(timeout: .now() + timeout) == .success else { evaluator.invalidate(); return (.timedOut, nil) }
    let (ok, laError) = reply.get()
    return ok ? (.approved, nil) : (.denied, laError)
}

/// argv without the program name -> the action, or nil for a usage error.
public enum Command: Equatable, Sendable { case available, confirm(String) }

public func parse(_ args: [String]) -> Command? {
    if args == ["available"] { return .available }
    if args.count == 3, args[0] == "confirm", args[1] == "--action", actions.contains(args[2]) { return .confirm(args[2]) }
    return nil
}

/// Reads at most limit+1 bytes so an oversized summary is refused without reading it all.
public func readSummary(_ handle: FileHandle, limit: Int = maxSummaryBytes) -> String? {
    var data = Data()
    while data.count <= limit {
        guard let chunk = try? handle.read(upToCount: limit + 1 - data.count), !chunk.isEmpty else { break }
        data.append(chunk)
    }
    guard data.count <= limit else { return nil }
    return String(data: data, encoding: .utf8)
}

/// The audit line. `available` reports "available" instead of "approved": nothing was approved.
public func line(_ verdict: Verdict, _ laError: Int?, probe: Bool = false) -> String {
    let name = probe && verdict == .approved ? "available" : verdict.name
    return "{\"la_error\":\(laError.map(String.init) ?? "null"),\"result\":\"\(name)\"}"
}

/// One LAContext per process run; the helper evaluates once and exits, so sharing it with the reply queue is safe.
public final class LocalAuthEvaluator: Evaluator, @unchecked Sendable {
    let context = LAContext()
    let policy = LAPolicy.deviceOwnerAuthentication
    public init() {}
    public func canEvaluate() -> (Bool, Int?) {
        var error: NSError?
        let ok = context.canEvaluatePolicy(policy, error: &error)
        return (ok, error?.code)
    }
    public func evaluate(_ reason: String, _ reply: @escaping @Sendable (Bool, Int?) -> Void) {
        context.evaluatePolicy(policy, localizedReason: reason) { ok, error in reply(ok, (error as NSError?)?.code) }
    }
    public func invalidate() { context.invalidate() }
}
