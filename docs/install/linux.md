# Linux

Install AgentMesh on Linux so this device can recall and exchange memory. Complete the [prerequisites](../getting-started/prerequisites) and obtain a private bootstrap first. You will have a local database and foreground worker to verify on your Linux host.

::: info
This installs the AgentMesh memory-sync application, not Hermes or other agents.
:::

## Before you begin

Complete the [prerequisites](../getting-started/prerequisites) and wait for the private exchange folder to finish syncing.

::: warning
Before running a bootstrap installer, coordinate which device will ingest each source and check for identical absolute transcript paths across devices. A fresh bootstrap enables default ingestion and starts the worker immediately, including with `--once`. Review the [default roots and first-cycle boundary](../use/ingest#before-the-first-bootstrap-cycle) now.
:::

## Run the installer

Open a terminal in the supplied private exchange folder containing `setup_linux.sh` and `bootstrap_windows.py`. Confirm the assigned device identity is unused before running the wrapper:

```sh
sh setup_linux.sh "/absolute/path/to/exchange"
```

Output (example from code)

Numbered checksum/import stages, a `Ready: local database installed; starting synchronization` message, then cycle JSON and a countdown. A checksum/validation error or `Sync needs attention` is not success; inspect the error and [status](../use/status). The foreground loop keeps the terminal occupied.

An optional second argument selects the local installation directory. Keep it outside the exchange folder.

## Use a different device identity

The platform setup wrapper supplies `--node linux` by default; that device identity must be unused in this group. If another device already uses it, choose a different [device identity](../getting-started/concepts#device-identity) with the cross-platform Python installer from the supplied setup directory:

```sh
python3 bootstrap_windows.py --exchange "/absolute/path/to/exchange" \
  --local-dir "/absolute/path/to/installation" --node windows
```

Output (example from code)

Verified local state followed by worker cycle reports, or a nonzero failure before successful setup. Despite its filename, `bootstrap_windows.py` is cross-platform. The example selects an alternative identity; confirm it is unused before proceeding. Keep that same selection for future bootstrap updates. The database filename follows the selected identity, so use the database path reported in `data/runtime.json` rather than assuming the platform default below.

The same [bootstrap size limits](../getting-started/prerequisites#check-the-supplied-files) apply on this platform. Fresh packages contain the CLI modules used in this guide and write `runtime.json` owner-only; older installations must [re-bootstrap once](./update-remove#update-a-bootstrap-installation) from an updated package.

Treat the **whole installation root as private**. In addition to the database, it contains `app/baseline.jsonl.gz`, `app/bootstrap-manifest.json` and `data/baseline.jsonl`: the baseline contains memory and the manifest contains private snapshot metadata. Never share the installed `app` directory as a code bundle. Check access permissions across the entire root, including `app`; owner-only runtime permissions do not establish privacy for all extracted files.

## What gets installed

| Item | Default path |
| --- | --- |
| Application | `${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh/app` |
| Local database | `${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh/data/linux.db` |
| Runtime paths | `${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh/data/runtime.json` |

The installer verifies the bootstrap and preserves an existing database with the matching device identity and sync group. Use the paths recorded in `data/runtime.json` if you chose another location.

## The worker runs in the foreground

::: warning
The terminal stays occupied until you press Ctrl-C. Open a second terminal for status and recall checks. Stop this worker before enabling a background service to avoid duplicate workers.
:::

## Restart the worker later

For the wrapper's default installation; if you selected a different identity or location, substitute the paths from `data/runtime.json`:

```sh
LOCAL="${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh"
EXCHANGE="/absolute/path/to/exchange"
PYTHON="$(command -v python3)"
"$PYTHON" "$LOCAL/app/sync_worker.py" "$LOCAL/data/linux.db" "$EXCHANGE" --interval 60
```

Output (example from code)

Cycle JSON on stdout and progress/countdown on stderr. Inspect `failure_kind` and `warning`; a running loop can still be reporting failures. With `--once`, a successful cycle exits `0`, while [transient (retryable exchange) or permanent (review required) failure](../use/worker#respond-to-failures-and-timeouts) exits `1`.

Append `--once` to run one cycle and exit. These commands cover the basic setup; installations with signed sync enabled should use their configured managed worker.

::: warning
This guide does not establish operation on your Linux host. Check Python/SQLite, Syncthing connectivity, successive completed cycles without permanent failure, and the [A → B recall test](../getting-started/quick-start#prove-a-record-travels-from-a-to-b). If using a service, verify it after login and explicitly test whether it survives logout before relying on unattended operation.
:::

See the [setup reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-SETUP.md) for details.

Next, [check sync status](../use/status) on your Linux host.
