# macOS

Install AgentMesh on macOS so this device can recall and exchange memory. Complete the [prerequisites](../getting-started/prerequisites) and obtain a private bootstrap first. You will have a local database and a foreground worker, ready for a status check.

::: info
This installs the AgentMesh memory-sync application, not Hermes or other agents.
:::

## Before you begin

Complete the [prerequisites](../getting-started/prerequisites) and wait for the private exchange folder to finish syncing.

## Run the installer

From the supplied setup directory:

```sh
sh setup_macos.sh "/absolute/path/to/exchange"
```

An optional second argument selects the local installation directory. Keep it outside the exchange folder.

## Use a different device identity

The launcher's default identity must be unused in this group. If another device already uses it, choose a different [device identity](../getting-started/concepts#device-identity) with the cross-platform Python installer from the supplied setup directory:

```sh
python3 bootstrap_windows.py --exchange /absolute/path/to/exchange \
  --local-dir /absolute/path/to/installation --node windows
```

The example selects an alternative identity; confirm it is unused before proceeding. Keep that same selection for future bootstrap updates. The database filename follows the selected identity, so use the database path reported in `data/runtime.json` rather than assuming the platform default below.

## What gets installed

| Item | Default path |
| --- | --- |
| Application | `$HOME/Library/Application Support/AgentMesh/app` |
| Local database | `$HOME/Library/Application Support/AgentMesh/data/mac.db` |
| Runtime paths | `$HOME/Library/Application Support/AgentMesh/data/runtime.json` |

The installer verifies the bootstrap and preserves an existing database with the matching device identity and sync group. Use the paths recorded in `data/runtime.json` if you chose another location.

## The worker runs in the foreground

::: warning
The terminal stays occupied until you press Ctrl-C. Stop this worker before enabling a background service to avoid duplicate workers.
:::

## Restart the worker later

For the wrapper's default installation; if you selected a different identity or location, substitute the paths from `data/runtime.json`:

```sh
LOCAL="$HOME/Library/Application Support/AgentMesh"
EXCHANGE="/absolute/path/to/exchange"
PYTHON="$(command -v python3)"
"$PYTHON" "$LOCAL/app/sync_worker.py" "$LOCAL/data/mac.db" "$EXCHANGE" --interval 60
```

Append `--once` to run one cycle and exit. These commands cover the basic setup; installations with signed security should use their configured managed worker.

## Next steps

[Run in the background](./background-service), [recall memory](../use/recall), or [check sync status](../use/status).

See the [setup reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-SETUP.md) for details.

Next, [check sync status](../use/status) before relying on this worker.
