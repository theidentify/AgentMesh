import ControlCore
import SwiftUI

struct AuditView: View {
    let model: StatusModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Last 100 control-plane actions, newest first").font(.headline)
                Spacer()
                Button { Task { await model.loadAudit() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
            }
            .padding(10)
            if let error = model.auditError { Text(error).foregroundStyle(.red).padding(.horizontal, 10) }
            Table(model.audit.indices.map { IndexedRow(index: $0, row: model.audit[$0]) }) {
                TableColumn("Time") { Text(time($0.row.ts)).monospacedDigit() }.width(140)
                TableColumn("Operation") { Text($0.row.op).font(.body.monospaced()) }.width(130)
                TableColumn("Args") { Text(args($0.row)).lineLimit(1) }
                TableColumn("Outcome") { Text($0.row.outcome).foregroundStyle(outcomeColor($0.row.outcome)) }.width(120)
                TableColumn("Caller") { Text(caller($0.row)).lineLimit(1).truncationMode(.head) }
            }
        }
        .task { await model.loadAudit() }
    }

    struct IndexedRow: Identifiable { let index: Int; let row: AuditRow; var id: Int { index } }

    private func time(_ ms: Int) -> String {
        Date(timeIntervalSince1970: Double(ms) / 1000).formatted(date: .abbreviated, time: .standard)
    }

    private func args(_ row: AuditRow) -> String {
        (row.args ?? [:]).keys.sorted().map { "\($0)=\(row.args![$0]!)" }.joined(separator: " ")
    }

    private func caller(_ row: AuditRow) -> String {
        [row.callerExe, row.callerPid.map { "pid \($0)" }].compactMap { $0 }.joined(separator: " · ")
    }

    private func outcomeColor(_ outcome: String) -> Color {
        switch outcome {
        case "ok": .green
        case "requested": .secondary
        default: .orange
        }
    }
}
