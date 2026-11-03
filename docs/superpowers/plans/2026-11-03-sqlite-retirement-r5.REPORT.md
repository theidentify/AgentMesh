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
