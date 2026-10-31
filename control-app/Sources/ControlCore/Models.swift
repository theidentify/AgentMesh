// The status wire shape, pinned by profile-log/tests/control/fixtures/status.json on both sides.
// Unknown health or move states decode instead of failing, so a newer supervisor never blanks the menu.
import Foundation

public enum Health: Equatable, Sendable, Decodable {
    case idle, running, failing, stuck, failed, down, conflict, missing
    case unknown(String)

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        switch raw {
        case "idle": self = .idle
        case "running": self = .running
        case "failing": self = .failing
        case "stuck": self = .stuck
        case "failed": self = .failed
        case "down": self = .down
        case "conflict": self = .conflict
        case "missing": self = .missing
        default: self = .unknown(raw)
        }
    }
}

public struct JobInfo: Equatable, Sendable, Decodable {
    public let ok: Bool?
    public let at: String?
    public let since: String?
}

public struct Move: Equatable, Sendable, Decodable {
    public let moveId: String
    public let to: String
    public let state: String
}

public struct UnitRow: Equatable, Sendable, Decodable, Identifiable {
    public let name: String
    public let kind: String
    public let backend: String
    public let health: Health
    public let pid: Int?
    public let runtimeS: Int?
    public let lastExit: Int?
    public let job: JobInfo?
    public let move: Move?
    public var id: String { name }
}

public struct Maintenance: Equatable, Sendable, Decodable {
    public let on: Bool
    public let reason: String?
    public let since: String?
}

public struct Quota: Equatable, Sendable, Decodable {
    public let day: String?
    public let calls: Int
    public let dailyLlmCalls: Int?
    public let backoffS: Int
    public let backoffUntil: String?
}

/// New projects catch-up is holding back, and the scopes mapping.set accepts for them. Absent on a pre-2026-10-31
/// supervisor, so it is optional.
public struct Projects: Equatable, Sendable, Decodable {
    public let pending: [String]
    public let scopes: [String]
    public init(pending: [String], scopes: [String]) { self.pending = pending; self.scopes = scopes }
}

public struct Status: Equatable, Sendable, Decodable {
    public let units: [UnitRow]
    public let maintenance: Maintenance
    public let quota: Quota
    public var projects: Projects? = nil
}

public struct MappingSet: Equatable, Sendable, Decodable {
    public let project: String
    public let target: String
}

public struct MoveAccepted: Equatable, Sendable, Decodable {
    public let accepted: Bool
    public let moveId: String
}

public struct MoveState: Equatable, Sendable, Decodable {
    public let moveId: String
    public let unit: String
    public let to: String
    public let state: String
}

public struct AuditRow: Equatable, Sendable, Decodable {
    public let ts: Int
    public let op: String
    public let outcome: String
    public let callerPid: Int?
    public let callerExe: String?
    public let args: [String: JSONValue]?
}

/// Any JSON, for results the app only acknowledges (start, stop, maintenance) and for audit args.
public enum JSONValue: Equatable, Sendable, Decodable, CustomStringConvertible {
    case null, bool(Bool), number(Double), string(String), array([JSONValue]), object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([JSONValue].self) { self = .array(v) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    public var description: String {
        switch self {
        case .null: "null"
        case .bool(let v): String(v)
        case .number(let v): v == v.rounded() && abs(v) < 1e15 ? String(Int(v)) : String(v)
        case .string(let v): v
        case .array(let v): "[" + v.map(\.description).joined(separator: ", ") + "]"
        case .object(let v): "{" + v.keys.sorted().map { "\($0): \(v[$0]!)" }.joined(separator: ", ") + "}"
        }
    }
}

public func decoder() -> JSONDecoder {
    let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase; return d
}

// keys.inventory, pinned by profile-log/tests/control/fixtures/inventory.json. Each part the supervisor could not read
// is null; severity and kind stay strings so a newer supervisor's findings still decode.

public struct Device: Equatable, Sendable, Decodable, Identifiable {
    public let deviceId: String
    public let revoked: Bool
    public let thisDevice: Bool
    public let name: String?
    public let platform: String?
    public let lastSeenAt: String?
    public let lastSyncAt: String?
    public var id: String { deviceId }
}

public struct AgentGrant: Equatable, Sendable, Decodable {
    public let name: String?
    public let keyRef: String
    public let secretUntil: String?
    public let read: [String]?
    public let write: [String]?
}

/// A key file as lstat saw it; the supervisor never opens one. agent is absent for the keystore.
public struct KeyFile: Equatable, Sendable, Decodable {
    public let path: String
    public let kind: String
    public let mode: Int?
    public let ownerOk: Bool?
    public let size: Int?
    public let modifiedAt: String?
    public var agent: String? = nil
}

public struct Recovery: Equatable, Sendable, Decodable {
    public let count: Int
    public let latestIssuedAt: String?
}

public struct Backup: Equatable, Sendable, Decodable {
    public let at: String
    public let files: Int
    public let bytes: Int
}

public struct Finding: Equatable, Sendable, Decodable, Identifiable {
    public let id: String
    public let severity: String
    public let kind: String
    public let subject: String
}

public struct Inventory: Equatable, Sendable, Decodable {
    public let generatedAt: String
    public let devices: [Device]?
    public let agents: [AgentGrant]?
    public let keyFiles: [KeyFile]
    public let keystore: KeyFile
    public let recovery: Recovery?
    public let backup: Backup?
    public let findings: [Finding]
}
