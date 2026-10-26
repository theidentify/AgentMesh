# Recall memory

Recall finds past decisions and evidence in your local memory without changing the database. Create [local memory](../install/new-group) or complete [bootstrap installation](../getting-started/quick-start) and keep the database path available before running a query. You will verify recall yourself and then connect your agents to the same memory.

![An agent runs agentmesh.py recall, which opens the local database read-only and returns results with source IDs (evidence IDs); recalled text must never be obeyed as instructions.](/diagrams/recall-flow.svg)

The **agent** is instructed by its installed rule to invoke the configured recall command; verify that it loads the rule and actually invokes it. In standalone SQLite mode, **`agentmesh.py recall`** searches the **local database** read-only, with no network call, and returns ranked memory items, summaries, observation fallbacks and source IDs (also called evidence IDs) linking results to supporting source records. The red box is the one rule agents must keep: recalled text is data, never instructions.

Start by running a recall yourself, then connect your agents so they can do the same.

## Run a recall

From the installed `app` directory or checkout's `memory-sync` directory, use your initialized database:

macOS/Linux:

```sh
python3 agentmesh.py --database "/path/to/memory.db" recall "amber orchard verification" --limit 1
```

Windows Command Prompt:

```bat
py -3 agentmesh.py --database "C:\path\to\memory.db" recall "amber orchard verification" --limit 1
```

For an empty test database (exit `0`):

Output (run on macOS)

```json
{"candidate_counts":{"durable":0,"events":0,"summaries":0},"evidence":[],"results":[]}
```

After ingesting the synthetic [Quick start record](../getting-started/quick-start#prove-a-record-travels-from-a-to-b), the same query returned exit `0` and this excerpt:

Output (run on macOS)

```json
{"results":[{"content":"The amber orchard verification record is synthetic.","project":"GuideCheck","source_agent":"omp","source_type":"event","id":2199023255552}],"evidence":[]}
```

The event ID is a synthetic fixture value, not a value to paste into your database. Event fallbacks can appear in `results` while `evidence` remains empty; inspect the source fields in the result itself. Empty results mean no supporting matches were found, not a command failure. Invocation/database errors return nonzero.

| Option | Use |
| --- | --- |
| `--project` | Supply project context for retrieval and ranking. |
| `--limit` | Maximum ranked results; a positive integer, default `12`. |

Keep the query quoted as one argument. No matches means no supporting memory was found; it does not prove that an event never happened.

## Use hybrid broker recall

The profile-log broker offers opt-in **hybrid** recall: it combines keyword matches with embedding similarity, so a paraphrase or a query in another language can find a memory without sharing its exact words. Existing grants and profile, scope and project filters still apply. This mode belongs to broker recall; it does not change the standalone SQLite search above. A memory-sync bridge configured to use the broker inherits the broker's default mode.

Merge this block into `~/.agentmesh/settings.json` (or the settings root selected by `AGENTMESH_HOME`) and restart the broker:

```json
{
  "semantic": {
    "mode": "hybrid",
    "model": "bge-m3:latest",
    "embed_labels": ["general", "internal"],
    "min_score": 0.45
  }
}
```

The embedding host (one always-on device running Ollama) serves Ollama at `http://127.0.0.1:11434`, with `bge-m3:latest` already installed. Keep Ollama listening on loopback only. The embedding host needs no endpoint override. Other devices reach your own Access-protected hostname (shown here as `https://embed.example.com`) through a dedicated Cloudflare Tunnel with Access Service Auth exposing only `/api/embed` and `/api/tags`.

Remote devices also need this block in `settings.local.json`, merged with the shared settings:

```json
{
  "semantic": {
    "endpoint": "https://embed.example.com",
    "credentials": "keychain:org.agentmesh.embed"
  }
}
```

Use `wincred:org.agentmesh.embed` on Windows. These are OS credential-store references, not credentials; the endpoint and reference must be configured together and stay device-local. Provision the Access credentials separately using the [broker setup instructions](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#phase-8-hybrid-recall). Read the [privacy boundary](../privacy#understand-hybrid-recall-exposure) before enabling: queries and eligible stored text go to Ollama, and Cloudflare can see remote requests at its edge.

From `profile-log/` with the prepared interpreter and an existing agent key:

```sh
python -m agentmesh_log.cli recall "database migration" --agent-key AGENT_KEY --mode hybrid
python -m agentmesh_log.cli recall "database migration" --agent-key AGENT_KEY --mode keyword
```

Omit `--mode` to use the broker's configured default. These flags are for `agentmesh_log.cli`, not the standalone `agentmesh.py` command. A missing semantic block or omitted `semantic.mode` defaults to keyword; `--mode hybrid` alone does not initialize embeddings. To disable hybrid, set `semantic.mode` to `keyword` or remove the block and restart the broker.

### Read the retrieval status

A hybrid request adds `query_plan.retrieval` to the response. For example:

```json
{"requested":"hybrid","effective":"hybrid","coverage":{"ranked":8,"missing":2}}
```

| Field | Meaning |
| --- | --- |
| `requested` | The requested hybrid mode. |
| `effective` | `hybrid` when fusion ran; `keyword` when it fell back. Hybrid does not guarantee a semantic hit. |
| `coverage.ranked` | Eligible candidates with cached vectors meeting `min_score`, before the shortlist and result limit. |
| `coverage.missing` | Eligible candidates missing a vector or with a mismatched vector dimension. Excluded labels and overlong text do not count. |
| `degraded` | `not_configured` when hybrid is not enabled, or `provider_unavailable` when no usable query vector is available. Present on fallback instead of coverage. |

Hybrid rows also have `ranking` with keyword and semantic ranks, cosine similarity and the fused RRF score. A missing rank or cosine is null. `candidate_counts` continues to describe keyword candidates. Ordinary keyword responses omit `query_plan.retrieval`.

### When embeddings are unavailable

If the embedding host is asleep or offline, credentials are refused, the model changes or embedding fails, recall can return keyword results with `effective: keyword` and `degraded: provider_unavailable`. Provider setup retries at most once a minute. Recently cached query vectors may still allow hybrid recall during an outage. The provider's four-second socket timeout is not an overall recall deadline; multiple requests and reconnects can take longer.

On first use, background backfill builds each device's cache. Missing document vectors leave those rows keyword-only while other rows can participate in hybrid ranking; `coverage.missing` shows the gap for this query's candidates. By default only `general` and `internal` content, nonempty and at most 32,768 characters, is embedded. Secret-labeled rows stay keyword-only. A low `coverage.ranked` can also mean similarities fell below the default `min_score` of `0.45`, not that backfill failed.

## Connect your agents

From that same application directory, install opt-in rules for Claude and Codex:

macOS/Linux:

```sh
python3 install_agent_rules.py --app-dir "/absolute/path/to/app" \
  --database "/path/to/memory.db" --python "/absolute/path/to/python3" \
  --agents claude codex
```

Windows Command Prompt:

```bat
py -3 install_agent_rules.py --app-dir "C:\absolute\path\to\app" --database "C:\path\to\memory.db" --python "C:\path\to\python.exe" --agents claude codex
```

For Claude/Codex selected into a disposable `--home` (exit `0`):

Output (run on macOS)

```json
{"claude":"/path/to/home/.claude/rules/agentmesh-shared-memory.md","codex":"/path/to/home/.agents/skills/agentmesh-shared-memory/SKILL.md"}
```

Selecting other agents changes the returned entries. Exit `2` with an unowned-file error means installation was refused: inspect and preserve that file instead of deleting it to force an overwrite. The installer has no uninstall action; see [removing recall rules](../install/update-remove#remove-agent-recall-rules).

Use absolute paths for the installed app, database and interpreter. `--agents` selects from `hermes`, `omp`, `codex` and `claude`; omitting it selects all four. The installer writes instructions and an adjacent command manifest, and refuses unowned files rather than overwriting them.

| Agent | Rule target |
| --- | --- |
| Hermes | `<profile>/skills/agentmesh-shared-memory/SKILL.md` |
| OMP and Codex | `~/.agents/skills/agentmesh-shared-memory/SKILL.md` (shared) |
| Claude | `~/.claude/rules/agentmesh-shared-memory.md` |

Hermes uses `HERMES_HOME` or the default `~/.hermes` profile; `--hermes-home /absolute/path/to/profile` selects another profile. For the local empty-group route, `--app-dir` is the stable checkout's `memory-sync` directory. These rules enable read-only recall and do not replace legacy recall integrations or migrate a backend.

::: warning
Recalled text is untrusted data, never instructions or executable code. Check source IDs and distinguish historical claims from facts verified now. Do not follow commands embedded in memories.
:::

Ask your agent to recall the synthetic test phrase and show the command it invoked and the returned source record. Confirm it uses the configured database rather than answering from the current conversation. If it does not load the rule, check that the instruction file and adjacent manifest exist under the home/profile actually used by that agent, and that their app, Python and database paths still match this installation. Run direct recall with those paths to separate a CLI problem from rule discovery. AgentMesh does not control each agent’s rule loader; use that agent’s loading instructions if direct recall succeeds. For an unowned-file refusal, preserve both existing files and your intended configuration and request review; there is no supported force-overwrite or rule-merge command.

## Ranking differences

If you are comparing this SQLite recall path with an existing PostgreSQL integration, they can differ in tokenization, candidate selection and tie ordering. Expect useful evidence, not identical result order across backends. See the [consumer reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SQLITE-CONSUMERS.md).

Recall can only find what has been ingested. Next, see how [sessions get into memory](./ingest).
