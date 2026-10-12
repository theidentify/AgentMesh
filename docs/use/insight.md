# Insight dashboard

Insight is a local, read-only workspace for inspecting memory and operations. It does not run ingestion, summarization or sync jobs.

| View | What to inspect |
| --- | --- |
| Overview | Source availability and memory counts. |
| Metrics | Recorded summary usage and coverage. |
| Memory Browser | Memory items, summaries and linked evidence. |
| Agents | Observed agent session and event metadata. |
| Pipeline | Known ingestion and summary job observations. |
| Sync | Sync metadata, peer reports and transport observations. |

Missing sources are shown as unavailable. Historical observations and completed-cycle reports do not prove current liveness.

## Start it

From the repository root:

```sh
python3 -u insight/server.py --port 0
```

The server chooses an available port and prints JSON containing its `127.0.0.1` URL, PID and read-only flag. Open that URL locally. Stop the server with Ctrl-C.

## Point it at your data

Use an existing SQLite database and the runtime profile directory containing your metrics and job files:

```sh
python3 -u insight/server.py --port 0 \
  --sqlite /path/to/node.db --home /absolute/path/to/profile
```

`--home` defaults to `HERMES_HOME`, or `~/.hermes` if unset. `--sqlite` does not create a database. Choosing a connection does not migrate memory or configure workers. Authority labels require explicit backend configuration; see the [Insight reference](https://github.com/theidentify/AgentMesh/blob/main/insight/README.md).

::: tip
Optional PostgreSQL access takes its DSN only from the `INSIGHT_PG_DSN` environment variable, never a CLI flag or a file in Git. See the [connection instructions](https://github.com/theidentify/AgentMesh/blob/main/insight/README.md#run-from-the-repository-root) for the required format and read-only setup.
:::

::: warning
Insight binds only to `127.0.0.1` and is not an authenticated network service. Do not expose it through a tunnel. Memory excerpts and screenshots remain private; redaction does not guarantee that prose contains no secrets.
:::

If a view shows something unexpected, start with [troubleshooting](../troubleshooting).
