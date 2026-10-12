# Prerequisites

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

Pair only trusted devices and accept the private exchange folder. It must contain the supplied setup files, bootstrap ZIP and checksum manifest. Wait until Syncthing reports **Up to Date** before installing. Do not unpack the bootstrap into the exchange yourself.

## Choose a node name

Use `mac`, `windows` or `linux`, with only one active device per identity in a sync group. The platform installers select the matching node. Do not copy another device's initialized database or local sync state.

Continue with [macOS](../install/macos), [Linux](../install/linux) or [Windows](../install/windows) installation. See the [setup reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-SETUP.md) for prerequisite details.
