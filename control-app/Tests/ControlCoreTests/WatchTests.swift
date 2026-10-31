import Foundation
import Testing
@testable import ControlCore

let t0 = Date(timeIntervalSince1970: 1_800_000_000)
func at(_ seconds: TimeInterval) -> Date { t0.addingTimeInterval(seconds) }
func units(_ pairs: (String, Health)...) -> Status { status(pairs.map { row($0.0, health: $0.1) }) }

@Test func errorNotifiesAtOnceAndRecovers() {
    var watch = Watch()
    #expect(watch.update(units(("ingest", .idle)), now: at(0)).isEmpty)
    let down = watch.update(units(("ingest", .failed)), now: at(30))
    #expect(down.map(\.title) == ["AgentMesh: ingest is failed"] && down[0].body.contains("Reset"))
    #expect(watch.update(units(("ingest", .failed)), now: at(60)).isEmpty)
    #expect(watch.update(units(("ingest", .idle)), now: at(90)).map(\.title) == ["AgentMesh: ingest recovered"])
    #expect(watch.update(units(("ingest", .idle)), now: at(120)).isEmpty)
}

@Test func briefFailingStaysQuiet() {
    var watch = Watch()
    #expect(watch.update(units(("catch-up", .failing)), now: at(0)).isEmpty)
    #expect(watch.update(units(("catch-up", .failing)), now: at(599)).isEmpty)
    #expect(watch.update(units(("catch-up", .idle)), now: at(600)).isEmpty)
    #expect(watch.update(units(("catch-up", .failing)), now: at(900)).isEmpty)  // the clock restarts after a clean poll
}

@Test func lastingFailingNotifiesOnce() {
    var watch = Watch()
    _ = watch.update(units(("catch-up", .failing)), now: at(0))
    #expect(watch.update(units(("catch-up", .failing)), now: at(600)).map(\.title) == ["AgentMesh: catch-up is failing"])
    #expect(watch.update(units(("catch-up", .stuck)), now: at(900)).isEmpty)
    #expect(watch.update(units(("catch-up", .failed)), now: at(1200)).isEmpty)  // already notified for this episode
}

@Test func missingNeverNotifies() {
    var watch = Watch()
    #expect(watch.update(units(("retention", .missing)), now: at(0)).isEmpty)
    #expect(watch.update(units(("retention", .missing)), now: at(10_000)).isEmpty)
}

@Test func offlineAfterDelayThenBack() {
    var watch = Watch()
    #expect(watch.update(nil, now: at(0)).isEmpty)
    #expect(watch.update(nil, now: at(119)).isEmpty)
    #expect(watch.update(nil, now: at(120)).map(\.title) == ["AgentMesh supervisor not reachable"])
    #expect(watch.update(nil, now: at(400)).isEmpty)
    #expect(watch.update(units(("ingest", .idle)), now: at(430)).map(\.title) == ["AgentMesh supervisor is back"])
}

@Test func shortOutageIsQuiet() {
    var watch = Watch()
    _ = watch.update(nil, now: at(0))
    #expect(watch.update(units(("ingest", .idle)), now: at(60)).isEmpty)
    #expect(watch.update(nil, now: at(90)).isEmpty)  // a new outage starts its own clock
    #expect(watch.update(nil, now: at(200)).isEmpty)
}

@Test func failingAtLaunchStillWaits() {
    var watch = Watch()
    #expect(watch.update(units(("ingest", .failing), ("broker", .down)), now: at(0)).map(\.title) == ["AgentMesh: broker is down"])
}

func withProjects(_ pending: [String]) -> Status {
    var value = units(("ingest", .idle))
    value.projects = Projects(pending: pending, scopes: ["personal/common", "work/common", "skip"])
    return value
}

@Test func newProjectAskedOnce() {
    var watch = Watch()
    let first = watch.update(withProjects(["learn-x"]), now: at(0))
    #expect(first.count == 1 && first[0].project == "learn-x" && first[0].scopes == ["personal/common", "work/common", "skip"])
    #expect(first[0].title == "New project needs a scope" && first[0].body.hasPrefix("learn-x:"))
    #expect(watch.update(withProjects(["learn-x"]), now: at(30)).isEmpty)
    #expect(watch.update(withProjects(["learn-x", "other"]), now: at(60)).map(\.project) == ["other"])
}

@Test func mappedProjectCanBeAskedAgainLater() {
    var watch = Watch()
    _ = watch.update(withProjects(["learn-x"]), now: at(0))
    #expect(watch.update(withProjects([]), now: at(30)).isEmpty)
    #expect(watch.update(withProjects(["learn-x"]), now: at(60)).count == 1)
}

@Test func olderSupervisorHasNoProjects() {
    var watch = Watch()
    #expect(watch.update(units(("ingest", .idle)), now: at(0)).isEmpty)
}
