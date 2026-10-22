# Encrypted Profile Log, Phase 7 (Retire Packet Sync; Profile Log Is the Memory) Implementation Plan

> **For agentic workers:** execute in order. The spec is
> `docs/superpowers/specs/2026-10-18-encrypted-profile-log-design.md` ("Reuse and retirement", "Phases" item 7,
> "Memory broker"). Read it fully first, then `profile-log/README.md`, `profile-log/FORMAT.md` (Phase 5 additions),
> the Phase 5 plan and REPORT (dual-write, catch-up, authority, recall fallback) and the Phase 6 REPORT. The spec
> wins on conflict, except where this plan records an owner decision. Stop and report instead of guessing on
> anything that could lose memory rows or weaken a security gate; record other choices under Deviations in the
> REPORT.

**Goal:** the encrypted profile log is the only shared memory. Every device syncs through it (R2) and nothing
else. The legacy packet exchange between Mac and Windows (Syncthing folder, `RANGES`, packet conflict machinery)
is switched off and then deleted.

**Today (Phase 5/6, live on the Mac):**
- ingest → digest/summarize → `mac.db` (`memory_items`, `memory_summaries`) → post-commit hook + 300 s catch-up →
  broker → profile log → R2.
- Recall goes to the broker for the `work` profile, with an automatic SQLite fallback.
- Packet sync (`org.agentmesh.sync`) still publishes to `~/.agentmesh/exchange/changes/mac`. Windows has not
  exchanged packets since the 2026-10-20 relocation (its status file is from then), and it is not on the profile
  log yet.

**After Phase 7:**
- Ingest and digest still use a local SQLite database, now called the **staging database**. It is local-only
  working state that can be rebuilt from transcripts. It is never synced and is no longer an authority.
- The hook and catch-up stay as the write path from staging into the broker.
- Recall is broker-only.
- Packet sync is gone.
- Each device is the authority for the rows it ingested itself.

**Out of scope:**
- running the Windows rollout (parked by the owner; this phase ships the path and runbook only);
- removing the staging database or rewriting digest to call the broker directly;
- the R2 archive tier (dropped by the owner as too costly);
- connecting more AI apps;
- letting the `recall` agent read `personal` (still an open owner decision; unchanged here).

## Owner decisions (2026-10-22)

- **Cut the legacy sync out.** Owner: "อยากให้เริ่ม ตัดออกไปเลย" ("I want to start: cut it out").
- **Windows starts fresh.** Owner: "ฝั่ง window เริ่ม sync ใหม่ได้หรือเปล่า แบบที่ไม่ต้องสนของเดิม" ("can Windows start syncing fresh, without caring about the old data?").
  - Windows joins the profile log as a new device and reads everything from R2.
  - Its old SQLite database and packet history are not migrated.
  - Its ingest starts from the join time, with no backfill of old transcripts. Backfill would re-digest sessions
    whose memories already reached the Mac before 2026-10-20, and would cost summarize calls.
- **No new backups.** Nothing in this phase copies `mac.db` or any identity. If a step needs a snapshot, it must
  ask the owner first and encrypt it (standing backup policy). The old exchange folder and the old Windows
  database stay where they are, and the owner prunes them later.

**Proposed defaults.** The agent applies these unless the owner objects in the PR:
- **Two steps: 7a, then 7b.**
  - 7a switches behaviour off behind config and can be rolled back with one config change.
  - 7b deletes the dead code after 7a has run on the Mac for 7 days with gates P7-1 to P7-5 green.
- **No silent fallback.** If the broker is unavailable, recall returns an explicit `profile_log_unavailable`
  error and never SQLite content. An emergency flag `bridge.recall_fallback: true` (default false) restores the
  Phase 5 fallback; 7b removes it.

## Threat and failure model

- **Loss of rows.** A row written to staging must reach the log. The hook can miss rows, and catch-up repairs
  them (unchanged). Parity (`profile_log_parity.py`) is the gate.
- **Duplicate roots.**
  - Phase 5 allowed a single authority, because packet sync copied every row into every database.
  - Without packet sync, each staging database holds only its own rows, so every device can be an authority.
  - Two fresh installs can reuse the same integer ids once `RANGES` is gone, so new source keys carry the device id.
- **Silent degradation.** Before Phase 7, a broker outage silently turned recall into SQLite recall. After it,
  an outage is visible.
- **Rollback (7a only).** Set `bridge.recall_fallback: true`, reload `org.agentmesh.sync`, and set
  `packet_sync.enabled: true`. This restores Phase 5 behaviour. Writes made in between are already in both stores,
  because the staging database is still written first.

## Changes

### 7a — cutover (behind config)

1. **Per-device authority and source keys** (`profile-log/agentmesh_log/migrate/`, `memory-sync/profile_log_bridge.py`).
   - The migrate source becomes `{device, table, id}` for new ledger entries, where `device` is this device's
     32-hex device id. Existing ledger entries keep `{table, id}` and are matched as before; never rewrite them.
   - Remove the "single authority" refusal. Replace it with this rule: a device migrates only rows from its own
     staging database. The guard compares the staging database's node identity with the device's binding,
     recorded in the bridge config at enrolment.
   - Update FORMAT.md: the reserved `source` key gains `device`, and old events without it remain valid.
2. **Recall is broker-only** (`memory-sync/recall_memory.py`, bridge).
   - When `bridge.recall` is on and the broker is unavailable, return a structured error: exit code non-zero, and
     JSON `{"error":"profile_log_unavailable"}`.
   - Use the SQLite path only when `bridge.recall_fallback` is true, and keep the `backend: "sqlite-fallback"`
     marking.
   - Agent rules: regenerate the installed rule and skill text with `install_agent_rules.py`, so it says recall
     reads the profile log, not "local node SQLite database". `recall_argv` may stay the same entry point.
3. **Packet sync off** (`memory-sync/memory_sync.py`, `platform_service.py`, settings).
   - New setting `packet_sync.enabled` (default **false** for new installs and after this upgrade).
   - When false, the sync unit exits 0 without capturing, publishing or receiving, and logs one line.
   - `generate` stops emitting the sync unit. The upgrade path unloads `org.agentmesh.sync` on macOS and the
     matching Windows task.
   - Do not delete `~/.agentmesh/exchange`.
4. **Fresh-join path for Windows** (no execution; code and runbook only).
   - Add `ingest --start-at now|<iso>`, which seeds empty ingestion cursors at that time, so a new device does
     not backfill.
   - Add a runbook to `profile-log/README.md`: install the profile log, then pair (Phase 4 commit-reveal), then
     the first sync from R2, then the bridge config (`authority: true`, `dual_write: true`, `recall: true`), then
     ingest with `--start-at now`, then the Phase 6 bridge and `cloudflared` (same tunnel token).
   - State that the old Windows database is left untouched and unused.
5. **Insight** (`insight/adapters.py`, peer status).
   - Panes that read packet-sync peer status switch to profile-log devices from `profile-log-status.json`, or
     show "packet sync retired".
   - Panes that read the staging database keep working.
6. **Status and notes:** add `packet_sync: "retired"` to `agentmesh-status-v1`, and update README and REPORT.

### 7b — deletion (after 7 days of green 7a on the Mac)

- **Delete:**
  - the packet capture, publish and receive code paths;
  - `RANGES` and per-node integer id allocation (new ids use the database's own `INTEGER PRIMARY KEY`);
  - `packet_versions.py`, `protocol_activation.py`;
  - `resolution_*.py` and `conflict_commands.py`;
  - bootstrap ZIP distribution;
  - the packet-group parts of `install_join.py` / `install_setup.py`;
  - `bridge.recall_fallback`.
- **Keep:** anything the spec lists under "Reuse", including the Ed25519 signing helpers, `key_rotation.py`,
  `identity_backup.py`, `os_unlock`, ingest and Insight. Keep a module if a remaining module still imports it.
  Prove this with an import graph in the REPORT.
- **Tests:** remove the tests that only cover deleted code, and keep or port the rest.

## Gates

- **P7-1 Recall:**
  - With the broker up, recall returns broker results and never SQLite rows.
  - With the broker stopped, it returns `profile_log_unavailable`, exits non-zero, and prints no SQLite content.
  - With `recall_fallback: true`, it returns the Phase 5 behaviour.
- **P7-2 No packets:** after the upgrade, a full sync-unit cycle writes nothing under `exchange/changes/<node>`,
  and `launchctl list` has no `org.agentmesh.sync`.
- **P7-3 Per-device sources:**
  - Two staging databases with the same `table:id`, migrated by two different devices, produce two distinct
    roots.
  - A device refuses to migrate a staging database bound to another device.
  - Legacy ledger entries still match without being rewritten.
- **P7-4 Parity:** after catch-up, `profile_log_parity` reports zero missing and zero changed rows for the Mac
  staging database.
- **P7-5 Fresh join:** `ingest --start-at now` on an empty database ingests no transcript older than the start
  time, and makes zero summarize calls for old sessions.
- **P7-6 Rollback (7a):** the three-step rollback restores the Phase 5 recall and packet behaviour in a test
  harness.
- **P7-7 (7b):**
  - No deleted module is imported anywhere.
  - Python suites pass on ubuntu, macOS and Windows.
  - Insight tests pass.

## Owner steps (Mac, after the 7a merge)

1. Swap the Mac app to the merge commit (same swap script as Phase 6).
2. Run the upgrade command printed in the REPORT. It sets `packet_sync.enabled: false` and unloads
   `org.agentmesh.sync`.
3. Run `install_agent_rules.py` to refresh the recall rules for Claude, Codex and Hermes.
4. Check:
   - recall in Claude returns results;
   - `exchange/changes/mac` stops growing;
   - the Insight profile-log tab shows sync OK.
5. After 7 days of green gates, approve 7b.
