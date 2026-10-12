# Ingest agent sessions

Import supported local transcripts so recall can find evidence from your past work. Begin with an [initialized database](./worker#inspect-the-installation-first) and transcripts from OMP, Codex or Claude. You will be able to import sessions, recognize partial failures and choose the sources a worker discovers automatically.

## Discover and ingest supported sessions

Run from the installed `app` directory or checkout's `memory-sync` directory:

```sh
python3 agentmesh.py --database "/path/to/memory.db" ingest
```

Output (example from code)

JSON with `found`, `processed`, `skipped`, `inserted`, `errors` and `error_classes`. Exit `0` means no ingestion errors, but `inserted: 0` may mean nothing new was found. Exit `1` means at least one error or invocation failure; some valid records may already have been committed. The controlled custom-root run below shows actual output without reading private sessions.

| Agent | Default root |
| --- | --- |
| OMP | `~/.omp/agent/sessions` |
| Codex | `~/.codex/sessions` |
| Claude | `~/.claude/projects` |

Hermes is recall-only in this integration; its transcripts are not discovered. Discovery skips symlinks, Claude `subagents` directories, and unchanged fully consumed files. Codex requires supported session metadata and skips reviewer/subagent sources. Only complete newline-terminated records are consumed. The unified command refuses a database owned by the PostgreSQL transcript mirror.

## Before the first bootstrap cycle

Before running a bootstrap installer, coordinate which device will ingest each source and check for identical absolute transcript paths across devices. Review all three default roots above for private or duplicated sessions. Bootstrap installation enables default ingestion and starts the worker immediately; `--once` still runs ingestion once. There is no installer option to pause for root selection before that cycle. If those defaults are unsafe, do not run the installer yet: request an operator-reviewed configuration. For a synthetic-only local trial, use [New sync group](../install/new-group), which starts with ingestion disabled, then manually ingest only the test directory. That local route does not support adding B.

## Use a custom location

Choose one device to ingest each transcript source. Independently ingesting the same transcript on multiple devices can collide on event keys or synchronized cursor paths; identical absolute paths on different devices can also collide. Share the resulting records, not a second independently ingested copy of the same source.

```sh
python3 agentmesh.py --database "/path/to/memory.db" ingest \
  --root "omp=/absolute/path/to/test-transcripts" --project GuideCheck
```

For one tiny synthetic OMP transcript (exit `0`):

Output (run on macOS)

```json
{"error_classes":[],"errors":0,"found":1,"inserted":1,"processed":1,"skipped":0}
```

Repeating that command on the unchanged file returned exit `0` and:

Output (run on macOS)

```json
{"error_classes":[],"errors":0,"found":1,"inserted":0,"processed":0,"skipped":1}
```

Explicit roots replace **all** defaults. Repeat `--root` for different supported agents, one directory per agent; repeating the same agent in this unified CLI keeps only the last path. `--project` labels newly ingested events; it does not retag earlier records. Restrict the root before applying one project label to a collection.

## Handle partial results

After appending one malformed JSON line to the test transcript, the same command exited `1` with:

Output (run on macOS)

```json
{"error_classes":["JSONDecodeError"],"errors":1,"found":1,"inserted":0,"processed":1,"skipped":0}
```

`errors` counts failed files/directories, not every bad line. A file can contribute valid records and an error in the same attempt. Malformed consumed lines are recorded in `ingestion_errors`, and the cursor still advances. Fixing bytes in place and rerunning is not a reliable replay: an unchanged file identity/size can be skipped.

Preserve the original transcript, error record and cursor before recovery. Do not reset cursors, rewrite event IDs or import another copy independently on a peer. The code provides no supported general provenance-preserving replay command. Preserve a [consistent database backup](../troubleshooting#preserve-a-consistent-database-backup) along with the original transcript, error records and cursors; report the ingestion exit code, `errors`, `error_classes` and whether the intended record is missing through the [help template](../troubleshooting#request-help-without-sharing-memory). Request operator review before attempting a repair, then verify any recovered event by recall. Empty `error_classes` after a retry does not prove previously skipped material was recovered.

## Inspect errors and cursors

An ingestion cursor records a transcript’s file identity and the byte/line position already consumed. Read the error locations and consumed positions without modifying the database (macOS/Linux):

```sh
python3 -c "import sqlite3,sys,json; from pathlib import Path; c=sqlite3.connect(Path(sys.argv[1]).resolve().as_uri()+'?mode=ro',uri=True); c.row_factory=sqlite3.Row; print(json.dumps({t:[dict(r) for r in c.execute(sql)] for t,sql in [('errors','SELECT source_path,byte_offset,line_number,error_type FROM ingestion_errors ORDER BY source_path,line_number'),('cursors','SELECT source_path,byte_offset,line_number FROM ingestion_cursors ORDER BY source_path')]})); c.close()" "/path/to/memory.db"
```

In Windows Command Prompt, use the same one-line command with `py -3` and a Windows database path. No shell continuation is needed.

Output (run on macOS)

```json
{"errors":[{"source_path":"/path/to/test/transcripts/test.jsonl","byte_offset":164,"line_number":2,"error_type":"invalid_json"}],"cursors":[{"source_path":"/path/to/test/transcripts/test.jsonl","byte_offset":173,"line_number":2}]}
```

Exit `0` means inspection succeeded. This synthetic fixture’s malformed second line begins at byte 164, while its cursor has advanced through byte 173 and line 2. Inspect that location in the original transcript privately. Empty arrays mean no stored entries, not proof that every transcript was discovered; a traceback/nonzero exit means the query failed. Paths and error records remain private. The query omits raw error messages, hashes and file identities; retain the full database for review.

## Configure automatic ingestion

`--root` affects only that manual invocation. To configure the worker, edit its private `data/workflow.json` while the worker is stopped, retaining other existing settings. For a new empty group, this minimal configuration enables just the chosen OMP source and keeps summarization disabled:

```json
{"ingest":true,"summarize":false,"roots":{"omp":"/absolute/path/to/owned-transcripts"}}
```

Output (example from code)

The next worker cycle uses only this root. Check `workflow.ingestion.errors` and the inserted count in its cycle report. A new empty group starts with ingestion disabled. For bootstrap, complete the [source review before installation](#before-the-first-bootstrap-cycle).

The [Workflow reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/WORKFLOW.md) describes configuration, but its current “one logical node per OS” and “Mac” wording conflicts with the code. Device identities are OS-independent. The summary writer is selected by `summary.primary_node`, default `mac`, regardless of its operating system; do not enable independent summary writers on other identities.

## Windows single transcript

From Command Prompt, using your actual database path:

```bat
py -3 "%LOCALAPPDATA%\AgentMesh\app\sqlite_memory.py" ingest "%LOCALAPPDATA%\AgentMesh\data\windows.db" "C:\path\to\transcript.jsonl" --agent codex --project GuideCheck
```

Output (example from code)

A JSON report with `inserted`, `lines_processed` and `next_offset` (or `skipped: true` for an unsupported Codex session). The path shown is the wrapper default; replace it if your runtime selects another database. This low-level command is **not** the unified ingest health check: malformed lines can be recorded while it exits `0`. Check `ingestion_errors` and recall the intended event; do not infer complete success from its exit code alone.

After ingestion, a running worker can export the resulting records. Peers import them when transport, worker availability and packet validation permit. Next, [check sync status](./status).
