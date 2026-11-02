import Foundation
import Testing
@testable import ControlCore

@Test func memoryPaths() {
    let folder = MemoryEntry(name: "decisions/", type: "dir", count: 2, label: nil, kind: nil, updated: nil, summary: nil, stale: nil)
    let page = MemoryEntry(name: "INDEX.md", type: "page", count: nil, label: "general", kind: nil, updated: nil, summary: "s", stale: true)
    #expect(memoryPath("/", folder) == "/decisions")
    #expect(memoryPath("/work/a", folder) == "/work/a/decisions")
    #expect(memoryPath("/work/a/", page) == "/work/a/INDEX.md")
    #expect(folder.isFolder && !page.isFolder)
    #expect(memoryParent("/work/a/decisions") == "/work/a")
    #expect(memoryParent("/work") == "/")
    #expect(memoryParent("/") == "/")
}

@Test func memoryDecoding() throws {
    let raw = #"{"path":"/work","children":[{"name":"a/","type":"scope","count":3,"updated":"2026-11-02T00:00:00Z","summary":null},{"name":"INDEX.md","type":"page","label":"internal","updated":"2026-11-02T00:00:00Z","summary":"x","stale":false}],"more":0}"#
    let listing = try decoder().decode(MemoryListing.self, from: Data(raw.utf8))
    #expect(listing.children.map(\.name) == ["a/", "INDEX.md"])
    #expect(listing.children[1].stale == false && listing.children[0].count == 3)
    let unlock = try decoder().decode(MemoryUnlock.self, from: Data(#"{"unlocked":true,"idle_s":300}"#.utf8))
    #expect(unlock == MemoryUnlock(unlocked: true, idleS: 300))
    #expect(ControlClient.presenceOps.contains("memory.unlock"))
}
