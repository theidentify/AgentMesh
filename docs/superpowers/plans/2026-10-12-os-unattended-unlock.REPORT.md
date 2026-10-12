# OS unattended unlock implementation report

Executed tasks 1–10 in order in the requested worktree on `feat/os-unlock`; the spec took precedence.
No push, PR, merge, rebase, other branch/worktree changes, real identity migration, or real credential
store access was performed. `AGENTMESH_NATIVE_KEYCHAIN_TESTS` was never set on this machine.

## Task commits and verification

Every task commit ends with `Co-Authored-By: GPT (Codex) <noreply@openai.com>`.

| Task | Commit | Verification |
|---|---|---|
| 1: envelope | `cafde7a` | Missing-module red, then 6 passed |
| 2: backends | `2b3c1db` | 16 new failures before implementation, then 22 passed |
| 3: Security/cache | `ecd9f27` | 8 new failures before implementation; focused run 104 passed |
| 4: operations | `1abac7b` | New operation tests failed first; focused run 112 passed |
| 5: callers/CLI | `476e5a5` | 4 caller/CLI failures before implementation; regression run 118 passed |
| 6: restarts | `3959ca2` | Fresh-process signing, disable refusal and missing-item refusal: 1 passed |
| 7: native fixtures | `3f2c2ce` | 3 native tests skipped locally, as required |
| 8: frozen build/smoke | `26a501a` | 2 new build-contract failures before implementation; 13 passed |
| 9: documentation | `fa0e0fc` | Reviewed against the supplied spec and all nine fixed reasons |
| 10: final audit/report | This report's commit: `test: verify OS unlock implementation and record report (#7)` | Final results below |

Task 3's test process completed successfully after its commit was made; its successful result was
checked before Task 4 implementation. Other implementation-task green results were checked before
committing.

## Final verification

- Initial full suite: **699 passed, 16 skipped, 3 known failures**, one warning, 100.75 seconds.
- Final focused gate: **160 passed, 3 skipped**, exit zero; native tests stayed skipped. Covers envelopes, backends, cache, operations,
  callers, restart, key-protection regression, signed packets, and build contracts.
- Pre-readback-fix full suite: **727 passed, 16 skipped, 3 known failures**, one warning, 112.90 seconds.
- Final full suite after the readback cleanup fix: **728 passed, 16 skipped, 3 known failures**,
  one existing duplicate-archive-name warning, **103.41 seconds**. No unexpected failures.
- Acceptance ledger: **4 met, 0 unmet, 0 abandoned**; focused gate machine-verified and final
  suite/safety/commit gates reviewed against their evidence.
- `git diff --check`: passed.
- Required deprecated-API and `-A` greps: no matches under the requested paths.
- AST review: every OSUnlockError raise site explicitly uses `from None`.
- Timeout and subprocess-error tests assert both `__cause__` and `__context__` are absent and
  secret-like captured output is absent from CLI reports.
- Mock Windows APIs verify declarations, persistence/type/username, blob size checks, `CredFree`,
  error mapping and missing-delete behavior without calling Credential Manager.

The three accepted full-suite failures are:

- `test_sqlite_consumers.py::ConsumerTests::test_export_complete_collision_safe_manual_notes_and_source_anchors`
- `test_sqlite_consumers.py::ConsumerTests::test_export_infers_transcript_tasks_even_without_structured_task_ref`
- `test_sqlite_consumers.py::ConsumerTests::test_export_resolution_of_last_active_item_clears_current_memory`

These are the pre-existing temporary-path/symlink failures documented by the plan. Their code was
not changed.

## Small corrections to the plan

1. Task 1's fixture called the cache API scheduled for Task 3; it conditionally reset the cache until
   Task 3. Task 3 used a prepared envelope rather than depending on Task 4's enable operation,
   allowing separate ordered task commits. Load-count fixtures clear setup calls before assertions.
2. `from None` suppresses display but retains exception context. Backend failures are converted to
   fixed reasons and raised outside exception handlers so captured subprocess output is not retained
   in exception chains. Tests cover timeout and CalledProcessError.
3. The supplied cache indexed only ciphertext and could miss nonce tampering or repeat OS reads after
   envelope changes. The implementation serializes access, caches per directory/key ID, and binds
   success to the complete public identity and envelope. Changed authenticated data fails closed
   without another store read; refusals remain cached until reset/restart.
4. The lock wrapper catches acquisition timeout at context entry, as required by the actual lock API.
   Cleanup also covers a store that writes and then reports failure, and final Security readback
   (which the sample placed outside the recovery block). If the committed file cannot be
   reread after an error, K is retained rather than risking an envelope without K; status/disable can
   detect and clean up a stray item. Delete refusals are normalized to the fixed removal reason.
   Post-replace failure and retry paths are tested.
5. The release-key helper creates its own directory. Tests allow missing device files to refuse the
   release directory and separately verify release-private contents renamed into a valid device
   directory are rejected as an invalid private identity.
6. Frozen status may not read the real login store, despite Task 8's exception. A separate CI-only
   smoke binary has a generated runtime hook bound to the disposable native store. Normal builds
   have no hook or environment/CLI backend override. This also supplies the spec's omitted frozen
   strict `worker-run --once` success and post-disable refusal checks.
7. Even disposable keychain passwords go through stdin, overriding the plan's argv allowance.
   Native tests share a CI-only disposable-store helper and restore the Mac search list in `finally`.
8. The audit added AES-256 key-length validation, fixed refusal on Windows library-load failure,
   non-string error-reason sanitation, portable mocked Mac account/path fixtures, mocked Windows API
   coverage, threaded cache tests, managed-worker reason reporting, diagnostic/public no-store checks,
   and extra crash/refusal tests. New failing validation tests preceded fixes.
9. Existing key-protection tests match the stable LOCKED prefix, so no test-string edit was needed.
   The stale module description was updated along with the operator documentation.

## Native verification still to run externally

Native Mac/Windows store tests and frozen native smoke are wired into `standalone-smoke.yml` but
were deliberately not executed here. CI on macos-15/windows-2025 must establish native behavior.
Windows CI without a usable logon session records the fixed refusal and skips native unlock.
Frozen smoke builds are disposable, not release artifacts.

The spec's separately approved native-host checks remain external: LaunchAgent/logon task, screen
lock, another account/machine, deletion, Mac dialog count and password/reboot timing, and Windows
S4U/admin-password-reset behavior. No instructions were sent to another session or machine.

No unresolved implementation question remains. File-keychain dialog behavior and OS/login-session
availability are native verification limitations, not claims of local verification.
