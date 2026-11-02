# SQLite retirement R2: report

Phase R2 of `../specs/2026-11-02-sqlite-retirement-design.md`: the summarizer can run without the staging
database, behind a flag. Nothing changes on a device until its production config says `"backend": "broker"`
(the default stays `sqlite`; the R4 ceremony flips it).

## What was built

- **`memory-sync/broker_digest.py`.** Reads windows straight from the agents' JSONL files with `transcripts.read`,
  builds the same request (`bounded_protocol`), runs the same retry and conflict rules, and writes through the
  broker. State lives in `transcript-cursors.json` and `summary-state.json` (0600, atomic, under one lock).
- **`profile-log/agentmesh_log/summarize.py`, `agentmesh-log summary related|write`.** The summarizer agent's side:
  rebuilds the related context from `heads`, refuses a stale window, applies the quarantine rules, upserts by memory
  key (`remember` / `revise` / unchanged) and keeps the project summary current. Requests go on stdin.
- **Broker.** `heads` without `memory_keys` lists every keyed head of a scope (refused as `truncated` above 500), and
  each head carries `item_scope`, `scope_key` and `status`. A summarizer write with a non-active status hides its key.
  An agent with `allow_summarize` may revise an imported head of the same key. Documented in `profile-log/FORMAT.md`.
- **Production job.** `backend: "broker"` plus a `broker` section (`cli_argv`, `agent_key`, `mapping`,
  `state_dir`). `summary` runs `broker_digest.tick`; `ingest` only scans and reports (nothing to store); `context`,
  `recall`, `export` and `retention` are refused on that backend.

## Parity

`profile-log/tests/broker/test_summary_parity.py` runs fixture transcripts through both backends with one
deterministic extractor: windows of two events, the first ending mid-file and the second spanning two files, and
one key updated by a later window. Memory keys, kinds, scopes, scope keys, projects, contents and statuses match,
and so does the project summary's current-memory block. A second run is idle.

## Choices that differ from the spec

| Spec | Built | Why |
|---|---|---|
| Ingest cursor plus summary cursor | One cursor per file plus the keys already summarized after it | No events are stored, so only "summarized through" matters. A window can end mid-file, hence the key set. |
| Routing in settings `ingest.routing` or the mapping file (open item) | The migration mapping file, as is | New memories land exactly where the imported ones are. |
| `memory_key = summary:<project>` | `summary/<project>` | `:` is in the model's key alphabet, `/` is not, so a model key can never collide with a summary. |
| `body.metadata.source` | `body.metadata.origin` | `source` is reserved for imports. |

## Known gaps (to close before or at R4)

- **Confirming a quarantined deletion** has no broker path yet. Quarantine records are kept in `summary-state.json`
  (last 500, key and reason only).
- **Non-active items** (superseded, resolved, deleted) are hidden, like imported ones. SQLite still showed them to the
  model as related context. A later window can't reactivate them; it creates the key again.
- **A crash between the broker write and the state save** repeats the window. The repeat writes `unchanged` for
  identical items; a model that answers differently produces a revision. SQLite had one transaction for both.
- **A renamed transcript** is read again from the start (cursors are per path); SQLite deduplicated by event key.
- **A summarizer write the broker labels secret** is invisible to `heads`, so the same key later becomes a new event.
- **Run metrics** (`metrics_path`) and **the memory tree build after a summary tick** are not wired on the broker
  backend.
- **Seeding** the JSON state from SQLite (cursors, coverage, ingestion floor) is the R4 ceremony's job.

## Tests

- `profile-log/tests/broker/test_summarize.py`: list-all heads, hidden deletions, the 500 bound, imported-head revision
  (same key only, `allow_summarize` only), write rules (unchanged, deletion and outside-context quarantine, stale,
  unmapped project), request shape.
- `memory-sync/test_broker_digest.py`: mid-file windows and resume, failed and stale writes keep cursors, file
  replacement restarts, quiet files move, ingestion floor, state file modes and format, schedule, CLI error codes,
  production config.
