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

## Task 5

Task 4 commit: `b983cc6`. Ten new tests failed before implementation and passed afterward. Recovery, signed-packet, backup and OS-unlock suites: 319 passed from each working directory. Covers each journal-step crash, repeated STOPPED/lock fences, transaction collision rollback, sealed journal writes, lost trust, and fake OS backend success/failure/None.

Replay uses a private internal Security subclass bound to the exact sealed journal identity, with unchanged live trust authorization. Ordinary Security refuses while the journal exists. This resolves the spec's simultaneous journal guard and in-journal signing/verification requirements without a public bypass option. On cleanup failure the report explicitly requests manual removal of the old fingerprint: current `identity-os-unlock disable` targets the new identity and cannot remove an old item.

Process deviation: Task 4's commit was issued after both test processes started but before their final output was collected. Both subsequently completed successfully (57 each); later commits wait for completed results. One initial shell invocation used unavailable `python`; it did not edit files. The stale Task 1 report line was corrected in Task 2.

## Task 6

Task 5 commit: `9e0faec`. Twelve peer-rotation tests failed first. Green: 79 recovery tests passed from each location. Peer approval rechecks manifest bytes against the approved hash when copying; wrong fingerprint makes no changes. Competing-proposal count is the number of *other* proposal files, and any positive count triggers the warning. The console never supplies the expected full fingerprint as ghost text.

## Task 7

Task 6 commit: `5788f4f`. Six reporting tests failed first. Green: 85 recovery tests passed from each location. Byte snapshots confirm DB/sidecars/exchange remain unchanged. Report uses install_inspect's read-only URI selection plus query_only, validates envelope, filename and change schema, and emits only hashed row keys.

Small additions: `uncommitted` exposes redacted foreign forged candidates so the required B-side E2E result is observable. `in_manifest` requires UUID *and digest* equality; UUID-only membership could misleadingly mark altered content as approved. Without --security-dir it is null; a supplied security directory without approval yields false. Missing-file counts are independent of the mtime filter.

## Task 8

Task 7 commit: `8d79ac4`. E2E/restore tests: two expected leftover-refusal failures, two existing behaviors passed before the restore guard change. Green: 268 recovery/backup/signed-packet tests passed from each location. Real capture/publish/receive cycles converge after immediate peer revocation, rotation and approval; a stolen-key forgery is refused and reported, and prior receipts still verify historically. Empty-trust restore regression confirms local restoration does not undo remote revocation.
