# Encrypted Profile Log, Phase 7a: Report

**Scope.** This report covers plan `2026-10-22-encrypted-profile-log-phase7.md`, step **7a only**, items 1–6. It also covers the owner-approved extra item "local recall reads `personal` as well as `work`", added on 2026-10-22 ("9, 10 ทำเลย", "do 9 and 10 now").
- Nothing in 7b is done: no packet, RANGES or conflict code is deleted.
- Nothing here has touched the live `~/.agentmesh`, launchd or R2.
- No copy of `mac.db`, an identity or a key is made, by code or by the steps below.

**Workflow.** Codex (gpt-6-astra) implemented and Claude reviewed, in three review rounds. The gates were mutation-tested; see "Mutation testing".

## What changed

### 1. Per-device sources and authority
**Source keys.**
- New ledger sources are `{device, table, id}`, with `device` = the broker's 32-hex device id.
- Legacy `{table,id}` entries are matched as before and never rewritten.
- The broker ledger is keyed `(device|None, table, id)`. Ledger entries now carry `device`, the author of the head.

**Broker gates** (`broker/imports.py`):
- (a) A device-form import must name this broker's device.
- (b) When a foreign replica event enters the import ledger, its `source.device` must equal the envelope's device. Otherwise the event is ignored.
- (c) A legacy-form import may only revise or retire a legacy head **this device authored**. Creating a new legacy root is denied. A legacy scope move therefore retires the legacy chain and creates the new root in device form.

**Migrator** (`migrate/run.py`):
- It considers only this device's entries: device-keyed for this device, plus legacy entries this device authored.
- For each row it looks up the device entry first, then a locally authored legacy entry, and otherwise creates a device root.
- The "missing from staging → retire" sweep only ever touches local entries. A freshly joined device can therefore never retire or revise another device's rows.
- The single-authority refusal is gone. `--take-over` is accepted as a no-op.

**Binding guard:**
- The bridge config gains `device_id` and `staging_node`. `apply` records both: the device from broker state, and the node from `_sync_config.node`, or `agentmesh_staging_identity` on a fresh DB.
- `migrate run|rows|catch-up` refuses before any import when the binding is missing, or when the binding device ≠ the broker device, or when the staging node ≠ the binding node. The node is re-checked inside the read transaction and before each import.
- `apply` refuses to silently rebind an existing binding.

**Other parts:**
- **Index schema:** v6 adds `source_device`. Old snapshots rebuild.
- **Compat recall results** add `author_device`.
- **FORMAT.md** documents the optional `device` and the gates.

### 2. Broker-only recall
**Enabled bridge recall with the broker unavailable:**
- `recall_memory`, `agentmesh.py recall` and `shared_memory_context` return `{"error":"profile_log_unavailable"}` with a non-zero exit.
- They print no SQLite content.

**Invalid bridge config:** returns the same error. Before, it fell back to SQLite silently.

**SQLite fallback:**
- Only `bridge.recall_fallback: true` (default false) restores the Phase 5 fallback, marked `backend: "sqlite-fallback"`.
- An explicit `--backend sqlite` without the flag returns `{"error":"sqlite_recall_disabled"}`.

**Local evidence:** expanded from staging only for rows this device owns. A foreign device's integer id is never looked up locally.

**Agent rules:** the rule and skill text from `install_agent_rules.py` now say recall reads the encrypted profile log through the local broker. `recall_argv` is unchanged.

### 3. Packet sync off
**The setting.** `packet_sync.enabled` lives in `settings.json` / `settings.local.json` and defaults to **false**.

**When disabled, the sync worker skips only packet work:**
- the exchange marker check;
- the signing policy;
- `memory_sync.cycle`;
- summary sync.

**Workflow ingest/summary still run when the runtime's workflow config enables them** (Windows/generic installs). Without workflow work:
- `--once` exits 0;
- the looping worker logs one line and idles, honouring stop/nonce. It does not exit, because the live plist has `KeepAlive: true` and an exit-0 loop would respawn every 10 s.

**Unit generation and retirement:**
- `platform_service generate` and Windows `change_task` emit or keep the worker only when packet sync or workflow needs it.
- `agentmesh.py packet-sync retire|enable [--dry-run]` writes `settings.local.json` and the content-free `packet-sync-status.json`. On macOS it runs `launchctl bootout gui/<uid>/org.agentmesh.sync`, but only when workflow ingest and summary are off; status 3 (not loaded) is fine. It disables the Windows task through the ownership-checked autostart path, or `schtasks` for a legacy task.
- An unreadable runtime or workflow config means retire refuses to unload.
- `~/.agentmesh/exchange` and the unit files are never deleted.

### 4. Fresh-join path (code and runbook only)
- `ingest --start-at now|<offset ISO>` seeds a start floor in an empty staging DB.
  - It refuses if cursors or a start floor already exist, so it never moves cursors backwards.
  - Events older than the floor are never inserted, so there are no summarize calls for old sessions.
  - Ingest creates the `agentmesh_staging_identity` node on DBs without `_sync_config`.
- The runbook is in `profile-log/README.md` under "Fresh Windows join". The old Windows DB is left untouched and unused, and `packet_sync` stays false. Not executed.

### 5. Insight
- The packet peer, transport, outbox and receipts panes show "packet sync retired" unless `packet-sync-status.json` says enabled. They point to the profile-log tab, which already lists profile-log devices and sync status.
- Panes that read the staging DB are unchanged.
- No other Insight code is touched. This matters for the parallel Insight remote-access work.

### 6. Status and docs
- `agentmesh-status-v1` gains `packet_sync: "retired" | "enabled"`.
- README.md, `profile-log/README.md`, FORMAT.md and `memory-sync/WORKFLOW.md` are updated.

### Extra: recall reads `work` and `personal`
- The bridge config takes `profiles: [...]`. The old single `profile` is still accepted as a one-item list.
- `broker_recall` runs one **single-profile** recall per profile, concurrently, within one total `timeout_seconds`.
- It interleaves the results by rank in the configured order, dedupes by id, then cuts to the pool. `candidate_counts` are summed.
- Any failed profile fails the whole recall: no partial results.
- No cross-profile option or presence is ever requested. `allow_cross_profile` stays false, and the cross-profile gate in `broker/service.py` is untouched.
- Rows whose `profile` differs from the profile queried are rejected.

## Gates and evidence

| Gate | Tests |
|---|---|
| P7-1 recall | `memory-sync/test_phase7_recall.py`: `test_p7_1_broker_only`, `test_p7_1_down_cli_no_sqlite`, `test_p7_1_explicit_fallback`, `test_p7_1_context_error`, plus the multi-profile tests (order, single-profile argv, no partial results, legacy `profile`, unlisted personal never queried, total timeout) |
| P7-2 no packets | `test_phase7_packets.py`: `test_p7_2_full_unit_cycle_is_retired`, `test_p7_2_generate_emits_no_sync_unit`, `test_p7_2_retire_fake_bootout_preserves_exchange`; `test_phase7_worker.py`, `test_phase7_retire.py` (workflow retained, idle loop honours stop, unreadable config refuses unload) |
| P7-3 per-device | `profile-log/tests/migrate/test_phase7.py`: `test_p7_3_devices`, `test_p7_3_same_ids`, `test_p7_3_binding`, `test_p7_3_source_gate`, `test_p7_3_legacy_move` (move, then two catch-ups with zero new events), `test_p7_3_legacy_peer[revise|retire]`, `test_p7_3_rebind_during_scan`, `test_p7_3_cli_binding` |
| P7-4 parity | `test_profile_log_parity.py::test_p7_4_local_parity` (foreign rows present, zero missing or changed); live read-only `migrate verify` (`tests/migrate/test_verify.py`) |
| P7-5 fresh join | `test_phase7_ingest.py::test_p7_5_now_no_old_events_or_summarize` and refusal cases |
| P7-6 rollback | `test_phase7_integration.py::test_p7_6_recall_and_packets`, `test_phase7_packets.py::test_p7_6_packet_rollback_restores_cycle_and_generation` |

**Local suites** (macOS, Python 3.14, `~/.cache/agentmesh-testvenv`), base `4d0df68` → this branch:

| Suite | Base | Branch |
|---|---|---|
| `profile-log/tests` (repo root, and from `profile-log/`) | 735 passed, 1 failed | 772 passed, 1 failed (same test) |
| `memory-sync` | 1748 passed, 1 failed | 1837 passed, 1 failed (same test) |
| `insight` | 52 passed | 55 passed |

The two failures are pre-existing and fail on the base commit too:
- `test_p6_s12_close_cross_verifier` needs the gateway's npm dependencies, which CI installs;
- `memory-sync/test_digest_api.py::test_client_envelope_satisfies_bounded_extract_command`.

**CI.** It runs only `profile-log/tests` on ubuntu, macOS and Windows, plus the Windows standalone smoke subset. The memory-sync and Insight suites above are local evidence only.

### Mutation testing
Each mutation was applied alone; the targeted tests had to fail.

**Killed:**
- the broker device gate on import;
- the legacy-root gate;
- the foreign-legacy-head gate (it survived round 1 and was killed after adding `test_p7_3_legacy_peer`);
- the `admitted` device check;
- the migrator local-entry filter;
- legacy authorship in `local_entry`;
- the binding check;
- the binding device check;
- the device-first lookup;
- both staging-node checks together;
- the no-silent-fallback rule;
- `--backend sqlite` refusal;
- invalid config → error;
- the CLI exit code;
- the bridge profile check;
- the partial-results rule;
- the local-evidence ownership rule;
- the start-at filter;
- the worker no-op;
- the worker skip of `memory_sync.cycle`;
- packet sync defaulting to off.

**Survived, by design:**
- Each staging-node check alone. The other check catches it.
- Rewriting the continuation source on a legacy move. The re-entered `prepare` rewrites it again, so this is redundant defence.

## Deviations

1. **The idle worker does not exit.** The plan says "the sync unit exits 0". The live plist has `KeepAlive: true`, so a looping worker idles instead; `--once` exits 0. After `packet-sync retire` the unit is unloaded anyway.
2. **The worker is retained when workflow ingest/summary need it.** The plan has `generate` stop emitting the unit outright. On installs where the worker also runs ingest/summary (the Windows default), dropping it would silently stop memory creation. The Mac's `workflow.json` has both off, and its ingest and summary run as separate jobs, so the Mac's unit is unloaded.
3. **Broker recall rows use the log ULID as `id`.** Before, they used the source integer. Integer ids collide across devices. `source_ref` carries `{device?, table, id}`. In-repo consumers were checked (`shared_memory_context`, Insight, `pg_mirror`, bridge/MCP, parity, `agentmesh recall`): staging-only consumers keep integer ids, and broker consumers treat ids as opaque.
4. **Broker recall returns no raw `observation_events` fallback rows.** P7-1 says "never SQLite rows". Local evidence for locally owned rows is still attached.
5. **`--backend sqlite` with an enabled bridge needs `recall_fallback: true`.** Otherwise it returns `sqlite_recall_disabled`.
6. **P7-4.** `profile_log_parity` no longer snapshots `mac.db`, which follows the no-copy rule; it reads the source read-only. Live parity uses two pieces of evidence:
   - `migrate verify`, which gives counts only: `missing`, `retired_but_present`;
   - a second catch-up with `created = revised = 0`.
   The ledger carries no hashes, so content equality comes from the second catch-up.
7. **Insight reads a content-free `packet-sync-status.json`**, never the private settings files.
8. **`packet_sync.enabled` lives in the profile-log settings files** (same root, `~/.agentmesh`). The profile-log settings schema accepts it.

## Known limits
- **Between the app swap and `apply` (owner steps 1–3), the dual-write hook and catch-up refuse with `staging_binding_missing`.** This is fail-closed. New rows wait in `mac.db` and catch-up imports them once `apply` records the binding. Recall keeps working throughout, because the old config shape is accepted.
- **The first catch-up after the upgrade re-sends every row once.** The cache format went to v2. The broker answers `unchanged` and writes no new events for rows whose hash and scope match.
- **Expect one slower first recall.** The broker's index snapshot rebuilds once (schema v6).

## Owner steps: Mac upgrade (after merge)

Notation:
- `REV` is the merge commit on `main`.
- `NOTES` is the deploy-scripts folder: `~/Library/Application Support/Claude/side-session-notes/local_4093101b-4dea-405e-8320-bb9a84bd6406/deploy-scripts`.
- Run everything from your own terminal.
- Nothing here copies `mac.db`, keys or identities.

**1. Swap the profile-log app** (same script as Phase 6):
```sh
cd ~/Workspaces/AgentMesh && git fetch && git checkout main && git pull --ff-only
REV=$(git rev-parse HEAD) zsh "$HOME/Library/Application Support/Claude/side-session-notes/local_4093101b-4dea-405e-8320-bb9a84bd6406/deploy-scripts/swap_profile_log_app.sh"
```

**2. Update the memory-sync code in `~/.agentmesh/memory-sync`.** This overwrites code files only; `data/` and `identity/` are not touched. The live files currently equal `4d0df68`, and the script refuses if any changed file has drifted. Rollback is the same script with `FROM`/`REV` swapped; it also removes `packet_sync.py`, which the new revision added.
```sh
zsh "$HOME/Library/Application Support/Claude/side-session-notes/local_4093101b-4dea-405e-8320-bb9a84bd6406/deploy-scripts/overlay_memory_sync_code.sh"
```
The script uses `FROM=4d0df68` and `REV=$(git rev-parse main)` by default. It prints each file it replaces and runs an import check with `~/.agentmesh/jobs/.venv/bin/python`. The script is reproduced in the appendix; it was rehearsed forward, in reverse and with a drifted file on a scratch copy of the `4d0df68` tree.

**3. Settings.** Edit `~/.agentmesh/settings.json`:
- in `bridge`: remove `"profile": "work"`, then add `"profiles": ["work", "personal"]` and `"recall_fallback": false`;
- set `agents.recall.read` to `["work", "personal"]`, keeping `allow_cross_profile: false`;
- add the top-level `"packet_sync": {"enabled": false}`.

Then:
```sh
~/.agentmesh/bin/agentmesh-log settings validate
~/.agentmesh/bin/agentmesh-log apply --dry-run   # expect: agent_widen recall; bridge_update (profiles, recall_fallback, device_id, staging_node)
~/.agentmesh/bin/agentmesh-log apply             # agent console presence: type `apply`
```

**4. Retire packet sync.** This unloads `org.agentmesh.sync`, because the Mac's workflow has ingest and summarize off. It does not delete `exchange/`.
```sh
cd / && ~/.agentmesh/jobs/.venv/bin/python ~/.agentmesh/memory-sync/agentmesh.py packet-sync retire --dry-run
cd / && ~/.agentmesh/jobs/.venv/bin/python ~/.agentmesh/memory-sync/agentmesh.py packet-sync retire
```

**5. Refresh the recall rules** for Claude, Codex and Hermes:
```sh
cd / && ~/.agentmesh/jobs/.venv/bin/python ~/.agentmesh/memory-sync/install_agent_rules.py \
  --app-dir ~/.agentmesh/memory-sync --database ~/.agentmesh/memory-sync/data/mac.db \
  --python ~/.agentmesh/jobs/.venv/bin/python
```

**6. Checks:**
```sh
launchctl list | grep org.agentmesh                       # no org.agentmesh.sync; broker, catch-up, bridge, ingest, summary present
cd / && ~/.agentmesh/jobs/.venv/bin/python ~/.agentmesh/memory-sync/agentmesh.py \
  --database ~/.agentmesh/memory-sync/data/mac.db recall "agentmesh" --limit 5   # backend "broker"; rows from work and personal
A=(~/.agentmesh/bin/agentmesh-log migrate catch-up --db ~/.agentmesh/memory-sync/data/mac.db \
   --mapping ~/.agentmesh/migration-mapping.json --agent-key ~/.agentmesh/agents/importer.key)
"${A[@]}"; "${A[@]}"                                      # second run: outcomes have no created/revised, errors []
~/.agentmesh/bin/agentmesh-log migrate verify --db ~/.agentmesh/memory-sync/data/mac.db \
  --mapping ~/.agentmesh/migration-mapping.json --agent-key ~/.agentmesh/agents/importer.key   # {"missing":0,"retired_but_present":0}
ls ~/.agentmesh/exchange/changes/mac | wc -l               # repeat after 10 min: unchanged
```
- In Claude, ask for something recalled from memory and check that results come back.
- In Insight: the profile-log tab shows sync OK, and the packet panes say "packet sync retired".

**7. 7b clock.** After 7 days with these checks green (P7-1 to P7-5), approve 7b.

## Rollback (7a)
Run these steps:
1. Set `bridge.recall_fallback: true` in `settings.json`, then `agentmesh-log apply`.
2. Run `agentmesh.py packet-sync enable`.
3. Reload the unit: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.agentmesh.sync.plist`.
   - The plist and the old 0.3.3 worker binary are untouched, so this restores Phase 5 packet behaviour.

For a full code rollback, also run:
- `REV=<previous profile-log REVISION> swap_profile_log_app.sh`;
- the overlay script with `FROM=$REV REV=4d0df68` (it removes `packet_sync.py`).

Writes made in between are already in both stores, because staging is still written first.

## Appendix: `overlay_memory_sync_code.sh`

```zsh
#!/bin/zsh
# Overlay top-level memory-sync code files changed between FROM and REV into ~/.agentmesh/memory-sync.
# Code only: data/, identity/ and every non-code file are never touched. Refuses if a live file drifted from FROM.
# Rollback: FROM=<new rev> REV=<old rev> with the same script (files added by the new rev are deleted).
set -eu
REPO="$HOME/Workspaces/AgentMesh"
MS=${MS:-"$HOME/.agentmesh/memory-sync"}
PY=${PY:-"$HOME/.agentmesh/jobs/.venv/bin/python"}
FROM=${FROM:-4d0df68}
REV=${REV:-$(git -C "$REPO" rev-parse main)}
STAGE="$MS/.overlay-$REV"

files=(${(f)"$(git -C "$REPO" diff --name-only "$FROM" "$REV" -- memory-sync | grep -E '^memory-sync/[^/]+\.(py|sql)$' | grep -v -E '/(test_[^/]*|conftest)\.py$' || true)"})
[ ${#files} -eq 0 ] && { echo "no code changes between $FROM and $REV"; exit 0; }

sha() { shasum -a 256 | cut -d' ' -f1; }
for f in $files; do
  live="$MS/${f:t}"
  if git -C "$REPO" cat-file -e "$FROM:$f" 2>/dev/null; then
    [ -f "$live" ] || { echo "refusing: $live missing (expected $FROM version)"; exit 1; }
    [ "$(git -C "$REPO" show "$FROM:$f" | sha)" = "$(sha < "$live")" ] || { echo "refusing: $live differs from $FROM"; exit 1; }
  else
    [ ! -e "$live" ] || { echo "refusing: $live exists but is not in $FROM"; exit 1; }
  fi
done

rm -rf "$STAGE"; mkdir -m 700 "$STAGE"
for f in $files; do
  if git -C "$REPO" cat-file -e "$REV:$f" 2>/dev/null; then
    git -C "$REPO" show "$REV:$f" > "$STAGE/${f:t}"; chmod 600 "$STAGE/${f:t}"
  fi
done
# Import check against staged code (staged files first, then the live app) before replacing anything.
(cd / && "$PY" -I -B -c "import sys; sys.path[:0]=['$STAGE','$MS']; import recall_memory, profile_log_bridge, sync_worker, ingest_sessions, sqlite_memory, shared_memory_context, install_agent_rules, agentmesh; print('import ok')")

for f in $files; do
  name=${f:t}
  if [ -f "$STAGE/$name" ]; then mv -f "$STAGE/$name" "$MS/$name"; echo "replaced $name"
  else rm -f "$MS/$name"; echo "removed $name"; fi
done
rm -rf "$STAGE"
rm -rf "$MS/__pycache__"
echo "memory-sync code now at $REV (from $FROM)"
```
