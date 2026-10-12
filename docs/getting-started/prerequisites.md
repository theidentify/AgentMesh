# Prerequisites

Prepare Python, Syncthing and local storage before installing AgentMesh. Read [Concepts](./concepts) first to understand device identity and the private exchange folder. You will be ready either to use a supplied bootstrap or to create an empty group for local use.

![Bootstrap setup: check prerequisites, pair devices, run the installer, keep the foreground worker open, then optionally configure background operation.](/diagrams/install-flow.svg)

**Prerequisites** means checking Python and Syncthing. **Pair devices** shares the private exchange folder. **Run the installer**, highlighted, verifies a supplied bootstrap and creates local state. The **Foreground worker** runs while its terminal stays open; stop it before **Background (optional)** setup. This diagram shows the bootstrap path; starting alone follows [New sync group](../install/new-group) instead.

## Install and check Python

Bootstrap installation requires Python 3.10+ with SQLite FTS5 and JSON support. `setup-new` requires Python 3.11+ because it uses `hashlib.file_digest`. Choose a supported Python 3.11+ release when preparing a new environment.

| Platform | Installation starting point |
| --- | --- |
| macOS | Use the [official macOS downloads](https://www.python.org/downloads/macos/) and reopen your terminal after installation. |
| Windows | Use the [official Windows downloads](https://www.python.org/downloads/windows/); confirm `py -3` selects the intended interpreter. |
| Linux | Follow [Python's Unix installation guidance](https://docs.python.org/3/using/unix.html) for your distribution; verify the interpreter's SQLite capabilities below. |

On macOS/Linux, run:

```sh
python3 -c "import sys,sqlite3; c=sqlite3.connect(':memory:'); c.execute('create virtual table t using fts5(x)'); print(sys.version.split()[0]); print('FTS5: ok'); print('JSON:',c.execute(\"select json_valid('{}')\").fetchone()[0])"
```

For macOS (exit `0`):

Output (run on macOS)

```text
3.14.8
FTS5: ok
JSON: 1
```

Your version can differ; it must meet the minimum for your route. A missing command, traceback or missing success lines means this interpreter is not ready. Install a Python build with SQLite FTS5/JSON support from the platform links above, then rerun this check with its explicit interpreter path. Reuse that interpreter for AgentMesh and service configuration; a different Python on PATH can have a different SQLite build. AgentMesh has no command to repair an interpreter’s SQLite capabilities. If the replacement still fails, report the Python version, platform and failing capability without private paths.

On Windows, use the [Command Prompt version of this check](./quick-start#check-install-and-query).

Output (example from code)

A suitable version, `FTS5: ok`, `JSON: 1`, and exit `0`. The macOS/Linux setup wrappers perform prerequisite checks; **START-WINDOWS.cmd only finds a runnable Python command**. It does not verify the minimum version, FTS5, JSON or Syncthing for you.

## Install and start Syncthing

Use the [official Syncthing downloads](https://syncthing.net/downloads/) for macOS, Windows or Linux, or the linked distribution packages. Keep `syncthing` on PATH for the macOS/Linux wrappers. Start it, then open its local management GUI at `http://127.0.0.1:8384/` unless you configured another address. A reachable GUI confirms the local service is available, not that peer files have arrived. Choose startup behavior separately using [Syncthing's autostart documentation](https://docs.syncthing.net/users/autostart.html).

## Share the private exchange folder

For a supplied bootstrap, the person administering the group must assign an unused AgentMesh device identity. Check their device list and existing runtime inspections; an offline device still owns its identity. Syncthing device IDs are separate from AgentMesh device identities, and missing status files do not prove an identity is free.

1. On each device, open **Actions → Show ID**. Exchange those Syncthing IDs through a trusted channel.
2. Use **Add Remote Device** on both sides and enter the other device's ID.
3. On the supplying device, add the private exchange folder and select the intended peer in its sharing settings.
4. On the receiving device, accept the offered folder and choose a private local folder path outside AgentMesh's application and database directories.
5. Wait for the folder to show **Up to Date** and resolve any folder errors before installing.

Use a dedicated exchange folder for each sync group; a group UUID does not partition a shared folder. For a first device starting alone, add a new local folder in Syncthing; no remote invitation is required. Syncthing creates its `.stfolder` marker. Do not manufacture a marker to bypass an unavailable folder. See the [official getting-started guide](https://docs.syncthing.net/intro/getting-started.html) for the GUI steps and [folder settings](https://docs.syncthing.net/users/config.html#folder-element) for details.

## Check the supplied files

A bootstrap exchange contains `AgentMesh-bootstrap-v1.zip`, its matching `agentmesh-package.json`, `bootstrap_windows.py`, `setup_macos.sh`, `setup_linux.sh` and `START-WINDOWS.cmd`, plus supporting files such as `brand.py`, `terminal_progress.py` and `assets/`. Keep the supplied set together; do not unzip the bootstrap yourself. The installer compares the ZIP's hash with the manifest, then checks the snapshot hash.

Two separate limits apply: the total uncompressed size of all ZIP members must not exceed **200,000,000 bytes**, and the decompressed baseline must not exceed **200,000,000 bytes**. Oversized material fails with `unsafe archive size` or `unsafe baseline size`; see [troubleshooting](../troubleshooting#bootstrap-is-too-large). Free disk space alone does not remove these limits.

Next, follow [Quick start](./quick-start) if you received a bootstrap, or [New sync group](../install/new-group) if you are starting alone.
