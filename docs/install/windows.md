# Windows

Install AgentMesh on Windows from a supplied private bootstrap. Complete the [prerequisites](../getting-started/prerequisites) first, including Python and the accepted exchange folder. You will have a local database and foreground sync loop to check on the Windows host.

::: info
This installs the AgentMesh memory-sync application, not Hermes or other agents.
:::

## Before you begin

Complete the [prerequisites](../getting-started/prerequisites). Keep Syncthing running and wait for the exchange folder to show **Up to Date**. In Command Prompt, check that Python 3.10 or later is available:

```bat
py -3 --version
```

Output (example from code)

`Python` followed by the interpreter version, exit `0`. Confirm it is at least 3.10 and run the full [FTS5/JSON check](../getting-started/prerequisites#install-and-check-python); the launcher does not check those capabilities or Syncthing.

::: warning
Install with `--no-ingest` to keep local transcript ingestion disabled while reviewing source ownership and roots. The supplied snapshot is still imported and synchronization starts. Without this flag, a fresh bootstrap enables default ingestion immediately, including with `--once`. Then [review roots and enable ingestion](../use/ingest#before-the-first-bootstrap-cycle).
:::

## Run the installer

From Command Prompt in the supplied exchange folder, run `START-WINDOWS.cmd --no-ingest`. Double-clicking the launcher supplies no flag and enables ingestion on a fresh installation. Do not unzip the private bootstrap yourself.

Alternatively, run this from that folder in Command Prompt:

```bat
py -3 bootstrap_windows.py --no-ingest --exchange .
```

Output (example from code)

Checksum/import stages, `Ready: local database installed; starting synchronization`, then cycle JSON and a countdown. Errors or `Sync needs attention` require investigation.

For installation followed by only one sync pass:

```bat
py -3 bootstrap_windows.py --no-ingest --exchange . --once
```

Output (example from code)

Installation progress and one cycle JSON, then exit `0` on success or `1` for [transient or permanent cycle failure](../use/worker#respond-to-failures-and-timeouts). Read `failure_kind`, `warning` and the sync counters as well as the exit code.

## Use a different device identity

The platform setup wrapper uses the Python installer's `--node windows` default; that device identity must be unused in this group. If another device already uses it, choose a different [device identity](../getting-started/concepts#device-identity) with the cross-platform Python installer from the supplied setup directory:

```bat
py -3 bootstrap_windows.py --no-ingest --exchange "C:\path\to\exchange" --local-dir "C:\path\to\installation" --node mac
```

Output (example from code)

Progress followed by cycle reports, or a nonzero setup failure. The example selects an alternative identity; confirm it is unused before proceeding. Keep that same selection for future bootstrap updates. After reviewing and enabling ingestion in `workflow.json`, omit `--no-ingest` on restart; repeating it disables ingestion again. The database filename follows the selected identity, so use the database path reported in `data/runtime.json` rather than assuming the platform default below.

The same [bootstrap size limits](../getting-started/prerequisites#check-the-supplied-files) apply on this platform. Fresh packages contain the CLI modules used in this guide and write `runtime.json` owner-only; older installations must [re-bootstrap once](./update-remove#update-a-bootstrap-installation) from an updated package.

Treat the **whole installation root as private**. In addition to the database, it contains `app/baseline.jsonl.gz`, `app/bootstrap-manifest.json` and `data/baseline.jsonl`: the baseline contains memory and the manifest contains private snapshot metadata. Never share the installed `app` directory as a code bundle. Extracted files are now written owner-only before payload bytes are copied: mode `0600` on POSIX and private ACLs on Windows. Rerunning bootstrap replaces files and repairs older permissive permissions. Keep the whole root private; restricted permissions do not make `app` safe to share.

## What gets installed

| Item | Default path |
| --- | --- |
| Application | `%LOCALAPPDATA%\AgentMesh\app` |
| Local database | `%LOCALAPPDATA%\AgentMesh\data\windows.db` |
| Runtime paths | `%LOCALAPPDATA%\AgentMesh\data\runtime.json` |

The installer checks the package and snapshot, preserves an existing matching database, and starts a sync loop with a default 60-second wait after each completed cycle.

## Keep the window open

Closing the window stops memory import/export, even if Syncthing continues transferring files. For the default installation, rerun **START-WINDOWS.cmd** after login. If you selected a custom local directory or device identity, rerun the Python installer with the same explicit `--local-dir` and `--node` values from [your installation command](#use-a-different-device-identity). The launcher does not remember those custom values. Open a second terminal for checks while the worker occupies the first. This launcher does not configure autostart; [sign-in startup](./background-service#windows-sign-in-start) requires separate program installation.

## Reading the progress line

For a 40,000-row import, the progress renderer can show this line, followed later by a sync countdown:

Output (example from code)

```text
[4/6] Importing 40,000 rows into SQLite | [############--------] 60% | 24,000/40,000 rows | elapsed 0s
Last sync succeeded | Next sync in 42s
```

Your counts and timing will differ. Unknown-duration work uses a spinner and elapsed time. An existing database skips the import stage. Worker JSON goes to stdout; human-readable worker progress goes to stderr.

## Updating

For this bootstrap-based setup, wait for Syncthing to finish, stop the current worker using its foreground, managed or service controls, and rerun **START-WINDOWS.cmd** only for the default installation. For a custom local directory or device identity, repeat the Python installer command with the same explicit `--local-dir` and `--node` values, even if only the directory changed. It loads the supplied application while keeping your matching database. Separately installed program bundles use the [Windows installation reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/WINDOWS-INSTALLATION.md), which also explains confirmation prompts.

::: warning
This guide does not establish operation on your Windows host. Verify the Python/SQLite checks, Syncthing connection through the host firewall, private local file permissions, successive worker cycles and the [A → B recall test](../getting-started/quick-start#prove-a-record-travels-from-a-to-b). If enabling sign-in startup, test a real sign-out/sign-in and reboot, then inspect worker cycles; task registration alone is insufficient.
:::

Next, [check sync status](../use/status) or [ingest a transcript](../use/ingest#windows-single-transcript). See the [Windows setup reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-WINDOWS.md) for details.
