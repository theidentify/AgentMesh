# Prerequisites

Prepare Python, Syncthing and a private exchange before installing AgentMesh. Read [Concepts](./concepts) first so you can choose a device identity without duplicating another writer. You will finish with the prerequisites needed for bootstrap installation or a new empty group.

![Five steps: prerequisites, pair devices, run the installer, foreground worker, optional background service. Unzipping the bootstrap by hand is not allowed.](/diagrams/install-flow.svg)

Follow **Prerequisites** by preparing Python and Syncthing, then **Pair devices** and accept the private exchange. **Run the installer**, highlighted, verifies the bootstrap and creates local state. The **Foreground worker** ingests and exchanges records while its window stays open. For **Background (optional)** operation, stop the foreground worker before configuring startup. Do not unzip the bootstrap by hand; let the installer perform its checks.

## Python

Install Python 3.10 or later with SQLite FTS5 and JSON support. On macOS and Linux, put `python3` on PATH. Check FTS5 and print the SQLite version:

```sh
python3 -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('create virtual table t using fts5(x)'); print(sqlite3.sqlite_version)"
```

Check JSON support too:

```sh
python3 -c "import sqlite3; c=sqlite3.connect(':memory:'); print(c.execute(\"select json_valid('{}')\").fetchone()[0])"
```

The JSON check prints `1`. On Windows, use `py -3` in place of `python3`. The setup wrappers check these prerequisites; they do not install Python.

## Syncthing

Install Syncthing separately, put `syncthing` on PATH, and keep it running. Keep local application and database storage outside the exchange folder.

## Pair devices and accept the exchange folder

Pair only trusted devices and accept the private exchange folder. For bootstrap installation, it must contain the supplied setup files, bootstrap ZIP and checksum manifest. A [new empty group](../install/new-group) needs an accepted exchange but no bootstrap. Wait until Syncthing reports **Up to Date** before installing. Do not unpack the bootstrap into the exchange yourself.

## Choose a device identity

Choose an unused [device identity](./concepts#device-identity) in the group. Platform wrappers choose a default label, which may already belong to another device running the same OS. In that case use the Python installer with an explicit unused identity as described on each install page. Do not copy another device's initialized database or local sync state.

Continue with [macOS](../install/macos), [Linux](../install/linux) or [Windows](../install/windows) installation. See the [setup reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-SETUP.md) for prerequisite details.
