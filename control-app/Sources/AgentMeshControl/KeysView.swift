// Keys & devices: the supervisor's read-only keys.inventory. Findings come first; the rest is reference. Nothing here
// changes a key or a grant (device and grant ops arrive in M3d, behind the approval state machine).
import ControlCore
import SwiftUI

struct KeysView: View {
    let model: StatusModel

    var body: some View {
        DisclosureGroup(isExpanded: Binding(get: { model.keysOpen }, set: { model.keysOpen = $0 })) {
            if let inventory = model.inventory { details(inventory) }
            else if let error = model.inventoryError {
                Text(error).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            } else { ProgressView().controlSize(.small) }
        } label: { label }
    }

    private var warnings: Int { model.inventory?.findings.filter { $0.severity == "warning" }.count ?? 0 }

    private var label: some View {
        HStack(spacing: 6) {
            Text("Keys & devices").font(.callout.weight(.semibold))
            if warnings > 0 {
                Text("\(warnings)").font(.caption2.weight(.semibold)).padding(.horizontal, 5).padding(.vertical, 1)
                    .background(.orange.opacity(0.3), in: Capsule())
            }
            Spacer()
            if let inventory = model.inventory, let checked = age(inventory.generatedAt) {
                Text("checked \(checked)").font(.caption2).foregroundStyle(.secondary)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture { model.keysOpen.toggle() }
    }

    private func details(_ inventory: Inventory) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            let findings = sortedFindings(inventory.findings)
            if findings.isEmpty {
                Label("Nothing needs attention", systemImage: "checkmark.circle").font(.caption).foregroundStyle(.green)
            } else {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(findings) { finding in
                        Label(findingText(finding, in: inventory),
                              systemImage: finding.severity == "warning" ? "exclamationmark.triangle.fill" : "info.circle")
                            .font(.caption).foregroundStyle(finding.severity == "warning" ? Color.orange : Color.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            section("Devices") {
                if let devices = inventory.devices {
                    ForEach(devices) { device in line(deviceLine(device)) }
                } else { line("Not reported by the broker yet") }
            }
            section("Agents") {
                if let agents = inventory.agents {
                    ForEach(agents, id: \.keyRef) { agent in line(agentLine(agent)) }
                } else { line("Not reported by the broker yet") }
            }
            section("Key files") {
                ForEach(inventory.keyFiles + [inventory.keystore], id: \.path) { file in
                    HStack(spacing: 6) {
                        Text(file.path).font(.caption.monospaced()).lineLimit(1).truncationMode(.middle)
                        Spacer()
                        Text(fileDetail(file)).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            section("Recovery and backup") {
                line(inventory.recovery.map { $0.count == 0 ? "No recovery kit" : "Recovery kit set up\($0.latestIssuedAt.flatMap { age($0) }.map { ", \($0)" } ?? "")" }
                     ?? "Recovery: not reported by the broker yet")
                line(inventory.backup.map { "Last backup \(age($0.at) ?? $0.at): \($0.files) files" } ?? "No backup recorded")
            }
        }
        .padding(.top, 4)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            content()
        }
    }

    private func line(_ text: String) -> some View {
        Text(text).font(.caption).lineLimit(1).truncationMode(.middle)
    }

    private func agentLine(_ agent: AgentGrant) -> String {
        var parts = [agent.name ?? "unnamed (\(agent.keyRef.prefix(8)))"]
        if let read = agent.read { parts.append("reads " + (read.isEmpty ? "nothing" : read.joined(separator: ", "))) }
        if let until = agent.secretUntil { parts.append("secret until \(until.prefix(16).replacingOccurrences(of: "T", with: " "))") }
        return parts.joined(separator: " · ")
    }

    private func fileDetail(_ file: KeyFile) -> String {
        if file.kind != "file" { return file.kind }
        var parts = [modeText(file.mode)]
        if let agent = file.agent { parts.append(agent) }
        if let modified = age(file.modifiedAt) { parts.append(modified) }
        return parts.joined(separator: " · ")
    }
}
