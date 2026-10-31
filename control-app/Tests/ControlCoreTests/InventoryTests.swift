import Foundation
import Testing
@testable import ControlCore

func inventoryFixture() throws -> Data {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()
    return try Data(contentsOf: root.appendingPathComponent("profile-log/tests/control/fixtures/inventory.json"))
}

func finding(_ kind: String, _ subject: String, _ severity: String = "warning") -> Finding {
    Finding(id: kind + ":" + subject, severity: severity, kind: kind, subject: subject)
}

func inventory(_ findings: [Finding], devices: [Device]? = nil) -> Inventory {
    Inventory(generatedAt: "2027-01-15T08:00:00+00:00", devices: devices, agents: nil, keyFiles: [],
              keystore: KeyFile(path: "profile-log/keystore.sealed", kind: "file", mode: 0o600, ownerOk: true, size: 1,
                                modifiedAt: nil, agent: nil),
              recovery: nil, backup: nil, findings: findings)
}

@Test func inventoryFixtureDecodes() throws {
    let envelope = try decoder().decode(Envelope<Inventory>.self, from: inventoryFixture())
    let value = try #require(envelope.result)
    #expect(value.devices?.map(\.deviceId) == ["d1", "d2"] && value.devices?.first?.thisDevice == true)
    #expect(value.agents?.first?.name == "reader" && value.agents?.first?.read == ["work/common"])
    #expect(value.agents?.first?.secretUntil != nil && value.agents?.first?.keyRef.count == 32)
    #expect(value.keyFiles.count == 3 && value.keyFiles.first?.mode == 0o600 && value.keyFiles.first?.agent == "reader")
    #expect(value.keystore.path == "profile-log/keystore.sealed" && value.keystore.agent == nil)
    #expect(value.recovery?.count == 1 && value.backup == nil)
    #expect(value.findings.map(\.kind) == ["backup_never", "key_file_orphan"])
}

@Test func inventoryNullsDecode() throws {
    let body = #"{"generated_at":"x","devices":null,"agents":null,"key_files":[],"recovery":null,"backup":null,"findings":[],"keystore":{"path":"profile-log/keystore.sealed","kind":"missing","mode":null,"owner_ok":null,"size":null,"modified_at":null}}"#
    let value = try decoder().decode(Inventory.self, from: json(body))
    #expect(value.devices == nil && value.keystore.kind == "missing" && value.keystore.mode == nil)
}

@Test func modeAsOctal() {
    #expect(modeText(0o600) == "0600" && modeText(0o40) == "0040" && modeText(nil) == "–")
}

@Test func findingsReadable() {
    let device = Device(deviceId: "d2", revoked: false, thisDevice: false, name: "MacBook Pro", platform: "macos",
                        lastSeenAt: nil, lastSyncAt: nil)
    let value = inventory([], devices: [device])
    #expect(findingText(finding("device_stale", "d2"), in: value) == "MacBook Pro has not been seen for over 14 days")
    #expect(findingText(finding("device_stale", "d9"), in: value) == "Device d9 has not been seen for over 14 days")
    #expect(findingText(finding("key_file_unsafe", "agents/recall.key"), in: value).contains("agents/recall.key"))
    #expect(findingText(finding("backup_never", "profile"), in: value) == "No backup recorded yet")
    #expect(findingText(finding("something_new", "x"), in: value) == "something_new: x")
}

@Test func warningsFirst() {
    let sorted = sortedFindings([finding("key_file_orphan", "a", "info"), finding("no_recovery", "profile")])
    #expect(sorted.map(\.kind) == ["no_recovery", "key_file_orphan"])
}

@Test func deviceLines() {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let seen = ISO8601DateFormatter().string(from: now.addingTimeInterval(-3 * 86400))
    let other = Device(deviceId: "d2", revoked: false, thisDevice: false, name: "MacBook Pro", platform: "macos",
                       lastSeenAt: seen, lastSyncAt: nil)
    #expect(deviceLine(other, now: now) == "MacBook Pro · macos · seen 3d ago")
    let own = Device(deviceId: "d1", revoked: false, thisDevice: true, name: nil, platform: nil, lastSeenAt: nil, lastSyncAt: nil)
    #expect(deviceLine(own, now: now) == "d1 · this Mac")
    let gone = Device(deviceId: "d3", revoked: true, thisDevice: false, name: "old", platform: "windows", lastSeenAt: nil, lastSyncAt: nil)
    #expect(deviceLine(gone, now: now) == "old · windows · revoked")
}

@Test func inventoryWarningShowsInIcon() {
    let healthy = status([row(health: .idle)])
    #expect(iconState(healthy, inventory: inventory([finding("backup_never", "profile")])) == .warning)
    #expect(iconState(healthy, inventory: inventory([finding("key_file_orphan", "a", "info")])) == .ok)
    #expect(iconState(healthy, inventory: nil) == .ok)
    var asking = healthy; asking.projects = Projects(pending: ["x"], scopes: ["skip"])
    #expect(iconState(asking, inventory: inventory([finding("backup_never", "profile")])) == .needsYou)
    #expect(iconState(status([row(health: .failed)]), inventory: inventory([finding("backup_never", "profile")])) == .error)
    #expect(iconState(nil, inventory: inventory([finding("backup_never", "profile")])) == .offline)
}

@Test func findingNotifiesOnce() {
    var watch = Watch()
    let first = watch.update(inventory: inventory([finding("backup_never", "profile"), finding("key_file_orphan", "a", "info")]))
    #expect(first.map(\.id) == ["finding.backup_never:profile"] && first[0].title == "AgentMesh: No backup recorded yet")
    #expect(watch.update(inventory: inventory([finding("backup_never", "profile")])).isEmpty)
    #expect(watch.update(inventory: nil).isEmpty)  // a failed poll changes nothing
    #expect(watch.update(inventory: inventory([finding("backup_never", "profile")])).isEmpty)
}

@Test func clearedFindingCanNotifyAgain() {
    var watch = Watch()
    _ = watch.update(inventory: inventory([finding("no_recovery", "profile")]))
    #expect(watch.update(inventory: inventory([])).isEmpty)
    #expect(watch.update(inventory: inventory([finding("no_recovery", "profile")])).count == 1)
}
