# Control the worker

Run one sync cycle, keep a foreground loop open, or control a managed worker so memory continues moving between devices. First complete [installation](../getting-started/quick-start) and locate your database, exchange and runtime paths. You will be able to start, inspect and stop the appropriate worker without running duplicate loops.

## Inspect the installation first

Run these commands from the installed `app` directory or the repository's `memory-sync` directory. On Windows with Python installed, use `py -3` in place of `python3`. For a standalone program installation, invoke the selected installed AgentMesh executable in place of `python3 agentmesh.py`, keeping the same action and arguments; this starts the selected program version rather than an older source copy.

```sh
python3 agentmesh.py inspect-install --runtime /path/to/runtime.json
```

Inspection is read-only and does not create missing state. Its output includes private paths and group metadata; do not publish it. Pass the runtime explicitly, especially for custom installation locations. `inspect-install` and managed lifecycle actions do not require a global `--database` argument.

Stop any existing foreground loop or [background service](../install/background-service) before starting another worker. Managed status cannot prove that unrelated source workers have stopped.

## Run a basic unsigned cycle or loop

For a basic unsigned installation, choose one command:

```sh
python3 agentmesh.py --database /path/to/memory.db \
  --exchange /absolute/path/to/exchange once
```

```sh
python3 agentmesh.py --database /path/to/memory.db \
  --exchange /absolute/path/to/exchange watch --interval 60
```

`once` runs a cycle and exits; `watch` repeats in the foreground until Ctrl-C. Both require the database and exchange. The loop interval defaults to 60 seconds. They use `workflow.json` beside the database, unless you supply global `--config /path/to/workflow.json` before the action. Ingestion and summarization depend on that configuration; an empty group starts with both disabled.

`once` forces the configured summary schedule, while managed startup does not. It returns failure if the cycle report records errors, conflicts or invalid packets. Inspect [status](./status) as well as the exit code.

::: warning
The unified `once` and `watch` actions do not accept a signing-directory option. Use the configured managed lifecycle below for signed installations; do not use an unsigned command to bypass required policy.
:::

## Run a managed foreground worker

For an unsigned installation, after confirming the prior worker has stopped:

```sh
python3 agentmesh.py worker-run --runtime /path/to/runtime.json --legacy-drained
```

Add `--once` for a single managed cycle. `--interval` changes the loop interval, whose default is 60 seconds. For a signed installation, omit `--legacy-drained`; its runtime must identify the existing matching security directory. The flag acknowledges that old unsigned execution was drained and unsigned policy is intended; it does not stop another worker.

## Start, inspect and stop a managed worker

For unsigned policy, after the same drain check:

```sh
python3 agentmesh.py worker-start --runtime /path/to/runtime.json --legacy-drained
python3 agentmesh.py worker-status --runtime /path/to/runtime.json
python3 agentmesh.py worker-stop --runtime /path/to/runtime.json
```

Run these as separate steps when you need each operation. For signed policy, omit `--legacy-drained` from startup. `worker-start` launches a detached process and waits for its first healthy cycle; it does not register login startup. `worker-stop` requests a cooperative stop and waits for the lifetime lock to drain rather than killing a PID from a file. Startup and stop each default to a 60-second timeout, adjustable with `--timeout`.

Missing metadata can report stopped even when an unrelated worker exists. Stale or corrupt state needs investigation; a status result alone is not permission to launch duplicates. See the [managed lifecycle reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/MAC-REPLACEMENT.md#managed-lifecycle-and-adoption-without-service-replacement) and [CLI implementation](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/agentmesh.py).

Next, [check sync status](./status) to confirm cycles are completing and records are being imported.
