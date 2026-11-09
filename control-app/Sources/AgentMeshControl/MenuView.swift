import ControlCore
import ServiceManagement
import SwiftUI

struct MenuView: View {
    let model: StatusModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            if let error = model.lastError {
                Text(error).font(.callout).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
            if let status = model.status {
                VStack(spacing: 2) {
                    ForEach(status.units) { row in
                        UnitRowView(model: model, row: row, info: model.infos[row.name])
                    }
                }
                if let projects = status.projects, !projects.pending.isEmpty {
                    Divider()
                    pendingProjects(projects)
                }
                if model.inventorySupported {
                    Divider()
                    KeysView(model: model)
                    if let workspaces = model.inventory?.workspaces, !workspaces.items.isEmpty {
                        Divider()
                        WorkspacesView(model: model, workspaces: workspaces)
                    }
                }
                Divider()
                maintenance(status.maintenance)
                Text(quotaText(status.quota)).font(.caption).foregroundStyle(.secondary)
            }
            if model.supervisorItem.available {
                Divider()
                SupervisorItemView(item: model.supervisorItem)
            }
            Divider()
            footer
        }
        .padding(12)
        .frame(width: 400)
        .background(MenuBarAnchor())  // keep the panel attached to the menu bar when sections expand or collapse
    }

    private var header: some View {
        HStack {
            Text("AgentMesh").font(.headline)
            if model.status?.maintenance.on == true {
                Text("maintenance").font(.caption).padding(.horizontal, 6).padding(.vertical, 1)
                    .background(.orange.opacity(0.25), in: Capsule())
            }
            Spacer()
            Button { Task { await model.refresh(inventory: true) } } label: { Image(systemName: "arrow.clockwise") }
                .buttonStyle(.borderless).help("Refresh")
        }
    }

    @ViewBuilder
    private func maintenance(_ state: Maintenance) -> some View {
        if state.on {
            HStack {
                Text("Maintenance: \(state.reason ?? "on")").font(.callout)
                Spacer()
                Button("Turn off") { Task { await model.setMaintenance(on: false, reason: "") } }
            }
        } else if model.askingReason {
            HStack {
                TextField("Reason", text: Binding(get: { model.reason }, set: { model.reason = $0 }))
                    .textFieldStyle(.roundedBorder).onSubmit(turnOn)
                Button("Pause jobs", action: turnOn).disabled(model.reason.trimmingCharacters(in: .whitespaces).isEmpty)
                Button("Cancel") { model.askingReason = false; model.reason = "" }
            }
        } else {
            Button("Maintenance mode…") { model.askingReason = true }
                .help("Pause scheduled jobs (ingest, summary, retention). The broker keeps running.")
        }
    }

    private func pendingProjects(_ projects: Projects) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("New project needs a scope").font(.callout.weight(.semibold))
            Text("The summary holds back its memory until you choose.").font(.caption).foregroundStyle(.secondary)
            ForEach(projects.pending, id: \.self) { project in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(project).font(.caption.monospaced()).lineLimit(1).truncationMode(.middle)
                        Spacer()
                        if model.working.contains("project." + project) { ProgressView().controlSize(.small) }
                        ForEach(projects.scopes, id: \.self) { scope in
                            Button(scopeTitle(scope)) { model.confirmingScope[project] = scope }
                                .disabled(model.working.contains("project." + project))
                        }
                    }
                    if let scope = model.confirmingScope[project] {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(mappingSummary(project, to: scope)).font(.caption).fixedSize(horizontal: false, vertical: true)
                            HStack {
                                Spacer()
                                Button("Cancel") { model.confirmingScope[project] = nil }
                                Button("Continue") {
                                    model.confirmingScope[project] = nil
                                    Task { await model.choose(scope, for: project) }
                                }.keyboardShortcut(.defaultAction)
                            }
                        }
                        .padding(8).background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6))
                    }
                    if let error = model.projectErrors[project] {
                        Text(error).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private func turnOn() {
        let text = model.reason.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return }
        model.askingReason = false; model.reason = ""
        Task { await model.setMaintenance(on: true, reason: text) }
    }

    private func quotaText(_ quota: Quota) -> String {
        let cap = quota.dailyLlmCalls.map { " of \($0)" } ?? ""
        let backoff = quota.backoffS > 0 ? " · backing off \(quota.backoffS)s" : ""
        return "LLM calls today: \(quota.calls)\(cap)\(backoff)"
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Button("Audit…") { openWindow(id: "audit"); NSApp.activate() }
                Button("Memory…") { openWindow(id: "memory"); NSApp.activate() }
                Button("Approve connector…") { ApproveModel.shared.open() }
                Spacer()
                Toggle("Launch at login", isOn: Binding(get: { SMAppService.mainApp.status == .enabled }, set: setLoginItem))
                    .toggleStyle(.checkbox)
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }
            }
            if let error = model.loginError { Text(error).font(.caption).foregroundStyle(.red) }
            Text("AgentMesh Control " + versionText(
                short: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
                build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String))
                .font(.caption2).foregroundStyle(.secondary).textSelection(.enabled)
        }
    }

    private func setLoginItem(_ on: Bool) {
        // Unbundled, SMAppService registers the bare executable as a legacy login item, which macOS then opens in
        // Terminal at every login. Only the installed bundle may register; unregistering stays allowed everywhere.
        if on && Bundle.main.bundleIdentifier == nil {
            model.loginError = "Launch at login needs the installed app (control-app/bundle.sh)."
            return
        }
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            model.loginError = nil
        } catch {
            model.loginError = "Launch at login needs the installed app (control-app/bundle.sh)."
        }
    }
}

/// "Run supervisor at login": the supervisor's own background item, from this app's bundle.
struct SupervisorItemView: View {
    let item: SupervisorLoginItem

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Toggle("Run supervisor at login", isOn: Binding(get: { item.state.isOn }, set: { _ in item.confirming = true }))
                    .toggleStyle(.checkbox).disabled(!item.state.actionable || item.working || item.confirming)
                Spacer()
                if item.working { ProgressView().controlSize(.small) }
                if item.state == .requiresApproval {
                    Button("Open Login Items") { SMAppService.openSystemSettingsLoginItems() }
                }
            }
            if let caption = item.state.caption {
                Text(caption).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if item.confirming {
                VStack(alignment: .leading, spacing: 6) {
                    Text(item.state.isOn
                         ? "The supervisor and every unit it runs stop, and nothing starts them again until you tick this or run `agentmesh-log control handover --back`."
                         : item.state == .ready
                         ? "The supervisor restarts under AgentMesh Control; its units are down for about a second."
                         : "The supervisor starts from AgentMesh Control and at every login.")
                        .font(.caption).fixedSize(horizontal: false, vertical: true)
                    HStack {
                        Spacer()
                        Button("Cancel") { item.confirming = false }
                        Button(item.state.isOn ? "Stop supervisor" : "Continue") {
                            let on = !item.state.isOn
                            item.confirming = false
                            Task { await item.apply(on) }
                        }.keyboardShortcut(.defaultAction)
                    }
                }
                .padding(8).background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6))
            }
            if let error = item.error {
                Text(error).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
