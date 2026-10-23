# Insight dashboard

Use Insight to browse local memory and recorded operations without changing them. First [check your installation paths](./worker#inspect-the-installation-first) and have a repository checkout available to run the dashboard. You will open a local read-only workspace for memory, ingestion and sync observations.

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

Output (example from code)

JSON containing `url` on `127.0.0.1`, `pid` and `readonly: true`. The server chooses an available port. A bind error or missing URL is startup failure; an open page with unavailable sources means the server works but the data source needs checking. Open that URL locally. Stop the server with Ctrl-C.

## Point it at your data

Use `--sqlite` for an existing database. Insight reads everything else (job status, summary metrics, profile-log status, the frozen history of the retired cron digest) from the AgentMesh home, `--agentmesh-home` (default `$AGENTMESH_HOME`, otherwise `~/.agentmesh`). Missing files show as unavailable or empty:

```sh
python3 -u insight/server.py --port 0 \
  --sqlite "/path/to/node.db" --agentmesh-home "/absolute/path/to/agentmesh"
```

Output (example from code)

The local URL/PID/`readonly` startup JSON, then a read-only dashboard. Inspect Memory Browser connection availability; an absent database is not created or repaired.

Insight reads nothing from a Hermes profile. `--sqlite` does not create a database. Choosing a connection does not migrate memory or configure workers. To label a backend authoritative in Insight, configure its role explicitly; see the [Insight reference](https://github.com/theidentify/AgentMesh/blob/main/insight/README.md).

::: tip
Optional PostgreSQL access takes its DSN only from the `INSIGHT_PG_DSN` environment variable, never a CLI flag or a file in Git. See the [connection instructions](https://github.com/theidentify/AgentMesh/blob/main/insight/README.md#run-from-the-repository-root) for the required format and read-only setup.
:::

::: warning
Insight binds only to `127.0.0.1` and is not an authenticated network service. Do not expose it through a tunnel. Memory excerpts and screenshots remain private; redaction does not guarantee that prose contains no secrets.
:::

If a view shows something unexpected, start with [troubleshooting](../troubleshooting).
