// When to raise a macOS notification. This Mac has no Discord webhook, so the menu bar app is where alerts surface.
// failed, down and conflict notify at once; failing and stuck only after they last warningDelay (one failed run of a
// 5-minute job stays quiet); a unit that was notified about gets one "recovered" notice. An unreachable supervisor
// notifies after offlineDelay, and again once it answers. A new warning in the key inventory notifies once.
import Foundation

public struct Notice: Equatable, Sendable {
    public let id: String
    public let title: String
    public let body: String
    /// Set for a new project waiting for a scope: the notification then offers one button per scope.
    public var project: String? = nil
    public var scopes: [String] = []
}

public struct Watch: Sendable {
    public var warningDelay: TimeInterval = 600
    public var offlineDelay: TimeInterval = 120
    var since: [String: Date] = [:]
    var notified: Set<String> = []
    var offlineSince: Date?
    var offlineNotified = false
    var askedProjects: Set<String> = []
    var notifiedFindings: Set<String> = []

    public init(warningDelay: TimeInterval = 600, offlineDelay: TimeInterval = 120) {
        self.warningDelay = warningDelay; self.offlineDelay = offlineDelay
    }

    public mutating func update(_ status: Status?, now: Date = Date()) -> [Notice] {
        guard let status else {
            let start = offlineSince ?? now
            offlineSince = start
            if !offlineNotified, now.timeIntervalSince(start) >= offlineDelay {
                offlineNotified = true
                return [Notice(id: "supervisor", title: "AgentMesh supervisor not reachable",
                               body: "control.sock has not answered for \(Int(offlineDelay / 60)) minutes.")]
            }
            return []
        }
        var notices: [Notice] = []
        if offlineNotified {
            notices.append(Notice(id: "supervisor", title: "AgentMesh supervisor is back", body: "control.sock answers again."))
        }
        offlineSince = nil; offlineNotified = false
        let pending = Set(status.projects?.pending ?? [])
        for project in (status.projects?.pending ?? []) where !askedProjects.contains(project) {
            askedProjects.insert(project)
            notices.append(Notice(id: "project." + project, title: "New project needs a scope",
                                  body: "\(project): choose where its memory goes. Until then catch-up holds it back.",
                                  project: project, scopes: status.projects?.scopes ?? []))
        }
        askedProjects.formIntersection(pending)
        for row in status.units {
            let level = severity(row.health)
            if level >= .warning {
                let start = since[row.name] ?? now
                since[row.name] = start
                let delay = level >= .error ? 0 : warningDelay
                if !notified.contains(row.name), now.timeIntervalSince(start) >= delay {
                    notified.insert(row.name)
                    notices.append(Notice(id: row.name, title: "AgentMesh: \(row.name) is \(name(row.health))",
                                          body: detail(row)))
                }
            } else {
                since[row.name] = nil
                if notified.remove(row.name) != nil {
                    notices.append(Notice(id: row.name, title: "AgentMesh: \(row.name) recovered", body: "Now \(name(row.health))."))
                }
            }
        }
        return notices
    }

    /// One notice per warning finding when it first appears; info findings never notify. A finding that clears may
    /// notify again later. nil (a failed poll, or a supervisor without keys.inventory) changes nothing.
    public mutating func update(inventory: Inventory?) -> [Notice] {
        guard let inventory else { return [] }
        let warnings = inventory.findings.filter { $0.severity == "warning" }
        let notices = warnings.filter { !notifiedFindings.contains($0.id) }.map {
            Notice(id: "finding." + $0.id, title: "AgentMesh: " + findingText($0, in: inventory),
                   body: "See Keys & devices in the AgentMesh menu.")
        }
        notifiedFindings = Set(warnings.map(\.id))
        return notices
    }

    func name(_ health: Health) -> String {
        switch health {
        case .idle: "idle"
        case .running: "running"
        case .failing: "failing"
        case .stuck: "stuck"
        case .failed: "failed"
        case .down: "down"
        case .conflict: "in conflict"
        case .missing: "not installed"
        case .unknown(let raw): raw
        }
    }

    func detail(_ row: UnitRow) -> String {
        var parts = ["Backend: \(row.backend)."]
        if let exit = row.lastExit, exit != 0 { parts.append("Last exit \(exit).") }
        switch row.health {
        case .failed: parts.append("Fix it, then Reset from the menu.")
        case .conflict: parts.append("launchd and the supervisor both schedule it.")
        default: parts.append("See its log.")
        }
        return parts.joined(separator: " ")
    }
}
