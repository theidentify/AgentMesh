# Standalone CLI build (v0.2.0 work in progress)

This is an **early macOS/Windows CLI build path**, not an installer, GUI, signed OTA package or production rollout. It wraps the existing `agentmesh.py` commands (`status`, `recall`, `ingest`, `summarize`, `once`, `watch`) and adds a **read-only** `inspect-install` command. Other commands still require an initialized SQLite database path. Inspection can find the default platform runtime configuration or accept `--runtime /path/to/runtime.json`; it does not create, repair or approve anything. It does not run the security wizard, manage services, install the OTA receiver or update an existing worker. Do not treat a successful build as completion of the v0.2.0 milestone.

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
```

Build output and scratch data must remain outside the source tree, Syncthing exchange and Git. Never package real databases, identity files, private keys, bootstrap snapshots, credentials or transcripts. The bundled CLI includes the schema and parser modules but does not contain a production database. `inspect-install` returns only a limited status projection; `identity=present_unverified` is not a key/ACL check, and an inspection snapshot must never authorize strict activation or a worker restart. It refuses missing/unsafe runtime paths and a strict database without an identity, rather than creating replacements. The PyInstaller hook override is scoped to the project's own `workflow.py`; the similarly named third-party package's hook otherwise requires unrelated distribution metadata.

**Security and rollout gates:** The local macOS build is neither Developer ID signed nor notarized; Windows build/native tests and distribution signing have not been done. Do not publish this binary as a trusted release asset or replace a running worker with it. The existing signing/key storage, backup/recovery and OTA limitations remain unchanged. The standalone CLI `once`/`watch` path also needs explicit signed-mode and installation-lifecycle integration before it may replace a strict production worker. Track remaining acceptance criteria in GitHub issues #2–#5.
