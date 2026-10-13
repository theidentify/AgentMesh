# Key rotation recovery implementation report

Worktree: encrypted-key-storage-28470f. Branch: feat/key-recovery. No push, merge, rebase, or other branch changes.

## Acceptance and execution

Execute Tasks 1–11 in order, test first, verify from memory-sync and repository root before each commit. Preserve historical verification and enforce exact own-packet bytes. Recovery uses streamed authenticated manifests, fenced forward-only replay, sealed secrets, fixed errors, injectable OS storage, read-only reporting, and disposable fixtures. Final acceptance requires the full suite from both working directories (only the three documented sqlite-consumer failures permitted).

## Task 1

Added sealed disposable fixtures and five R1 regressions. Red: two failures, three passes. Green: 129 passed from memory-sync; 129 passed from the repository root.

Spec-over-plan correction: own-node byte mismatches are rejected immediately with the existing fixed reason. Falling through as the plan suggests accepts whitespace-different files through `_seen`'s JSON equality. Own-packet matches clear diagnostics and do not record a signature verification attempt.

## Deviations / design issues

- No shared-memory recall: its private database conflicts with the disposable-DB-only scope.
- No GATES or other bookkeeping files; acceptance evidence stays here.
- Journal replay must sign and verify internally while ordinary `Security.check_self` refuses the journal. A scoped internal replay mechanism is necessary; the public signing guard must remain closed.

## Remaining

Tasks 3–11 are not yet completed. No final full-suite result yet.

## Task 2

Task 1 commit: `0aae4a5`. Added streaming manifest IO, domain-separated proposals, and strict private rotation registry. Red: 25 failed / 5 passed; green: 30 passed from each working directory. A 60,000-entry manifest passes without packet parse/typed limits. Errors are raised outside exception handlers with fixed reasons.

## Task 3

Task 2 commit: `5544210`. R2 red: two failures / five passes. Green: 131 tests passed in memory-sync and repository root (recovery, signed-packet, and security-regression suites). Revoked trust selects manifest verification after committed receipts and before validation/application. Membership never bypasses historical signature verification. Lazy caches include failed loads to bound hostile repeated reads.

## Task 4

Task 3 commit: `15d9a1d`. Preparation tests red: 20 failures. Green: 57 tests passed from each location. Refusals snapshot security directory, DB/sidecars and exchange bytes. Locks stay held for the context. Read-only preparation rechecks DB state after acquiring the fence.

Small corrections: pending-file refusal uses a fixed reason without interpolated UUID, honoring the strict fixed-error rule. Runtime status calls `Security` internally and therefore fails for lost/revoked identities and journals; in that case recovery reads the same owned worker metadata and liveness directly, still taking both locks. Lock infrastructure can create its usual lock files outside the security directory, DB and exchange; this is necessary to acquire the specified fence.
