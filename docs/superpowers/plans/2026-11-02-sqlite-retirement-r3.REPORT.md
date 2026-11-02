# SQLite retirement R3: report

Phase R3 of `../specs/2026-11-02-sqlite-retirement-design.md`: Insight reads memory counts through the broker, and
the legacy job actions and the plaintext export are gone. The staging database is still the summary path on
every device until R4.

## What changed

- **Broker `stats` op** and `agentmesh-log stats`: per-scope counts (total, kinds, labels, summarizer-keyed heads,
  newest update) and the top 50 projects, for the calling agent's readable scopes. No content.
- **Insight.**
  - `/api/log-counts` runs `agentmesh-log stats` as the `insight` agent (`agents/insight.key`, or
    `--insight-agent-key`). The answer is projected onto a fixed schema, so only counts, scope and project names reach
    the page. It is allowed through the remote tunnel (counts only). Overview shows "Memory in the profile log".
  - Storage adds "Summarizer state (broker backend)": sizes of `jobs/transcript-cursors.json` and
    `summary-state.json`, transcripts followed, quarantine count, last full run. No paths or keys.
- **Production job.**
  - `context`, `recall`, `export` and `retention` actions are refused, and `jobs/agentmesh_retention.py` is removed.
  - Ingest no longer writes the Markdown export (`export_root`, `last-export.json`). Summary runs the scheduler
    with no exporter. `export_root` stays in the window identity, so a window opened before R3 still resumes.
  - The existing `~/.agentmesh/export` is left as is; R4 archives it with the database.

## Deferred, with reasons

| Spec item | Status | Why |
|---|---|---|
| Insight memory browse on the broker | The SQLite browse stays local-only (remote never had it); the broker side is counts only | Content belongs in the control app's Memory window. The SQLite browse goes with the database at R4. |
| Storage drops the database rows | Kept while a database is configured | The database is still live until R4. |
| Remove the `retention` unit | Kept (`staging_retention`, control unit `retention`) | Windows still runs SQLite until R5 and needs it to bound its staging database. On the Mac the unit is not loaded. |
| Delete `sqlite_export.py`, `retention_audit.py` | Callers removed, files kept | Deleted with the rest of the SQLite code in R5 (their tests still exercise staging retention). |
| Bridge settings refuse `recall_fallback`, `dual_write`, `database`, `authority` | Not done | Depends on the cutover flag, which R4 introduces. |

## Owner setup (when wanted)

Grant a read-only agent named `insight` (read the scopes to count, no write), apply, and restart Insight. Without
the key the panel says "Insight agent not configured" and nothing else changes.

## Tests

- profile-log 1486 passed; Insight 248 passed; memory-sync 2125 passed, 1 known failure that also fails on `main`
  (`test_job_units::test_sqlite_private_permissions_skip_windows`).
- New: `test_summarize.py::test_stats_counts_only`, `insight/test_memory_stats.py` (projection drops unknown fields
  and text, fixed error codes, remote allowlist, summarizer state), production job tests for the removed actions and
  the export that is no longer written.
