// The connector approval window: pops up by itself when the gateway has a new request. The owner types the code the
// browser shows; the supervisor checks it belongs to the shown request, then asks for Touch ID with its own helper.
import AppKit
import ControlCore
import SwiftUI

@MainActor @Observable
final class ApproveModel {
    static let shared = ApproveModel(client: StatusModel.shared.client)
    static let pollSeconds: UInt64 = 30

    let client: ControlClient
    var requests: [ConnectorRequest] = []
    var nowMs = 0
    var selected: String?
    var code = ""
    var busy = false
    var error: String?
    var approved: ConnectorApproved?
    private var seen: Set<String> = []
    private var started = false
    private var panel: NSPanel?

    init(client: ControlClient) { self.client = client }

    func start() {
        guard !started else { return }
        started = true
        Task {
            var failures = 0
            while true {
                failures = await poll(popUp: true) ? 0 : failures + 1
                try? await Task.sleep(nanoseconds: connectorPollDelay(failures: failures, base: Self.pollSeconds) * 1_000_000_000)
            }
        }
    }

    /// false when the supervisor or the gateway refused, so the loop backs off.
    @discardableResult
    func poll(popUp: Bool) async -> Bool {
        let client = client
        let result = await Task.detached { () -> Result<ConnectorPending, ControlError> in
            do { return .success(try client.connectorPending()) }
            catch let error as ControlError { return .failure(error) }
            catch { return .failure(.protocolError("unexpected")) }
        }.value
        guard case .success(let pending) = result else {
            // Polling stays quiet: an unconfigured Mac or an offline gateway is not worth a window.
            if !popUp, case .failure(let failure) = result { error = describeConnector(failure) }
            return false
        }
        nowMs = pending.nowMs ?? Int(Date().timeIntervalSince1970 * 1000)
        requests = pending.requests
        if selected == nil || !requests.contains(where: { $0.codeHash == selected }) { selected = requests.last?.codeHash }
        let fresh = newRequests(requests, seen: seen)
        seen.formUnion(requests.map(\.codeHash))
        if popUp, let newest = fresh.last { selected = newest.codeHash; approved = nil; show() }
        return true
    }

    func show() {
        if panel == nil {
            let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 420, height: 300),
                                styleMask: [.titled, .closable, .utilityWindow], backing: .buffered, defer: false)
            panel.title = "Approve a Claude connector"
            panel.level = .floating
            panel.isReleasedWhenClosed = false
            panel.contentView = NSHostingView(rootView: ApproveView(model: self))
            panel.center()
            self.panel = panel
        }
        NSApp.activate()
        panel?.makeKeyAndOrderFront(nil)
    }

    func open() { show(); Task { await poll(popUp: false) } }

    func close() { panel?.orderOut(nil); code = "" }

    func approve() async {
        guard let hash = selected else { return }
        guard let typed = normalizedCode(code) else { error = "Type the 6-digit code shown in the browser."; return }
        busy = true; error = nil; defer { busy = false }
        let client = client
        let result = await Task.detached { () -> Result<ConnectorApproved, ControlError> in
            do { return .success(try client.connectorApprove(code: typed, codeHash: hash)) }
            catch let error as ControlError { return .failure(error) }
            catch { return .failure(.protocolError("unexpected")) }
        }.value
        code = ""
        switch result {
        case .success(let value): approved = value; requests.removeAll { $0.codeHash == hash }
        case .failure(let failure): error = describeConnector(failure)
        }
    }
}

struct ApproveView: View {
    @Bindable var model: ApproveModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let done = model.approved {
                Label("Approved \(done.clientName ?? "the connector")", systemImage: "checkmark.seal.fill").font(.headline)
                Text("Reads \((done.read ?? []).joined(separator: ", ")); remembers into \(done.remember ?? "its inbox").")
                Text("Go back to the browser and press Continue after approval.").foregroundStyle(.secondary)
                HStack { Spacer(); Button("Done") { model.approved = nil; model.close() }.keyboardShortcut(.defaultAction) }
            } else if model.requests.isEmpty {
                Text("No connector is waiting for approval.").font(.headline)
                Text("Start the connection in Claude; this window opens by itself when the request arrives.")
                    .foregroundStyle(.secondary)
                if let error = model.error { Text(error).foregroundStyle(.red) }
                HStack { Button("Check again") { Task { await model.poll(popUp: false) } }; Spacer(); Button("Close") { model.close() } }
            } else {
                Text("A connector is asking for access").font(.headline)
                Picker("Request", selection: $model.selected) {
                    ForEach(model.requests) { request in
                        Text("\(request.clientName) · \(remaining(request, nowMs: model.nowMs))").tag(Optional(request.codeHash))
                    }
                }
                .pickerStyle(.radioGroup)
                Text("Type the 6-digit code shown in the browser. Approve only a request you started.")
                    .foregroundStyle(.secondary)
                TextField("123456", text: $model.code)
                    .font(.system(size: 22, weight: .medium, design: .monospaced))
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await model.approve() } }
                if let error = model.error { Text(error).foregroundStyle(.red) }
                HStack {
                    Button("Not now") { model.close() }
                    Spacer()
                    if model.busy { ProgressView().controlSize(.small) }
                    Button("Approve with Touch ID…") { Task { await model.approve() } }
                        .keyboardShortcut(.defaultAction)
                        .disabled(model.busy || normalizedCode(model.code) == nil || model.selected == nil)
                }
            }
        }
        .padding(18)
        .frame(width: 420)
    }
}
