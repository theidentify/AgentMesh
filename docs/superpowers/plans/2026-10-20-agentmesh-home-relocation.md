# Move the AgentMesh runtime from `~/.hermes` into `~/.agentmesh` (Mac first): plan

Status: **decisions recorded 2026-10-20; implementation not started.** Implements step 5 of the phasing in
`docs/superpowers/specs/2026-10-20-profile-log-settings-and-admin-ui-design.md`. The owner wants every
AgentMesh memory file under one root. **This touches the live sync path:** the worker, its launchd service,
the runtime and identity, the digest/ingest jobs, and the Syncthing exchange. Nothing here runs until the
owner answers the decisions below and approves the plan. Windows follows later on the same layout; it is
skipped for now because the PC is off.

## What lives where today (Mac, read-only inventory 2026-10-20)

| Item | Path today | Size | Who refers to it |
| --- | --- | --- | --- |
| App root (Python runtime used by jobs and recall) | `~/.hermes/omp-memory/sqlite-sync/` | 677 MB incl. data | `runtime.json app_root`, `agentmesh-production.json runtime`, rules |
| Authority DB (+ `-wal`, `-shm`, locks) | `sqlite-sync/data/mac.db` | 356 MB | `runtime.json`, `security-wizard.json`, production config, bridge `mac.db.profile-log.json`, Insight plist, settings.local |
| Runtime + state | `sqlite-sync/data/{runtime,workflow,security-wizard}.json`, `security-backups/`, `.agentmesh-worker/`, `worker.lock`, logs | small | sync plist (`--runtime`) |
| Signing identity | `sqlite-sync/identity/{identity,trust}.json` | small | `runtime.json security_dir`; the keychain item is keyed by `key_id`, **not by path** |
| Syncthing exchange | `~/.hermes/omp-memory/sync/` (has `.stfolder`) | 442 MB | runtime, `security-wizard.json`, Syncthing `config.xml`, Windows installers folder |
| Worker binaries | `~/.hermes/omp-memory/standalone-versions/<sha>/agentmesh` | 63 MB | sync plist, `mac-replacement-trial/*.json` |
| Replacement backups | `standalone-replacement-backups/` | 1.3 GB | rollback (`mac-rollback --backup`) |
| Other history | `cutover-backups/` (324 MB), `releases/` (90 MB), `runtime-backups/`, metrics and analysis files | ~0.5 GB | nothing live |
| Job entrypoints (Hermes-owned) | `~/.hermes/scripts/agentmesh_production.py`, `omp_memory_*` cron scripts, `~/.hermes/omp-memory/.venv`, `agentmesh-production.json`, `production-status/` | small | summary/ingest/insight plists, Discord job notifications |
| Agent rules | `~/.claude/rules/agentmesh-shared-memory{.md,.command.json}`, `~/.codex/rules/shared-memory.rules` | — | every agent's recall command |

Constraints from the code (`install_adopt.validate`):

- `runtime.json` must be inside `app_root`.
- The exchange must be outside `app_root` and outside all local state.
- The exchange needs `.stfolder`.
- Every path must be user-owned and not group/world-writable.
- No symlinks anywhere.
- The DB scope (`_sync_config` node/group, `_sync_security` binding) does not include paths, so a byte-identical copy of the DB stays valid.
- `mac-replace` refuses a runtime path that differs from the plist (`existing runtime path mismatch`), so it cannot move anything.

## Target layout

```
~/.agentmesh/                         0700 (already holds settings, profile-log, agents, venv, app/profile-log)
  memory-sync/                        app_root: the Python runtime files (from the release tag)
    data/  mac.db (+wal/shm), runtime.json, workflow.json, security-wizard.json, security-backups/, worker state, logs
    identity/  identity.json, trust.json
  exchange/                           Syncthing folder (moved, D2); never contains anything else
  versions/<sha>/agentmesh            worker binaries
  backups/archive/                    everything historical, including the old sqlite-sync tree (D4)
  jobs/                               production selector + ingest/summary/insight entrypoints and their venv (D3)
  status/                             job status files (production-status today)
```

`profile-log/`, `settings*.json`, `agents/` and `services/` stay where they are. The bridge's `database` in
`settings.local.json` changes, and `apply` re-renders `memory-sync/data/mac.db.profile-log.json`. That counts
as widening, so it asks for presence.

## Mechanism: a tested `mac-relocate` command (recommended, D1)

This adds `agentmesh mac-relocate --manifest M [--dry-run]` to memory-sync, next to `mac-replace`, and reuses
its parts: drain, backup, plist rewrite, bootstrap, health wait and rollback.

1. **Plan (read-only):** validate the current runtime. Check that the target is absent or empty, user-owned and
   has no symlinks, and that there is enough free space (2× the DB plus the exchange if it moves). Print every
   file and every reference that will change.
2. **Confirm:** the operator types `RELOCATE`, the same pattern as `REPLACE`.
3. **Drain:** stop the worker the way `mac-replace` does. Also take the bounded-digest and scheduler locks, so
   the summary and ingest jobs can't write during the copy.
4. **Copy:**
   - `mac.db` via the SQLite backup API from a `mode=ro` source, then `PRAGMA integrity_check` and per-table
     row counts compared with the source;
   - identity and state files byte-for-byte, keeping their modes, with SHA-256 compared afterwards;
   - the Python runtime files from the release tag, with `profile_log_bridge.py` included.
5. **Rebind:** write the new `runtime.json` through `adopt()` (same node, group, key_id and policy), point the
   sync plist at the new runtime and binary, and update the job, Insight and production configs (D3). Update the
   rules files and `settings.local.json` (D5). Each file is first written next to the original as `.new`, then
   swapped in with `os.replace`.
6. **Start and verify:**
   - bootstrap the sync service and wait for a `completed_cycle` health record;
   - run one ingest and one summary tick;
   - do a recall through the agent rule command (expect `backend: "broker"`);
   - run `migrate catch-up` (expect `unchanged`);
   - check that the profile-log bridge hook fires.
7. **Keep the old tree:** rename `~/.hermes/omp-memory/sqlite-sync` to `sqlite-sync.pre-relocate-<ts>` and make
   it read-only (`chmod -R a-w`). The SQLite files there can no longer be written by mistake. Delete it only
   after the owner confirms, at least 14 days later (D4).

**Rollback** (`agentmesh mac-relocate --rollback BACKUP`):

1. Restore the plists and configs saved in step 5.
2. Restore the old tree's permissions and name, and start the old service.
3. Any memory written at the new location after the move stays in the new DB and is **not** merged back
   automatically. The command reports how many rows were written after the move. The worker's packets are
   still in the exchange, so peers do not lose them.

## Acceptance gates (for the implementation PR; Codex implements, Claude reviews and mutation-tests)

- **R-1 No data loss:** on a synthetic install, row counts per table, `integrity_check` and the identity
  file hashes are identical before and after. Each copy step is checked separately, by mutation.
- **R-2 Nothing writes during the copy:** a writer that tries to commit while the copy runs is blocked by the
  locks, or the copy aborts and restarts the service unchanged.
- **R-3 Refusals:** a target that is not empty, contains a symlink, is group-writable, sits inside the
  exchange, has an exchange inside it, or lacks the disk space makes the command refuse before draining.
- **R-4 Every reference changes:** after relocation, a scan of every config, plist and rule file listed above
  finds no remaining `~/.hermes/omp-memory/sqlite-sync` path. Unrelated Hermes content is untouched.
- **R-5 Rollback:** an injected failure at each step restores the old service with the old binary and old
  runtime. A test also covers the post-move-writes report.
- **R-6 Same identity:** after the move the worker signs with the same `key_id`. A peer test (two synthetic
  nodes) still applies the moved node's new packets.
- **R-7 Dry run:** the dry run writes nothing anywhere (every file's hash and mtime unchanged).
- **R-8 Real host (owner, after merge):** run the dry run, then the real relocation with the owner at the
  terminal. Then check the worker health cycle, recall `backend: broker`, catch-up `unchanged`, and that an
  ingest and a summary run succeed, with the old tree kept read-only.

## Owner decisions (2026-10-20)

- **D1 Mechanism: a tested `mac-relocate` command.** Codex implements it, Claude reviews and mutation-tests it,
  and the owner runs it on the real host after merge.
- **D2 The Syncthing exchange moves too**, to `~/.agentmesh/exchange`. Extra steps for `mac-relocate`:
  1. Pause the folder through the local Syncthing REST API, using the API key from Syncthing's `config.xml`.
     The key is read at run time and never printed or stored.
  2. Copy the folder tree (keeping `.stfolder`), and compare file lists and hashes.
  3. Set the folder's `path` to the new location with the **same folder ID**, then resume it.
  4. Wait until the folder state is `idle` with no `needFiles` and no errors.

  Windows is unaffected, because folder IDs and device IDs do not change. The Windows installer delivery path
  becomes `~/.agentmesh/exchange/installers/`. Rollback restores the previous folder path. If Syncthing is not
  running or its API is unreachable, the command refuses before draining.
  Gate **R-9:** using a fake Syncthing API, every call is checked, along with a refusal when the API is
  unreachable, rollback restoring the path, and a test that the API key never appears in output or logs.
- **D3 Hermes-owned job entrypoints move into `~/.agentmesh/jobs/`:** `agentmesh_production.py`, the ingest,
  summary, export, metrics and watch scripts that read AgentMesh data, their `.venv` (recreated from a pinned
  requirements file rather than copied), and `agentmesh-production.json`. `production-status/` moves to
  `~/.agentmesh/status/`. The ingest, summary and Insight plists are rewritten to call them, and Discord job
  notifications keep their webhook file in `~/.config/agentmesh`. Scripts are copied first. The originals
  stay in place until the old tree is archived, and each is replaced with a one-line notice of its new path.
  These scripts are not in this repository: the plan PR only adds the relocation tooling. The scripts are
  copied from the live host, never committed.
- **D4 Archive everything historical:** the old `sqlite-sync` tree (renamed `sqlite-sync.pre-relocate-<ts>`,
  read-only), the replacement backups, `cutover-backups/`, `releases/`, `runtime-backups/` and the analysis
  files all move to `~/.agentmesh/backups/archive/`. They stay read-only and are deleted only on a separate
  explicit request from the owner. After this, `~/.hermes/omp-memory` holds only the notices. A rollback
  within the archive window points the service back at the archived tree.
- **D5 Agent rules are updated automatically** (the default, since the owner asked for the whole move):
  `~/.claude/rules/agentmesh-shared-memory{.md,.command.json}` and `~/.codex/rules/shared-memory.rules`
  get the new recall argv. The old copies are kept in the relocation backup.

## Follow-ups

- Windows relocation to `%USERPROFILE%\.agentmesh` (today `C:\Users\krisb\.omp_memory`), with a
  `windows-relocate` command, once the PC is available.
- Update the Windows RC delivery notes for the new exchange path.
