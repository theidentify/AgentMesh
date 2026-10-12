<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/agentmesh-logo-dark.svg">
  <img src="assets/agentmesh-logo.svg" alt="AgentMesh" width="384" height="80">
</picture>

# macOS and Linux setup

This installs the AgentMesh Python/SQLite memory-sync application, not Hermes or every agent application. Install Python 3.10+ with SQLite FTS5 and JSON support and Syncthing separately. Put `python3` and `syncthing` on PATH. Pair devices, accept the exchange folder, and wait for the bootstrap ZIP and manifest to finish syncing. No wrapper runs brew/apt, changes root configuration, or registers services.

Run from the supplied setup directory:

```sh
# macOS
sh setup_macos.sh "/absolute/path/to/exchange"
# Linux
sh setup_linux.sh "/absolute/path/to/exchange"
```

The exchange argument defaults to the directory containing the wrapper. An optional second argument selects the local installation directory. Defaults are macOS `$HOME/Library/Application Support/AgentMesh` and Linux `${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh`. Keep local application/database storage outside the Syncthing exchange folder. Only exchange artifacts should sync, never the live SQLite database.

Installation starts a foreground worker: the shell remains occupied until Ctrl-C. Running the macOS wrapper does **not** enable a background service. Stop this worker before enabling persistent operation to avoid duplicate workers.

## Review ingestion roots before the first cycle

By default, a new bootstrap enables local transcript ingestion immediately. To
install and synchronize without scanning local transcripts, use the leading flag:

```sh
sh setup_macos.sh --no-ingest "/absolute/path/to/exchange" "/absolute/path/to/local"
sh setup_linux.sh --no-ingest "/absolute/path/to/exchange" "/absolute/path/to/local"
```

The flag also works directly as `python3 bootstrap_windows.py --no-ingest` with
your usual `--exchange`, `--local-dir`, and `--node` options. Windows Command Prompt:

```bat
START-WINDOWS.cmd --no-ingest
```

This still imports the supplied bootstrap snapshot and exchanges existing memory;
it disables **local transcript ingestion**, not synchronization. On re-run it sets
`ingest` to `false` while preserving chosen roots and all other workflow settings.

To enable ingestion afterwards:

1. Stop the foreground worker with Ctrl-C (and stop any separately managed worker).
2. Open the local `data/workflow.json` path printed by bootstrap. Preserve existing
   fields, and set these two fields to your reviewed source directories:

   ```json
   {
     "ingest": true,
     "roots": {"omp": "/absolute/path/to/reviewed-sessions"}
   }
   ```

   On Windows, use native paths such as
   `"roots": {"codex": "C:/Users/you/reviewed-sessions"}` (forward slashes avoid
   JSON backslash escaping). Supported agent keys are `omp`, `codex`, and `claude`.
   Include only sources this machine should ingest. An explicit `roots` object
   replaces all default roots; omitted agents are not scanned, and `{}` scans none.
   Without a `roots` field, enabling ingestion scans the default agent directories.
3. Restart with the same installation paths and identity, **without `--no-ingest`**.
   Bootstrap preserves the edited configuration. Check the cycle's ingestion
   results before adding more roots. Repeating `--no-ingest` disables it again.

This uses the existing workflow JSON format; no new configuration command is
needed. Keep this file private and outside the exchange folder.

## Manual restart after installation

Use the installation paths reported by bootstrap (`data/runtime.json`). For default locations:

```sh
# macOS
LOCAL="$HOME/Library/Application Support/AgentMesh"
NODE=mac
# Linux instead:
# LOCAL="${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh"
# NODE=linux
EXCHANGE="/absolute/path/to/exchange"
PYTHON="$(command -v python3)"
"$PYTHON" "$LOCAL/app/sync_worker.py" "$LOCAL/data/$NODE.db" "$EXCHANGE" --interval 60
# Append --once for one cycle only.
```

## Optional macOS user LaunchAgent

Use absolute paths, including an interpreter available at login. Run the generator from the setup source directory:

```sh
LOCAL="$HOME/Library/Application Support/AgentMesh"
EXCHANGE="/absolute/path/to/exchange"
PYTHON="$(command -v python3)"
PLIST="$HOME/Library/LaunchAgents/org.agentmesh.sync.plist"
"$PYTHON" platform_service.py generate --platform macos --python "$PYTHON" \
  --app-dir "$LOCAL/app" --database "$LOCAL/data/mac.db" --exchange "$EXCHANGE" \
  --output "$PLIST" --interval 60
plutil -lint "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl print "gui/$(id -u)/org.agentmesh.sync"
```

The plist runs at login/loading and restarts the worker. Logs are in the private local `data` directory (`agentmesh.stdout.log`, `agentmesh.stderr.log`), not in the exchange. To stop/unregister: `launchctl bootout "gui/$(id -u)" "$PLIST"`.

Optional read-only PostgreSQL mirroring: supply `OMP_MEMORY_DSN` securely in the environment **when generating**, and append `--postgres` to the generator command. There is no default host/user. This embeds the DSN in the generated mode-0600 plist, so keep it local, never sync/commit/share it. The worker also needs its PostgreSQL driver installed separately. The generator never prints the DSN or accepts it as a command-line argument. Linux service generation does not configure PostgreSQL mirroring.

## Optional Linux systemd user service

```sh
LOCAL="${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh"
EXCHANGE="/absolute/path/to/exchange"
PYTHON="$(command -v python3)"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/agentmesh.service"
"$PYTHON" platform_service.py generate --platform linux --python "$PYTHON" \
  --app-dir "$LOCAL/app" --database "$LOCAL/data/linux.db" --exchange "$EXCHANGE" \
  --output "$UNIT" --interval 60
systemd-analyze --user verify "$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now agentmesh.service
systemctl --user status agentmesh.service
journalctl --user -u agentmesh.service
```

This is rootless user-session configuration, not a systemwide unit. A working systemd user manager/session is required; services may stop on logout. No lingering is assumed or enabled. On systems without a user manager, use foreground operation or a locally approved supervisor. Stop with `systemctl --user disable --now agentmesh.service`.

The generator only writes configuration; it never invokes a shell, registers services, or contacts a network. It rejects relative paths and control characters, quotes spaces/backslashes/quotes, escapes systemd percent specifiers, and disables environment expansion in ExecStart. Output creation is exclusive (no overwrite) and mode 0600; remove an old configuration explicitly after stopping its service if regenerating.

## Identity and validation limits

Currently only one logical node per `mac`, `windows`, and `linux` is supported in a sync group. Do not clone local state or run the same node identity on multiple machines. Windows setup remains documented separately in README-WINDOWS.md.

Tests exercise argument fidelity, plist parsing, validation, private output, and both wrappers on macOS. These tests are **not** Linux-host systemd validation. Verify the generated Linux unit and worker on your actual Linux host before relying on persistent operation.
