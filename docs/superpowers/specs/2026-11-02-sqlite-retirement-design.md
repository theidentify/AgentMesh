# Retire the SQLite staging database: design

Status: draft for owner review, 2026-11-02. Builds on the encrypted profile log
(`2026-10-18-encrypted-profile-log-design.md`), its migration and bridge (Phase 5),
the local control plane (`2026-10-23-local-control-plane-design.md`) and the memory
tree (`2026-11-02-memory-tree-design.md`).

## Problem

Agents no longer read SQLite: recall and the memory tree go through the broker, and
broker-mode agent rules are installed. But SQLite is still the **only way in** for new
memory:

```
agent transcripts ──ingest──▶ SQLite: observation_events (+ FTS, cursors, errors)
                                   │
                        summary ───┤ windows of events + existing memory_items by memory_key
                                   ▼
                     SQLite: memory_items, memory_summaries, summary_state
                                   │
       bounded_digest.notify → profile_log_bridge.notify_rows → `migrate rows` (importer agent)
       catch-up unit         → `migrate catch-up`                                    ▼
                                                                         encrypted profile log
```

The pipeline also keeps a second copy of every memory in a plaintext-at-rest SQLite
file (`~/.agentmesh/memory-sync/data/<node>.db`, 0600), plus WAL/SHM, exports and
locks. It needs its own migration mapping, row hashes, a ledger and a catch-up job,
and it has two sources of truth that can drift.

Other readers of SQLite today:

| Reader | Use | After retirement |
|---|---|---|
| `jobs/agentmesh_production.py` `recall`, `context` | legacy standalone recall and context | removed; broker recall and tree |
| `sqlite_export.publish` (after ingest and summary) | export root for packet sync | removed; packet sync retired in Phase 7a |
| `retention_audit` (`retention` unit, not loaded) | retention audit | removed |
| `recall_memory` bridge fallback | `recall_fallback` (already `false`) | removed |
| Insight (`adapters.py`, `metrics.py`, `storage.py`) | browse items and summaries, storage sizes | browse moves to the broker; storage view drops the DB rows |
| `profile_log_bridge.notify_rows`, `migrate rows/catch-up/verify` | import into the profile log | removed after cutover |

## Owner decisions (2026-11-02)

1. Stop using SQLite entirely: no staging database, no dual write and no SQLite
   recall, on every device.
2. Memories and summaries are written to the profile log **directly through the
   broker**.
3. Raw transcripts stay local to the device that ingests them (unchanged rule from
   the profile log design). They are read from the agents' own JSONL files and are
   not copied into a new store.
4. The old database is **archived, not deleted**, at cutover. It is deleted only
   later, by the owner, through a presence-checked step.

## Non-goals

- Changing what the summarizer extracts (kinds, memory keys, quarantine rules) or
  its prompts.
- Syncing raw transcripts between devices.
- Re-importing history. Everything already imported stays as is. Parity is verified
  once, at cutover.

## Design

### 1. Transcript reader without a database (`ingest` → `transcripts`)

- Reuse the existing per-agent parsers (OMP, Codex, Claude) and their safety rules:
  complete newline-terminated records only, the timestamp floor
  (`agentmesh_ingestion_start`) and files that fail are skipped without advancing.
  Today they live in `sqlite_memory.ingest_file`. Move the parsing into a pure module
  that yields events instead of inserting rows.
- **Cursors** (`source_path → file identity, byte offset, line, hash`) move from the
  `ingestion_cursors` table to `jobs/transcript-cursors.json`. It is written
  atomically at 0600 and covered by the jobs lock.
- **Pending window:** the summarizer reads new events straight from the files,
  between the summary cursor and the ingest cursor, at most `max_events` per window
  (the existing bounded window, default 24). No event is stored on disk. A window that
  fails keeps its cursor, so the next tick retries it.
- `ingestion_errors` becomes counts only, in the job status (`last-ingest`), as
  `raise_for_errors` already reports.
- FTS over raw events goes away. Nothing reads it once SQLite recall is gone.

### 2. Summary writes through the broker

- A dedicated **`summarizer`** agent replaces the `importer` for new data. It writes
  to the mapped scopes (the migration mapping's project → scope rules move to
  settings `ingest.routing`).
- **Upsert by memory key:** today `bounded_digest.apply` looks up `memory_items` by
  `memory_key` to revise, quarantine or delete. The broker gets a read op `heads`:
  `{scope, memory_keys: [..]}` returns the current head event per key in that scope.
  It is allowed only for agents with a new grant flag `allow_summarize` and only
  within their write scopes. The memory key lives in `body.metadata.memory_key`, and
  the agent index adds a `memory_key` column.
- **Writes:** a new key uses `remember`; a changed key uses `revise` with `revises` set
  to the current head. A deletion is a revision with status `deleted`, which the
  index already hides like `retired`. Project summaries
  (`memory_summaries`, scope project) become ordinary events of kind `summary` with
  `memory_key = summary:<project>`.
- **Provenance:** `body.metadata` carries `{memory_key, status, batch_event_ids,
  source: {device, agent: omp|codex|claude, session}}`. No row hash or SQLite id.
  The broker keeps verifying the author's agent certificate as usual.
- **Related context for the prompt:** the summarizer reads existing memories of the
  same project through `heads` and broker `recall` (scoped), in place of the SQLite
  query. Size limits stay the same (`related_chars`, `context_chars`).
- **State:** `summary_state` and the digest window state move to
  `jobs/summary-state.json`, written atomically at 0600 under the jobs lock.
- The memory tree writer (`memory build`) runs after a summary tick that wrote
  something, with the existing debounce and budget, so pages follow new memories.

### 3. Insight and other readers

- **Insight browse** (items and summaries) reads through the broker with a read-only
  `insight` agent. Insight is reachable through a tunnel, so it shows **counts and
  metadata only**, never content. The content view is the control app's Memory
  window (T3b).
- Insight storage drops the database rows and adds `jobs/*.json` state.
- Remove the `context`, `recall`, `export` and `retention` actions from
  `agentmesh_production.py`, `sqlite_export.publish` and the `retention` unit.
- Remove `recall_fallback`, `dual_write`, `database`, `authority` and the import
  agent from the bridge settings. Apply refuses them once the cutover flag is set.

### 4. Cutover, per device

One owner-run ceremony per device, `agentmesh control sqlite-retire`. It is tier 3
(Touch ID plus typed confirmation), since it changes where memory comes from.

1. Maintenance on: ingest, summary and catch-up pause, and the broker keeps serving.
2. Final `migrate catch-up`, then `migrate verify`: every live `memory_items` and
   `memory_summaries` row must have its event in the profile log. Abort on any gap.
3. Seed the new state from SQLite. Transcript cursors come from
   `ingestion_cursors`, and summary state from `summary_state` and the digest windows,
   so nothing is summarized twice or skipped. A dry run prints counts first.
4. Switch the production config (`primary_backend: broker`) and the bridge settings.
   Grant `summarizer`. Revoke `importer` (its historical import-agent membership
   stays, so old provenance still verifies).
5. **Archive** the DB, WAL, SHM, exports and locks to
   `backups/archive/sqlite-<node>-<date>/`. Files are 0400 in a 0700 folder, with a
   README. Not synced, not backed up (same rule as other archives).
6. Maintenance off, then one forced ingest and summary tick, checked by its status
   file.
7. Rollback for 30 days: restore the archive and the previous config (printed by the
   ceremony). After 30 days the control app shows a reminder, and the owner deletes
   the archive with `sqlite-archive delete` (tier 3).

The Mac Air goes first. Windows follows after native verification of the same steps,
because Windows has its own staging DB and bridge.

## Security

- **Removes** a second plaintext-at-rest copy of all memory and the FTS index of raw
  events. After cutover, memory lives only in the encrypted log; transcripts stay
  only in each agent's own files.
- `allow_summarize` is narrow: `heads` only reads its own write scopes, and the
  flag grants no secret reads (`allow_secret` stays false). Secret detection in
  `sanitize` still runs on every write.
- The summarizer sends transcript windows to Claude as today. Unchanged trust.
- The archive is plaintext SQLite. It is owner-only, never synced, and deleted by an
  explicit presence-checked step.

## Phases

| Phase | Work | Reversible |
|---|---|---|
| **R0** | Inventory and parity: `migrate verify` on the live DB, every SQLite reader listed with tests, the transcript parsers extracted into a pure module with no behavior change. | yes |
| **R1** | Broker `heads` op, `allow_summarize` flag (plumbed like `allow_pages`), `memory_key` in the index. | yes |
| **R2** | Transcript reader with JSON cursors, summary writing through the broker with JSON state, both behind `ingest.backend: broker` (default stays `sqlite`). Parity test: both backends produce the same items from a fixture transcript set. | yes |
| **R3** | Insight on the broker (counts only); remove legacy actions, export and retention. | yes |
| **R4** | `control sqlite-retire` ceremony and the archive; Mac Air cutover. | 30-day rollback |
| **R5** | Windows cutover after native verification; then delete the SQLite code paths and their tests. | after R4 + 30 days |

## Testing

- Parsers: today's ingest fixtures produce the same events with no DB.
- Cursors: crash between window and write leaves the cursor; partial lines never
  advance; file rotation (identity change) restarts safely.
- Summary parity: fixture transcripts through `sqlite` and `broker` backends give
  the same memory keys, kinds, statuses and contents (ids aside).
- `heads`: scoped to write scopes, refuses without `allow_summarize`, ignores
  secret heads, returns the current head after concurrent revisions.
- Cutover: refuses on any verify gap; seeding gives no duplicate and no skipped
  window; rollback restores a working SQLite pipeline; archive modes and README.
- Insight: no content in any broker-backed view.

## Open items for planning

- Whether `heads` should also serve the memory tree writer (it lists by node today).
- Where the routing rules live: settings `ingest.routing`, or the mapping file kept as is.
- Windows: same ceremony through `control` (Windows supervisor is v1.1) or a CLI-only
  step with console presence.
- Whether to keep a tiny raw-event spool for windows larger than one tick, if
  transcript files rotate faster than the summarizer runs.
