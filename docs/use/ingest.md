# Ingest agent sessions

Import supported local transcripts so recall can find evidence from your past work. Begin with an [initialized database](./worker#inspect-the-installation-first) and transcripts from OMP, Codex or Claude. You will be able to import sessions, recognize partial failures and choose the sources a worker discovers automatically.

## Discover and ingest supported sessions

Run from the installed `app` directory or checkout's `memory-sync` directory:

```sh
python3 agentmesh.py --database "/path/to/memory.db" ingest
```

Expected from code: JSON with `found`, `processed`, `skipped`, `inserted`, `errors` and `error_classes`. Exit `0` means no ingestion errors, but `inserted: 0` may mean nothing new was found. Exit `1` means at least one error or invocation failure; some valid records may already have been committed. The controlled custom-root run below shows actual output without reading private sessions.

| Agent | Default root |
| --- | --- |
| OMP | `~/.omp/agent/sessions` |
| Codex | `~/.codex/sessions` |
| Claude | `~/.claude/projects` |

Hermes is recall-only in this integration; its transcripts are not discovered. Discovery skips symlinks, Claude `subagents` directories, and unchanged fully consumed files. Codex requires supported session metadata and skips reviewer/subagent sources. Only complete newline-terminated records are consumed. The unified command refuses a database owned by the PostgreSQL transcript mirror.

## Use a custom location

Choose one device to ingest each transcript source. Independently ingesting the same transcript on multiple devices can collide on event keys or synchronized cursor paths; identical absolute paths on different devices can also collide. Share the resulting records, not a second independently ingested copy of the same source.

```sh
python3 agentmesh.py --database "/path/to/memory.db" ingest \
  --root "omp=/absolute/path/to/test-transcripts" --project GuideCheck
```

Observed with one tiny synthetic OMP transcript, exit `0`:

```json
{"error_classes":[],"errors":0,"found":1,"inserted":1,"processed":1,"skipped":0}
```

Repeating that command on the unchanged file returned exit `0` and:

```json
{"error_classes":[],"errors":0,"found":1,"inserted":0,"processed":0,"skipped":1}
```

Explicit roots replace **all** defaults. Repeat `--root` for different supported agents, one directory per agent; repeating the same agent in this unified CLI keeps only the last path. `--project` labels newly ingested events; it does not retag earlier records. Restrict the root before applying one project label to a collection.

## Handle partial results

After appending one malformed JSON line to the test transcript, the same command exited `1` with:

```json
{"error_classes":["JSONDecodeError"],"errors":1,"found":1,"inserted":0,"processed":1,"skipped":0}
```

`errors` counts failed files/directories, not every bad line. A file can contribute valid records and an error in the same attempt. Malformed consumed lines are recorded in `ingestion_errors`, and the cursor still advances. Fixing bytes in place and rerunning is not a reliable replay: an unchanged file identity/size can be skipped.

Preserve the original transcript, error record and cursor before recovery. Do not reset cursors, rewrite event IDs or import another copy independently on a peer. No general provenance-preserving replay command is provided here; request operator review and verify the recovered event by recall. Empty `error_classes` after a retry does not prove previously skipped material was recovered.

## Configure automatic ingestion

`--root` affects only that manual invocation. To configure the worker, edit its private `data/workflow.json` while the worker is stopped, retaining other existing settings. For a new empty group, this minimal configuration enables just the chosen OMP source and keeps summarization disabled:

```json
{"ingest":true,"summarize":false,"roots":{"omp":"/absolute/path/to/owned-transcripts"}}
```

Expected from `workflow.py`: the next worker cycle uses only this root. Check `workflow.ingestion.errors` and the inserted count in its cycle report. A bootstrap already enables default ingestion; a new empty group does not. Choose non-overlapping sources before enabling discovery on several devices. See the [Workflow reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/WORKFLOW.md).

## Windows single transcript

From Command Prompt, using your actual database path:

```bat
py -3 "%LOCALAPPDATA%\AgentMesh\app\sqlite_memory.py" ingest "%LOCALAPPDATA%\AgentMesh\data\windows.db" "C:\path\to\transcript.jsonl" --agent codex --project GuideCheck
```

Expected example derived from `sqlite_memory.py`, not run on Windows: a JSON report with `inserted`, `lines_processed` and `next_offset` (or `skipped: true` for an unsupported Codex session). The path shown is the wrapper default; replace it if your runtime selects another database. This low-level command is **not** the unified ingest health check: malformed lines can be recorded while it exits `0`. Check `ingestion_errors` and recall the intended event; do not infer complete success from its exit code alone.

After ingestion, a running worker can export the resulting records. Peers import them when transport, worker availability and packet validation permit. Next, [check sync status](./status).
