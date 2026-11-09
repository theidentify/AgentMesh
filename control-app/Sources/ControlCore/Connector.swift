// Claude connector approval: the supervisor lists live gateway requests and approves one after its own Touch ID
// sheet. The app only carries the code the owner typed; it never proves presence and never stores the code.
import Foundation

public struct ConnectorRequest: Equatable, Sendable, Decodable, Identifiable {
    public let codeHash: String
    public let clientName: String
    public let created: Int
    public let expires: Int
    public var id: String { codeHash }
    public init(codeHash: String, clientName: String, created: Int, expires: Int) {
        self.codeHash = codeHash; self.clientName = clientName; self.created = created; self.expires = expires
    }
}

public struct ConnectorPending: Equatable, Sendable, Decodable {
    public let requests: [ConnectorRequest]
    public let capped: Bool
    public let nowMs: Int?
}

public struct ConnectorApproved: Equatable, Sendable, Decodable {
    public let appId: String
    public let state: String
    public let clientName: String?
    public let read: [String]?
    public let remember: String?
}

/// Six digits, spaces ignored; anything else is not sent.
public func normalizedCode(_ text: String) -> String? {
    let digits = text.filter { !$0.isWhitespace }
    return digits.count == 6 && digits.allSatisfy({ $0.isASCII && $0.isNumber }) ? digits : nil
}

/// Requests the app has not shown yet: a new one pops the approval window once.
public func newRequests(_ requests: [ConnectorRequest], seen: Set<String>) -> [ConnectorRequest] {
    requests.filter { !seen.contains($0.codeHash) }
}

/// "9 min left" style text for a request's expiry, given the supervisor's clock.
public func remaining(_ request: ConnectorRequest, nowMs: Int) -> String {
    let seconds = max(0, (request.expires - nowMs) / 1000)
    if seconds == 0 { return "expired" }
    return seconds >= 60 ? "\(seconds / 60) min left" : "\(seconds) s left"
}

/// Readable text for connector codes (connector_cli.CODES); falls back to the generic text.
public func describeConnector(_ error: ControlError) -> String {
    if case .server(let code, _) = error {
        switch code {
        case "code_not_found_or_expired": return "No live request has that code. Codes last 10 minutes; reload the connector page."
        case "code_already_used": return "That request was already approved or rejected."
        case "code_mismatch": return "That code belongs to a different request. Check the code in the browser."
        case "invalid_code", "code_required": return "Type the 6-digit code shown in the browser."
        case "no_gateway_origin": return "This Mac does not know the gateway yet. Run `agentmesh-log connector gateway-use` once."
        case "gateway_denied": return "The gateway refused this Mac's signed request."
        case "gateway_clock_skew": return "This Mac's clock is more than a minute off."
        case "gateway_unreachable": return "The gateway did not answer. Check the network."
        case "gateway_unavailable": return "The gateway answered with an error."
        case "not_authorized": return "This Mac cannot read every profile in the approval settings."
        case "unknown_profile": return "A profile in the approval settings does not exist on this Mac."
        case "presence_required": return "Not approved: Touch ID or password was cancelled."
        default: break
        }
    }
    return describe(error)
}

extension ControlClient {
    public func connectorPending() throws(ControlError) -> ConnectorPending {
        try call("connector.pending", as: ConnectorPending.self, timeout: 15)
    }
    public func connectorApprove(code: String, codeHash: String) throws(ControlError) -> ConnectorApproved {
        try call("connector.approve", ["code": .string(code), "code_hash": .string(codeHash)], as: ConnectorApproved.self,
                 timeout: Self.moveTimeout)
    }
}
