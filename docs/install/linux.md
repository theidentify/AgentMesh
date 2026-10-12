# Linux

::: info
This installs the AgentMesh memory-sync application, not Hermes or other agents.
:::

## Before you begin

Complete the [prerequisites](../getting-started/prerequisites) and wait for the private exchange folder to finish syncing.

## Run the installer

From the supplied setup directory:

```sh
sh setup_linux.sh "/absolute/path/to/exchange"
```

An optional second argument selects the local installation directory. Keep it outside the exchange folder.

## What gets installed

| Item | Default path |
| --- | --- |
| Application | `${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh/app` |
| Local database | `${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh/data/linux.db` |
| Runtime paths | `${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh/data/runtime.json` |

The installer verifies the bootstrap and preserves an existing database with the matching node and sync group. Use the paths recorded in `data/runtime.json` if you chose another location.

## The worker runs in the foreground

::: warning
The terminal stays occupied until you press Ctrl-C. Stop this worker before enabling a background service to avoid duplicate workers.
:::

## Restart the worker later

For a default installation:

```sh
LOCAL="${XDG_DATA_HOME:-$HOME/.local/share}/AgentMesh"
EXCHANGE="/absolute/path/to/exchange"
PYTHON="$(command -v python3)"
"$PYTHON" "$LOCAL/app/sync_worker.py" "$LOCAL/data/linux.db" "$EXCHANGE" --interval 60
```

Append `--once` to run one cycle and exit. These commands cover the basic setup; installations with signed security should use their configured managed worker.

::: warning
Linux-host verification remains outstanding. Wrapper tests on macOS do not validate Linux systemd behavior; verify operation on your Linux host before relying on it.
:::

## Next steps

[Run in the background](./background-service), [recall memory](../use/recall), or [check sync status](../use/status).

See the [setup reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-SETUP.md) for details.
