# Encrypted profile log Phase 3 REPORT

Date: 2026-10-19. All seven implementation tasks were worked in order. Source,
CLI, tests, synthetic corpus, README Broker section and FORMAT appendix are
present, including the reviewer follow-up fixes below. Acceptance of this revised
version is **not fully verified**: the sandbox denies socket binding,
and Windows/Linux execution is still required. No commit or branch was created.

## Final measured verification

Prepared interpreter: `~/.cache/agentmesh-logvenv/bin/python` only. Both final
commands used `PYTHONDONTWRITEBYTECODE=1 AGENTMESH_R2_LIVE=` to avoid bytecode
artifacts and ensure the opt-in cloud tests remained disabled.

| Invocation directory | Command arguments after interpreter | Result |
| --- | --- | --- |
| Repository root | `-m pytest -q -p no:cacheprovider profile-log/tests` | **247 passed, 5 skipped, 3 failed**, 15.62 s |
| `profile-log/` | `-m pytest -q -p no:cacheprovider tests` | **247 passed, 5 skipped, 3 failed**, 15.62 s |

The three failures are preserved, with explicit messages, not skipped or xfailed:

- `tests/broker/test_isolation.py::test_p3_1_processes`: `P3-1 blocked: sandbox denies socket binding`.
- `tests/broker/test_transport.py::test_real_transport`: `IPC acceptance blocked: sandbox denies socket binding`.
- Existing `tests/test_storage.py::test_s3_custom_http_endpoint`: `HTTP acceptance gate blocked: sandbox denies 127.0.0.1 bind`.

Baseline before source edits was **184 passed, 5 skipped, 1 failed** (the same
existing HTTP bind failure). The plan's reference to 167 existing tests was stale.
The five skips are existing opt-in R2 tests and Windows-only junction tests; no
new socket/pipe skip was introduced. There are 65 added tests relative to that
baseline: 63 pass here and 2 fail explicitly on the sandbox bind restriction.

Detector budget command:
`-m pytest -q -s -p no:cacheprovider profile-log/tests/broker/test_detect.py`.
Measured results on the synthetic repository fixture corpus:

| Category | Lines with findings | Corpus lines | Measured rate | Budget |
| --- | ---: | ---: | ---: | ---: |
| Secret | 0 | 800 | **0.00%** | ≤ 1% |
| PII | 25 | 800 | **3.125%** | ≤ 5% |

The detector module's four tests pass. The 200 new benign timestamp-bearing lines
include 13-digit epoch milliseconds in logs and JSON `created_at`/`updated_at`
fields. Twenty-five lines produce PII findings; the Thai-ID rule was not changed.
These are synthetic-corpus measurements,
not a claim about production false-positive or false-negative rates.

Additional checks: `git diff --check` passes; AST comparison confirms copied
`analyze_query` and `_score` are identical to the reference functions; byte
comparison confirms `broker/vectors.py` is identical to the reference module.
Neither reference package is imported by the new package. Requirements files are
unchanged. Working-tree changes are limited to `profile-log/**` and this REPORT.

## Gate-to-test mapping

All paths below are relative to `profile-log/tests/broker/`. “Pass” describes
executed tests on this macOS host, not an unperformed cross-platform run.

| Gate | Dedicated tests and supporting coverage | Evidence/status |
| --- | --- | --- |
| P3-1 Grant enforcement | `test_isolation.py::test_p3_1_processes`; `test_service.py::test_dispatch`, `test_scopes_filter`; `test_grants.py::test_descendants` | **Real-process gate blocked** at socket bind. Test creates separate agent subprocesses, checks sibling exclusion, denied recalls/writes/revisions, parent reads and exclusion of root/common from child grants. Supporting dispatch/hierarchy tests pass. |
| P3-2 Credential binding | `test_handshake.py::test_p3_2_binding`; `test_transport.py::test_foreign_uid`; `test_isolation.py::test_p3_7_wire_audit` | Pass: wrong signing key, replay against new server nonce, foreign device with its own matching grant, foreign account, nonce freshness, revoked open connection and foreign-UID refusal at accept. Real OS peer credential behavior still needs the external IPC run. |
| P3-3 Impostor broker | `test_handshake.py::test_p3_3_impostor`; `test_client.py::test_impostor_no_auth` | Pass: wrong pinned device key fails, closes client channel and sends only hello, never auth. |
| P3-4 Separate indexes | `test_isolation.py::test_p3_4_indexes`; `test_index.py::test_snapshot` | Pass: direct row inspection, distinct connections/files, swapped snapshot rejection/rebuild, distinct objects for identical read grants, grant/AAD mismatch rejection. |
| P3-5 No plaintext at rest | `test_isolation.py::test_p3_5_at_rest` | Pass: body/tag/task markers written and recalled through client/handshake; every regular home file, including keystore, scanned for UTF-8, UTF-16LE and UTF-16BE markers. |
| P3-6 No data keys sent | `test_isolation.py::test_p3_6_no_keys`; `test_client.py::test_author_scope_checks`; `test_cli.py::test_admin` | Pass: captures every framed broker message in a full client session, checks raw/hex/base64url data keys absent, exact response fields, exact agent-file fields, author and caller-supplied opaque scope binding. |
| P3-7 Audit | `test_service.py::test_p3_7_audit`; `test_isolation.py::test_p3_7_wire_audit`; `test_audit.py::test_audit` | Pass: exactly one line per request/final handshake outcome, checks line count before send, successful/invalid/denied/unauthenticated outcomes, stable HMAC, no query text, append failure returns only unavailable. |
| P3-8 Secret/cross-profile | `test_service.py::test_p3_8_policies`, `test_scopes_filter` | Pass: secret exclusion/inclusion after grant change, multi-profile denial/allowance and audit flag, null common write denied, explicit common write exercised. |
| P3-9 Offline | `test_offline.py::test_p3_9_offline`; `test_replica.py::test_sync`, `test_object_failures` | Pass: remote raising on every method, local recall/remember/revise/whoami, growing then empty outbox, second home verification/index/recall, tampered event rejection, failed transfers leave local watermark unchanged. |
| P3-10 Detection | `test_detect.py::test_p3_10_secrets`, `test_p3_10_pii`, `test_p3_10_budget`; `test_isolation.py::test_p3_10_outputs` | Pass: generated positives for all named secret rules, PII retention/label raising, recursive body/tag/task redaction, unsafe object-key rejection, 800-line rates above, response and audit contain kinds/counts rather than credential spans. |
| P3-11 Revisions | `test_service.py::test_p3_11_revisions`, `test_revision_scope_guard`; `test_heads.py::test_conflicts` | Pass: cross-scope revision denied even with both write grants, unreadable source denied, lowering denied, concurrent heads and further single-head revision preserve conflict. |
| P3-12 Recall shape | `test_isolation.py::test_p3_12_shape`; `test_query.py::test_port`; `test_index.py::test_selection` | Pass: exact top-level/query-plan/count/result fields, independently written decision/constraint/fact expected order, Thai analyzer behavior, two-term selection threshold and safely quoted MATCH tokens. |
| P3-13 Robustness | `test_service.py::test_p3_13_sessions`, `test_discard_pending`, `test_pending_cross_session`; `test_isolation.py::test_p3_13_frames`; `test_framing.py::test_frames`; `test_client.py::test_close_bad_frame` | Pass: oversize before body read, zero/malformed/duplicate JSON, unknown/extra fields, missing pending state, wrong signature, distinct pending envelopes for the same agent on two sessions, invalid-op discard, malformed connection closes while another session continues. |

The client/session capture tests use framed, queue-backed channels through the
actual broker connection/handshake/dispatch loop. They do not substitute a claim
that real socket tests passed. Native transport remains exercised by the explicit
failing IPC tests and is ready for the reviewer to rerun outside the sandbox.

## Reviewer follow-up: findings addressed

### Incremental indexing and deterministic evidence

- Replica startup reads object contents once to cache their SHA256 hashes. New
  puts and sync downloads update that cache from the bytes already in hand.
  `watermark()` computes/caches the same sorted-path digest from memory only;
  ordinary recall does not even need to recalculate it. Out-of-band immutable
  replica changes require broker restart; direct file edits are not monitored on
  requests.
- The broker opens/decrypts each new event once and distributes its normalized
  row only to authorized indexes. Each index retains authorized revision rows
  and adjacency/waiting-child maps. Only the affected connected component has its
  visible heads recomputed. Unknown parents remain separate roots until their
  authenticated event arrives. Cross-scope edges remain separate.
- Existing per-agent indexes survive appends and syncs. Full reconstruction is
  limited to initial missing/invalid snapshots or changed grants. Existing
  snapshot schema versions without the new event table rebuild safely. The
  broker's payload cache prevents repeated decryption across rebuilds for
  multiple agents. Prior revision authorization/label checks use retained
  authorized index rows, including after snapshot restoration.
- SQL applies FTS MATCH, literal substring and task-reference conditions together
  with scope/profile/project filters before decoding or scoring any candidate.
  Stored rows were validated at insertion and snapshots authenticated on load;
  selected candidates use JSON decoding without repeated wire validation.
- Snapshots are encrypted and lazy: clean close or explicit `flush_indexes()`
  persists them. The snapshot-swap test now closes/flushes the broker **before**
  replacing B's persisted snapshot with A's, preserving the actual AAD test.

`test_incremental.py` adds deterministic tests:

| Test | Verified invariant |
| --- | --- |
| `test_one_write` | With two readers and existing history, one remember+sign calls `open_event` once, decrypt once and replica `get` zero times; both indexes receive the row. |
| `test_recall_no_io` | Recall calls replica `get`, `open_event` and decrypt zero times. |
| `test_sync_once_each` | Four new downloaded events call `open_event` and decrypt exactly four times, followed by a zero-I/O recall. |
| `test_sql_candidates` | Only two qualifying rows are decoded from a fixture containing 104 rows; unrelated text, wrong scopes and wrong profiles are excluded by SQL. |
| `test_sql_task_and_thai` | SQL task-reference and Thai substring candidates preserve project filtering before decoding. |
| `test_restart_snapshot` | A current snapshot loads and serves recall without decryption; revising its prior event uses the stored label and decrypts only the new revision. |
| `test_revision_components` | Out-of-order children stay separate until the parent arrives, then expose a conflict; a cross-scope reference remains a separate component. |
| `test_lazy_snapshots` | Appending never calls full-history reconstruction or rewrites snapshots; close flushes changed ciphertext with no plaintext marker. |

Observed red-to-green evidence: the initial one-write test counted **8** opens
instead of 1, recall counted **6** replica gets instead of 0, four-event sync
counted **8** opens instead of 4, and SQL narrowing decoded unrelated rows. All
now pass. A restart test additionally caught decryption of the previous revision
and passed after the index-based label lookup was implemented.

### Benchmark

Run from the repository root with the prepared interpreter:

```sh
PYTHONDONTWRITEBYTECODE=1 ~/.cache/agentmesh-logvenv/bin/python profile-log/tests/broker/bench_broker.py
```

The script is not collected by pytest. It uses `tests/broker/support.setup_home`
with disposable identities and the two standard agent grants, populates events
through actual remember+sign dispatch, and measures 20 samples at each size.
The broad query `migrate database` matches every event. Each sample adds one
record, so populations grow 1,000→1,020 and 5,000→5,020 during sampling. Writes
include detection, encryption, signatures, replica publication and audit fsync;
recall includes authorization and audit fsync. Population and shutdown snapshot
flush are outside timed samples. No socket or remote is used.

Final measured timings on this Mac:

| Initial events | Write median | Write maximum | Recall median | Recall maximum |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | **10.073 ms** | 208.659 ms | **8.563 ms** | 14.470 ms |
| 5,000 | **10.378 ms** | 19.769 ms | **28.487 ms** | 46.838 ms |

The final 5,000-event median and maximum both meet the requested write <50 ms /
recall <100 ms targets across these 20 samples. The 1,000-event write spike is
reported rather than discarded; these measurements do not promise hard latency
bounds. An earlier incremental version measured 107.038 ms recall at 5,000; the
final removal of redundant row wire validation reduced it to the value above.

### Surviving mutants

`test_mutants.py` contains one dedicated test per finding:

- `test_m11_cross_profile`: the agent reads both work and personal scopes before
  testing denial with `allow_cross_profile=False`, then succeeds when it is true.
- `test_m14_exact_write`: work-root write permission does not cover work/a;
  work/a write permission does not cover work. Explicitly granted scopes prepare
  successfully as positive controls.
- `test_m15_pending_grant`: after remember, a grant digest change, write-scope
  removal, or revocation denies sign and leaves the event replica empty.

All three pass on the real implementation. Each named mutant was then injected
into a separate disposable interpreter by compiling a modified `Broker.dispatch`
function, without editing the source file. **M11, M14 and M15 each caused its
corresponding dedicated test to fail (pytest exit 1): all three killed.**
These supplement P3-8, P3-1 and P3-2/P3-13 respectively.

### Name uniqueness and corpus

Duplicate profiles were already rejected by `BrokerState.create` and CLI init.
The new failing test exposed that `ScopeTree.add` accepted duplicate sibling
names. It now rejects duplicates before generating a key or wrap. `BrokerState.add`
centralizes named scope creation for the CLI, and imported state/name resolution
also rejects ambiguity rather than selecting the first match. Identical project
names under different parents remain valid.

`test_names.py`: `test_state_duplicates`, `test_cli_duplicates`,
`test_imported_duplicates`, and `test_distinct_parents` all pass.
`test_detect.py::test_epoch_corpus` first failed because no timestamp lines were
present and now checks the 200 added lines and real Thai-ID findings. Updated
rates are **0/800 secret (0%) and 25/800 PII (3.125%)**; the rule and 5% assertion
are unchanged. Timestamp false positives remain a documented detector limitation,
but the expanded corpus does not exceed the owner's budget.

## Task execution and TDD evidence

1. Event two-step/private helpers: new tests first failed on missing
   `prepare_event` and `Account.from_private`; after implementation, the new tests
   plus existing event tests passed (42 tests). Controlled encryption/ULID input
   compares output bytes against the old sealing formula; unchanged verify/open
   functions validate and decrypt the result.
2. State/home/grants: tests first failed importing the missing broker package.
   Round-trip epochs/wraps, atomic replacement failure, modes, symlink/unsafe-root
   refusal, grant digest/revoke and descendant coverage then passed.
3. Framing/transport/handshake: tests first failed importing missing modules;
   implemented unit checks passed, and native IPC remained explicitly failing
   because binding was denied. Pipe byte-message framing has a portable adapter
   test; native Windows pipe execution is pending.
4. Detection: tests first failed importing the missing detector. Implemented
   rules and runtime-generated positives passed on the initial 600-line corpus.
   The reviewer expansion to 800 lines and current rates are documented above.
5. Query/index/heads/vectors: tests first failed importing missing modules; copied
   query/vector logic and implemented RAM indexes, encrypted snapshots and head
   grouping then passed their tests.
6. Replica/audit/service/client: tests first failed importing missing components;
   implementation passed dispatch, revision, audit, replica and client checks.
7. CLI/acceptance/docs: CLI test first failed because the module was absent.
   Integrated acceptance tests cover all thirteen gates. Later regression tests
   first exposed and then verified fixes for invalid-op pending-write retention,
   delayed per-agent index updates, client framing-error closure, CLI usage-error
   argument leakage, and missing wrap publication after live scope additions.

## Deviations and implementation choices

- No GATES.md or shared-memory lookup was used: the explicit task ground rules
  prohibit those artifacts and user-data access. This REPORT is the acceptance
  ledger. No sub-agents were used.
- Several Task 7 acceptance tests passed immediately when added against completed
  Tasks 1–6. Component tests and the listed regression fixes followed observed
  red-to-green runs; no artificial failing implementation was inserted solely to
  make those already-working integration cases fail.
- Optional request fields may be omitted, equivalent to their documented
  null/default values. Unknown fields remain rejected. Empty profile/scope lists
  select nothing. `whoami` names its readable/writable lists `read` and `write`.
- `broker run --sync-interval` also accepts the sync command's explicit remote
  flags. An interval without a remote is rejected. The plan supplied an interval
  but no way for that command to choose a remote; no implicit remote or extra
  persistent configuration was introduced.
- Equally narrow default-write candidates use lexical local-name order; common
  is never chosen implicitly. `--name` is accepted as an administration label but
  not persisted, because the specified grant and agent-file schemas have no name
  field.
- Local scope names are resolved by the authenticated, pinned broker. The client
  checks all author/certificate bindings and supports mandatory equality to an
  opaque ID when its caller supplies `expected_scope`. The specified agent file
  and response schemas supply no name-to-opaque-ID map, so the CLI cannot
  independently pre-pin an opaque scope ID from a requested name. No new protocol
  field or key-file field was invented. This is a protocol limitation for review;
  server-side scope authorization is still enforced and tested.
- Recall ranks all matching authorized heads rather than applying the reference
  database's intermediate candidate-pool cap. Analyzer, scoring formula, ordering
  and the specified two-matched-term selection rule are preserved; body `project`
  filtering follows the owner's updated plan.
- Remote wrap objects additionally require a valid authenticated wrap under a
  locally retained parent epoch and a matching object path. Unknown parent epochs
  are counted invalid; enrollment/keyring distribution remains deferred.
- Added `profile-log/.gitignore` solely to unignore the required synthetic JSONL
  corpus, which the repository's general `*.jsonl` rule otherwise hides.
- Tests resolve the temporary `/tmp` root to its actual path on macOS before
  BrokerHome validation, preserving short socket paths and symlink refusal.

## Open questions and remaining external verification

1. The reviewer reported **234 passed, 5 skipped** outside the sandbox for the
   pre-follow-up version, including P3-1 and native transport. That result is
   attributed to the reviewer, not to a run here. The revised version still needs
   an outside-sandbox native IPC rerun and the macOS/Linux/Windows CI matrix
   (`windows-2025`). Its whole-change all-tests-pass criterion is not certified here.
2. Should a future protocol provide an authenticated local-name-to-scope-ID map
   so CLI clients can independently assert their expected opaque scope before
   signing? The present fixed schemas do not provide that mapping.
3. Phase 4 must replace the plaintext 0600 key store, remove account-root keys
   from daily devices and implement the marked presence checks. Malicious code
   running as the same OS user remains outside Phase 3's protection, as required
   and documented in README. Audit tamper evidence remains deferred.

No external network, installation, new dependency, real credential, live memory
DB, `~/.hermes`, identity exchange or keychain was used. All created identities
and test data are disposable. The original source modules, installers, services,
workflows and plan/spec documents were not modified. No tests were softened to
hide the sandbox failures, and no commit was made.

## Reviewer verification (Claude, 2026-10-19)

Run outside the Codex sandbox on macOS with the prepared interpreter. The suite passes from the repository root
and from `profile-log/`: **250 passed, 5 skipped** (opt-in R2 and Windows-junction skips). This includes
`test_p3_1_processes` (separate agent processes over the real Unix socket), `test_real_transport` and the
existing loopback HTTP test. Three-OS CI (incl. the Windows named pipe) runs on the PR.

Review round 1 found that every write rebuilt every agent's index from scratch, and that each request
re-hashed the whole replica (writes took 1.07 s at 400 events). Codex made indexing incremental. Measured
after the fix: write median 9.7 ms and recall median 28 ms at 5,000 events.

Mutation testing of the isolation properties. Each mutant was applied alone and the broker suite run against
it. All 19 mutants are now killed. Three mutants survived round 1 (M11, M14, M15); a dedicated test was then
added for each.

| Mutant | Result |
| --- | --- |
| M1 readable() ignores grant | killed |
| M2 one index shared across agents | killed |
| M3 secret-label filter removed | killed |
| M4 audit skipped on successful requests | killed |
| M5 auth signature not verified | killed |
| M6 grant not re-checked per request (revocation) | killed |
| M7 peer-uid check disabled | killed |
| M8 client skips pinned device-key check | killed |
| M9 recall `scopes` subset check removed | killed |
| M10 revise across scopes allowed | killed |
| M11 cross-profile check removed | killed (round 2 test) |
| M12 snapshot AAD drops agent and grant digest | killed |
| M13 whoami leaks the index master key | killed |
| M14 write grant covers descendants | killed (round 2 test) |
| M15 sign skips grant digest and write scope | killed (round 2 test) |
| M16 index rows ignore readable scopes | killed |
| M17 auth accepts another device's agent | killed |
| M18 client skips author binding check | killed |
| M19 audit stores query text | killed |
