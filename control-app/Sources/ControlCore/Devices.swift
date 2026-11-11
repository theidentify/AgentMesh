// Device revoke (M3e): the app asks; the supervisor checks Touch ID with its own helper, every time, then runs the same
// remove_device as `agentmesh-log device remove` (a signed revocation plus new keys for every scope the device held).
import Foundation

/// Only another, still trusted device of this account can be revoked here. A device never revokes itself.
public func canRevoke(_ device: Device) -> Bool { !device.thisDevice && !device.revoked }

/// What the inline confirmation says before Touch ID.
public func revokeSummary(_ device: Device) -> String {
    let name = device.name.map { "\($0) (\(device.deviceId.prefix(8)))" } ?? device.deviceId
    return "Revoke \(name)? It can no longer read or sync this account, and the keys of every scope it could read are "
        + "rotated. Stop AgentMesh on that device first, after its outbox is empty. This cannot be undone."
}

/// The device codes first; everything else as the supervisor's other refusals.
public func describeDevice(_ error: ControlError) -> String {
    if case .server(let code, _) = error {
        switch code {
        case "current_device": return "A device cannot revoke itself. Revoke it from another device."
        case "unknown_device": return "That device is not in this account's registry."
        case "already_revoked": return "That device is already revoked."
        case "insufficient_authority": return "This Mac cannot rotate every scope that device could read."
        case "presence_required": return "Not revoked: Touch ID or password was cancelled."
        default: break
        }
    }
    return describe(error)
}

public struct DeviceRemoved: Equatable, Sendable, Decodable {
    public let deviceId: String
    public let revoked: Bool
}

extension ControlClient {
    public func deviceRemove(deviceId: String) throws(ControlError) -> DeviceRemoved {
        try call("device.remove", ["device_id": .string(deviceId)], as: DeviceRemoved.self, timeout: Self.moveTimeout)
    }
}
