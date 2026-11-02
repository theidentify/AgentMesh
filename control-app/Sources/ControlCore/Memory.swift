// The memory tree as the supervisor returns it (memory.ls / memory.cat). Content is shown, never stored by the app.
import Foundation

public struct MemoryEntry: Equatable, Sendable, Decodable, Identifiable {
    public let name: String
    public let type: String          // scope, dir, page or event
    public let count: Int?
    public let label: String?
    public let kind: String?
    public let updated: String?
    public let summary: String?
    public let stale: Bool?
    public var id: String { name }
    public var isFolder: Bool { type == "scope" || type == "dir" }
}

public struct MemoryListing: Equatable, Sendable, Decodable {
    public let path: String
    public let children: [MemoryEntry]
    public let more: Int
}

public struct MemoryPage: Equatable, Sendable, Decodable {
    public let path: String
    public let markdown: String
    public let stale: Bool?
}

public struct MemoryUnlock: Equatable, Sendable, Decodable {
    public let unlocked: Bool
    public let idleS: Int?
}

/// The tree path of a child entry: folders end with "/" in listings, items and pages do not.
public func memoryPath(_ parent: String, _ entry: MemoryEntry) -> String {
    let base = parent.hasSuffix("/") ? String(parent.dropLast()) : parent
    let name = entry.name.hasSuffix("/") ? String(entry.name.dropLast()) : entry.name
    return base + "/" + name
}

/// The parent of a tree path; the root is its own parent.
public func memoryParent(_ path: String) -> String {
    let parts = path.split(separator: "/")
    return parts.count <= 1 ? "/" : "/" + parts.dropLast().joined(separator: "/")
}

extension ControlClient {
    public func memoryUnlock() throws(ControlError) -> MemoryUnlock {
        try call("memory.unlock", as: MemoryUnlock.self, timeout: Self.moveTimeout)
    }
    public func memoryLock() throws(ControlError) -> MemoryUnlock { try call("memory.lock", as: MemoryUnlock.self) }
    public func memoryList(_ path: String) throws(ControlError) -> MemoryListing {
        try call("memory.ls", ["path": .string(path), "depth": .int(1)], as: MemoryListing.self)
    }
    public func memoryRead(_ path: String) throws(ControlError) -> MemoryPage {
        try call("memory.cat", ["path": .string(path)], as: MemoryPage.self)
    }
}
