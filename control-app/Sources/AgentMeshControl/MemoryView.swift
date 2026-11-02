// Read-only memory tree. The supervisor unlocks it with its own Touch ID sheet and locks it again when idle;
// this window keeps content in memory only and drops it when it locks or closes.
import ControlCore
import SwiftUI

@MainActor @Observable
final class MemoryModel {
    let client: ControlClient
    var path = "/"
    var listing: MemoryListing?
    var page: MemoryPage?
    var locked = true
    var busy = false
    var error: String?

    init(client: ControlClient) { self.client = client }

    nonisolated func run<R: Sendable>(_ body: @escaping @Sendable () throws -> R) async -> Result<R, ControlError> {
        await Task.detached {
            do { return .success(try body()) }
            catch let error as ControlError { return .failure(error) }
            catch { return .failure(.protocolError("unexpected")) }
        }.value
    }

    func handle(_ error: ControlError) {
        if case .server(code: "locked", _) = error { clear(); return }
        switch error {
        case .server(code: "not_configured", _): self.error = "Set memory_tree.agent_key in settings.local.json first."
        case .server(code: "presence_required", _): self.error = "Not unlocked."
        case .server(code: let code, _): self.error = code.replacingOccurrences(of: "_", with: " ")
        case .unreachable: self.error = "The supervisor is not running."
        default: self.error = "Request failed."
        }
    }

    func clear() { locked = true; listing = nil; page = nil }

    func unlock() async {
        busy = true; defer { busy = false }
        let client = client
        switch await run({ try client.memoryUnlock() }) {
        case .success: locked = false; error = nil; await open("/")
        case .failure(let failure): handle(failure)
        }
    }

    func lock() async {
        let client = client
        _ = await run({ try client.memoryLock() })
        clear()
    }

    func open(_ target: String) async {
        let client = client
        switch await run({ try client.memoryList(target) }) {
        case .success(let value): listing = value; path = value.path; page = nil; error = nil; locked = false
        case .failure(let failure): handle(failure)
        }
    }

    func read(_ entry: MemoryEntry) async {
        let client = client, target = memoryPath(path, entry)
        switch await run({ try client.memoryRead(target) }) {
        case .success(let value): page = value; error = nil
        case .failure(let failure): handle(failure)
        }
    }
}

struct MemoryView: View {
    let model: MemoryModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header.padding(10)
            if let error = model.error { Text(error).foregroundStyle(.red).padding(.horizontal, 10).padding(.bottom, 6) }
            Divider()
            if model.locked { lockedPane } else { browser }
        }
        .onDisappear { model.clear() }
    }

    private var header: some View {
        HStack {
            Button { Task { await model.open(memoryParent(model.path)) } } label: { Image(systemName: "chevron.up") }
                .disabled(model.locked || model.path == "/")
            Text(model.locked ? "Memory is locked" : model.path).font(.headline.monospaced()).lineLimit(1).truncationMode(.head)
            Spacer()
            if !model.locked {
                Button { Task { await model.open(model.path) } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                Button { Task { await model.lock() } } label: { Label("Lock", systemImage: "lock") }
            }
        }
    }

    private var lockedPane: some View {
        VStack(spacing: 12) {
            Image(systemName: "lock.shield").font(.largeTitle).foregroundStyle(.secondary)
            Text("Viewing memory needs Touch ID or your Mac password. It locks again after 5 minutes without use.")
                .multilineTextAlignment(.center).foregroundStyle(.secondary)
            Button("Unlock…") { Task { await model.unlock() } }.disabled(model.busy)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity).padding()
    }

    private var browser: some View {
        HSplitView {
            List(model.listing?.children ?? []) { entry in
                Button { Task { entry.isFolder ? await model.open(memoryPath(model.path, entry)) : await model.read(entry) } } label: {
                    row(entry)
                }
                .buttonStyle(.plain)
            }
            .frame(minWidth: 280)
            ScrollView {
                if let page = model.page {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text(page.path).font(.caption.monospaced()).foregroundStyle(.secondary)
                            if page.stale == true { badge("stale", .orange) }
                        }
                        Text(rendered(page.markdown)).textSelection(.enabled)
                    }
                    .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    Text("Select a page or an item.").foregroundStyle(.secondary).padding(12)
                }
            }
            .frame(minWidth: 360)
        }
    }

    private func row(_ entry: MemoryEntry) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Image(systemName: entry.isFolder ? "folder" : entry.type == "page" ? "doc.text" : "doc")
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(entry.name).font(.body.monospaced()).lineLimit(1)
                    if let count = entry.count { Text("\(count)").foregroundStyle(.secondary).monospacedDigit() }
                    if entry.stale == true { badge("stale", .orange) }
                    if entry.label == "secret" { badge("secret", .red) }
                }
                if let summary = entry.summary { Text(summary).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
            }
        }
        .contentShape(Rectangle())
    }

    private func badge(_ text: String, _ color: Color) -> some View {
        Text(text).font(.caption2).padding(.horizontal, 4).background(color.opacity(0.2), in: .capsule)
    }

    private func rendered(_ markdown: String) -> AttributedString {
        (try? AttributedString(markdown: markdown, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(markdown)
    }
}
