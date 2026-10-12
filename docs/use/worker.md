# Control the worker

Choose one worker for the installation so memory can be processed without duplicate loops. Start with a [bootstrap installation](../getting-started/quick-start) or [local empty group](../install/new-group), and locate its runtime and database paths. You will be able to run a cycle, inspect failures and start or stop managed execution.

## Choose one mode

For a fresh bootstrap, keep its foreground worker open and use a **second terminal** for checks. For an empty group, start with manual ingestion and one cycle. Switch to managed execution only after stopping the previous foreground worker or [service](../install/background-service).

| Mode | Use | Stop control |
| --- | --- | --- |
| `once` | One unsigned cycle | Exits after the cycle |
| `watch` / bootstrap worker | Unsigned foreground loop | Ctrl-C in its terminal |
| `worker-run` | Managed foreground loop; supports configured signed sync | Ctrl-C or `worker-stop` |
| `worker-start` | Managed detached loop | `worker-stop` |
| OS service | Startup managed by the OS | The service's own controls |

These commands run from the installed `app` directory or checkout's `memory-sync` directory. Older bootstrap installations need a freshly built package and one [re-bootstrap](../install/update-remove#update-a-bootstrap-installation) to receive all CLI modules. On Windows, replace `python3` with `py -3`. The multiline examples below use POSIX shell syntax; in Command Prompt, enter each complete command on one line without the trailing backslashes. For a standalone installation, use the selected installed executable instead of `python3 agentmesh.py`.

## Inspect the installation first

```sh
python3 agentmesh.py inspect-install --runtime "/path/to/runtime.json"
```

For an empty group (exit `0`):

Output (run on macOS)

```json
{"status":"ready","policy":"legacy","identity":"absent"}
```

Full output includes private paths and group metadata; keep it private. `ready` confirms inspection, not worker health. `not_found` can also exit `0`; errors exit `1`. Runtime-based actions do not require global `--database`.

## Run a basic unsigned cycle or loop

With no other worker running:

```sh
python3 agentmesh.py --database "/path/to/memory.db" \
  --exchange "/absolute/path/to/exchange" once
```

After one synthetic record was ingested, exit `0`:

Output (run on macOS)

```json
{"cycle":{"capture":{"changes":2,"packets":1},"publish":{"published":1,"unavailable":false},"receive":{"applied":0,"conflict":0,"invalid":0,"pending":0,"unavailable":false}},"failure_kind":null,"warning":null}
```

The two changes were the event and its ingestion cursor, the database record tracking the transcript file identity and the byte/line position already consumed. Counts vary. Exit `0` means no classified failure; it does not prove a peer received the record. With the exchange marker unavailable, the same command exited `1` and returned this excerpt:

Output (run on macOS)

```json
{"cycle":{"receive":{"unavailable":true}},"failure_kind":"transient","warning":"exchange unavailable"}
```

For a repeating foreground loop instead:

```sh
python3 agentmesh.py --database "/path/to/memory.db" \
  --exchange "/absolute/path/to/exchange" watch --interval 60
```

Output (example from code)

Each cycle prints JSON, with progress and a countdown on stderr. Successful cycles have no failure classification; unavailable exchange reports `failure_kind: transient`. The loop continues after transient **and permanent reported cycle failures** until Ctrl-C; continued execution is not proof of success.

Both actions read `workflow.json` beside the database unless global `--config "/path/to/workflow.json"` selects another file. When summarization is enabled, `once` requests it immediately rather than waiting for its scheduled time; it does not enable disabled summarization. `watch`, the bootstrap loop and the generated macOS/Linux services also force **legacy** summarization on their first cycle in each process, so restarting can call the provider earlier than its scheduled interval. Managed startup follows the configured schedule. These differences have no effect when summarization is disabled. Ingestion is also conditional on workflow settings.

## Use managed execution

For an unsigned installation, first stop all prior workers. Then:

```sh
python3 agentmesh.py worker-run --runtime "/path/to/runtime.json" --legacy-drained
```

Output (example from code)

This holds the terminal and records managed state; it does not print the full cycle JSON. Inspect it from a second terminal. A completed `--once` run was observed to exit `0` without stdout; an unavailable-exchange `--once` run exited `1`. Without `--once`, transient exchange failures are retried, while permanent failures stop the worker with exit `1`.

For signed sync, omit `--legacy-drained`. The worker resolves explicit security paths or the same defaults as the wizard; no manual runtime patch is needed for a standard bootstrap. It validates existing signing identity and policy. `--legacy-drained` only acknowledges that prior unsigned execution stopped; it does not stop a worker or change policy.

For a detached worker instead:

```sh
python3 agentmesh.py worker-start --runtime "/path/to/runtime.json" --legacy-drained
```

With the exchange unavailable (exit `0`):

Output (run on macOS)

```json
{"state":"running","failure_kind":"transient","warning":"exchange unavailable","last_error":"ExchangeUnavailable"}
```

Startup accepts a completed transient cycle with a warning. This proves the instance started, **not** that sync succeeded. Normal operation has increasing `cycles`, null `failure_kind` and no `last_error`. Startup does not register login startup.

```sh
python3 agentmesh.py worker-status --runtime "/path/to/runtime.json"
```

Before any managed worker existed (exit `0`):

Output (run on macOS)

```json
{"cycles":0,"state":"stopped"}
```

This means no managed record exists, not that every possible worker is stopped. With a record, inspect `state`, `cycles`, `failure_kind`, `warning` and `last_error_detail`. Status exit `0` means the query worked, even for a failed worker.

```sh
python3 agentmesh.py worker-stop --runtime "/path/to/runtime.json"
```

Output (run on macOS)

`"state": "stopped"`, exit `0`. Stop requests cooperation and waits for the worker to release its lifetime lock; it never kills a PID read from metadata. Missing metadata also returns stopped, so use the original terminal or service controls for unmanaged execution.

## Respond to failures and timeouts

| Result | Meaning and next action |
| --- | --- |
| `failure_kind: transient` | Exchange read/write or status publication is unavailable. Restore the accepted folder, mount, permissions and Syncthing availability; a managed loop retries. |
| `failure_kind: permanent` | Conflict, invalid packet, ingestion/summary failure or another non-retryable error. A managed loop stops; inspect [status and diagnostics](./status), fix the cause and review retained diagnostics before restarting. |
| `pending > 0` only | Dependencies are missing; this alone is not classified as failure. Investigate missing packets if they do not arrive. |

A peer being offline does not pause local ingestion while this device’s exchange folder remains accessible. If the exchange folder or `.stfolder` is unavailable, the cycle returns before **automatic ingestion and summarization**; local recall can therefore become stale even while a managed loop remains running. [Manual ingestion](./ingest#use-a-custom-location) remains possible for sources owned by this device. After the folder returns, check ingestion errors and inserted counts as well as sync results. Other transient errors later in a cycle do not necessarily mean ingestion was skipped.

All worker one-shot paths (`agentmesh.py once`, `worker-run --once`, `sync_worker.py --once`, bootstrap `--once`) exit `1` for transient or permanent failure. Low-level `memory_sync.py once` retains different exit semantics; do not use it as a substitute health check.

The default interval is a 60-second **wait after each cycle**. Use positive finite interval and timeout values. Managed start/stop each default to a 60-second timeout. Startup timeout requests a stop but does not wait for it to finish; stop timeout likewise does not establish that the worker exited. Inspect `worker-status` and [diagnose](./status#diagnose-a-worker), allow the current cycle to finish, and confirm stopped execution before retrying startup, update or removal. Only then consider a longer `--timeout`. If the original worker cannot be identified through its terminal or service configuration, preserve state and request help rather than guessing which PID to kill.

Next, [check sync status](./status) and verify the actual record on the receiving device.
