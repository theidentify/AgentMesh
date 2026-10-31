// What the menu shows and offers, kept out of SwiftUI so it is testable. The app offers an action only when the supervisor
// would accept it; the supervisor still decides (rate limits, maintenance, presence).
import Foundation

/// The `units` op: the registry, including which units may move to the supervisor.
public struct UnitInfo: Equatable, Sendable, Decodable {
    public let name: String
    public let label: String
    public let kind: String
    public let wrapped: Bool
    public let maxRuntimeS: Int?
    public let nativeOk: Bool
}

public enum Severity: Int, Comparable, Sendable {
    case neutral = 0, ok, warning, error, offline
    public static func < (a: Severity, b: Severity) -> Bool { a.rawValue < b.rawValue }
}

/// `missing` is neutral: a unit not installed on this Mac (retention, insight-tunnel today) is not an alarm.
public func severity(_ health: Health) -> Severity {
    switch health {
    case .idle, .running: .ok
    case .failing, .stuck, .unknown: .warning
    case .failed, .down, .conflict: .error
    case .missing: .neutral
    }
}

/// The menu bar icon: the worst unit, or offline when the supervisor cannot be reached.
public func overall(_ status: Status?) -> Severity {
    guard let status else { return .offline }
    return status.units.map { severity($0.health) }.max() ?? .ok
}

public enum Action: Equatable, Sendable, Hashable {
    case start, restart, stop, reset
    case move(to: String)

    public var op: String {
        switch self {
        case .start: "unit.start"
        case .restart: "unit.restart"
        case .stop: "unit.stop"
        case .reset: "unit.reset"
        case .move: "unit.backend"
        }
    }

    public var title: String {
        switch self {
        case .start: "Start"
        case .restart: "Restart"
        case .stop: "Stop"
        case .reset: "Reset failed state"
        case .move(let to): to == "native" ? "Move to supervisor…" : "Move to launchd…"
        }
    }

    public func args(_ unit: String) -> [String: Arg] {
        if case .move(let to) = self { return ["unit": .string(unit), "to": .string(to)] }
        return ["unit": .string(unit)]
    }
}

/// Actions for one row. Nothing while a move of that unit is in flight; moves only for native-capable units.
public func actions(for row: UnitRow, info: UnitInfo?) -> [Action] {
    if row.move != nil || row.health == .missing { return [] }  // nothing to start on a unit that is not installed
    var list: [Action] = [.start, .restart]
    if row.kind == "job" { list.append(.stop) }
    if row.health == .failed { list.append(.reset) }
    if info?.nativeOk == true { list.append(.move(to: row.backend == "native" ? "launchd" : "native")) }
    return list
}

/// The confirmation the app shows before unit.backend. The real check is the supervisor's own sheet.
public func moveSummary(_ unit: String, to: String) -> String {
    let target = to == "native" ? "the AgentMesh supervisor" : "launchd"
    return "Move \(unit) to \(target).\n\nThe AgentMesh supervisor will ask for Touch ID or your Mac password. "
        + "Approve only if you started this move."
}

/// A move is over once it reached ok or any error code.
public func moveFinished(_ state: String) -> Bool { !["deciding", "moving", "waiting"].contains(state) }

/// A runtime in seconds as "45s", "12m 5s" or "1h 24m".
public func duration(_ seconds: Int) -> String {
    if seconds < 60 { return "\(seconds)s" }
    if seconds < 3600 { return "\(seconds / 60)m \(seconds % 60)s" }
    return "\(seconds / 3600)h \((seconds % 3600) / 60)m"
}

/// Human-readable age of an ISO-8601 timestamp, for job rows.
public func age(_ iso: String?, now: Date = Date()) -> String? {
    guard let iso else { return nil }
    let parser = ISO8601DateFormatter()
    parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let date = parser.date(from: iso) ?? { parser.formatOptions = [.withInternetDateTime]; return parser.date(from: iso) }()
    guard let date else { return nil }
    let seconds = max(0, Int(now.timeIntervalSince(date)))
    if seconds < 60 { return "\(seconds)s ago" }
    if seconds < 3600 { return "\(seconds / 60)m ago" }
    if seconds < 86400 { return "\(seconds / 3600)h ago" }
    return "\(seconds / 86400)d ago"
}

/// A scope as a button title: personal/common -> Personal, work/common -> Work, work/x -> Work / x, skip -> Skip.
public func scopeTitle(_ scope: String) -> String {
    if scope == "skip" { return "Skip" }
    let parts = scope.split(separator: "/").map(String.init)
    guard let first = parts.first else { return scope }
    let head = first.prefix(1).uppercased() + first.dropFirst()
    let rest = parts.dropFirst().filter { $0 != "common" }
    return ([head] + rest).joined(separator: " / ")
}

/// The confirmation shown before mapping.set; the supervisor's own sheet carries the authoritative summary.
public func mappingSummary(_ project: String, to scope: String) -> String {
    let what = scope == "skip" ? "Keep \(project) out of the profile log."
        : "Send memory from \(project) to \(scope). Agents that may read \(scope) will see it."
    return what + "\n\nThe AgentMesh supervisor will ask for Touch ID or your Mac password."
}
