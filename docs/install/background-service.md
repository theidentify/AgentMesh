# Run in the background

Keep AgentMesh running without a foreground terminal by configuring startup for your platform. Complete [installation](../getting-started/quick-start) and stop the existing worker first. You will have a service or sign-in launcher whose worker health you can verify separately.

::: info
These steps configure background operation for the AgentMesh memory-sync application. They do not install Hermes or other agents.
:::

## Before you enable a service

Stop the foreground worker with Ctrl-C first. Use the installed paths from `data/runtime.json`, and an absolute Python interpreter path available at login. The macOS and Linux service examples below are for unsigned sync and run from the supplied setup source directory. For an installation with signed sync required, use a service configuration that starts the [configured managed worker](../use/worker#use-managed-execution); these generated examples do not supply signing configuration. Their database filenames match the wrapper defaults; if you selected another device identity, use the database path from your runtime instead.

The generator writes a private configuration file but does not register it. It refuses to overwrite an existing file. Inspect the interpreter, script, database, exchange, interval and log paths in the generated file, and ensure they match this installation before running the registration commands; stop an old service before explicitly removing its configuration to regenerate it.

## Unlock a protected key before unattended startup

For signed sync with a passphrase-protected device key, [enable and verify OS unlock](./security#enable-unattended-os-unlock) under the account that runs the configured managed worker before enabling login startup. A service does not inherit an unlock from `wizard-resume`, and workers never ask for a passphrase. Without a working OS-unlock path they refuse before sync writes; there is no plaintext or unsigned fallback.

The supported backends are the logged-in macOS user’s login keychain and Windows Credential Manager for the task’s user. Linux has no OS-unlock backend. These are user-session mechanisms, not a guarantee of operation before login or after logout. OS-store access is attempted at most once per identity per worker process, including a refusal: fix account/store access and restart the worker. Disabling OS unlock does not clear keys already held by running processes; stop workers before changing policy. The macOS/Linux generated service examples below remain **unsigned** examples and do not configure signing or unlock.

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

Output (run on macOS)

The generator exits `0` with no stdout and `plutil` prints `/path/to/agentmesh.plist: OK`. A generator error (exit `2`) or failed lint is a stop condition. Lint checks syntax, not that the selected paths are correct.

After inspecting it, load the service and check it:

```sh
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl print "gui/$(id -u)/org.agentmesh.sync"
```

Output (example from code)

`print` should find the loaded service with the intended program arguments. An OS command failure or repeated failed cycles is not successful setup; inspect private logs and perform the A → B test.

The LaunchAgent runs at login/loading and restarts the worker. Private logs are `agentmesh.stdout.log` and `agentmesh.stderr.log` in the local `data` directory. To stop and unload it for the current login session, run the following command. To prevent it from loading at a later login, also move the plist out of the LaunchAgents directory after unloading it:

```sh
launchctl bootout "gui/$(id -u)" "$PLIST"
```

Output (example from code)

`bootout` unloads this exact service. Verify it is no longer loaded and no worker from it remains; a failed command does not establish that it stopped.

### Name shown in Login Items

macOS shows these Python-based agents as "python" in App Background Activity / Login Items. For agents using a virtual environment, preview the name change from the repository root, then apply it:

```sh
python3 memory-sync/mac_launch_name.py
python3 memory-sync/mac_launch_name.py --apply
```

The helper adds an `AgentMesh` link next to each venv's Python interpreter and updates the plist to use it. It reloads only units launchd has loaded; unloaded units remain unloaded. Use `--no-reload` with `--apply` to update files only. Re-run the helper after re-rendering a plist. To switch back to the venv's `python`, run `python3 memory-sync/mac_launch_name.py --revert --apply`.

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

Output (run on macOS)

The generator exits `0` with no stdout and writes a unit with the selected `ExecStart` arguments.

Output (example from code)

The `systemd-analyze` step should verify the unit successfully. Errors require correction before enabling.

After inspecting it, enable the service and view its status and logs:

```sh
systemctl --user daemon-reload
systemctl --user enable --now agentmesh.service
systemctl --user status agentmesh.service
journalctl --user -u agentmesh.service
```

Output (example from code)

Status should identify the intended service as active and logs should show recurring successful cycles. A failed/inactive service or recurring cycle errors requires investigation. Registration alone is not a memory-delivery test.

To stop and disable it:

```sh
systemctl --user disable --now agentmesh.service
```

Output (example from code)

The service stops and is disabled for future startup. Check status again; a failed disable operation is not evidence of stopped execution.

::: warning
Linux-host service verification remains outstanding. This requires a working systemd user session and may stop on logout. This guide configures login-session operation only; no lingering is enabled. For SSH/headless operation that must survive logout, have the host administrator configure the user manager lifetime deliberately using the [systemd loginctl reference](https://github.com/systemd/systemd/blob/main/man/loginctl.xml), then test logout and reboot. Do not assume an active SSH session proves unattended operation.
:::

## Windows sign-in start

First install the standalone Windows program for your existing database and runtime using the [Windows installation reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/WINDOWS-INSTALLATION.md). Bootstrap alone does not create the program installation records required by autostart. The standalone program bundle is a separate code distribution, not the private bootstrap ZIP; its source/download and installation remain operator procedures. Confirm all prior workers have stopped before enabling it.

For an existing basic, unsigned installation, run this read-only preview from Command Prompt:

```bat
py -3 "%LOCALAPPDATA%\AgentMesh\app\agentmesh.py" windows-autostart --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --enable --legacy-drained --dry-run
```

Output (example from code)

JSON with `status: planned`, exit `0`, and no changes. An error blocks progress; a successful dry run does not execute the later stopped-worker precheck.

After reviewing the preview, enable it interactively:

```bat
py -3 "%LOCALAPPDATA%\AgentMesh\app\agentmesh.py" windows-autostart --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --enable --legacy-drained
```

Output (example from code)

A confirmation prompt, then `status: enabled` and exit `0` after successful task registration. Cancel returns exit `2`; errors return `1`. This is not proof of a running worker.

`--legacy-drained` explicitly acknowledges that prior legacy workers have stopped. For an installation with signed sync required, omit `--legacy-drained` from the enable command; missing managed-worker metadata needs a separate `--unmanaged-drained` acknowledgment after confirming source workers have exited. Follow the [startup prerequisites and confirmations](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/WINDOWS-INSTALLATION.md#opt-in-start-at-login) for your installation.

Enabling schedules the installed program for the next matching user sign-in; it does not start a worker immediately. Disable the launcher with:

```bat
py -3 "%LOCALAPPDATA%\AgentMesh\app\agentmesh.py" windows-autostart --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --disable
```

Output (example from code)

After confirmation, `status: disabled`, exit `0`. Cancel/error is not successful removal of startup. Disabling does not stop an already running worker. These commands use the runtime to find the database, so `--database` is unnecessary.

::: warning
Actual Windows sign-in behavior remains a separate host-verification step. A scheduled task's state alone does not establish worker health; [check the worker](../use/status).
:::

For advanced configuration, including PostgreSQL mirroring, see the [setup reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-SETUP.md).

Next, [check sync status](../use/status) to verify that the worker completes cycles.
