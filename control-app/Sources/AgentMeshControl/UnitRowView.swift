import ControlCore
import SwiftUI

struct UnitRowView: View {
    let model: StatusModel
    let row: UnitRow
    let info: UnitInfo?
    private var confirmingMove: String? { model.confirmingMove[row.name] }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Circle().fill(color).frame(width: 8, height: 8).help(healthName)
                Text(row.name).font(.body.monospaced())
                Text(row.backend).font(.caption2).padding(.horizontal, 5).padding(.vertical, 1)
                    .background(row.backend == "native" ? Color.accentColor.opacity(0.2) : Color.secondary.opacity(0.15),
                                in: Capsule())
                Spacer()
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                if model.working.contains(row.name) { ProgressView().controlSize(.small) }
                menu
            }
            if let move = row.move {
                Text("Moving to \(move.to == "native" ? "supervisor" : "launchd"): \(move.state)")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let to = confirmingMove { confirmation(to) }
            if let error = model.rowErrors[row.name] {
                Text(error).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, 3)
    }

    private var menu: some View {
        let available = actions(for: row, info: info)
        return Menu {
            ForEach(available, id: \.self) { action in
                Button(action.title) {
                    if case .move(let to) = action { model.confirmingMove[row.name] = to }
                    else { Task { await model.perform(action, unit: row.name) } }
                }
            }
        } label: { Image(systemName: "ellipsis.circle") }
            .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
            .disabled(available.isEmpty || model.working.contains(row.name))
    }

    private func confirmation(_ to: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(moveSummary(row.name, to: to)).font(.caption).fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("Cancel") { model.confirmingMove[row.name] = nil }
                Button("Continue") {
                    model.confirmingMove[row.name] = nil
                    Task { await model.perform(.move(to: to), unit: row.name) }
                }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(8)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6))
    }

    private var detail: String {
        if let pid = row.pid { return "pid \(pid)" + (row.runtimeS.map { " · \($0)s" } ?? "") }
        var parts: [String] = []
        if let exit = row.lastExit, exit != 0 { parts.append("exit \(exit)") }
        if let ago = age(row.job?.at) { parts.append(ago) }
        if parts.isEmpty { parts.append(healthName) }
        return parts.joined(separator: " · ")
    }

    private var healthName: String {
        switch row.health {
        case .idle: "idle"
        case .running: "running"
        case .failing: "failing"
        case .stuck: "stuck"
        case .failed: "failed"
        case .down: "down"
        case .conflict: "conflict"
        case .missing: "not installed"
        case .unknown(let raw): raw
        }
    }

    private var color: Color {
        switch severity(row.health) {
        case .neutral: .secondary
        case .ok: .green
        case .warning: .orange
        case .error, .offline: .red
        }
    }
}
