# Staging Retention for Raw Observation Events: Report

**Decision.** On 2026-10-22 the owner chose retention ("โอเค เอาแค่ retention ไปก่อน", "OK, just retention for now") over removing the staging database. The local SQLite staging DB stays, and raw `observation_events` stop growing forever.

**Out of scope.**
- Dropping `_sync_*`/`_pg_*` and VACUUM belong to Phase 7b (PR #91 plan).
- No backup or copy of any DB is made.
- Nothing was run against the live Mac except one read-only count query, described under "What the live DB looks like".

**Workflow.** Codex (gpt-6-astra) implemented. Claude reviewed and mutation-tested every keep-gate.

## What it does
`agentmesh.py staging retention [--older-than-days N] [--apply] [--json]` is implemented in `memory-sync/staging_retention.py`.

**Dry-run.** This is the default. It opens the DB read-only, prints counts only, and writes nothing.

**`--apply`.** It deletes in one `BEGIN IMMEDIATE` transaction, which also:
- removes the FTS entries (the external-content `events_fts_delete` trigger is required; otherwise it refuses);
- writes hashed tombstones;
- records a run in `agentmesh_retention_runs`.

It refuses while `packet_sync.enabled` is true, and checks this both before and inside the transaction.

**N (`older_than_days`).**
- Defaults to 30.
- Can be set in `settings.json`/`settings.local.json` as `staging.retention.older_than_days`. The profile-log settings schema accepts it.
- Values under 7 are refused.

**Database.** Without `--database` it uses `AGENTMESH_DATABASE` or `~/.agentmesh/memory-sync/data/runtime.json`.

A row is deleted only if it passes every keep-gate:

| Gate | Kept when |
|---|---|
| Evidence | Its id is in `memory_sources` (any memory status), or in any `memory_items`/`memory_summaries` metadata `event_ids`/`batch_event_ids`/`source_event_ids`/`evidence_event_ids`, or in bounded-digest window state; native string ids too. Recall's local evidence expansion stays intact. |
| Coverage | It is pending for **any** known summary consumer: the default consumer, consumers seen in `summary_state`/metadata/digest tables, the workflow config, and `jobs/agentmesh-production.json`. Rows that are not user or assistant need an explicit per-event receipt. |
| Age | `occurred_at` is within N days, missing, or unparseable. |
| Session complete | Any of: the cursor is missing; the cursor updated within N days; the cursor is the most recent; the file identity changed; the file size ≠ cursor offset; the file mtime is within N days; the transcript is missing; or the transcript is the newest one. |

**No re-ingest or re-summarize.**
- Ingest skips any event whose `sha256(event_key)` is tombstoned, even after a cursor reset. Tombstones store no transcript content.
- `summary_state` receipts, cursors, memory tables and `_sync_config` are never touched.
- A digest tick after retention makes zero summarize calls (tested).

**Audit.**
- `retention_audit` now reports `retention_enabled`, `last_run_at`, `rows_deleted_total` and the last run's counts. Its warning drops peer delivery, since packet sync is retired.
- Insight's ingest pane shows the same fields. No other Insight code changed.

**Scheduling.** No unit is added. The owner decides after a dry-run.

## What the live DB looks like (read-only count query, 2026-10-22)
| Item | Count |
|---|---|
| Summary consumers | `agentmesh/sqlite-summary-v1` only (plus the retired scalar `hermes` checkpoint, which is ignored) |
| Per-event receipts | 42,435 |
| `observation_events` | 60,140 |
| — `tool_call` | 31,324 |
| — `tool_result` | 20,663 |
| — `user_request` | 4,426 |
| — `assistant_response` | 3,999 |

Receipts outnumber the user and assistant rows, so tool rows carry receipts too and retention will find candidates. The owner's dry-run gives the real number.

## Behaviour to know before applying
1. **Export notes lose the raw text of retired events.**
   - `sqlite_export.publish` regenerates session notes from the DB.
   - Retired events keep their `^event-N` anchor under "Retired observations", so links still resolve, but their bodies leave the export.
   - The transcripts on disk are untouched and remain the raw source.
2. **Re-enabling packet sync after retention would publish the deletions** to peers. That is why `--apply` requires packet sync off. The 7a rollback should be done before any retention run, not after.
3. **Content can survive in two places until 7b:**
   - copies of deleted rows may remain in `_sync_shadow`;
   - the file does not shrink until 7b's VACUUM. Freed pages are reused for new rows.
4. **The rules are conservative.** Rows from a transcript that was moved, deleted or is still growing are kept indefinitely. The dry-run's `kept_*` counts show why rows stay.

## Tests
`memory-sync/test_staging_retention.py` has 26 tests:
- evidence through `memory_sources` for every status, through metadata, and through native/digest history;
- pending rows of every kind;
- every consumer, including a configured consumer before its first receipt;
- new or invalid timestamps;
- active or unverifiable sessions;
- digest windows;
- a dry run that is byte-identical and prints counts only;
- FTS deletion, and the protected tables staying unchanged;
- a missing FTS trigger refusing atomically;
- tombstones stopping re-ingest after a cursor reset, and with other parsers;
- no re-summarize and no export regeneration;
- the packet-sync refusal;
- N < 7;
- the settings override and the audit's last run;
- rollback on a delete failure;
- the CLI.

Also: `insight/test_staging_retention_view.py` (2 tests) and `profile-log/tests/settings/test_retention.py` (3 tests).

**Mutation testing.** Each mutation was applied alone, and `test_staging_retention.py` had to fail. All were killed:
- the evidence gate;
- `memory_sources`;
- metadata evidence;
- the pending gate;
- non-summarized kinds;
- every consumer;
- age;
- bad timestamp kept;
- active session;
- missing cursor;
- recent cursor;
- incomplete file;
- N ≥ 7;
- the tombstone write;
- the tombstone read in ingest;
- dry-run read-only;
- both packet-sync checks together. Each one alone survives, because the other check catches it.

**Suites** (local, macOS):

| Suite | Passed |
|---|---|
| profile-log | 782 |
| memory-sync | 1901 |
| insight | 184 |

The only failures also fail on `main`:
- `test_p6_s12_close_cross_verifier` needs the gateway's npm dependencies, which CI has;
- `test_digest_api.py::test_client_envelope_satisfies_bounded_extract_command`.

CI covers profile-log on three OSes, plus the standalone smokes.

## Owner steps (Mac, after merge)
`REV` is the merge commit. Run from your own terminal. Nothing below copies `mac.db`.

**1. Update the code.** This uses the same scripts as 7a; the profile-log app swap is needed only for the new settings key.
```sh
cd ~/Workspaces/AgentMesh && git checkout main && git pull --ff-only
D="$HOME/Library/Application Support/Claude/side-session-notes/local_4093101b-4dea-405e-8320-bb9a84bd6406/deploy-scripts"
FROM=f54197c REV=$(git rev-parse HEAD) zsh "$D/overlay_memory_sync_code.sh"
REV=$(git rev-parse HEAD) zsh "$D/swap_profile_log_app.sh"
```

**2. Dry-run.** This is read-only and prints counts only.
```sh
cd / && ~/.agentmesh/jobs/.venv/bin/python ~/.agentmesh/memory-sync/agentmesh.py staging retention --json
```
Check two things:
- `candidates` is the number of rows that would go;
- `kept_evidence`, `kept_pending`, `kept_too_new` and `kept_active_session` say why the rest stay.

**3. Apply, only if the dry-run looks right.** Packet sync must be off, as it is since 7a.
```sh
cd / && ~/.agentmesh/jobs/.venv/bin/python ~/.agentmesh/memory-sync/agentmesh.py staging retention --apply --json
cd / && ~/.agentmesh/jobs/.venv/bin/python ~/.agentmesh/memory-sync/retention_audit.py ~/.agentmesh/memory-sync/data/mac.db
```

**4. Optional: a different window.** Either:
- add `"staging": {"retention": {"older_than_days": 45}}` to `~/.agentmesh/settings.json`, then run `~/.agentmesh/bin/agentmesh-log settings validate`;
- or pass `--older-than-days 45` for one run.

**5. Scheduling.** None is installed. Run it by hand whenever you want, or ask for a unit after you have seen a few runs.
