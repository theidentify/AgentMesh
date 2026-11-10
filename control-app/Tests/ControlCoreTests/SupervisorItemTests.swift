import Testing
@testable import ControlCore

@Test func legacyLoadedLooksEnabledButIsNotOurs() {
    // SMAppService says enabled while the legacy job holds the label: the toggle must not show on.
    #expect(SupervisorItem.decide(status: .enabled, loaded: .legacy, legacyPlist: true) == .needsHandover)
    #expect(SupervisorItem.decide(status: .notFound, loaded: .legacy, legacyPlist: true) == .needsHandover)
}

@Test func afterHandoverWaitForMacOSToForgetTheOldItem() {
    #expect(SupervisorItem.decide(status: .enabled, loaded: .legacy, legacyPlist: false) == .waiting)
    #expect(SupervisorItem.decide(status: .notRegistered, loaded: .legacy, legacyPlist: false) == .waiting)
    #expect(SupervisorItem.decide(status: .notFound, loaded: .legacy, legacyPlist: false) == .ready)
}

@Test func appOwnedAndPlainStates() {
    #expect(SupervisorItem.decide(status: .enabled, loaded: .app, legacyPlist: false) == .on)
    #expect(SupervisorItem.decide(status: .enabled, loaded: .none, legacyPlist: true) == .on)
    #expect(SupervisorItem.decide(status: .notRegistered, loaded: .none, legacyPlist: false) == .off)
    #expect(SupervisorItem.decide(status: .notFound, loaded: .none, legacyPlist: false) == .off)
    #expect(SupervisorItem.decide(status: .requiresApproval, loaded: .none, legacyPlist: false) == .requiresApproval)
    #expect(SupervisorItem.on.actionable && SupervisorItem.ready.actionable && !SupervisorItem.waiting.actionable)
    #expect(SupervisorItem.needsHandover.caption!.contains("control handover"))
    #expect(SupervisorItem.waiting.pending && SupervisorItem.ready.pending && SupervisorItem.needsHandover.pending)
    #expect(!SupervisorItem.on.pending && !SupervisorItem.off.pending && !SupervisorItem.requiresApproval.pending)
}

@Test func bootedOutButStillRegisteredOffersARestart() {
    // `launchctl bootout` of the app's job: SMAppService still says enabled, launchd has nothing.
    let item = SupervisorItem.decide(status: .enabled, loaded: .none, legacyPlist: false)
    #expect(item == .stopped)
    #expect(!item.isOn && item.actionable && !item.pending)
    #expect(item.caption!.contains("registers it again"))
}

@Test func loadedJobFromLaunchctlPrint() {
    let legacy = "gui/501/org.agentmesh.control = {\n\tactive count = 1\n\tpath = /Users/me/Library/LaunchAgents/org.agentmesh.control.plist\n\tstate = running\n"
    let app = "gui/501/org.agentmesh.control = {\n\tpath = (submitted by smd.345)\n\t\tpath = /nested\n"
    #expect(loadedJob(printExit: 0, output: legacy) == .legacy)
    #expect(loadedJob(printExit: 0, output: app) == .app)
    #expect(loadedJob(printExit: 113, output: "") == LoadedJob.none)
}
