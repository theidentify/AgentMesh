# Migrate from memory-sync

The hourly transcript jobs (`ingest`, `summary`) and the `retention` job's wrapper used to run from the `memory-sync`
tree. They now live in the profile-log package as `agentmesh_log.ingest` and `agentmesh_log.control.jobrun`. This
runbook moves a device over without missing a scheduled run and without touching its cursors or summary state. Run it
once on each device: the MacBook Air, the MacBook Pro and Windows.

::: info What does not change
- **State.** The jobs keep the same files in the same places: `transcript-cursors.json`, `summary-state.json`,
  `summary-unmapped.json` and `summary-metrics.jsonl` in the jobs state directory (`~/.agentmesh/jobs`). The
  `last-*.json` and `job-*.json` files stay in the status directory, and `quota.json` and `maintenance.json` in
  `~/.agentmesh/control`. Lock file names are also unchanged, so an old run and a new run never overlap.
- **Schedule.** The supervisor reads a unit's command each time it starts the unit, and it schedules from the last
  run. A rewired unit runs at its next normal time. No run is skipped or doubled, and a run in progress is left alone.
- **Personal only.** Ingestion reads your own transcripts and writes only to your personal home. It refuses a team
  workspace home (`~/.agentmesh/ws/<id>/`), any configured path inside one, and any summarizer key that reaches a
  workspace broker. Workspace scopes never receive personal transcripts.
:::

## Before you start

In this runbook, `AM` is the AgentMesh home (`~/.agentmesh` on a Mac, `%USERPROFILE%\.agentmesh` on Windows) and
`PY` is the Python of the AgentMesh venv that runs the broker. Run `agentmesh-log` as `PY -m agentmesh_log.cli`,
from the deployed `profile-log` directory or with it on `PYTHONPATH`, the same way you run the broker.

1. Check that the supervisor runs the three jobs:

   ```sh
   agentmesh-log control units
   ```

   `ingest`, `summary` and `retention` should show the native backend. If a unit is still under launchd or Task
   Scheduler, run `agentmesh-log control consolidate --apply` first, or see [Units outside the
   supervisor](#units-outside-the-supervisor).
2. Note where the old runtime is. The `runtime` key in `AM/jobs/agentmesh-production.json` names the `memory-sync`
   directory the jobs import today. `summary.command` names `claude_digest_worker.py` in it.

## 1. Deploy

Update the device's AgentMesh checkout or app bundle to a version that contains `agentmesh_log/ingest/`, the same way
you deploy any update. Restart the broker as usual.

You do not need to stop anything. `memory-sync/` now holds compatibility shims that load the new package, so the
jobs keep running their old commands until step 2. If `runtime` points to a separate copy that the deploy does not
touch, those jobs keep running the old code, which is also fine.

Wait for the next `ingest` run and check that it is still `ok`:

```sh
agentmesh-log control status
```

## 2. Rewire the jobs

Run the dry run first, with the `PY` and `profile-log` that the jobs should use from now on:

```sh
agentmesh-log control rewire-ingest
```

Check the output:

- Each of `ingest`, `summary` and `retention` shows `"action": "rewire"` with its old and new command. A unit already
  rewired shows `not_memory_sync`.
- `imports` is `true` for every interpreter. Each job keeps its own Python, which must import the package with the
  `PYTHONPATH` that the result shows.
- `pythonpath` is the deployed `profile-log` directory, not a development checkout or worktree.

Then apply it and type `migrate` when asked:

```sh
agentmesh-log control rewire-ingest --apply
```

The old commands are saved in `AM/control/rewire-ingest.json`. On a Mac, the parked plists under `AM/control/units/`
are updated too.

## 3. Migrate the job config

Only do this after step 2. The old job scripts need the `runtime` key that this step removes.

```sh
agentmesh-log ingest migrate-config
agentmesh-log ingest migrate-config --apply
```

The dry run lists the changed keys (`runtime`, `summary`) and the new `summary.command`, which is
`PY -m agentmesh_log.ingest.claude_worker` plus the worker options it had before. `--apply` keeps the original
beside the config as `agentmesh-production.json.memory-sync.bak`.

## 4. Check

```sh
agentmesh-log control start summary
agentmesh-log control status
```

- `job-ingest.json` and `job-summary.json` in the wrapper's `--state-dir` (usually `AM/production-status`) show
  `"ok": true` with a new `at` time.
- The `last-summary.json` in the status directory has a new `observed_at` time, and the last row of
  `AM/jobs/summary-metrics.jsonl` is `completed`, `pending` or `scheduled`.
- `agentmesh-log control audit` shows `unit.rewire` with the outcome `ok`.

`control start summary` runs the job now. If a summary is not due, it ends `scheduled` without calling the model,
which still proves the command works.

## Undo

Both steps can be undone separately. Restore the config first, because the old scripts need its `runtime` key:

```sh
cp ~/.agentmesh/jobs/agentmesh-production.json.memory-sync.bak ~/.agentmesh/jobs/agentmesh-production.json
agentmesh-log control rewire-ingest --back
```

On Windows, use `Copy-Item` in place of `cp`. `--back` puts back the saved commands and, on a Mac, the parked plists.

## 5. Clean up (one day later)

After a day of `ok` runs, remove only the old program files:

- `AM/jobs/agentmesh_production.py`, `agentmesh_ingest.py` and `agentmesh_summary.py`, and any `.bak` copies of them.
  Keep everything else in `AM/jobs`: the config, the cursors, the summary state and the metrics.
- The old `runtime` directory, if it was a separate copy outside the deployed checkout (for example
  `~/.agentmesh/memory-sync`).
- `agentmesh-production.json.memory-sync.bak`, once you no longer want the undo.

When all three devices are done, tell the maintainers. The follow-up change removes `memory-sync/` and its shims.

## Per device

### MacBook Air

The supervisor is owned by the AgentMesh Control app, which runs it as a login item. `rewire-ingest` changes only
the supervisor's unit records, so the handover stays as it is. Run the steps above in Terminal.

### MacBook Pro

Run `control units` first. If the jobs still run under launchd and not the supervisor (the Pro was out of sync from
2026-10-28 to 2026-11-02), either consolidate them first or follow [Units outside the
supervisor](#units-outside-the-supervisor).

### Windows

Use PowerShell. Set `$PY` to `"$env:USERPROFILE\.agentmesh\venv\Scripts\python.exe"` (or wherever the install put
it), set `$AM` to `"$env:USERPROFILE\.agentmesh"`, and run each command as `& $PY -m agentmesh_log.cli ...`.

Windows has no parked plist. The supervisor's saved copy of each original task (used by `control release`) keeps the
old command. If you later `release` a job back to Task Scheduler, it runs through the compatibility shims until the
follow-up change removes them. Run `consolidate` and `rewire-ingest` again before that.

## Units outside the supervisor

If a job is still a launchd plist or a scheduled task of its own, `rewire-ingest` skips it (`not_native`). Edit its
command by hand to the new form, keeping the interpreter, the options and the schedule:

```text
PY -m agentmesh_log.control.jobrun --name ingest --state-dir ... [--webhook-file ...] -- PY -m agentmesh_log.ingest scan
PY -m agentmesh_log.control.jobrun --name summary --state-dir ... [--webhook-file ...] -- PY -m agentmesh_log.ingest summary
```

Set `PYTHONPATH` to the deployed `profile-log` directory: under `EnvironmentVariables` in a plist, or in a task, by
running the action through a small `.cmd` file that sets it. Then reload the unit. Doing this is usually more work than
running `control consolidate --apply` and then `rewire-ingest`.
