// agentmesh-presence available | confirm --action <action>   (summary on stdin)
// Exit 0 approved, 1 denied, 2 unavailable, 3 timed out, 64 usage. Prints one JSON line for the audit.
import Foundation
import PresenceCore

func finish(_ verdict: Verdict, _ laError: Int?, probe: Bool = false) -> Never {
    print(line(verdict, laError, probe: probe)); fflush(stdout)
    exit(verdict.rawValue)
}

guard let command = parse(Array(CommandLine.arguments.dropFirst())) else { finish(.usage, nil) }
let evaluator = LocalAuthEvaluator()
switch command {
case .available:
    let (verdict, code) = available(evaluator)
    finish(verdict, code, probe: true)
case .confirm(let action):
    guard let summary = readSummary(FileHandle.standardInput) else { finish(.usage, nil) }
    let (verdict, code) = confirm(action: action, summary: summary, evaluator: evaluator)
    finish(verdict, code)
}
