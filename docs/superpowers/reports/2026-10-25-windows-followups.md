# Windows rollout follow-ups

Implemented A/B/C in this worktree. No commit, deployment, or external network access.

## Changes and tests

- **A:** `memory-sync/claude_digest_worker.py` classifies stderr and unstructured stdout into fixed auth/rate-limit/exit reasons, and reports timeout/missing/denied executable reasons. `memory-sync/bounded_digest.py` accepts only fixed worker reasons (including signed exit codes). Existing scheduler, production-job and notification paths already preserve the reason; no scheduler change was needed. `memory-sync/test_claude_digest_worker.py` covers each marker on both streams, launch failures and secret exclusion. `memory-sync/test_bounded_digest.py::BoundedTests::test_failure_status` checks actual worker failure through summary metrics and `job-summary.json` (`fields.detail`), including arbitrary stderr exclusion.
- **B:** `profile-log/agentmesh_log/broker/client.py`, `migrate/run.py`, and `cli.py` preserve migration presence failures, emit the stderr-only presence command with the supplied/default class (the hint's `--home` is `AGENTMESH_HOME/profile-log`), and map connection-stage missing/refused errors to `broker_unreachable` (a busy pipe, winerror 231, stays `unavailable` because the broker is running). Other errors retain their existing mapping. `profile-log/tests/migrate/test_errors.py` covers migration identity/ledger/import calls, class hints, all three CLI commands, and other error codes.
- **C:** `profile-log/agentmesh_log/broker/service.py` prefetches with `PREFETCH_WORKERS=16`, collects results on the caller thread, and throttles stderr progress to five seconds only above 200 missing keys. It retains StorageError skipping and other exception propagation. SyncRemote and `profile-log/agentmesh_log/storage/s3.py` protect counter mutations without holding locks over network operations. S3Storage creates its client before prefetch and shares that client, not a session; FilesystemStorage GET remains read-only. `profile-log/tests/broker/test_prefetch.py` covers sequential/parallel equivalence, actual overlap, failures, exact SyncRemote/S3 counters (with and without SDK hooks), progress boundaries/throttling and absent stderr.

New text file I/O uses UTF-8. The profile-log collection check for node IDs ≤80 characters passed. `git diff --check` passed.

## Final suite commands

```sh
~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests
# 1013 passed, 11 skipped, 10 failed, 2 warnings (123.36 s)

~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider memory-sync/test_claude_digest_worker.py memory-sync/test_bounded_digest.py memory-sync/test_digest_api.py
# 95 passed, 10 failed (7.46 s)
```

Focused B/C command: the same pytest invocation with `profile-log/tests/broker/test_prefetch.py profile-log/tests/migrate/test_errors.py`: **36 passed**.

Profile-log failures:

- Known baseline: `test_p6_s12_close_cross_verifier`.
- Socket restrictions: `test_p3_1_processes`, `test_connections_offline`, `test_real_transport`, `test_supervisor_round_trip`, `test_ticker_and_close_hooks`, `test_p5_4_concurrent_processes`, `test_s3_custom_http_endpoint`. Socket creation/binding reports `Operation not permitted`; supervisor startup failures also report this in thread warnings.
- Process inspection restrictions: `test_panic_revert`, `test_reconcile_same_process_only`. These depend on `/bin/ps` start tokens; this sandbox denies `ps`, preventing identification of processes to stop.

All ten digest API failures occur while binding the localhost test server (`PermissionError: Operation not permitted`). This includes the named baseline `test_client_envelope_satisfies_bounded_extract_command`, whose later assertion could not be reached here.

## Not done

No native Windows, live Claude login, or live S3 verification; all new remote/CLI tests use local fakes. The full suites were run, but cannot be green under the current sandbox restrictions and known baseline failure. No unrelated baseline fixes were attempted. Local shared-memory recall was unavailable because its configured Python executable was missing.

## Review (Claude, 2026-10-25)

Fixed in review: the presence hint pointed at `agent_key.parent.parent` (`~/.agentmesh`, not the broker home), and pipe-busy was reported as unreachable. Outside the sandbox: profile-log 1023 passed (+1 known baseline failure), digest suites 114 passed (+1 known failure on main). 20 mutation gates killed.
