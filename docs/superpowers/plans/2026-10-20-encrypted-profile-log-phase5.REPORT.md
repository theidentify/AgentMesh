# Encrypted profile log Phase 5 REPORT

Date: 2026-10-20. Implements all eight Phase 5 tasks and both owner amendments.
The portable-provenance blocker is resolved with device-level trust and persistent
local import-agent history. Whole-change acceptance still requires the reviewer to
rerun sandbox-denied socket tests and perform the three-OS checks. No commit,
branch or push was made.

## Work completed

| Task | Implementation |
| --- | --- |
| 1. Source and bodies | Read-only URI plus query_only, schema validation, whitelisted bodies, integer evidence, typed row hash, UTC source timestamps, private backup snapshot. |
| 2. Mapping | Strict UTF-8 JSON, exact then longest-prefix matching, skip/unmapped handling, names/counts dry run and preflight before writes. |
| 3. Broker import | allow_import, reserved-key rejection, signed imports, source reservation/busy, retirement/remapping, replica-derived ledger, permanent local import-agent set and peer device trust. |
| 4. Compatibility recall | Global/project/task selection, summary inclusion, source references and provenance, retired-head hiding and schema-5 index rebuild. Ordinary recall retains its result fields. |
| 5. Runner/report | plan/run/rows/catch-up/report, bounded busy retry, authority/takeover guard, resumable imports and exclusive 0600 content-free reports. Disposable-only file-keystore init. |
| 6. Bridge/hooks | Strict opt-in config, detached notifications after commit, local evidence with broker candidates, sanitized fallback, rollback and bridge-only packaging additions. |
| 7. Parity/services | Disposable offline parity with restricted old-side candidates; launchd, systemd and Windows broker/catch-up units; exclusive creation, snapshots and unchanged default sync output. |
| 8. Docs | README “Migration from SQLite”, FORMAT “Phase 5 migration bodies”, wire messages, trust limitation, rollout/rollback and this report. |

Only allowlisted files changed. No network, installation, real database, transcript,
identity, native keychain or `~/.hermes` access occurred. Tests use generated temporary
state and the prescribed interpreters. No socket failure was skipped or xfailed.
No GATES.md or other working artifact is left in the worktree.

## Owner amendments

**Snapshot exception approved 2026-10-20:** the private parity SQLite snapshot and
its `-wal`/`-shm`/`-journal` sidecars are excluded from the plaintext scan. Five
separate tests assert directory 0700, all files 0600 (POSIX), snapshot/sidecars as
the only marker-bearing plaintext files, removal on success, and removal on
injected error. The actual completed backup has no sidecar holding a marker;
the snapshot is the sole marker-bearing file. Every other file is scanned for
UTF-8 and UTF-16 markers. This is the approved exception, not encryption of the
SQLite snapshot itself.

**Device trust approved 2026-10-20:** private `control.import_agents` persists all
agents that have held allow_import and is never pruned by revoke. Peer provenance
uses the existing same-account historical certificate/device-chain verification
at event ULID time, including revocation. No replicated permission record was
added. A compromised paired device can forge or retire migrated rows in scopes
whose keys it holds. Source timestamps do not determine device-revocation validity.
README and FORMAT document this limitation and the existing historical-time caveat.

## Gate-to-test mapping

`M` means `profile-log/tests/migrate/`; `S` means `memory-sync/`.

| Gate | Dedicated tests | Evidence |
| --- | --- | --- |
| P5-1 | M/test_source.py::test_p5_1_source; S/test_profile_log_end_to_end.py::test_p5_1_operations_read_only | WAL and DELETE source hash/schema unchanged through all operations; write refusal persists with query_only disabled; no rollback journal. |
| P5-2 | M/test_mapping.py; M/test_run.py::test_p5_2_run_report, test_p5_2_skipped_and_catchup | Exact/prefix routing, one source chain, every row reported, inactive/older/session reasons, disappearance and catch-up. |
| P5-3 | M/test_import.py::test_p5_3_import_and_retire, test_p5_3_remap_restart | Unchanged rerun; revision/retirement; old-scope retirement then new root; restart and remap-back idempotence. |
| P5-4 | M/test_run.py::test_process_exit, test_p5_4_resume; M/test_import.py::test_p5_4_reservation, test_p5_4_discard_reservation; M/test_processes.py::test_p5_4_concurrent_processes | Real child exits with code 73 after the first persisted event; rerun completes two heads, each with one event. WAL/DELETE variants pass. Session contention/release passes. Two-run/one-rows native concurrency test fails explicitly at sandbox bind; reviewer rerun required. |
| P5-5 | M/test_source.py::test_bodies; M/test_import.py::test_p5_5_secret_and_metadata; M/test_run.py::test_no_evidence_text, test_no_remote | Generated positive secret redacted/labelled secret; observation and metadata markers absent from decrypted bodies; integer evidence retained; encrypted-home scan; only local replica storage, no S3 or sync_once, exact migrated-event outbox increment. |
| P5-5 snapshot | S/test_profile_log_parity.py::test_snapshot_dir_mode, test_snapshot_file_modes, test_snapshot_plaintext, test_snapshot_success, test_snapshot_error | Five independent tests for the owner-approved exception. |
| P5-5 offline/report | S/test_profile_log_parity.py::test_parity_no_remote, test_p5_6_parity; S/test_profile_log_end_to_end.py::test_hook_no_remote | Storage constructor/sync spies for parity and hook; 330 source heads remain local in the outbox. Report excludes content/query/project markers. |
| P5-6 | S/test_profile_log_parity.py::test_p5_6_parity; M/test_import.py::test_p5_6_compat_selection | 300 items, 30 summaries, English/Thai, globals/tasks/five projects, 205 generated queries; overlap_at_12=1.0, top5_hit=1.0, exact_order=1.0 (thresholds 0.95/0.98). Real-data result under "Reviewer verification": 1.0/1.0/1.0. |
| P5-7 local never-import | M/test_import.py::test_p5_7_replica_forgery | Correctly sealed provenance from a local never-import agent is not admitted. |
| P5-7 peer | M/test_peer_authority.py::test_p5_7_peer_import | Peer has neither source agent grant nor import-agent history; after restart, verified import yields one ledger entry and compat integer source ID. |
| P5-7 revoked peer | M/test_peer_authority.py::test_peer_revoked | A signed device revocation makes subsequent provenance inadmissible, including direct ingestion. |
| P5-7 local history | M/test_peer_authority.py::test_local_revoked | Import, close, revoke, save, restart: old event still admitted and importer ID retained. |
| P5-7 API | M/test_import.py::test_p5_7_authority, test_import_scope, test_no_ordinary_revision | Import/ledger denied without permission, scope-write enforcement, reserved keys rejected on remember/revise, ordinary overwrite of a migrated row denied. |
| P5-8 | S/test_profile_log_bridge.py::test_p5_8_after_commit, test_failed_commit, test_p5_8_detached, test_hook_disabled, test_p5_8_hanging_child_does_not_block | Both writers notify exactly once after visibility to another connection; hook exception preserves commit result; failed and stale commits never notify; absent/disabled/non-authority/dual-write-off start nothing; detached hanging child returns within 1 second. |
| P5-9 | S/test_profile_log_bridge.py::test_p5_9_stored_golden, test_sql_golden, test_subprocess_recall, test_p5_9_bad_config, test_p5_9_cli_failures | Original output/key order and original SQL hashes; actual subprocess success with broker content/local evidence and existing row keys; exit/timeout/malformed JSON/wrong shape all return full SQLite fallback with one warning; invalid configs disable safely. |
| P5-10 | S/test_profile_log_bridge.py::test_rollback_state, test_p5_9_hybrid_and_rollback | Disabling restores serialized golden output, starts no hook process and leaves all other files byte-identical. |
| P5-11 | S/test_profile_log_services.py::test_snapshots, test_exclusive, test_sync_unchanged, test_p5_11_units | Separate platform/unit snapshots and exclusive-create cases; 0600 outputs; validated paths; no sync interval; default generate bytes match fixtures captured from original HEAD code. |
| Integration | S/test_profile_log_end_to_end.py::test_end_to_end | Initial import, digest changes/resolution, hook, catch-up and hybrid/SQLite fixed-query row-ID agreement. |

## Final verification

Every command uses `PYTHONDONTWRITEBYTECODE=1`. Profile-log and dependent
memory tests additionally use `AGENTMESH_R2_LIVE=`. No dependencies were installed.
`LOGPY` below means `~/.cache/agentmesh-logvenv/bin/python`; `TESTPY` means
`~/.cache/agentmesh-testvenv/bin/python`.

| Directory / interpreter | Final command arguments | Result |
| --- | --- | --- |
| Root / LOGPY | `-m pytest -q -p no:cacheprovider --tb=short profile-log/tests` | **360 passed, 5 skipped, 4 failed**, 43.30 s |
| profile-log/ / LOGPY | `-m pytest -q -p no:cacheprovider --tb=short tests` | **360 passed, 5 skipped, 4 failed**, 43.30 s |
| Root / TESTPY | `-m pytest -q -p no:cacheprovider --tb=short memory-sync` | **1563 passed, 19 skipped, 10 baseline failures, 20 subtests passed, 1 warning**, 305.11 s |
| Root / LOGPY | `-m pytest -q -p no:cacheprovider --tb=short memory-sync/test_profile_log_bridge.py memory-sync/test_profile_log_parity.py memory-sync/test_profile_log_services.py memory-sync/test_profile_log_end_to_end.py` | **55 passed**, 71.39 s |

The profile-log failures are explicit bind denials, not functional assertion
failures (paths relative to profile-log/):

- tests/broker/test_isolation.py::test_p3_1_processes — existing P3-1 bind denial.
- tests/broker/test_transport.py::test_real_transport — existing native IPC bind denial.
- tests/test_storage.py::test_s3_custom_http_endpoint — existing local HTTP bind denial.
- tests/migrate/test_processes.py::test_p5_4_concurrent_processes — new P5-4
  two-run/one-rows process acceptance test, explicitly fails at sandbox socket bind.

The five profile-log skips are existing opt-in live R2 and Windows-only cases.
The test-venv memory suite skips optional dependencies, including the two new
agentmesh_log-dependent modules via the prescribed pyrage import guard; those
modules execute successfully in the separate logvenv run above.

Memory-sync baseline failures, all from unchanged test_digest_api.py and all due
to denied ThreadingHTTPServer binding at 127.0.0.1:

- test_digest_routes_by_provider_and_reports_routed_model
- test_digest_rejects_bad_routes_and_never_echoes_worker_output, five cases:
  nope:x-404, fake-400, fake:bad name-400, fake:boom-502, fake:liar-502
- test_token_is_required_when_configured
- test_client_worker_emits_scheduler_envelope
- test_client_envelope_satisfies_bounded_extract_command
- test_bench_reports_validity_stability_and_cost

The existing OTA warning is duplicate ZIP entry SYNC.md. No baseline test was
modified to suppress a failure. `git diff --check` passes; 38 changed/new paths
are all allowlisted. Packaging changes add only profile_log_bridge to the two
module lists. Event v1 bytes, installers, workflows, ingest and sync machinery
are unchanged.

## TDD and implementation choices

The existing peer regression and the new revoked-local restart regression failed
before the trust change (zero admitted entries). Both pass after persisting importer
history and admitting valid peer device chains. Remaining coverage exercises existing
implementation boundaries: real process exit, injected commit failure, subprocess
fallbacks, independent snapshot conditions, storage spies and service snapshots.
A service test initially exposed a fixture mismatch between interval 60 and 60.0;
the fixture was corrected against the original default generate command. No service
behavior was changed to fit it. Output and SQL golden fixtures were captured from
original HEAD recall code using only synthetic temporary SQLite data. SQL fixtures
store hashes, not statement/query logs.

- Compat adds whitelisted source and evidence_event_ids to supply the bridge's
  original-row conversion. It never exposes row_hash. This extension is documented
  in FORMAT; non-compat fields are unchanged.
- Task summaries have no project and map through null_project, documented in README.
- Parity uses the real signing client against in-process broker dispatch. Its old
  side applies profile/secret eligibility before candidate limits. It does not
  substitute for the blocked native transport test.
- Generated units read launch/key/mapping settings from the bridge config and write
  a new output directory. Direct catch-up honors disabled/non-authority configuration.
- The unlazy acceptance ledger is this REPORT rather than a forbidden GATES.md.
  Shared-memory recall was not invoked because real database access is prohibited.
  No sub-agents were used.

## Reviewer verification (Claude, 2026-10-20)

**Real-data parity (owner-approved, read-only snapshot).** Source: the Mac live
database, opened only through the tool's `mode=ro` backup snapshot in a private
temporary directory (removed afterwards; verified none left). Mapping for the run:
every project, `global` and `null_project` to `work/common` (the plan's default;
the owner still writes the real mapping from `migrate plan`). Corpus 483 items
(409 active) and 33 summaries; 228 queries (200 sampled items plus one status
query per project); 0 withheld, 0 unmapped.

| Run | overlap_at_12 | top5_hit | exact_order |
| --- | --- | --- | --- |
| As implemented ("latest version" summaries) | 0.967 | **0.958 (fail)** | 0.798 |
| After the summary amendment | **1.0** | **1.0** | **1.0** |

All 46 differing queries missed one 36 KB project summary. The live
`memory_summaries.version` values are range-allocated IDs (1 and 2199023255554 for
the same project), so "latest version" chose the wrong row, while `recall_memory`
returns every matching summary. The owner chose to migrate every non-session summary
row (plan amendment; `older_version` removed; regression
`M/test_source.py::test_every_summary_row`). Reports hold metrics and integer IDs
only and stayed in the reviewer's private scratch directory.

**Fixes made in review.** The hook in `bounded_digest.apply` and
`summarize_memory.commit_response` no longer lets an import failure of the optional
bridge escape after commit. The `oskeystore.py` module docstring is restored above its
new import. New tests: `test_retired_summary_hidden` (a retired summary must
disappear even though summaries need no text match), `test_future_timestamp`
(`created_at` more than 5 minutes ahead is refused), `test_unmapped_refuses_all` (an
unmapped project writes nothing).

**Mutation testing.** 22 mutants on the key properties. 17 were killed in round 1.
After the new tests, 20 of 22 are killed. The two survivors are defense in depth with no
observable change:

- removing `PRAGMA query_only` (the `mode=ro` URI still refuses writes; swapping
  `mode=ro` is killed);
- skipping the import-agent bookkeeping in `revoke` (`record()` already persisted the
  agent at grant time).

Killed: rw source; reservation and `busy`; the unchanged check; the local import
check; historical device verification; reserved keys in `remember`; the import
scope check; the `allow_import` check; retired-head hiding; compat global
selection; compat summary selection; the hook's authority flag; recall fallback;
the future timestamp limit; the unmapped refusal; parity directory mode; snapshot
file mode; the hook before commit; metadata leaking into bodies; skipped redaction.

**Reviewer test runs (outside the sandbox, macOS).**

- Profile-log, full: all pass, including the four socket tests that were blocked in
  the sandbox.
- Memory-sync, full (test venv): 1572 passed, 19 skipped, 1 failed. The failure is
  `test_digest_api.py::test_client_envelope_satisfies_bounded_extract_command`, which
  also fails on an unmodified `main` checkout, so it is pre-existing.
- The agentmesh_log-dependent memory-sync tests (logvenv) pass.

## Owner steps

1. Rerun the four explicit socket tests outside this sandbox and execute the suite
   on macOS, Linux and Windows. Perform the native Mac/Windows key-store smoke
   separately; automated tests here use disposable file stores/fake backends.
2. After review, use migrate plan to write and review the mapping. Prefer common
   plus project filters; create project scopes only where reader boundaries differ.
   Real-data parity already passed in review (above); rerun it after writing the real mapping.
3. After merge, initialize a dedicated OS-protected Mac broker home, preserve the
   recovery kit, create profiles/scopes, grant the importer allow_import plus mapped
   writes, and give the recall agent its scoped access. Open first_access presence
   for first requests. Start the broker without automatic remote sync; migrate run,
   inspect its content-free report, then enable the bridge with authority=true.
4. Generate and inspect the broker/catch-up units, then manually load them. Keep
   exactly one migration authority. Use catch-up to repair missed hook notifications
   and incorporate other devices' rows arriving through existing packet sync.
5. After Windows native key-store smoke and pairing, enable recall there with
   authority=false and dual_write=false. Inspect/register only the desired units.
   Device-level provenance trust and its limitation apply to every paired device.
6. Rollback by setting enabled=false or removing the bridge config and disabling
   generated units. Before any broker sync, remove the dedicated migration home to
   discard local migrated objects. After sync, use profile delete and its recovery,
   presence and old-share/offline-backup procedures. Bridge disable alone does not
   delete ciphertext or retract distributed keys.

Reviewer commits. No implementation blocker remains; native transport, cross-OS
execution, real-data parity and rollout are explicitly assigned above.
