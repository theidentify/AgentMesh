# Windows

::: info
This installs the AgentMesh memory-sync application, not Hermes or other agents.
:::

## Before you begin

Complete the [prerequisites](../getting-started/prerequisites). Keep Syncthing running and wait for the exchange folder to show **Up to Date**. In Command Prompt, check that Python 3.10 or later is available:

```bat
py -3 --version
```

## Run the installer

Double-click **START-WINDOWS.cmd** in the supplied exchange folder. Do not unzip the private bootstrap yourself.

Alternatively, run this from that folder in Command Prompt:

```bat
py -3 bootstrap_windows.py --exchange .
```

For installation followed by only one sync pass:

```bat
py -3 bootstrap_windows.py --exchange . --once
```

## What gets installed

| Item | Default path |
| --- | --- |
| Application | `%LOCALAPPDATA%\AgentMesh\app` |
| Local database | `%LOCALAPPDATA%\AgentMesh\data\windows.db` |
| Runtime paths | `%LOCALAPPDATA%\AgentMesh\data\runtime.json` |

The installer checks the package and snapshot, preserves an existing matching database, and starts a sync loop with a 60-second interval.

## Keep the window open

Closing the window stops memory import/export, even if Syncthing continues transferring files. Run the same CMD after login to resume. This launcher does not configure autostart; [sign-in startup](./background-service#windows-sign-in-start) requires separate program installation.

## Reading the progress line

For a 40,000-row import, the progress renderer can show the following line, followed later by a sync countdown:

```text
[4/6] Importing 40,000 rows into SQLite | [############--------] 60% | 24,000/40,000 rows | elapsed 0s
Last sync succeeded | Next sync in 42s
```

Your counts and timing will differ. Unknown-duration work uses a spinner and elapsed time. An existing database skips the import stage. Worker JSON goes to stdout; human-readable worker progress goes to stderr.

## Updating

For this bootstrap-based setup, wait for Syncthing to finish, stop the old worker with Ctrl-C, and run **START-WINDOWS.cmd** again. It loads the supplied application while keeping your matching database. Separately installed program bundles use the [Windows installation reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/WINDOWS-INSTALLATION.md), which also explains confirmation prompts.

::: warning
Native Windows production verification remains outstanding in the repository's documented boundary. Tests on macOS do not establish Windows command execution, firewall behavior or local permissions. Verify these on the Windows host.
:::

Next, [check sync status](../use/status) or [ingest a transcript](../use/ingest#windows-single-transcript). See the [Windows setup reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-WINDOWS.md) for details.
