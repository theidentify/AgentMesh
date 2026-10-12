# Check sync status

Distinguish file delivery from completed record imports before relying on shared memory. Start with a [configured worker](./worker) and its database and runtime paths. You will be able to inspect local counts, peer reports and worker failures, then verify a record at its destination.

![Basic cycle: ingest supported sessions, export records, exchange packets, import peer packets and write a report; a conflict quarantines the whole packet.](/diagrams/worker-cycle.svg)

**Ingest** reads supported sessions when enabled. **Export** captures record changes by comparing local records with the last synchronized view; the **Exchange folder** carries their packets through independently running Syncthing. **Import** validates and applies whole packets or leaves them pending; **Report** publishes observations. The highlighted folder connects peers. The red box rejects silent overwrite: one conflicting row prevents every change in that packet from applying. When enabled, summarization runs after the initial import and before **Report**; committed summaries trigger another export/import cycle before reporting.

Open a second terminal if the foreground worker occupies the first. Commands below run from the installed `app` directory or checkout's `memory-sync` directory.

## Local status

```sh
python3 agentmesh.py --database "/path/to/memory.db" status
```

For an empty test database (exit `0`):

Output (run on macOS)

```json
{"counts":{"ingestion_cursors":0,"ingestion_errors":0,"memory_items":0,"memory_sources":0,"memory_summaries":0,"observation_events":0,"source_sessions":0,"summary_state":0},"sync":{"conflict":0,"invalid":0,"outbox":0,"pending":0,"received":0}}
```

This excerpt omits device/group and security metadata. Exit `0` means status was read, not that syncing succeeded. Omitting `--database` exits `2`; database errors exit `1`.

- `outbox` counts captured packets not yet published. It excludes local changes not captured by a worker cycle; `outbox: 0` does not mean nothing remains to send. Published outbox records are retained.
- `received` counts local packet receipts (accepted packet identifiers and checksums stored in the local database), not proof of two-way convergence.
- `pending`, `conflict` and `invalid` count diagnostic file paths, not rows. Run a cycle before interpreting incoming files: status itself does not scan or import them.
- Equal record counts do not prove equal contents. Verify a distinctive record by recall on the receiving device.

## Peer status files

The worker attempts to publish `status/<node>.json` in the private exchange folder; `<node>` is the database's node slot. These files report platform, counts, sync health and a timestamp. They can be stale or absent, especially when the exchange is unavailable. They include sync identifiers and may include filesystem paths through errors; keep raw reports private.

One-shot workers now exit `1` for both transient exchange failure and permanent failure. A status query can still exit `0` while showing conflicts. See the [worker failure table](./worker#respond-to-failures-and-timeouts), and inspect both the report and exit status.

## Diagnose a worker

```sh
python3 agentmesh.py diagnose --runtime "/path/to/runtime.json" --json
```

Before a managed record existed (exit `0`):

Output (run on macOS)

```json
{"read_only":true,"status":"ok","worker":{"cycles":0,"process_observation":"no_record","state":"stopped"}}
```

`ok` does not mean a worker is running. After a managed worker ran and stopped, diagnosis returned `status: attention`, a worker check with `code: WORKER_NOT_RUNNING`, and exit `1`. An invalid installation can fail an earlier check and skip worker inspection. Neither diagnosis nor inspection repairs state.

Omit `--json` for the human-readable report.

Output (example from code)

Staged check results, `Result: OK` or `Result: ATTENTION`, and limitations. Exit `0` is only for `ok`; all other report statuses exit `1`. For exchange retries, inspect `worker-status` too: the diagnostic projection does not expose the new `failure_kind`/`warning` fields and may stop at an unavailable-exchange installation check.

## Managed worker

```sh
python3 agentmesh.py worker-status --runtime "/path/to/runtime.json"
```

Before managed execution (exit `0`):

Output (run on macOS)

```json
{"cycles":0,"state":"stopped"}
```

After a transient one-shot failure, the observed excerpt was:

Output (run on macOS)

```json
{"state":"failed","cycles":1,"failure_kind":"transient","warning":"exchange unavailable","last_error":"ExchangeUnavailable"}
```

That one-shot instance has finished; a recurring managed loop instead remains running for transient failures. A permanent failure stops the managed loop. Use `last_error_detail` and [troubleshooting](../troubleshooting) to inspect the cause before restarting. Missing metadata does not account for unrelated foreground or service workers.

Next, perform the [A → B recall check](../getting-started/quick-start#prove-a-record-travels-from-a-to-b). To browse selected observations, open [Insight](./insight).
