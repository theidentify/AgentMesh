# Receive backlog capture execution report

2026-10-16. Branch: `fix/receive-capture-backlog`.

Status: blocked after Task 1; Tasks 2–5 are not implemented. The plan explicitly says
“Stop and report instead of guessing.” No production code or verification was changed.

## Completed

Task 1 is committed as `58f7986` (`test: reproduce full scans during legacy and indexed
backlog receive`), with the required co-author trailer.

`memory-sync/test_receive_backlog_capture.py` generates a disposable database with
3,000 rows and 52 unseen packets from another node. A successor sorts before its
predecessor, so both legacy and migrated-indexed receive must retry a pending chain.
Both tests verify all 52 receipts, the final chain value, the retry, capture key sets,
and the requested bound of two full `rows()` calls. They intentionally remain red.
Private exchange directories use `identity_backup.private_mkdir`; no subprocesses
are introduced by the fixture. Windows execution was not available.

## Blocking design omission

The design counts per-packet capture scans but omits the per-packet application
verification scans:

- `memory_sync._apply` calls `rows(c)` after writing, then compares the entire live
  database with the entire shadow plus the packet. This detects off-packet cascades
  and triggers. Migrated legacy packets use this same function.
- `resolution_apply._write` calls `sync.rows(c)` before and after writing. Its first
  comparison rejects any uncaptured local edit anywhere; its second verifies the
  expected whole-database result. New-format changes and resolutions use this path.

Measured independently in each reproduction: **106 full scans**, consisting of
**54 capture scans and 52 application verification scans**. All 52 packets apply;
the failures are the intended scan/key-set assertions, not transport or ancestry
failures. Replacing only the per-packet captures would still leave 53 full scans
(one initial capture plus 52 verification scans), exceeding the spec's bound.

There is also a correctness incompatibility: leaving an unrelated local edit
uncaptured, as required by the spec, makes `_apply`'s full-shadow comparison fail,
and makes `_write` reject the packet as `uncaptured local edits`. This follows
directly from those comparisons; it is not a measured post-implementation result.

Simply deleting these checks, restricting them to packet keys, or bypassing them
would weaken verification and could miss cascades/triggers affecting other rows.
Renaming or hiding their `rows()` calls would only evade the test. None of those
changes was made. The smallest safe change at this boundary is the reproduction
tests and this report, pending a design that covers application verification too.

## Required design decision

Specify how application verification will distinguish pre-existing unrelated local
edits from off-packet effects of applying a packet, while still detecting every
unsupported cascade/trigger and retaining foreign-key checks, rollback, signature,
ancestry, and receipt semantics. The solution must address both `_apply` and
`_write`, and the full-scan performance bound. Targeted capture alone does not do so.

Alternatively, revise the performance bound to count capture scans only, but that
alone does not solve the unrelated-edit correctness requirement or eliminate the
remaining per-packet verification cost. No spec or plan text was changed.

## Validation

The following command was run from `memory-sync/`:

```sh
UV_CACHE_DIR=/private/tmp/claude-501/uvcache uv run --no-project --with pytest==8.4.2 --with pytest-timeout --with-requirements requirements-security.txt python -m pytest -q -p no:cacheprovider --timeout 300 test_memory_sync.py test_resolution_apply.py test_receive_backlog_capture.py
```

Result: **71 passed, 2 failed** (the two new reproductions), in 14.34 seconds.
Both named #47 regressions pass, as do all existing tests in these two modules.

The equivalent repository-root command uses
`--with-requirements memory-sync/requirements-security.txt` and prefixes all three
test filenames with `memory-sync/`. Result: **71 passed, 2 failed** (the same two
reproductions), in 14.32 seconds.

An initial fixture setup attempt failed because `private_mkdir` does not create
parents. The fixture was corrected to create each directory with that helper;
this setup error is not counted as the required red reproduction.

## Deviations and remaining work

Execution stops before Task 2 under the plan's stop-and-report instruction. This
report is written early at the sole authorized path to document that blocker.
Targeted capture, interrupt plumbing, installer recovery, documentation changes,
and the benchmark are not implemented. Full-suite runs and before/after CPU timings
belong to Task 5 and have not been performed; no performance improvement is claimed.
The known unrelated digest test was not run by the focused commands above.

Only disposable test data was used. No `~/.hermes`, real database, identity,
exchange, launchd job, or running worker was accessed. No `GATES.md` was created.
There was no push, PR, merge, rebase, branch switch, or other-worktree change.

## Resumption after Amendments 1 and 2 — second design gap

2026-10-16, starting at `973e496`. Read the complete spec and plan. The first
blocker is resolved by the amendments; it is not the reason for this stop.
Tasks 2–5 remain unimplemented because the amended FK-check scope misses an
existing required regression. No production Python or tests were changed.

`test_resolution_apply.py::test_relational_repairs_refused_atomically[foreign-key]`
creates an unsynced `test_child` with a deferred FK to `test_parent`, and an
`AFTER UPDATE ON summary_state` trigger that inserts missing parent ID 99 into
`test_child`. The packet updates `summary_state`; it deletes no parents.
`summary_state` has no FKs. The current `_write` detects the trigger's violation
with its global `PRAGMA foreign_key_check`, raises
`ValueError('unsupported relational repair')`, and rolls back atomically.

Amendment 1 tracks only `TABLES`, so it does not record the `test_child` insert.
Amendment 2 checks FK parents of packet-written rows and children of deleted
parents. Neither check visits this violating row. Synced-row value comparisons
also cannot see it. This is a packet-caused violation, not an unrelated existing
violation that Amendment 2 deliberately permits.

### Disposable experiment

Used `tempfile.TemporaryDirectory`, `sqlite_memory.init_database`, and
`sync.initialize` with a synthetic UUID. Seeded `summary_state('x',1,'fixture')`
before initialization, then installed the same two tables and trigger as the
existing regression. Constructed one `protocol.Transition` installing x=5.
Inside `BEGIN IMMEDIATE`, invoked `_write` normally and rolled back. Repeated
inside `BEGIN IMMEDIATE; SAVEPOINT packet` with a connection proxy that delegates
every `execute` except the exact `PRAGMA foreign_key_check`, which returns an
empty cursor (`SELECT 1 WHERE 0`). No repository code was modified by the probe.

Observed output:

```text
Existing checker: unsupported relational repair
Without global FK check: _write and nested savepoint release returned successfully
Packet table FK list: []
Deleted packet parents: 0
Actual deferred violations: [('test_child', 1, 'test_parent', 0)]
Unsynced violating table: True
Outer commit: IntegrityError FOREIGN KEY constraint failed
```

This isolates the existing global check's role; it is not a completed targeted
implementation. From the specified scope, the proposed tracking and targeted
checks have no additional observation that would detect this violation.
SQLite's outer commit still protects integrity, but a nested packet savepoint
does not enforce deferred constraints. Relying on outer commit would change
the existing exception/classification and rollback boundary, failing the required
regression. Keeping global FK scans would violate Amendment 2.

**Required decision:** specify how packet-induced FK violations in unsynced
tables are detected before releasing the application savepoint, without global
FK scans and without rejecting unrelated existing violations. For example,
authorize broader changed-row tracking for FK-bearing unsynced tables (including
their row identifiers and trigger lifecycle), or explicitly revise the supported
trigger contract and this regression. Neither choice was assumed or implemented.
Stopped under the user's explicit instruction to report another real design gap.

### Verified Windows startup behavior and corrected operator procedure

`windows_install.run` initializes `restarting=False`. Only the `if replace` branch
sets it to true. The `worker-start` launch and health checks are guarded by
`if restarting`. Without `--replace`, upgrade calls `precheck_stopped`, selects
the version/task binding, writes `ready` with no pending operation, and returns
`worker_changed=False` (or `unchanged` for the already selected version).
Therefore Amendment 2 section 9 step 4 is factually incorrect: that upgrade
does **not** start the fixed worker. `windows_task` registers a logon trigger;
binding/enabling it is not a worker launch.

The correct procedure **once replace/stop recovery is implemented and shipped**:

1. Keep the task disabled; sign out and back in, or restart, to end the old rc.12
   worker. Confirm with `worker-status` that no worker is running.
2. In one session without signing out, re-enable the exact recorded task with
   `schtasks /Change /TN "<recorded task name>" /ENABLE`.
3. Run the fixed bundle's `windows-recover --runtime "<existing runtime>"` and
   confirm `RECOVER`, resetting only the supported `replace/stop` state.
4. Run the fixed bundle's `windows-upgrade --runtime "<existing runtime>"`
   **without `--replace`**, with `--legacy-drained` if required by legacy policy.
   The stopped-worker precheck must pass.
5. Start the **selected installed version's** executable explicitly with
   `worker-start --runtime "<existing runtime>" --interval <configured interval>
   --timeout <configured timeout>` (and `--legacy-drained` when required).
   Verify `worker-status` reports the selected worker running and healthy cycles.
   Alternatively, leave it stopped until the next logon invokes the enabled task.

This procedure cannot yet clear today's stuck shape with the repository's current
code: `recoverable` still accepts only `upgrade/start`. No installer/recovery
commands were executed. WINDOWS-INSTALLATION.md now explicitly documents the
no-replace startup behavior and current recovery limitation. Native Windows
execution was not available; the startup conclusion comes from code inspection.

### Resumption validation and scope

Re-ran the same three focused modules and commands recorded above, including
the existing FK regression and both named #47 tests:

- From `memory-sync/`: **71 passed, 2 failed**, 15.18 seconds.
- From the repository root: **71 passed, 2 failed**, 14.41 seconds.

Only the two committed Task 1 backlog reproductions fail, still at 106 scans.
The existing conflict/pending tests in these modules remain green. Full-suite
runs, the amended implementation's tests, and the benchmark remain unperformed;
no Task 2–5 completion or performance improvement is claimed. `git diff --check`
passes. The only deliverables of this stop are this appended report and the
Windows documentation correction, committed with the requested co-author trailer.
No `~/.hermes` or real data was accessed, no GATES.md was created, and no push,
merge, rebase, branch switch, or other-worktree change occurred.

## Amendment 3 execution — Task 2

The global FK check is retained in both application paths. Added keyed normalized
row reads, a capture probe with whole-transaction fallback, TEMP write tracking
(including OLD/NEW keys, main-qualified triggers and restored recursive_triggers),
install-only allowed touches, preserved-shadow checks, fail-closed packet key
extraction, and retained-envelope digest checks before local outbox decoding.

TDD: the six initial backlog/probe/index tests failed for the expected scan,
untargeted-capture, missing keys argument and repeated-decode reasons. After
implementation the three focused modules passed 77 tests from the root. Additional
cascade/preserve tests initially had a missing fixture parent; replaced that helper
with a small explicit fixture using closing product connections. Final counts are
recorded with Task 5 validation below. Existing #47 and relational repair tests pass.

Scope deviation: before reading the plan's prohibition, this resumption ran the
read-only shared-memory recall wrapper in ~/.hermes. This was an instruction error;
no further access there was made. No production writes or worker actions were run.
No GATES.md was created. Acceptance is tracked through tests and this report.
