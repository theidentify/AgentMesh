# SQLite retirement R5: report

Phase R5 of `../specs/2026-11-02-sqlite-retirement-design.md`, in two parts:

- **R5a (this change):** the `sqlite-retire` ceremony runs on Windows, and the Windows runbook below, built from what
  went wrong on the Mac.
- **R5b (later):** delete the SQLite code paths and their tests. Rollback restores a working SQLite pipeline, so this
  waits until every device's 30-day rollback window has closed: the Mac's on 2026-12-02 (archive
  `sqlite-mac-20261102-135815`), Windows' 30 days after its cutover.

## The ceremony on Windows

The ceremony already avoided launchd: maintenance is a file (`control/maintenance.json`) that `job_notify.py` checks
before every wrapped job, and the Windows ingest, summary and catch-up tasks are wrapped the same way. The supervisor's
Touch ID helper is used only on a Mac. On Windows typed console presence is the whole check, as the spec's open item
allowed (the Windows supervisor and Windows Hello are later work).

- `tests/control/test_retire.py` no longer skips on Windows: the full retire, rollback and delete, the refused
  presence and both failure-restore paths now run on the Windows CI job. Only the POSIX file-mode assertions are
  POSIX-only; on Windows the archive's protection is the private user profile folder.
- The confirmation text says "this device" instead of "this Mac".

After the cutover the Windows catch-up task is harmless: with `authority` off it takes the disabled path, which only
writes a status file and never opens the database.

## What the Mac cutover taught (2026-11-02 to 11-03)

Fixed in code, so Windows does not hit them:

| Symptom on the Mac | Cause | Fix |
|---|---|---|
| First summary: `related failed: truncated` | `work/common` holds more than 500 keyed heads | #156: `heads` pages 500 at a time |
| Next summary: `write failed: unavailable` | summarizer bound the scope name, the event carries the scope id | #157: binding dropped; test through the real client |
| `apply` failed with an empty diff | apply opened the archived staging database for its node id | #158: keeps the recorded node; bridge recall-only |
| Next apply would have re-enabled catch-up | cutover switched the bridge **file** only; settings and the applied record kept `dual_write` and `authority` on | #158: no database means `dual_write` and `authority` off |

Owner steps the Mac run needed that are not obvious:

- The deploy must also refresh the production job **copy** in `jobs\agentmesh_production.py`; the ceremony's dry run
  reports `deployed production job predates R2` otherwise.
- A new agent's first broker call needs a presence window: run `presence open --class first_access` before the dry
  run, or the dry run reports `summarizer cannot write mapped scopes` (the broker answered `presence_required`).
- After a broker restart, wait until it accepts connections before running a job (a run 4 s after the restart got
  `broker_unreachable`).

## Windows cutover (2026-11-03)

Applied as archive `sqlite-windows-20261103-034127` (16 transcripts, 16 waiting events, floor 2026-10-16T05:52Z). The
switch, archive and bridge were as expected, but every summary then failed with `summarizer related failed:
unmapped_project`. Four throwaway Claude sessions (diagnostic `claude -p` runs and the owner's login tests, from the home
folder, `.agentmesh`, System32 and the PowerShell install folder) had no route in the migration mapping, and one
unmapped project stopped every summary. Under SQLite only that project's rows waited at import.

Fixed: the broker summary holds back only an unmapped project's events (its cursors stay), carries on with the others,
and lists the held names in `jobs/summary-unmapped.json`. The control app's new-project prompt reads that file as well
as catch-up's status (catch-up imports nothing after the cutover). The mapping still never guesses a route. On Windows,
which has no control app, the owner routes them in `settings.json` `migration.projects` (a scope, or `skip`) and applies.

## Windows runbook (owner, in PowerShell)

`$PY` is the Windows venv python of the AgentMesh install (`%USERPROFILE%\.agentmesh\venv\Scripts\python.exe` unless the
install put it elsewhere), and `$AM` is `%USERPROFILE%\.agentmesh`.

1. **Deploy `main`** to the Windows install the same way as the W7 rollout, then copy
   `memory-sync\jobs\agentmesh_production.py` from the deployed runtime over `$AM\jobs\agentmesh_production.py` (keep
   the old one as `.bak`). Restart the broker task and wait until a broker call answers (step 3 does one).
2. **Grant the summarizer** in `$AM\settings.json`, under `agents`:
   `"summarizer": {"read": ["work/common", "personal/common"], "write": ["work/common", "personal/common"],
   "default_write": null, "allow_secret": false, "allow_cross_profile": false, "allow_import": false,
   "allow_summarize": true}`, then `& $PY -m agentmesh_log.cli apply`.
3. **Open a presence window** for its first call:
   `& $PY -m agentmesh_log.cli presence open --home $AM\profile-log --class first_access`.
4. **Dry run:** `& $PY -m agentmesh_log.cli control sqlite-retire`. It must print `"problems": []`. Check the seed counts
   (`transcripts`, `waiting_events`, `unreadable` must be 0) and the archive list.
5. **Cut over:** `& $PY -m agentmesh_log.cli control sqlite-retire --apply` and type `migrate`. Expect `"ok": true` with
   `first_run` ingest and summary `ok`. A failure before the switch restores everything; a `first_run` failure leaves
   the cutover in place (the summary cursor does not move, so nothing is lost) and is a bug to report.
6. **Check the summary:** the last row of `$AM\jobs\summary-metrics.jsonl` is `committed` or `pending` (more batches
   waiting), and `agentmesh-log stats --agent-key $AM\agents\summarizer.key` shows the keyed count rising.
7. **Revoke the importer:** delete the `importer` entry under `agents` (keep `"import_agent": "importer"` in `bridge`),
   then `apply`. Expect `agent_remove importer` and a `bridge_update` to `dual_write: false, authority: false`.
8. **After 30 days:** `control sqlite-archive delete --archive sqlite-windows-<stamp>`.

Undo within 30 days: `control sqlite-rollback --archive sqlite-windows-<stamp>`, then put the importer back in
settings and apply.

## R5b: what gets deleted

`test_transcripts.py::SqliteInventoryTests` pins every production module that still touches SQLite (`RETIRE`). R5b
removes them with their tests and shrinks `RETIRE` to empty, leaving `KEEP` (the broker's in-memory index and vector
cache, and the supervisor's `state.db`). It also removes the ceremony's rollback (nothing to roll back to), the
`sqlite` branch of the production job, the bridge's import fields, and the `migrate rows`, `catch-up` and `verify`
commands with the importer role. `sqlite-archive delete` stays until every archive is gone.

## R5b (2026-11-09): done early, by the owner's choice

The owner asked for R5b before the rollback windows closed (Air 12-02, Windows and Pro 12-03), because the MacBook Pro is
going back on 2026-12-03. Every device had been on the broker summary since 11-03 with no rollback needed, so they
gave up rollback on all three.

Removed, in one change (memory-sync and profile-log; Insight follows separately):

- **memory-sync.** Every module except the broker summary path is gone: `broker_digest`, `claude_digest_worker`,
  `transcripts`, `timestamps`, `bounded_protocol`, `job_notify`, `jobs/` and the `omp_memory` parsers stay. The
  six helpers the summary still used from the SQLite modules now live in `digest_io.py`:
  - `canonical`, `load_packet` and `utf8_stdio`, from `memory_sync`;
  - `run_lock`, `append_metric` and `extract_command`, from `bounded_digest`.

  The 0.3.1 worker stack went with the rest. It was uninstalled from Windows in W5, and its installers, OTA,
  signed packets, key rotation and standalone builds only imported the SQLite modules. Its three CI workflows went
  too: `standalone-smoke`, `standalone-trial-artifacts` and `windows-runtime-acl-repair`.
- **Production job.** `jobs/agentmesh_production.py` runs only `backend: broker`. A config without it is refused as
  `SQLite backend retired` and never runs.
- **profile-log.** These are gone:
  - `migrate plan/run/rows/catch-up/report/verify` and their source, run, ledger and verify modules;
  - the `sqlite-retire` ceremony and `sqlite-rollback`;
  - the staging bridge in `apply` (the `bridge` block is accepted and ignored);
  - the `catch-up` control unit;
  - the `migration` and `last_catch_up` status sections (an older file loses them on its next write);
  - the admin TUI's staging rows and import preview;
  - `verify_remote --sample-db`.

  `control sqlite-archive list/delete` stays, with no 30-day window now that there is nothing to roll back to. The
  migration mapping stays, because the broker summary routes projects with it.
- **Kept on purpose.** The broker's import verification (`allow_import`, `import_agents`, imported heads) stays.
  Events the importer wrote are in every replica's history and must keep verifying. The `retention` control unit
  stays in the registry (it is not installed anywhere); the supervisor consolidation will drop it.

`SqliteInventoryTests.RETIRE` now lists only Insight's four modules. `memory-sync/schema.sql` stays until the Insight change too,
because Insight's tests build their fixtures from it.

Owner steps after merge:
1. Deploy `main` to the Air and Windows, then unload the catch-up job: the `org.agentmesh.profile-log.catch-up` plist
   on the Air, the scheduled task on Windows.
2. Delete each archive with `control sqlite-archive delete --archive sqlite-<node>-<stamp>`.
3. Decommission the Pro: revoke its device, unload its agents, remove `~/.agentmesh` and its Keychain items.
