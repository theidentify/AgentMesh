import Foundation
import Testing
@testable import ControlCore

func row(_ name: String = "ingest", kind: String = "job", backend: String = "launchd", health: Health = .idle,
         move: Move? = nil) -> UnitRow {
    UnitRow(name: name, kind: kind, backend: backend, health: health, pid: nil, runtimeS: nil, lastExit: 0, job: nil, move: move)
}

func info(_ nativeOk: Bool) -> UnitInfo {
    UnitInfo(name: "ingest", label: "org.agentmesh.ingest", kind: "job", wrapped: true, maxRuntimeS: 300, nativeOk: nativeOk)
}

func status(_ rows: [UnitRow]) -> Status {
    Status(units: rows, maintenance: Maintenance(on: false, reason: nil, since: nil),
           quota: Quota(day: nil, calls: 0, dailyLlmCalls: nil, backoffS: 0, backoffUntil: nil))
}

@Test func overallIsWorstAndMissingIsQuiet() {
    #expect(overall(nil) == .offline)
    #expect(overall(status([row(health: .idle), row(health: .missing)])) == .ok)
    #expect(overall(status([row(health: .idle), row(health: .failing)])) == .warning)
    #expect(overall(status([row(health: .failing), row(health: .conflict)])) == .error)
    #expect(overall(status([row(health: .unknown("new"))])) == .warning)
}

@Test func jobActions() {
    #expect(actions(for: row(), info: info(true)) == [.start, .restart, .stop, .move(to: "native")])
    #expect(actions(for: row(backend: "native"), info: info(true)) == [.start, .restart, .stop, .move(to: "launchd")])
}

@Test func serviceCannotStopOrMove() {
    #expect(actions(for: row("broker", kind: "service", health: .running), info: info(false)) == [.start, .restart])
    #expect(actions(for: row("broker", kind: "service"), info: nil) == [.start, .restart])
}

@Test func resetOnlyWhenFailed() {
    #expect(actions(for: row(health: .failed), info: info(false)).contains(.reset))
    #expect(!actions(for: row(health: .failing), info: info(false)).contains(.reset))
}

@Test func nothingDuringMove() {
    #expect(actions(for: row(move: Move(moveId: "m", to: "native", state: "moving")), info: info(true)) == [])
}

@Test func moveArgsAndOps() {
    #expect(Action.move(to: "native").args("ingest") == ["unit": .string("ingest"), "to": .string("native")])
    #expect(Action.stop.args("ingest") == ["unit": .string("ingest")])
    #expect(Action.move(to: "launchd").op == "unit.backend" && Action.reset.op == "unit.reset")
}

@Test func moveText() {
    #expect(moveSummary("ingest", to: "native").hasPrefix("Move ingest to the AgentMesh supervisor."))
    #expect(moveSummary("ingest", to: "launchd").contains("Touch ID or your Mac password"))
    #expect(!moveFinished("moving") && !moveFinished("deciding") && moveFinished("ok") && moveFinished("busy"))
}

@Test func ages() {
    let now = ISO8601DateFormatter().date(from: "2026-10-29T05:10:00Z")!
    #expect(age("2026-10-29T05:00:00+00:00", now: now) == "10m ago")
    #expect(age("2026-10-29T05:09:30.5+00:00", now: now) == "29s ago")
    #expect(age("2026-10-27T05:10:00+00:00", now: now) == "2d ago")
    #expect(age(nil) == nil && age("garbage") == nil)
}

@Test func unitInfoDecodes() throws {
    let raw = #"[{"kind":"job","label":"org.agentmesh.ingest","max_runtime_s":300,"name":"ingest","native_ok":true,"wrapped":true}]"#
    #expect(try decoder().decode([UnitInfo].self, from: Data(raw.utf8)) == [info(true)])
}

@Test func notInstalledHasNoActions() {
    #expect(actions(for: row(health: .missing), info: info(true)) == [])
}

@Test func durations() {
    #expect(duration(45) == "45s" && duration(725) == "12m 5s" && duration(5096) == "1h 24m")
}
