# Ingest agent sessions

Recall is only as good as what has been ingested. Ingestion reads the session transcripts your agents already write and stores them in your initialized SQLite database. With the workflow configuration the installer creates, the running worker does this for the default locations on every cycle; the commands below are for running it yourself or for sessions stored elsewhere. Run the following commands from the installed `app` directory. Substitute your node's database path.

## Ingest everything found

```sh
python3 agentmesh.py --database /path/to/mac.db ingest
```

Discovery looks for JSONL files beneath these default locations:

| Agent | Default root |
| --- | --- |
| OMP | `~/.omp/agent/sessions` |
| Codex | `~/.codex/sessions` |
| Claude | `~/.claude/projects` |

Ingestion is incremental: unchanged, fully consumed files are skipped. Claude `subagents` directories and symlinked candidates are skipped. Only complete newline-terminated records are consumed.

::: info
The unified CLI refuses ingestion on a database with PostgreSQL mirror state, because that mirror owns transcript ingestion on the source machine.
:::

## Use a custom location

`--root` accepts `AGENT=PATH`. Repeat it for different agents:

```sh
python3 agentmesh.py --database /path/to/mac.db ingest \
  --root "codex=/absolute/path/to/codex-sessions" \
  --root "claude=/absolute/path/to/claude-projects"
```

Explicit roots **replace all defaults**. Use one directory per agent; in this unified CLI, repeating the same agent keeps only its last path. Supported agents are `omp`, `codex` and `claude`.

## Tag a project

Supply a fixed project for newly ingested events:

```sh
python3 agentmesh.py --database /path/to/mac.db ingest --project AgentMesh
```

Use your actual project name. Ingestion alone does not exchange records; the running worker handles sync. The bootstrap creates a workflow with ingestion enabled and summarization disabled, so the worker can also discover sessions at the default roots. Custom session locations need explicit configuration or a manual command.

## Windows single transcript

From Command Prompt:

```bat
py -3 "%LOCALAPPDATA%\AgentMesh\app\sqlite_memory.py" ingest "%LOCALAPPDATA%\AgentMesh\data\windows.db" "C:\path\to\transcript.jsonl" --agent codex --project AgentMesh
```

Replace the transcript path and use your actual project name. `--agent` accepts `omp`, `codex` or `claude`. This direct SQLite ingestion does not require PostgreSQL or psycopg.

Once sessions are ingested locally, the worker shares them with your other devices. Next, [check that sync is working](./status).

See the [writer reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SQLITE-WRITERS.md) and [Windows setup reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-WINDOWS.md) for details.
