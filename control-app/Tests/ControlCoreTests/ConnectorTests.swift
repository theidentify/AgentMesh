import Foundation
import Testing
@testable import ControlCore

@Test func connectorDecoding() throws {
    let raw = #"{"requests":[{"code_hash":"ab","client_name":"Claude","created":1,"expires":600001}],"capped":false,"now_ms":60001}"#
    let pending = try decoder().decode(ConnectorPending.self, from: Data(raw.utf8))
    #expect(pending.requests == [ConnectorRequest(codeHash: "ab", clientName: "Claude", created: 1, expires: 600001)])
    #expect(remaining(pending.requests[0], nowMs: pending.nowMs ?? 0) == "9 min left")
    #expect(remaining(pending.requests[0], nowMs: 580001) == "20 s left")
    #expect(remaining(pending.requests[0], nowMs: 700000) == "expired")
    let approved = try decoder().decode(ConnectorApproved.self, from: Data(#"{"app_id":"x","state":"active","client_name":"Claude","read":["personal"],"remember":"personal/inbox"}"#.utf8))
    #expect(approved.remember == "personal/inbox")
    #expect(ControlClient.presenceOps.contains("connector.approve"))
}

@Test func connectorCodeAndNewRequests() {
    #expect(normalizedCode(" 123 456 ") == "123456")
    #expect(normalizedCode("12345") == nil && normalizedCode("12345a") == nil && normalizedCode("１２３４５６") == nil)
    let a = ConnectorRequest(codeHash: "a", clientName: "A", created: 1, expires: 2)
    let b = ConnectorRequest(codeHash: "b", clientName: "B", created: 1, expires: 2)
    #expect(newRequests([a, b], seen: ["a"]) == [b])
    #expect(describeConnector(.server(code: "code_mismatch", message: "")).contains("different request"))
    #expect(describeConnector(.server(code: "busy", message: "")) == describe(.server(code: "busy", message: "")))
}
