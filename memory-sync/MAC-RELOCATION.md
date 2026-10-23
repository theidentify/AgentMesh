# Mac home relocation

`mac-relocate` moves an existing, bound installation into one AgentMesh root.
The owner runs this only after review and merge. Development and tests use
temporary installations, fake launchd supervision and an injected Syncthing
transport.

## Prepare the selected release and manifest

Use the Python source distribution from the selected release tag. The command
copies that distribution's `memory-sync` Python modules, `src` modules and schema,
including `profile_log_bridge.py`. A frozen executable needs the selected release's
`memory-sync/` source directory beside it. The existing bound worker binary is
copied into `versions/<sha>/agentmesh`; relocation does not upgrade the worker or
change its signing identity.

Create a user-owned 0600 JSON manifest with absolute paths (expand `~` yourself).
Live data, state, identity, exchange and job files must be user-owned, not
group/world-writable, and have no symlinks. Job interpreters and archive-only
trees have the narrow exceptions described below.
The following is a template: replace every example path and list every actual
job entrypoint, rule and historical source-root entry before use.

```json
{
  "format": "agentmesh-relocation-v1",
  "from": {
    "runtime": "/Users/OWNER/.hermes/omp-memory/sqlite-sync/data/runtime.json",
    "plist": "/Users/OWNER/Library/LaunchAgents/org.example.agentmesh.sync.plist",
    "binary": "/Users/OWNER/.hermes/omp-memory/standalone-versions/SHA/agentmesh",
    "exchange": "/Users/OWNER/.hermes/omp-memory/sync"
  },
  "to_root": "/Users/OWNER/.agentmesh",
  "syncthing": {
    "config": "/Users/OWNER/Library/Application Support/Syncthing/config.xml",
    "folder_id": "EXISTING-FOLDER-ID"
  },
  "jobs": {
    "plists": [
      {"plist": "/Users/OWNER/Library/LaunchAgents/org.example.ingest.plist", "label": "org.example.ingest"},
      {"plist": "/Users/OWNER/Library/LaunchAgents/org.example.summary.plist", "label": "org.example.summary"},
      {"plist": "/Users/OWNER/Library/LaunchAgents/org.example.insight.plist", "label": "org.example.insight"}
    ],
    "files": {
      "/Users/OWNER/.hermes/scripts/agentmesh_production.py": "agentmesh_production.py",
      "/Users/OWNER/.hermes/scripts/omp_memory_ingest.py": "agentmesh_ingest.py",
      "/Users/OWNER/.hermes/scripts/omp_memory_summary_cron.py": "agentmesh_summary.py"
    },
    "production_config": "/Users/OWNER/.hermes/omp-memory/agentmesh-production.json",
    "status_dir": "/Users/OWNER/.hermes/omp-memory/production-status",
    "venv_requirements": "/Users/OWNER/relocation-inputs/requirements.txt"
  },
  "rules": [
    "/Users/OWNER/.claude/rules/agentmesh-shared-memory.md",
    "/Users/OWNER/.claude/rules/agentmesh-shared-memory.command.json",
    "/Users/OWNER/.codex/rules/shared-memory.rules"
  ],
  "archive": [
    "/Users/OWNER/.hermes/omp-memory/standalone-versions",
    "/Users/OWNER/.hermes/omp-memory/standalone-replacement-backups",
    "/Users/OWNER/.hermes/omp-memory/cutover-backups",
    "/Users/OWNER/.hermes/omp-memory/releases",
    "/Users/OWNER/.hermes/omp-memory/runtime-backups"
  ],
  "settings_local": "/Users/OWNER/.agentmesh/settings.local.json"
}
```

Unknown manifest fields are rejected. Job plist labels must match their contents;
one ingest label and one summary/summarize label identify verification ticks.
Each plist must use `ProgramArguments` with its interpreter, optional `-u`, `-I`
or `-B` flags, then a script listed in `jobs.files` or inside `jobs.status_dir`.
Include export, metrics and watch entrypoints too when installed.
One JSON rule must contain the actual `recall_argv`. The existing bridge must have
enabled recall, dual-write and authority with accessible private agent keys and
migration mapping. These credentials are not printed.

Supply exact `package==version` pins for the complete dependency closure.
Place already acquired wheels in `wheels/` beside the requirements file when
dependencies are needed. For standard-library-only jobs an empty requirements
file is accepted; no wheelhouse or pip installation is needed.
Venv creation uses the resolved real interpreter of the actual ingest job
(the inner interpreter for a wrapper), `venv --copies`, offline
wheel-only pip installation with no dependency downloads, and `pip check`.
Job interpreters may be symlink chains: their final real target must be a regular,
executable file owned by the user or root and not group/world-writable. An old
venv interpreter becomes `jobs/.venv/bin/python`; an external interpreter such
as Homebrew stays at its existing argv path. Generated venv entries still must
not contain symlinks. Keep the
requirements and wheelhouse outside the old memory root, or explicitly account
for their archive location.

For the real Mac ingest/summary layout, the script immediately after the outer
interpreter is `<app_root>/job_notify.py`. Its `--name`, `--state-dir`, optional
`--webhook-file` and optional positive integer `--timeout` are validated, and
`--` must separate the inner interpreter/script command. List the inner
Hermes `omp_memory_ingest.py` and `omp_memory_summary_cron.py` scripts in
`jobs.files`, not `job_notify.py`; each value is the installed name under
`<to_root>/jobs`, so map them to `agentmesh_ingest.py` and `agentmesh_summary.py`; the selected release must include that wrapper. Both
interpreter positions and `--state-dir` are rewritten, while the webhook path
stays unchanged. The inner script must be in `jobs.files`.

Insight may use `python3 -u <status_dir>/insight-release-<hex>/server.py --sqlite
<old db> --port 51877`. Its server script is copied with the status tree rather
than listed in `jobs.files`. The status path and `--sqlite` value are rewritten;
`-u`, the external interpreter and the port remain unchanged.

Symlinks inside archive-only trees, including the old `.venv`, are preserved
with their exact target text and recorded as `link -> target`. They are never
followed, hashed as target content or chmodded. The normal archive operation
renames these trees intact; archive-only copies create links with `os.symlink`.
Live source trees retain strict symlink refusal. Rollback checks the recorded
link inventory before changing archive permissions.

The existing destination root may be populated. Its existing entries are
preserved except for the requested settings-local update; normal broker/service
writes during verification remain expected. Each new destination (`memory-sync`,
`exchange`, `versions`, `jobs`, `status`, `backups/archive`) must be absent or an
empty user-owned 0700 directory. Free space must cover two database copies plus
the exchange. Preflight also requires reachable loopback Syncthing, the existing
folder ID/path, `.stfolder`, matching live worker/job arguments, and complete
accounting of the old memory root's history.

## Inspect and relocate

Before the confirmed relocation, the owner must unload the separately managed
`org.agentmesh.profile-log.catch-up` service and wait for its running tick to
finish. Keep its current plist for recovery. Do not include it in `jobs.plists`:
its command uses `-m agentmesh_log.cli`, whereas relocation job entries require
an interpreter followed by a script in `jobs.files`. Keep catch-up unloaded
through the move and verification.

From the selected release:

```sh
python memory-sync/agentmesh.py mac-relocate --manifest /absolute/manifest.json --dry-run
python memory-sync/agentmesh.py mac-relocate --manifest /absolute/manifest.json --timeout 300
```

Dry run performs reads and Syncthing GET requests only. Review its complete change
list and reference mapping. The real command asks for `RELOCATE`, rechecks the
inputs, then uses the existing adoption flow's `BIND` confirmation. Keep the
owner at the terminal for both prompts. Exit codes are 0 for success, 2 for a
declined operation, and 1 for failure.

The private backup contains a write-ahead `relocation.json`, original reference
files and a SQLite baseline. The ordered transaction drains services, takes both
job locks and a SQLite write reservation, pauses Syncthing, copies and checks the
database/state/identity/exchange, installs source and jobs, binds the new runtime,
publishes references, resumes Syncthing at the same folder ID, starts the worker
and verifies it. Per-command timeouts apply; Syncthing must reach idle with zero
needed files and no errors.

Verification runs ingest and summary ticks, invokes the installed bridge hook
and its generated migration command, runs the rule recall (requiring
`backend: broker`), and requires nonempty catch-up results containing only
`unchanged`. It then archives the original runtime and listed history, makes
archives read-only, writes one-line notices at old job script paths, and restores
the previously loaded job services. The Discord webhook path remains unchanged.

After success the owner runs `agentmesh-log apply` through their usual approved
entrypoint, supplying presence if requested. The directly rendered bridge
sidecar uses the same fields as apply. Confirm that apply introduces no changes
apart from the requested database-path change. Preserve the read-only archives
for at least 14 days and until a separate explicit deletion request.

After relocation and apply, regenerate the catch-up service against the new
database using the selected release's generator:

```sh
python memory-sync/platform_service.py generate --unit catch-up --platform macos \
  --database /Users/OWNER/.agentmesh/memory-sync/data/mac.db \
  --home /Users/OWNER/.agentmesh/profile-log \
  --output /absolute/new-catch-up-output
```

Use the actual broker home, preserve the existing interval with `--interval`
if customized, and choose an output directory that does not already exist.
Inspect the generated `org.agentmesh.profile-log.catch-up.plist`, install it in
place of the unloaded plist, and load the service using the owner's usual
launchd procedure. Confirm its `--db` points to the new DB and its next tick
succeeds. If relocation fails and restores original paths, reload the saved old
plist only after recovery completes. After an explicit archived-runtime
rollback, regenerate against the restored archived DB before loading catch-up.

## Rollback and recovery

Use the exact backup path returned by relocation:

```sh
python memory-sync/agentmesh.py mac-relocate-rollback --backup /absolute/backups/relocation-ID
```

Type `ROLLBACK` at the prompt. An automatic rollback on relocation failure
restores original paths, permissions, references and previously loaded services.
A rollback after a successful move points the old service and jobs at the archived
runtime, restores permissions needed to use it and restores the folder path to
that archived exchange. It never merges the new database into the old database.

The result reports per-table inserted/changed row counts and deleted row counts
relative to the copy baseline, without printing row contents. The new database
and exchange remain available for owner review. Immutable new packets are also
preserved in the restored exchange; a conflicting packet refuses recovery rather
than overwriting data.

Interrupted rollback can be retried with the same backup, including a failure
while starting the old worker or a later job. Changed external references,
changed snapshots/binaries, foreign service arguments or conflicting archive
paths require manual review. Inspect the returned status and private journal;
do not delete backups, modify hashes, force overwrites, or start both workers.
Relocation does not automatically clear retained destinations for another attempt.

## Owner acceptance (R-8)

After merge, review the dry run on the real host and perform the confirmed move.
Unload `org.agentmesh.profile-log.catch-up` first as described above; after
success, regenerate it with `platform_service.py generate --unit catch-up
--database <new db>` and the other required options, install the generated
plist, and load it. This module-based service cannot be a `jobs.plists` entry.
Check the new worker's completed health cycle, rule recall's broker backend,
catch-up's unchanged outcomes, a successful ingest and summary tick, the same
Syncthing folder ID at the new path with idle/zero pending state, and the
read-only archived old tree. Run apply as above and check all three rule files
and all job plists. Windows delivery is now `~/.agentmesh/exchange/installers/`;
peer folder IDs and device IDs are unchanged. Record these host results
separately: synthetic tests do not satisfy R-8.

## Job script names

The Mac was relocated on 2026-10-20 with the Hermes names kept
(`~/.agentmesh/jobs/omp_memory_ingest.py`, `omp_memory_summary_cron.py`).
Since 2026-10-23 the jobs run the tracked AgentMesh shims in `jobs/`:
`agentmesh_ingest.py`, `agentmesh_summary.py` and `agentmesh_retention.py`,
each a four-line call to `agentmesh_production.execute`. The Hermes-era `omp_*`
files, including the PostgreSQL-era `blocked` writers and the Hermes metrics and
watch scripts, were removed from `~/.agentmesh/jobs` after the switch.
The relocation manifest still names the Hermes paths as sources, because those
are what a Hermes host has.

Relocation rewrites job `ProgramArguments` but not `WorkingDirectory`, so the
Mac ingest/summary plists kept `~/.hermes/omp-memory` as their working
directory until 2026-10-23, when it became `~/.agentmesh/jobs`
(`support/hermes-cleanup.zsh`). The same script moves the one-line notices in
`~/.hermes/scripts`, the legacy `omp-*` Hermes skills and the stale
pre-relocation catch-up render into
`~/.agentmesh/backups/archive/hermes-remnants-20261023`; the Insight deploy
handles `~/.hermes/omp-memory`. Nothing at runtime reads `~/.hermes` afterwards;
`~/.hermes/skills/agentmesh-shared-memory` is the Hermes agent's recall skill
and stays.
