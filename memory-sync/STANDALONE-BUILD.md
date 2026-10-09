# Standalone CLI build (v0.2.0 work in progress)

This is an **early macOS/Windows CLI build path**, not an installer, GUI, signed OTA package or production rollout. It wraps the existing `agentmesh.py` commands (`status`, `recall`, `ingest`, `summarize`, `once`, `watch`) and adds **read-only** `inspect-install` and `wizard-status`, plus interactive **existing-install-only** `wizard-resume`. Other commands still require an initialized SQLite database path. Inspection can find the default platform runtime configuration or accept `--runtime /path/to/runtime.json`; it does not create, repair or approve anything. Wizard status projects existing state without setup. Wizard resume reuses the validated runtime's exact database, exchange, security directory and wizard state paths. It does not bootstrap a ZIP, install dependencies, start a worker, manage services, install the OTA receiver or update an existing worker. Do not treat a successful build as completion of the v0.2.0 milestone.

On each target OS, build natively from the reviewed release source (do not cross-compile):

```sh
uv venv /private/agentmesh-build-venv
uv pip install --python /private/agentmesh-build-venv/bin/python -r memory-sync/requirements-security.txt 'pyinstaller==6.16.0' 'pyinstaller-hooks-contrib==2026.8'
/private/agentmesh-build-venv/bin/python memory-sync/standalone_build.py --dist /private/agentmesh-dist --work /private/agentmesh-build
```

On Windows, use the build venv's `Scripts\\python.exe` and local private paths for `--dist`/`--work`; the resulting executable is `agentmesh.exe`. The build script refuses output paths within the source tree. This is a developer build tool; **end users do not need to install Python or Git to run the resulting executable**.

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

Native CI already invokes `memory-sync/smoke_standalone.py` on macOS and Windows. The smoke uses disposable fixtures and a minimal PATH, declines CREATE, explicitly creates a fixture-only identity, then resumes custom security paths with pairing/activation pending. It verifies DB/runtime preservation, fingerprint/state/backup-reference preservation, no proposal without PUBLISH, missing runtime/EOF failure and strict missing-key refusal. It also prepares real two-peer signed probe receipts, enters the compiled activation prompts, types BOTH/DRAINED but declines ACTIVATE, and verifies that policy remains legacy. Run it locally with `RUNNER_TEMP` set to a private scratch root and `AGENTMESH_DIST` to the built output directory.

Build output and scratch data must remain outside the source tree, Syncthing exchange and Git. Never package real databases, identity files, private keys, bootstrap snapshots, credentials or transcripts. The bundled CLI includes the schema and parser modules but does not contain a production database. `inspect-install` returns only a limited status projection; `identity=present_unverified` is not a key/ACL check, and an inspection snapshot must never authorize strict activation or a worker restart. It refuses missing/unsafe runtime paths and a strict database without an identity, rather than creating replacements. The PyInstaller hook override is scoped to the project's own `workflow.py`; the similarly named third-party package's hook otherwise requires unrelated distribution metadata.

**Security and rollout gates:** A pull-request workflow builds and smoke-tests the CLI on macOS and Windows using disposable data, but does **not** upload or publish unsigned binaries. The local macOS build is neither Developer ID signed nor notarized; Windows build/native results and distribution signing must be checked in GitHub before claiming cross-platform readiness. Do not publish this binary as a trusted release asset or replace a running worker with it. The existing signing/key storage, backup/recovery and OTA limitations remain unchanged. The standalone CLI `once`/`watch` path also needs explicit signed-mode and installation-lifecycle integration before it may replace a strict production worker. Track remaining acceptance criteria in GitHub issues #2–#5.
