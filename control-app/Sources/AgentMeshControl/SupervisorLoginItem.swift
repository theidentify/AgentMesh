// "Run supervisor at login": registers the supervisor's job embedded in this bundle with SMAppService, so macOS lists
// one background item that belongs to AgentMesh Control. The decision lives in ControlCore.SupervisorItem.
import ControlCore
import Foundation
import Observation
import ServiceManagement

@MainActor @Observable
final class SupervisorLoginItem {
    /// org.agentmesh.control, or a test build's label (bundle.sh --label).
    let label = Bundle.main.object(forInfoDictionaryKey: "AgentMeshSupervisorLabel") as? String ?? "org.agentmesh.control"
    var state: SupervisorItem = .off
    var error: String?
    var confirming = false
    var working = false

    private var service: SMAppService { SMAppService.agent(plistName: label + ".plist") }
    private var domain: String { "gui/\(getuid())" }

    /// Only an installed bundle carries the job; a `swift run` build has nothing to register.
    var available: Bool {
        Bundle.main.bundleIdentifier != nil && FileManager.default.fileExists(
            atPath: Bundle.main.bundleURL.appendingPathComponent("Contents/Library/LaunchAgents/\(label).plist").path)
    }

    private var legacyPlist: Bool {
        let path = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/\(label).plist").path
        return (try? FileManager.default.attributesOfItem(atPath: path)) != nil  // a symlink counts too
    }

    func refresh() async {
        guard available else { return }
        let (code, out) = await launchctl(["print", "\(domain)/\(label)"])
        state = SupervisorItem.decide(status: mirror(service.status), loaded: loadedJob(printExit: code, output: out),
                                      legacyPlist: legacyPlist)
    }

    /// Tick or untick, after the inline confirmation.
    func apply(_ on: Bool) async {
        working = true; defer { working = false }
        await refresh()
        do {
            if on {
                switch state {
                case .off: try service.register()
                case .ready:
                    // macOS has forgotten the legacy record: boot the legacy job out and register at once.
                    let (code, _) = await launchctl(["bootout", "\(domain)/\(label)"])
                    guard code == 0 else { error = "Could not stop the legacy supervisor (launchctl \(code))."; return }
                    try service.register()
                default: await refresh(); return
                }
            } else if state == .on {
                try await service.unregister()
            }
            error = nil
        } catch {
            self.error = "\(error.localizedDescription) To return to the launchd plist: agentmesh-log control handover --back"
        }
        try? await Task.sleep(for: .seconds(1))
        await refresh()
    }

    private func mirror(_ status: SMAppService.Status) -> ServiceStatus {
        switch status {
        case .enabled: .enabled
        case .requiresApproval: .requiresApproval
        case .notRegistered: .notRegistered
        case .notFound: .notFound
        @unknown default: .notFound
        }
    }

    /// /bin/launchctl with exact argv, off the main actor.
    private func launchctl(_ args: [String]) async -> (Int32, String) {
        await Task.detached {
            let process = Process(); let pipe = Pipe()
            process.executableURL = URL(fileURLWithPath: "/bin/launchctl"); process.arguments = args
            process.standardOutput = pipe; process.standardError = FileHandle.nullDevice
            do { try process.run() } catch { return (Int32(-1), "") }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return (process.terminationStatus, String(decoding: data, as: UTF8.self))
        }.value
    }
}
