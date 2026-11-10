# memory-sync (retired)

memory-sync was AgentMesh's first runtime: SQLite archives synced as packets over Syncthing. The encrypted profile log
replaced it, and the SQLite retirement (R5b, 2026-11-09) removed everything except the transcript ingest and summary.
Those now live in `profile-log/agentmesh_log/`:

| Was | Now |
| --- | --- |
| `jobs/agentmesh_ingest.py`, `jobs/agentmesh_summary.py`, `jobs/agentmesh_production.py` | `python -m agentmesh_log.ingest scan\|summary` (`agentmesh-log ingest scan\|summary`), `ingest/production.py` |
| `job_notify.py` | `python -m agentmesh_log.control.jobrun` (`agentmesh-log job run`), `control/jobrun.py` |
| `broker_digest.py`, `bounded_protocol.py`, `claude_digest_worker.py`, `digest_io.py`, `transcripts.py`, `timestamps.py` | `ingest/digest.py`, `ingest/protocol.py`, `ingest/claude_worker.py`, `ingest/digest_io.py`, `ingest/transcripts.py`, `ingest/timestamps.py` |
| `src/omp_memory/` | `ingest/parsers/` |
| `assets/` | `assets/` at the repository root |

## What is left here

These files are compatibility shims only. Each one loads the package module that replaced it, so a device that
deploys this tree before it migrates keeps running its hourly jobs unchanged. That covers the supervisor's stored
commands, the copies in `~/.agentmesh/jobs` and a `summary.command` that still names `claude_digest_worker.py`.
`profile-log/tests/ingest/test_shims.py` tests them.

Migrate each device with the runbook in
[docs/install/migrate-memory-sync.md](../docs/install/migrate-memory-sync.md). Once the Air, the Pro and Windows have all
run it, a follow-up change (PR B) deletes this directory and the shim tests.
