# Receive chain order implementation report

Date: 2026-10-16. Branch: `fix/receive-chain-order`.
Starting commit: `7cae7d82d059d218bb9043e1c20fbbb80c551ac9`.
The approved receive-chain-order design was read in full before implementation.

## Changes

- First-pass order is `(mtime_ns, filename, path)`; path breaks otherwise equal
  names across peer directories. Failed stats leave files eligible for the existing
  per-attempt I/O handling.
- Pending packets register their parsed parent revision hints. Successful applies
  immediately schedule dependants on an iterative depth-first stack. Each attempt
  rereads and validates its packet and performs the existing transaction and checks.
  No recursion is used, and interruption is checked before dependency attempts too.
- Pending packets without useful hints use the existing no-progress safety loop.
  Indexed conflict retries and the actual first-migration rescan remain: retained
  evidence can resolve these cases. A database already indexed at entry no longer
  needs the redundant migration rescan.
- Diagnostics retain the same classification and message, with the final failure
  written once per path per receive. Successful retries discard deferred failures.
  Successful resolutions also clear deferred diagnostics for exactly matching covered
  packet paths, preserving the existing behavior when interrupted before another pass.
- No changes to authorization, signatures, validation, seen/receipt checks, targeted
  capture, row application, file-mark rules, or application transaction boundaries.
  The indexed adapter's optional diagnostic buffer leaves standalone calls unchanged.

## Task commits and TDD

1. `99e9b2c` — failing scheduling, attempt-bound, diagnostic and outcome tests.
2. `8d400e3` — scheduler and deferred diagnostics, with additional edge-case coverage.
3. `perf: benchmark receive chain scheduling and record validation` — chain benchmark,
   smoke tests and this report (the commit containing this report).

Each task commit ends with the requested GPT (Codex) co-author trailer.

Before implementation, the 200-link legacy test made **11,056** `_apply` attempts
against a bound of 400. The indexed equivalent exceeded its 300-second test timeout.
Both interleaved depth-first tests failed; unchanged pending/conflict/invalid tests
passed. A separate root run showed seven expected failures for depth-first order,
diagnostic suppression, dependency interruption and v2 attempt bounds, with two
unchanged-outcome tests passing. The small diagnostic reproduction wrote nine rows
instead of one; v2 used 99 adapter attempts against a bound of 60.

The final tests cover shuffled 200-link chains on legacy and pre-indexed paths,
interleaved chains with heads last, deterministic mtime/name ordering, multiple
parents, missing-parent fallback, exact pending diagnostics, conflict/invalid
classification, final-only diagnostic writes, interruption/resumption, v2 packets,
and resolution-covered diagnostic cleanup on interruption.

The covered-diagnostic interruption regression was first observed failing with
`conflict=1` instead of zero, then passed after the fix. The three benchmark smoke
tests first failed because the chain argument did not exist, then passed.

The existing backlog fixture now assigns explicit mtimes to preserve its intended
successor-before-predecessor setup under the new order. Its assertions were kept.
New fixture directories use `private_mkdir`; timestamps use `os.utime(ns=...)` with
one-second spacing, with no sleeps or POSIX-only permission assumptions.

## Validation commands

From `memory-sync/`:

```sh
UV_CACHE_DIR=/private/tmp/claude-501/uvcache uv run --offline --no-project --with pytest==8.4.2 --with pytest-timeout --with-requirements requirements-security.txt python -m pytest -q -p no:cacheprovider --timeout 300
```

From repository root (including the declared profile-memory dependencies):

```sh
UV_CACHE_DIR=/private/tmp/claude-501/uvcache uv run --offline --no-project --with pytest==8.4.2 --with pytest-timeout --with-requirements memory-sync/requirements-security.txt --with-requirements profile-memory/requirements-dev.txt python -m pytest -q -p no:cacheprovider --timeout 300
```

The benchmark smoke file was added after full-suite collection. It was separately
run from both directories: **3 passed** each. The final focused chain plus benchmark
run from `memory-sync/` was **19 passed** in 26.63 seconds. Earlier focused receive,
capture and resolution coverage passed **104 tests** before the additional covered-
diagnostic interruption test, which subsequently passed separately and is included
in the full-suite runs below.

## Benchmark method

`memory-sync/support/benchmark_receive_backlog.py` accepts `--chain` and an indexed
legacy mode (`--mode indexed`), retaining its independent-backlog default. It counts
legacy `_apply` attempts, indexed adapter attempts and diagnostic writes, alongside
existing scan, decode and FK metrics. Fixed independent shuffles of filenames and
mtimes force dependency retries even when creation-order sorting alone cannot help.

Both versions run the same new benchmark script with different import paths. The
baseline is a disposable `git archive` of the starting commit, not another worktree.
Every run creates and deletes its own synthetic SQLite database and exchange.

```sh
# Extract the baseline to a fresh disposable directory first:
git archive 7cae7d82d059d218bb9043e1c20fbbb80c551ac9 memory-sync | tar -x -C /private/tmp/receive-chain-baseline.VWvf5Z
PYTHONPATH=/private/tmp/receive-chain-baseline.VWvf5Z/memory-sync UV_CACHE_DIR=/private/tmp/claude-501/uvcache uv run --offline --no-project --with-requirements memory-sync/requirements-security.txt python memory-sync/support/benchmark_receive_backlog.py --rows 20000 --packets 200 --mode legacy --chain
PYTHONPATH=memory-sync UV_CACHE_DIR=/private/tmp/claude-501/uvcache uv run --offline --no-project --with-requirements memory-sync/requirements-security.txt python memory-sync/support/benchmark_receive_backlog.py --rows 20000 --packets 200 --mode legacy --chain
```

Legacy measurements use 50, 100 and 200 links; indexed-legacy uses 50 links
and v2 uses 10 links. Each database has 20,000 baseline rows, 450-character payload padding and
20 unrelated local outbox packets. The initial baseline measurements overlapped the full suites; the final after
measurements ran after those suites completed. CPU and wall measurements are local
illustrative results, not isolated hardware benchmarks. Attempt counts are the
primary comparison. The optional 50-link v2 baseline was interrupted because its
unchanged transitive proof revalidation dominated runtime; both v2 measurements
were reduced to 10 links. No result is claimed for the interrupted run.

## Benchmark results

| Mode | Links | Attempts before → after | Diagnostic writes before → after | CPU seconds before → after | Wall seconds before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| legacy | 50 | 702 → 99 | 652 → 0 | 3.662 → 0.472 | 13.153 → 1.092 |
| legacy | 100 | 2,557 → 196 | 2,457 → 0 | 9.989 → 0.771 | 28.694 → 1.849 |
| legacy | 200 | 11,056 → 397 | 10,856 → 0 | 45.447 → 1.443 | 151.443 → 4.083 |
| indexed | 50 | 703 → 99 | 652 → 0 | 86.696 → 5.098 | 103.736 → 6.142 |
| v2 | 10 | 34 → 16 | 22 → 0 | 7.235 → 2.343 | 8.060 → 2.565 |

Attempts count `_apply` for legacy/indexed-legacy and `receive_indexed` for v2.
All ten completed measurements applied every packet with zero pending, conflict,
invalid or unavailable outcomes. All after measurements stayed within 2N attempts.
Every run performed one full row scan and exactly one FK check per applied packet.
The v2 runs decoded the 20 local outbox packets once each, before and after.
Legacy databases were 29,364,224 bytes; indexed/v2 databases were 43,888,640 bytes.
The 200-link legacy attempt reduction is 27.85×; its CPU reduction is 31.50×,
subject to the concurrency caveat above.

## Full-suite results

| Entry point | Passed | Skipped | Failed | Time |
| --- | ---: | ---: | ---: | ---: |
| `memory-sync/` | 1,525 | 17 | 0 | 513.40 s |
| Repository root, with both requirements files | 1,778 | 55 | 1 known | 544.10 s |

The root failure is exactly the exception allowed by the inherited Ground rules:
`memory-sync/test_digest_api.py::test_client_envelope_satisfies_bounded_extract_command`.
It raises `bounded_protocol.ValidationError: model worker unavailable or invalid;
coverage unchanged`; receive is not on that failure path. It passed in the
`memory-sync/` invocation. No unrelated product changes were made to address it.
All receive, #47, capture, resolution, signing and interruption tests passed.
The duplicate-ZIP-member warning comes from its intentional rejection test.
Native/optional skips remain skips; this was not a native Windows test run.

Final collection confirms 1,545 memory-sync tests: the full run collected 1,542,
plus the three benchmark smoke tests independently run and passed from both
entry points. Thus the supplementary run covers the only tests added after full
collection; there were no receive code changes after the full runs started.

## Scope and remaining issues

No design gaps were encountered. Indexed conflict retries and migration handling
were preserved because removing them would change existing proof semantics.
The only unresolved test failure is the explicitly allowed digest API failure above.

All data used was disposable. No access to `~/.hermes`, shared-memory recall,
real databases, identities, exchanges or running workers was performed. No
`GATES.md` was created. This is the only REPORT produced. No push, merge, rebase,
or operations on other branches/worktrees were performed.
