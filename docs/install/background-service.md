# Run in the background

::: info
These steps configure background operation for the AgentMesh memory-sync application. They do not install Hermes or other agents.
:::

## Before you enable a service

Stop the foreground worker with Ctrl-C first. Use the installed paths from `data/runtime.json`, and an absolute Python interpreter path available at login. The macOS and Linux commands below cover the basic setup and run from the supplied setup source directory.

The generator writes a private configuration file but does not register it. It refuses to overwrite an existing file. Inspect the generated file before running the registration commands; stop an old service before explicitly removing its configuration to regenerate it.

## macOS LaunchAgent

Generate and validate the configuration:

```sh
LOCAL="$HOME/Library/Application Support/AgentMesh"
EXCHANGE="/absolute/path/to/exchange"
PYTHON="$(command -v python3)"
PLIST="$HOME/Library/LaunchAgents/org.agentmesh.sync.plist"
"$PYTHON" platform_service.py generate --platform macos --python "$PYTHON" \
  --app-dir "$LOCAL/app" --database "$LOCAL/data/mac.db" --exchange "$EXCHANGE" \
  --output "$PLIST" --interval 60
plutil -lint "$PLIST"
```

After inspecting it, load the service and check it:

```sh
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl print "gui/$(id -u)/org.agentmesh.sync"
```

The LaunchAgent runs at login/loading and restarts the worker. Private logs are `agentmesh.stdout.log` and `agentmesh.stderr.log` in the local `data` directory. To stop and unregister it in the same shell:

```sh
launchctl bootout "gui/$(id -u)" "$PLIST"
```

## Linux systemd user service

Generate and validate the unit:

```sh
LOCAL="${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh"
EXCHANGE="/absolute/path/to/exchange"
PYTHON="$(command -v python3)"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/agentmesh.service"
"$PYTHON" platform_service.py generate --platform linux --python "$PYTHON" \
  --app-dir "$LOCAL/app" --database "$LOCAL/data/linux.db" --exchange "$EXCHANGE" \
  --output "$UNIT" --interval 60
systemd-analyze --user verify "$UNIT"
```

After inspecting it, enable the service and view its status and logs:

```sh
systemctl --user daemon-reload
systemctl --user enable --now agentmesh.service
systemctl --user status agentmesh.service
journalctl --user -u agentmesh.service
```

To stop and disable it:

```sh
systemctl --user disable --now agentmesh.service
```

::: warning
Linux-host service verification remains outstanding. This requires a working systemd user session and may stop on logout. No lingering is assumed or enabled; verify behavior on your host.
:::

## Windows sign-in start

First complete the separate [existing-install program installation](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/WINDOWS-INSTALLATION.md). Bootstrap alone does not create the owned program state required by autostart. Confirm all prior workers have stopped before enabling it.

For an existing basic, unsigned installation, run this read-only preview from Command Prompt:

```bat
py -3 "%LOCALAPPDATA%\AgentMesh\app\agentmesh.py" windows-autostart --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --enable --legacy-drained --dry-run
```

After reviewing the preview, enable it interactively:

```bat
py -3 "%LOCALAPPDATA%\AgentMesh\app\agentmesh.py" windows-autostart --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --enable --legacy-drained
```

`--legacy-drained` explicitly acknowledges that prior legacy workers have stopped. For signed policy, omit it; missing managed-worker metadata needs a separate `--unmanaged-drained` acknowledgment after confirming source workers have exited. Follow the [startup prerequisites and confirmations](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/WINDOWS-INSTALLATION.md#opt-in-start-at-login) for your installation.

Enabling schedules the installed program for the next matching user sign-in; it does not start a worker immediately. Disable the launcher with:

```bat
py -3 "%LOCALAPPDATA%\AgentMesh\app\agentmesh.py" windows-autostart --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --disable
```

Disabling does not stop an already running worker. These commands use the runtime to find the database, so `--database` is unnecessary.

::: warning
Actual Windows sign-in behavior remains a separate host-verification step. A scheduled task's state alone does not establish worker health; [check the worker](../use/status).
:::

For advanced configuration, including PostgreSQL mirroring, see the [setup reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-SETUP.md).
