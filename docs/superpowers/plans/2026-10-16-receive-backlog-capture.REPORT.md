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
