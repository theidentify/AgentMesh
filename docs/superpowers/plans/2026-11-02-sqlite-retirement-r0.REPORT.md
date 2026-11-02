# SQLite retirement R0: report

Phase R0 of `../specs/2026-11-02-sqlite-retirement-design.md` (PR #149): inventory and
parity, with no behavior change.

## Parity on the live Mac Air (2026-11-02)

`migrate verify` with the importer agent and the applied mapping file, read-only:

```
{"missing": 0, "retired_but_present": 0}
```

Every live staging row is already in the profile log. Staging database counts, read-only:

| Table | Rows |
|---|---|
| observation_events | 4535 |
| memory_items (all live) | 161 |
| memory_summaries | 4 |
| ingestion_cursors | 23 |
| summary_state | 649 |

The profile log holds far more (about 1,270 items). Most of it came from the earlier
PostgreSQL and Pro migrations, not from this staging database.

**Finding:** `agentmesh-log migrate verify --settings` fails with `invalid mapping` on this
machine (the CLI reports `unavailable`). `Mapping.load(~/.agentmesh/migration-mapping.json)`
works. The settings `migration` section does not build a valid `Mapping` by itself. Not
fixed, because this path is removed in R5. The R4 ceremony must use the applied mapping file.

## Transcript reader without a database

- New `memory-sync/transcripts.py`: `resolve` (project, Codex reviewer and missing-meta
  skip) and `read` (complete records after a cursor; identity change or truncation
  restarts; invalid JSON reported, not fatal). It never touches a database.
- `sqlite_memory.ingest_file` now uses it. Same cursor rules, same return values, same
  inserts. The 158 ingest/bootstrap/production tests and the full memory-sync suite pass
  unchanged (2114 passed). The one local failure, `test_job_units::test_sqlite_private_permissions_skip_windows`,
  also fails on `main` in this environment.
- `transcripts.py` is packaged (`build_package.py`).

## SQLite users, pinned

`test_transcripts.py::SqliteInventoryTests` pins the production modules that still touch
SQLite: 39 to retire (memory-sync, Insight, `migrate/source.py`, `verify_remote.py`) and 3
to keep (the broker's in-memory index and vector cache, and the supervisor's `state.db`).
The test fails on any new SQLite user, so the list can only shrink until R5.
