# Staging Retention for Raw Observation Events: Report

**Decision.** On 2026-10-22 the owner chose retention ("โอเค เอาแค่ retention ไปก่อน", "OK, just retention for now") over removing the staging database. The local SQLite staging DB stays, and raw `observation_events` stop growing forever.

**Out of scope.**
- Dropping `_sync_*`/`_pg_*` and VACUUM belong to Phase 7b (PR #91 plan).
- No backup or copy of any DB is made.
- The original live inspection was read-only. The owner later supplied the dry-run counts below. This revision used disposable test databases only; no live store access.

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
| Coverage | It is pending for **any** known summary consumer: the default consumer, consumers seen in `summary_state`/metadata/digest tables, the workflow config, and `jobs/agentmesh-production.json`. Assistants need their own coverage; anchors and non-conversation rows need whole-turn coverage, defined below. |
| Age | `occurred_at` is within N days, missing, or unparseable. |
| Session complete | Cursor missing, invalid, recent, or newest; existing file recent or newest by mtime; or same-identity file size ≠ cursor offset. Old gone/moved/replaced files no longer force active. Ties for newest stay active. |

**Turn coverage.** Within one `source_path`, order by parsed UTC `occurred_at`, then `id`. A turn starts at a `user_request` and ends before the next. It is covered only when its anchor and every `assistant_response` are absent from `pending_ids(consumer)` for every known consumer. With no assistant, anchor coverage suffices. Tools/system rows need no receipts of their own. Assistants retain their own-coverage rule.

IDs are not transcript positions: allocation ranges, cursor resets and imports can break that assumption. Parsers store native IDs, not line numbers. Missing/unparseable timestamps stay pending. Because their turn position is unknown, all anchors/tools in that transcript conservatively stay pending too. Rows before the first anchor, including transcripts without an anchor, stay pending.

An anchor is deletable only when its whole turn is covered, in addition to the unchanged evidence, age and session gates. Leftovers from a deleted anchor were therefore covered when it went. On later runs they can attach to an earlier surviving anchor: an uncovered earlier turn keeps them; a covered earlier turn can only delete already-covered rows. Without an earlier anchor they stay pending. Evidence-kept anchors remain. A two-run regression covers a too-new tool left behind by a deleted anchor and then attached to an earlier uncovered turn.

**Re-ingest and re-summarize.**
- Ingest skips any event whose `sha256(event_key)` is tombstoned, even after a cursor reset. Tombstones store no transcript content.
- `summary_state` receipts, cursors, memory tables and `_sync_config` are never touched.
- Gone-file discovery inserts zero rows. Same-path replacement with old lines hits the tombstones, inserts zero rows, and makes zero summarize calls (legacy and digest paths tested).
- **Pre-existing limit:** all three parsers include `source_path` in `event_key`. Moving a transcript to a new path creates new keys and can re-ingest/re-summarize its contents, with or without retention. Tombstones do not prevent this. The key format is unchanged; the move regression asserts this behavior.

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

The owner's subsequent 30-day dry-run found **0 candidates**: `kept_active_session=24678`, `kept_evidence=3547`, `kept_pending=18392`, `kept_too_new=14491`. The DB then had about 61k events, about 52k tool calls/results, and one consumer, `agentmesh/sqlite-summary-v1`. Receipt counts alone did not establish tool coverage. This prompted the turn-coverage and inactive-session changes. Counts are first-match keep reasons, not independent gate totals.

No live dry-run was repeated for this revision. Run the updated dry-run before applying; no new candidate count is claimed.

## Behaviour to know before applying
1. **Export notes lose the raw text of retired events.**
   - `sqlite_export.publish` regenerates session notes from the DB.
   - Retired events keep their `^event-N` anchor under "Retired observations", so links still resolve, but their bodies leave the export.
   - The transcripts on disk are untouched and remain the raw source.
2. **Re-enabling packet sync after retention would publish the deletions** to peers. That is why `--apply` requires packet sync off. The 7a rollback should be done before any retention run, not after.
3. **Content can survive in two places until 7b:**
   - copies of deleted rows may remain in `_sync_shadow`;
   - the file does not shrink until 7b's VACUUM. Freed pages are reused for new rows.
4. **The rules are conservative.** Growing same-identity files, recent files/cursors, newest transcripts, and unverifiable cursor stamps stay active. Quiet fully consumed files and old gone/moved/replaced files can become eligible. The dry-run's `kept_*` counts show why rows stay.

## Tests
`memory-sync/test_staging_retention.py` now passes 70 parametrized cases. Original coverage:
- evidence through `memory_sources` for every status, through metadata, and through native/digest history;
- pending conversation rows and whole-turn coverage for tools/system rows;
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

**Gate revision tests (2026-10-23).**
- `test_covered_turn_releases_tools_without_receipts` (3): every consumer covered, no tool receipt needed.
- `test_uncovered_turn_keeps_anchor_and_tools` (3): pending anchor, pending assistant, second consumer.
- `test_tools_before_first_anchor_stay_pending` (2): before-first and no-anchor transcripts.
- `test_unorderable_rows_keep_turns_pending` (6): invalid/missing anchor, assistant and tool stamps.
- `test_turn_order_uses_timestamp_then_id` (1).
- `test_deleted_anchor_leftover_attaches_to_uncovered_turn` (1): two retention runs.
- `test_inactive_transcript_is_eligible` (4): quiet, gone, moved, replaced.
- `test_retired_transcript_ingest_after_file_change` (3): gone, same-path rotation, new-path move.
- `test_recent_replaced_transcript_stays_active` (1): a file that was replaced and touched within the window, but is not the newest, stays active. This test was added in review after a mutation survived.

**Gate revision mutations (Claude review, 2026-10-23).** Each mutation was applied alone and had to fail `test_staging_retention.py`. All 12 were killed:

- turn coverage off;
- a turn anchor not required to be a `user_request`;
- a pending assistant ignored;
- a pending anchor ignored;
- the unorderable-transcript rule;
- order by id only;
- the anchor not held with its turn;
- a growing same-identity file;
- a recent mtime (killed only after the new test above);
- a gone file forced active;
- the newest transcript;
- a recent or newest cursor.

Updated old tests: `test_keeps_pending_all_legacy_kinds` became `test_keeps_pending_conversation_rows`; `test_keeps_new_or_invalid_timestamp` now expects invalid ordering to stay pending; `test_keeps_active_or_unverifiable_session` moved gone/identity cases to eligibility tests and added newest-mtime/recent-growing cases.

**Revision mutation checks.** All five mutations failed the targeted tests: bypass pending, drop anchor whole-turn coverage, ignore second consumer, sort by ID alone, bypass active-session gate. Checks ran in isolated Python processes without modifying working-tree code.

**Original mutation testing (before this revision).** Each mutation was applied alone, and `test_staging_retention.py` had to fail. All were killed:
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

**Revision suites (sandbox, 2026-10-23).**
- Requested retention command: **70 passed** (final file).
- Full `memory-sync insight`: **2063 passed, 65 skipped, 33 failed** in 307.70s.
- All 33 failures are `PermissionError: [Errno 1] Operation not permitted` at socket bind: `memory-sync/test_digest_api.py` (10), `insight/test_insight.py` (2), `insight/test_profile_log.py` (1), `insight/test_syncthing_transport.py` (20). Claude reruns these outside the sandbox.
- `git diff --check` passed. No commit, DB copy, VACUUM or live-store access.

**Original suites** (local, macOS):

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
