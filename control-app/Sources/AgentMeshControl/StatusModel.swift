// Polls the supervisor and sends actions. Socket calls block, so they run off the main actor; state changes on it.
import ControlCore
import Foundation
import Observation

@MainActor @Observable
final class StatusModel {
    let client: ControlClient
    var status: Status?
    var infos: [String: UnitInfo] = [:]
    var lastError: String?
    var rowErrors: [String: String] = [:]
    var working: Set<String> = []
    var audit: [AuditRow] = []
    var auditError: String?
    var menuOpen = false { didSet { if menuOpen { Task { await refresh() } } } }
    // View state lives here: this toolchain (CommandLineTools) has no SwiftUIMacros plugin, so no @State.
    var reason = ""
    var askingReason = false
    var confirmingMove: [String: String] = [:]
    var loginError: String?
    private var poller: Task<Void, Never>?

    init(client: ControlClient = ControlClient()) { self.client = client }

    func start() {
        guard poller == nil else { return }
        poller = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh()
                try? await Task.sleep(for: .seconds(self.menuOpen ? 5 : 30))
            }
        }
    }

    nonisolated func run<R: Sendable>(_ body: @escaping @Sendable () throws -> R) async -> Result<R, ControlError> {
        await Task.detached {
            do { return .success(try body()) }
            catch let error as ControlError { return .failure(error) }
            catch { return .failure(.protocolError("unexpected")) }
        }.value
    }

    func refresh() async {
        let client = client
        switch await run({ try client.status() }) {
        case .success(let value): status = value; lastError = nil
        case .failure(let error): status = nil; lastError = describe(error)
        }
        if infos.isEmpty, status != nil,
           case .success(let list) = await run({ try client.call("units", as: [UnitInfo].self) }) {
            infos = Dictionary(uniqueKeysWithValues: list.map { ($0.name, $0) })
        }
    }

    func perform(_ action: Action, unit: String) async {
        let client = client
        working.insert(unit); rowErrors[unit] = nil
        defer { working.remove(unit) }
        if case .move = action {
            switch await run({ try client.call(action.op, action.args(unit), as: MoveAccepted.self) }) {
            case .success(let accepted): await refresh(); await track(accepted.moveId, unit: unit)
            case .failure(let error): rowErrors[unit] = describe(error)
            }
        } else if case .failure(let error) = await run({ try client.call(action.op, action.args(unit), as: JSONValue.self) }) {
            rowErrors[unit] = describe(error)
        }
        await refresh()
    }

    /// Follows an accepted move until it ends; the supervisor may wait up to 10 minutes for launchd to go idle.
    func track(_ moveId: String, unit: String) async {
        let client = client
        for _ in 0..<400 {
            try? await Task.sleep(for: .seconds(2))
            switch await run({ try client.call("moves.get", ["move_id": .string(moveId)], as: MoveState.self) }) {
            case .success(let move) where moveFinished(move.state):
                if move.state != "ok" { rowErrors[unit] = describe(.server(code: move.state, message: "")) }
                return
            case .success: await refresh()
            case .failure(let error): rowErrors[unit] = describe(error); return
            }
        }
    }

    func setMaintenance(on: Bool, reason: String) async {
        let client = client
        let result = on
            ? await run({ try client.call("maintenance.on", ["reason": .string(reason)], as: JSONValue.self) })
            : await run({ try client.call("maintenance.off", as: JSONValue.self) })
        if case .failure(let error) = result { lastError = describe(error) }
        await refresh()
    }

    func loadAudit() async {
        let client = client
        switch await run({ try client.call("audit.tail", ["limit": .int(100)], as: [AuditRow].self) }) {
        case .success(let rows): audit = rows.reversed(); auditError = nil
        case .failure(let error): auditError = describe(error)
        }
    }
}
