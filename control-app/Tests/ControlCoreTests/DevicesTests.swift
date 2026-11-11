import Foundation
import Testing
@testable import ControlCore

private func device(_ id: String = "9e06c3bfb1c76aedf45bd0f21125642e", this: Bool = false, revoked: Bool = false,
                    name: String? = "MacBook Pro") -> Device {
    Device(deviceId: id, revoked: revoked, thisDevice: this, name: name, platform: "macOS", lastSeenAt: nil, lastSyncAt: nil)
}

@Test func onlyAnotherTrustedDeviceCanBeRevoked() {
    #expect(canRevoke(device()))
    #expect(!canRevoke(device(this: true)))
    #expect(!canRevoke(device(revoked: true)))
}

@Test func revokeSummaryNamesTheDevice() {
    #expect(revokeSummary(device()).hasPrefix("Revoke MacBook Pro (9e06c3bf)?"))
    #expect(revokeSummary(device(name: nil)).hasPrefix("Revoke 9e06c3bfb1c76aedf45bd0f21125642e?"))
    #expect(revokeSummary(device()).contains("cannot be undone"))
}

@Test func deviceRemoveIsAPresenceOpAndDecodes() throws {
    #expect(ControlClient.presenceOps.contains("device.remove"))
    let raw = #"{"device_id":"9e06c3bfb1c76aedf45bd0f21125642e","revoked":true}"#
    #expect(try decoder().decode(DeviceRemoved.self, from: Data(raw.utf8)) == DeviceRemoved(deviceId: "9e06c3bfb1c76aedf45bd0f21125642e", revoked: true))
    #expect(describeDevice(.server(code: "current_device", message: "")).contains("cannot revoke itself"))
    #expect(describeDevice(.server(code: "busy", message: "")) == describe(.server(code: "busy", message: "")))
}
