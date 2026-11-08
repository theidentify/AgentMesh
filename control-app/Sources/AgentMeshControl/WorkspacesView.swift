// Shared workspaces: counts from the broker's content-free status, plus what needs a person. Approvals (accepting
// invites, reviewing writes) stay in the CLI, where the broker asks for presence; this view only points at them.
import ControlCore
import SwiftUI

struct WorkspacesView: View {
    let model: StatusModel
    let workspaces: Workspaces

    private var findings: [Finding] { sortedFindings((model.inventory?.findings ?? []).filter(isWorkspaceFinding)) }
    private var warnings: Int { findings.filter { $0.severity == "warning" }.count }

    var body: some View {
        DisclosureGroup(isExpanded: Binding(get: { model.workspacesOpen }, set: { model.workspacesOpen = $0 })) {
            VStack(alignment: .leading, spacing: 8) {
                if let inventory = model.inventory, !findings.isEmpty {
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(findings) { finding in
                            Label(findingText(finding, in: inventory),
                                  systemImage: finding.severity == "warning" ? "exclamationmark.triangle.fill" : "info.circle")
                                .font(.caption).foregroundStyle(finding.severity == "warning" ? Color.orange : Color.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Text("Accept invites and review writes with agentmesh-log in a terminal (it asks for presence).")
                        .font(.caption2).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                ForEach(workspaces.items) { item in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.profile).font(.caption.weight(.semibold))
                        line("Members: " + countsLine(item.members, order: roleOrder))
                        line("Tasks: " + countsLine(item.tasks, order: taskOrder))
                        line("Leases: \(item.leasesLive) live, \(item.leasesExpired) expired · conflicts: \(item.conflicts)")
                        line("Waiting: \(item.pendingReviews) review, \(item.inviteUsesWaiting) invite · over quota: \(item.overQuota)")
                    }
                }
            }
            .padding(.top, 4)
        } label: {
            HStack(spacing: 6) {
                Text("Workspaces").font(.callout.weight(.semibold))
                if warnings > 0 {
                    Text("\(warnings)").font(.caption2.weight(.semibold)).padding(.horizontal, 5).padding(.vertical, 1)
                        .background(.orange.opacity(0.3), in: Capsule())
                }
                Spacer()
            }
            .contentShape(Rectangle())
            .onTapGesture { model.workspacesOpen.toggle() }
        }
    }

    private func line(_ text: String) -> some View {
        Text(text).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
    }
}
