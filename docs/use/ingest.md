# Ingest agent sessions

AgentMesh turns your own agent sessions into memory. Two scheduled jobs read the JSONL transcripts that OMP, Codex
and Claude Code write on this device. They summarize new turns in bounded windows with a Claude worker and write the
results through the local broker as the `summarizer` agent. Recall can then find decisions from earlier sessions on
any of your devices.

::: info Personal only
Ingestion works on your personal home (`~/.agentmesh`, or `AGENTMESH_HOME`). It refuses a team workspace home
(`~/.agentmesh/ws/<id>/`), any configured path inside one, and a summarizer key that reaches a workspace broker. So
your transcripts never reach a workspace's scopes. Share project knowledge with a workspace explicitly, through its
own agent keys.
:::

## What is read

| Agent | Default location |
| --- | --- |
| Claude Code | `~/.claude/projects` |
| Codex | `~/.codex/sessions` (reviewer sessions are skipped) |
| OMP | `~/.omp/agent/sessions` |

Only complete, newline-terminated records are read, so a line that is still being written waits for the next run.
User requests and assistant responses are summarized, and secrets are masked before anything leaves the device.
Nothing from a transcript is stored by AgentMesh except the memories the summary writes.

## The two jobs

The supervisor runs both jobs on this device, each through the job wrapper (`python -m agentmesh_log.control.jobrun`).
The wrapper applies maintenance mode and the summary's daily LLM quota, and alerts on failure and recovery.

- **`scan`** (unit `ingest`) reports what the next summary will read: files, new records and parse errors. It
  moves no cursor and writes only `last-ingest.json`.
- **`summary`** (unit `summary`) runs once a day and after each reboot. It works through new events window by window
  (at most `max_batches` windows per run), writes each window's memories, and only then moves that file's cursor. A
  window whose write fails keeps its cursor, so the next run retries the same events.

Run either one by hand:

```sh
python -m agentmesh_log.ingest scan
python -m agentmesh_log.ingest summary --force
```

`--force` runs a summary even when one already completed today. `agentmesh-log ingest scan|summary` is the same job
through the full CLI. Both exit with `0`, or `1` and a one-line JSON status on stderr.

## Configuration

The jobs read `~/.agentmesh/jobs/agentmesh-production.json` unless you pass `--config`:

```json
{
  "backend": "broker",
  "broker": {
    "cli_argv": ["/path/to/venv/python", "-m", "agentmesh_log.cli"],
    "agent_key": "/path/to/.agentmesh/agents/summarizer.key",
    "mapping": "/path/to/.agentmesh/jobs/mapping.json",
    "state_dir": "/path/to/.agentmesh/jobs"
  },
  "status_dir": "/path/to/.agentmesh/status",
  "summary": {
    "model": "claude-haiku-5-5",
    "command": ["/path/to/venv/python", "-m", "agentmesh_log.ingest.claude_worker"]
  }
}
```

Every path must be absolute. An optional `roots` object (`claude`, `codex`, `omp`) replaces the default locations. The
summarizer agent needs `allow_summarize` in `settings.json`. The mapping routes each project to a scope. A project
without a route waits, and is listed in `summary-unmapped.json` until you map it (the menu bar app asks) or skip it.

## State

Everything is in the jobs state directory, written atomically with owner-only permissions:

| File | Holds |
| --- | --- |
| `transcript-cursors.json` | per transcript: file identity, byte offset and line of the first record not yet summarized |
| `summary-state.json` | ingestion floor, last completed day and boot, quarantined items |
| `summary-unmapped.json` | projects held back for want of a route |
| `summary-metrics.jsonl` | one row per summary run, read by [Insight](./insight) |

Deleting a cursor makes the next summary read that file again from the ingestion floor. Leave these files alone unless
you mean to re-summarize.

## Upgrading from memory-sync

Devices set up before 2026-11 ran these jobs from the `memory-sync` directory. Move them with the
[memory-sync migration runbook](../install/migrate-memory-sync).
