// The supervisor's login item: which state the "Run supervisor at login" toggle shows and what ticking it does.
//
// AgentMesh Control embeds the supervisor's job (Contents/Library/LaunchAgents/<label>.plist) and registers it with
// SMAppService. The legacy plist in ~/Library/LaunchAgents runs the same label, and macOS keys its Login Items record on
// the label. Tested on macOS 27 (see `agentmesh-log control handover`):
// - while the legacy job is loaded, SMAppService reports `enabled` and register() changes nothing;
// - right after the legacy plist leaves LaunchAgents, a register() binds to the stale legacy record and the job fails
//   to spawn (EX_CONFIG) in a loop; unregister() then disables that record and register() gets EPERM;
// - about a minute later macOS drops the stale record (status `notFound`), even with the legacy job still running.
// So the app takes over only from `notFound`: boot the legacy job out, then register at once.
import Foundation

/// SMAppService.Status, mirrored so the decision is testable without ServiceManagement.
public enum ServiceStatus: Sendable { case notRegistered, enabled, requiresApproval, notFound }

/// Who launchd runs the label for now.
public enum LoadedJob: Sendable, Equatable { case none, legacy, app }

public enum SupervisorItem: Sendable, Equatable {
    /// The app's job owns the label.
    case on
    /// Nothing of the app's is registered and no legacy job is loaded: ticking registers.
    case off
    /// The legacy plist is still in ~/Library/LaunchAgents: the owner runs `agentmesh-log control handover` first.
    case needsHandover
    /// The legacy plist is parked but macOS still keeps its record: ticking now would break the job, so wait.
    case waiting
    /// The legacy job runs from a parked plist and macOS has forgotten it: ticking boots it out and registers.
    case ready
    /// The owner turned the item off in System Settings > Login Items.
    case requiresApproval

    public static func decide(status: ServiceStatus, loaded: LoadedJob, legacyPlist: Bool) -> SupervisorItem {
        if status == .requiresApproval { return .requiresApproval }
        if status == .enabled && loaded != .legacy { return .on }
        if legacyPlist { return .needsHandover }
        if loaded == .legacy { return status == .notFound ? .ready : .waiting }
        return .off
    }

    public var isOn: Bool { self == .on }
    /// Waiting on something outside the app, so worth re-checking even while the menu is closed.
    public var pending: Bool { self == .needsHandover || self == .waiting || self == .ready }
    /// Whether the toggle may be ticked or unticked in this state.
    public var actionable: Bool { self == .on || self == .off || self == .ready }

    public var caption: String? {
        switch self {
        case .on, .off: nil
        case .needsHandover: "The supervisor runs from its launchd plist. To move it here, run `agentmesh-log control handover` in Terminal."
        case .waiting: "macOS is still forgetting the supervisor's old login item. This takes about a minute."
        case .ready: "Ready: ticking restarts the supervisor under AgentMesh Control (about a second)."
        case .requiresApproval: "Turned off in System Settings > General > Login Items & Extensions."
        }
    }
}

/// Who runs the label, from `launchctl print gui/<uid>/<label>` (exit status and output).
public func loadedJob(printExit: Int32, output: String) -> LoadedJob {
    guard printExit == 0 else { return .none }
    for line in output.split(separator: "\n") where line.hasPrefix("\tpath = ") && !line.hasPrefix("\t\t") {
        return line.dropFirst("\tpath = ".count).hasPrefix("(submitted by") ? .app : .legacy
    }
    return .legacy
}
