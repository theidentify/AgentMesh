# Standalone CLI build (v0.2.0 work in progress)

This is a **native macOS/Windows CLI build path and Mac-first operator replacement trial**, not a GUI, signed OTA package, notarized public release or completed production rollout. Existing recall/ingest/summary commands remain available. `inspect-install`, `wizard-status`, `wizard-resume` and `setup-new` preserve their bounded setup behavior; none starts a service automatically.

`adopt-install` now binds existing DB/exchange/workflow/security paths only after BIND. `worker-run`, `worker-start`, `worker-status` and `worker-stop` provide an explicit managed lifecycle using the selected runtime and authoritative DB policy. On macOS, the build also produces `Replace-AgentMesh.command`; `mac-replace` and `mac-rollback` provide operator-confirmed replacement and code/service recovery without automatic DB restoration. See [Mac operator replacement](MAC-REPLACEMENT.md) for the required private manifest, drain/backup/confirmation gates and limitations. This does not close the full v0.2.0 milestone or activate signing, join a baseline, enable PostgreSQL or install a Windows service.

## Read-only diagnostics and safe error details

```powershell
.\agentmesh.exe diagnose --runtime "$env:LOCALAPPDATA\AgentMesh\data\runtime.json"
.\agentmesh.exe diagnose --runtime "$env:LOCALAPPDATA\AgentMesh\data\runtime.json" --json
```

`diagnose` shows program/package identity (when an adjacent BUILD.json is available), runtime validation, installation scope, worker-control permissions and worker state as separate timed checks. Human mode prints progress to stderr before each check; `--json` keeps stdout machine-readable. A failed dependency skips later checks rather than hiding the first failure. Exit **0** means completed observations without warnings/errors (a stopped worker can be normal); exit **1** means attention is required, not that a repair occurred. Windows trial bundles include a double-click `Diagnose-AgentMesh.cmd` using the human-readable mode.

Worker errors retain `error`, `reason` and trusted `stage` and add stable `code`, `next_action` and numeric OS/SQLite error codes when available. Known refusal messages, including stale process metadata, are explicitly allowlisted; arbitrary exception messages, paths, runtime contents, keys, tokens and source text are not printed. An unrecognized recorded worker error is shown as `RecordedWorkerError`, not its potentially private payload. Program metadata accepts only version/commit/checksum fields and verifies the executing binary against the adjacent manifest; this is **not** publisher-signature verification. Without that manifest, the version is explicitly unavailable, never guessed from a folder name.

Diagnosis never creates a control directory, WAL/SHM, key or wizard state, changes ACLs, stops/restarts a worker or repairs stale metadata. It uses immutable SQLite scope inspection: uncheckpointed WAL is excluded and this result cannot authorize activation or drain. `state=stale` means a record claimed running but its PID was observed absent; a present/inaccessible PID is not an ownership proof. Timestamp age is the record age, not proof of a successful sync or peer convergence. Do not kill a metadata-selected PID, delete worker state, rerun setup-new or relax permissions based on this report. No startup latency or Windows installer/service behavior is changed by diagnostics.

## Managed background worker lifetime

A frozen `worker-start` gives its child an independent PyInstaller extraction (`PYINSTALLER_RESET_ENVIRONMENT=1`); it must not reuse the starter's `_MEI` directory after the starter exits. Windows creation uses `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, with redirected standard handles; `DETACHED_PROCESS` alone is insufficient because the onefile bootloader's inner console-subsystem process creates a console again. POSIX uses a new session. This separates console/process lifetime, **not** Windows service installation, startup-at-login, job-object policy or survival of logout/reboot. Foreground `worker-run` retains its foreground behavior. Local private worker metadata includes the frozen `bundle_dir` and, on Windows, `console_attached` for diagnosis; these fields are not peer-status exchange fields.

`memory-sync/smoke_worker_lifetime.py` exercises the native executable against an isolated new fixture. It waits for the actual starter to exit (a separately owned console on Windows), verifies retained worker resources and two additional healthy cycles, then requests nonce-cooperative stop and verifies process exit, extraction cleanup, SQLite integrity and unchanged runtime/workflow/key/scope/policy. It never signals a metadata PID. Run with `RUNNER_TEMP` set to a private scratch parent and `AGENTMESH_DIST` to the built binary directory. A failed fixture is retained for diagnosis. This proof is not a test of a company-managed Windows Terminal's job-object policy or a real service installer.

Reference: [PyInstaller 6.16 independent instances](https://pyinstaller.org/en/v6.16.0/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart).

On each target OS, build natively from the reviewed release source (do not cross-compile):

```sh
uv venv /private/agentmesh-build-venv
uv pip install --python /private/agentmesh-build-venv/bin/python -r memory-sync/requirements-security.txt 'pyinstaller==6.16.0' 'pyinstaller-hooks-contrib==2026.8'
/private/agentmesh-build-venv/bin/python memory-sync/standalone_build.py --dist /private/agentmesh-dist --work /private/agentmesh-build
```

On Windows, use the build venv's `Scripts\\python.exe` and local private paths for `--dist`/`--work`; the resulting executable is `agentmesh.exe`. The build script refuses output paths within the source tree. This is a developer build tool; **end users do not need to install Python or Git to run the resulting executable**.

## New EMPTY installation (new isolated group only)

```sh
/private/agentmesh-dist/agentmesh setup-new \
  --local-dir /absolute/already-existing-parent/new-agentmesh \
  --exchange /absolute/accepted-syncthing-exchange --node mac
```

All three options are required; use allocation slot `windows` or `linux` as appropriate. The local root must **not exist**, even as an empty directory, file, partial runtime/database or orphaned identity. Existing installations use `inspect-install`, `wizard-status` and `wizard-resume` instead; this command never upgrades, repairs, resumes, replaces a key or reinterprets existing files. Paths must be absolute, without `..`, symlink/reparse ancestors or a symlink `.stfolder`. The exchange must contain an accepted `.stfolder` **directory**. The local parent must already exist: no untracked parents are created. Local storage must be outside exchange and must not contain exchange as a descendant.

A stderr prompt requires the exact word **NEW** before any filesystem write. Any other response returns a nonsecret JSON `status=pending` on stdout (exit **2**), with no mutation; EOF blocks (exit **1**). Invalid/preexisting scope blocks before prompting. Paths, ancestor directory identities, exchange and its marker are revalidated after confirmation. An exclusive `mkdir` claims the root; a losing installer never cleans up or overwrites the winner. Successful creation returns `status=created`, the fresh canonical UUID group, allocation node and runtime path (exit **0**). It provisions/verifies private local root, data directory and files using the existing POSIX permissions/Windows owner-only ACL mechanisms before sensitive writes.

The created layout contains `data/memory.db` (empty schema/FTS, new sync group and allocation ranges), `data/workflow.json` (`ingest=false`, `summarize=false`) and `data/runtime.json` pointing to the exact DB/exchange/node plus future local `identity` and `data/security-wizard.json` paths. **No identity/key or wizard state is created.** Existing resolver and wizard status validate the result; policy remains legacy and security setup remains pending. No CREATE/PUBLISH/PROBE/ACTIVATE, peer approval, exchange packets, worker/service, dependency install, ZIP/bootstrap, OTA or signed-mode activation is performed. Windows permission provisioning invokes its existing OS ACL adapter, not a worker. Do not start a strict production worker from this unsigned first-run path.

**This creates a different, empty sync group.** It does not migrate a database, restore a backup, copy a baseline, select an existing group or add a peer to an existing-data group. Do not use it as a shortcut around the existing baseline/join workflow. Issue **#3 remains partial** while baseline join/migration and full installer UX remain outstanding; issues #2–#5 and distribution/activation gates are not closed by this phase.

**Failure/rollback limits:** setup records an exclusively created root and `.setup-pending.json` marker. Ordinary failures remove only a complete manifest of proven-owned paths whose filesystem identities and completed bytes still match, using individual unlink/rmdir operations, never blanket recursive deletion. Interrupted DB initialization, unknown sidecars, replaced files or unexpected concurrent contents retain a marked partial root for manual review; another `setup-new` blocks without changing it. Packaged `inspect-install`, `wizard-status` and `wizard-resume` also refuse a root with this marker before inspection or wizard mutation. Do not resume/activate a partial installation just because a runtime file exists. Retain it for review/recovery, or choose a different new root; do not auto-delete unknown contents. Process death, permission failures before marker creation, and hostile concurrent writers can leave incomplete/unmarked state. Directory identity/fingerprint checks are not an OS-level hostile-writer lock, crash-atomic transaction or defense against adversarial ABA swaps. Keep parent, exchange and local contents stable throughout setup. SQLite handles are explicitly closed; a writable validation handle lets SQLite clean its own empty inspection WAL/SHM rather than adopting arbitrary sidecars for rollback.

Smoke-check with a disposable initialized database before touching a real installation:

```sh
/private/agentmesh-dist/agentmesh --help
/private/agentmesh-dist/agentmesh --database /private/test.db status
/private/agentmesh-dist/agentmesh --database /private/test.db recall example
/private/agentmesh-dist/agentmesh inspect-install --runtime /private/installation/data/runtime.json
/private/agentmesh-dist/agentmesh wizard-status --runtime /private/installation/data/runtime.json
/private/agentmesh-dist/agentmesh wizard-resume --runtime /private/installation/data/runtime.json
```

`wizard-resume` sends prompts to stderr and only a nonsecret JSON status to stdout. Exit codes: **0** fully active/approved with prerequisites ready; **2** safely pending; **1** blocked (including EOF). It preserves existing CREATE, PUBLISH, PROBE and BOTH/DRAINED/ACTIVATE gates. A fresh legacy installation without an identity only creates one after explicit CREATE; a lost persistent identity or strict DB with absent/invalid keys requires recovery, never a replacement. Initial setup may create the existing wizard's private SQLite backup and state file; later resumes preserve existing bindings, key fingerprint and backup reference. This is not a key-backup/export command.

Before every interactive resume operation, the packaged path rechecks runtime bytes, resolved paths, current SQLite node/group, persistent identity and strict sender binding. Configuration/scope changes while prompting fail closed. Keep configuration, identity and database scope stable during setup; these checks are not an OS-level lock against a hostile concurrent writer. Explicit activation still requires a current signed probe receipt and coordinated approval; no service is restarted even after activation.

The native CI workflow invokes `memory-sync/smoke_standalone.py` on macOS and Windows and includes focused source tests for `setup-new`. The smoke uses disposable fixtures and a minimal PATH. It first runs frozen `setup-new` with decline, EOF and explicit NEW, verifies the empty isolated group/runtime/permissions and status, refuses reuse and a fixture-marked partial root, and asserts no identity, packets or activation. It then preserves the existing two-peer flow: declines CREATE, explicitly creates a fixture-only identity, then resumes custom security paths with pairing/activation pending. It verifies DB/runtime preservation, fingerprint/state/backup-reference preservation, no proposal without PUBLISH, missing runtime/EOF failure and strict missing-key refusal. It also prepares real two-peer signed probe receipts, enters the compiled activation prompts, types BOTH/DRAINED but declines ACTIVATE, and verifies that policy remains legacy. Run it locally with `RUNNER_TEMP` set to a private scratch root and `AGENTMESH_DIST` to the built output directory.

Build output and scratch data must remain outside the source tree, Syncthing exchange and Git. Never package real databases, identity files, private keys, bootstrap snapshots, credentials or transcripts. The bundled CLI includes the schema and parser modules but does not contain a production database. `inspect-install` returns only a limited status projection; `identity=present_unverified` is not a key/ACL check, and an inspection snapshot must never authorize strict activation or a worker restart. It refuses missing/unsafe runtime paths and a strict database without an identity, rather than creating replacements. The PyInstaller hook override is scoped to the project's own `workflow.py`; the similarly named third-party package's hook otherwise requires unrelated distribution metadata.

**Security and rollout gates:** The main native CI workflow builds and smoke-tests disposable installations, including explicit frozen adoption/lifecycle, but does **not** publish a public release. The separate RC trial workflow retains allowlisted unsigned program/docs bundles only after native executable verification; those CI artifacts are not trusted-publisher releases. The Mac-only source-supervisor-injected rehearsal uses the real compiled worker and disposable WAL DB; it does not invoke real launchctl. The local macOS artifact is neither Developer ID signed nor notarized. A real user-operated Mac replacement trial, fresh native Windows results and distribution signing are separate gates. Do not publish this as a trusted release asset. The raw `once`/`watch` commands retain their existing semantics; use runtime-bound `worker-*` commands and the documented Mac installer for the bounded replacement path. Existing signing, baseline-join, key recovery and OTA limitations remain unchanged.
