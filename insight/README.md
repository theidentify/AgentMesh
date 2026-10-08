# AgentMesh Insight

A local, read-only operational workspace integrated with AgentMesh. Its sidebar,
connection switcher, data browser and inspection drawer follow the operational-tool
layout of Redis Insight, without copying vendor assets or branding.

Python 3.10+ and the standard library are sufficient for SQLite and metrics.
The optional PostgreSQL reader uses the system **libpq** shared library through
`ctypes`; no Python database package or web framework is required. No external
fonts, scripts, analytics, model calls or provider credentials are used.

## Run from the repository root

```sh
python3 -u insight/server.py --port 0
```

The server prints its exact localhost URL and PID. It always binds `127.0.0.1`.
This starts an inspection UI, not an ingest job, summary worker or sync service.
Missing sources are labeled unavailable, not replaced with demonstration data.

For an existing staging database and an optional authoritative PostgreSQL source:

```sh
# Runtime sources must remain outside this repository.
export HERMES_HOME="/private/runtime/hermes"
export INSIGHT_PG_DSN="host=127.0.0.1 port=5432 dbname=memory user=readonly"
python3 -u insight/server.py --home "$HERMES_HOME" \
  --sqlite "/private/runtime/agentmesh/node.db" --port 0
```

Use a least-privilege PostgreSQL account. Supply a libpq **keyword/value** DSN
through the environment, not a command-line argument, browser form or Git file.
Use the operating system's password mechanism if authentication is needed.
`INSIGHT_LIBPQ` can point to an installed libpq shared library if automatic
library discovery is insufficient. Connection and SQL timeouts are three seconds.

`--home` defaults to `HERMES_HOME`, otherwise `~/.hermes`. No SQLite database is
created when an optional path is absent or invalid. The connection picker keeps
**authority** separate from **comparison/staging**. Set
`INSIGHT_PRIMARY_BACKEND=sqlite` explicitly only after a verified local writer and
caller cutover; SQLite is then authority/default and PostgreSQL is read-only
comparison. Without that selector PostgreSQL remains authority and SQLite is
staging. Selecting a backend is not a migration and an unavailable source is
reported, never repaired by a write or a silent query fallback. The local SQLite
database supplies sync metadata independently of the memory browser selection.

## Views and sources

| View | Read-only source and interpretation |
| --- | --- |
| Overview | Connection read checks, item/summary counts, observed agents and cron executions. |
| Metrics | Existing summary accounting ported into `metrics.py`; separate historical, production-source bounded and isolated smoke histories, charts and coverage. |
| Memory Browser | Paginated `memory_items` and `memory_summaries` from the selected backend. Search content, exact project/scope key, kind, status and scope filters. |
| Agents | Aggregated `source_sessions` and `observation_events` metadata, plus content-free Hermes session metadata. Zero source-session rows do not hide agents with recorded events. |
| Pipeline | Exact known OMP ingest and summary cron job IDs, last execution and next scheduled time, coordinator window state, ingest counters/errors and summary checkpoints. |
| Sync | Existing AgentMesh `_sync_config`, `_sync_outbox`, `_sync_receipts`, `_sync_diagnostics` and optional `_agentmesh_worker_state` metadata. No packet payloads, group IDs, source paths, owners or diagnostic messages. |

Metrics reads `cron/usage_audit.jsonl`, `state.db`, and
`omp-memory/summary-metrics.jsonl`. Isolated smoke is read separately from
`omp-memory/summary-smoke-test-metrics.jsonl`. Jobs and coordinator state come from
`cron/jobs.json` and `omp-memory/.bounded_digest_state.json` beneath `--home`.
Known integration jobs are ingest `eabd6e41c22d` and summary `6fcf5646d34d`.
A different runtime without those IDs is explicitly reported as unavailable.

The canonical historical join and aggregate semantics are retained locally;
Insight does **not** import a user's home-directory module as its core.
Unknown values stay null, known/sample coverage remains explicit, cached prompt
input is distinguished from uncached input, and costs or workload-matched savings
are not invented. Latest successful LLM activity is separate from idle/export
state. Backlog is last observed in a run, not a live PostgreSQL backlog query.

The migrated metrics view is a same-origin frame inside the Insight workspace,
with shared theme and refresh controls; its accounting filters remain independent
of memory-backend selection. This preserves the existing working chart and
accounting UI without mixing production logs with isolated smoke samples.

### Activity is observation, not liveness

- Recent observation means a recorded timestamp is less than one hour old.
- Stale means an older or future-dated observation. Missing timestamps are unknown.
- A source file, historical open session, worker checkpoint or enabled cron is
  **not proof of a running agent**. No active process/heartbeat probe is configured.
- Cron results are observed last executions, not guaranteed future success.
- Receipts prove local packet imports, not two-way convergence or remote ACKs.
- Transport connectivity remains **unknown** without a verified transport probe.

## Read-only and privacy boundary

- SQLite is opened with URI `mode=ro`, `PRAGMA query_only=ON` and a read transaction.
- PostgreSQL uses `BEGIN READ ONLY` and bound parameters. Connections close without
  committing. There is no migration, arbitrary SQL endpoint or command executor.
- Exact HTTP route allowlist, loopback Host checks, same-origin checks, no CORS,
  no static directory traversal and no arbitrary file paths. Write methods are
  rejected. Security headers restrict frames, scripts, assets and connections.
- Browsing returns bounded durable-memory excerpts, not raw transcripts or arbitrary
  metadata. Explicit detail opens at most 20 linked event excerpts, each bounded
  to 4,000 characters. Durable detail is bounded to 20,000 characters; list excerpts
  to 2,000. Unlinked summary metadata is not fabricated into evidence.
- Credentials, obvious tokens and private keys receive best-effort redaction. All
  rendered prose is escaped and treated as untrusted reference data, not instructions.
  **Redaction is not a guarantee that prose is secret-free.** Use only on a trusted
  local machine. Do not publish runtime screenshots, excerpts, databases or logs.
- Exceptions are sanitized. A source failure is visible without disclosing DSNs,
  prompts, filesystem paths or raw error payloads.
- Backend metadata cache is 15 seconds; browser refresh is 20 seconds. Tables keep
  their overflow inside their panel. Filters use explicit Search and Clear actions.
- No middle-dot separators are used. Information is separated with layout and labels.

Insight is not an authenticated network service. Do not expose it through a tunnel
or bind it to a public interface. Background operation is not launch-at-login
persistence. Stop the server process to stop Insight. Existing standalone dashboard
instances can remain running until a user deliberately switches to this workspace.

## Verification

```sh
python3 -m unittest discover -s insight -v
python3 -m pytest -q memory-sync
git diff --check
```

Unit/integration tests use disposable fixtures and cover memory pagination,
parameter validation, literal search, evidence redaction, source isolation,
unknown values, actual SQLite write rejection, HTTP route/Host/Origin/write
rejection, missing backends and cache behavior. They never write configured runtime
memory. Existing AgentMesh tests remain unchanged.

Real-browser QA uses Node's native WebSocket and an **isolated** Chromium instance
with remote debugging enabled, not a copy of a locked user profile:

```sh
node insight/qa.mjs http://127.0.0.1:PORT CHROMIUM_DEBUG_PORT \
  /private/runtime/insight-qa
```

Use a disposable browser profile outside the repository. The test expects configured,
nonempty PostgreSQL and SQLite memory sources and existing summary metrics; it
checks all six navigation views, filtering/clearing, paging, explicit details,
connection switching, metrics charts/source tabs, desktop/mobile light/dark,
20-second auto-refresh, overflow and runtime exceptions. Screenshots and the QA
report are written only to the specified external directory. They contain local
operational and memory information and must not be committed.

## Files

- `server.py`: loopback HTTP routes, guards, cache and CLI.
- `adapters.py`: allowlisted read-only metrics, memory and operational projections.
- `libpq_reader.py`: parameterized, read-only PostgreSQL adapter.
- `metrics.py` and `metrics.html`: portable migration of existing summary accounting/UI.
- `index.html`: Insight navigation, browser, operational views and shared theme.
- `test_insight.py`: fixture-based safety and reader tests.
- `qa.mjs`: real Chromium/CDP acceptance checks.

No database schema, sync algorithm, ingest parser, production cron, provider
configuration or memory is changed by this subproject.
