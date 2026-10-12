# Quick start

Install from a supplied private bootstrap, check sync and run your first recall query. Complete the [prerequisites](./prerequisites) and have the group administrator assign an unused device identity first. You will verify the worker and, with a second device already in the group, test that a record reaches its destination.

## Choose your starting point

- **Received a private bootstrap:** use the checklist below. The person administering the group supplies the files and identity assignment; these are not public release assets.
- **Starting alone from GitHub:** use [New sync group](../install/new-group) for local ingestion and recall. That route cannot add another device yet, so the A → B test below does not apply to it.

## Check, install and query

1. Check Python from a terminal:

   ```sh
   python3 -c "import sys,sqlite3; c=sqlite3.connect(':memory:'); c.execute('create virtual table t using fts5(x)'); print(sys.version.split()[0]); print('FTS5: ok'); print('JSON:',c.execute(\"select json_valid('{}')\").fetchone()[0])"
   ```

   Observed: `3.14.8`, `FTS5: ok`, `JSON: 1`, exit `0`. Use `py -3` on Windows (expected from code, not run there). A traceback or a version below your route's minimum means stop and fix [Python setup](./prerequisites#install-and-check-python).

2. In Syncthing, confirm the supplied private exchange folder is **Up to Date**, then install on [macOS](../install/macos), [Linux](../install/linux) or [Windows](../install/windows). Keep the foreground worker open.
3. Open a **second terminal** in the installed `app` directory and check the runtime:

   ```sh
   python3 agentmesh.py inspect-install --runtime "/path/to/runtime.json"
   ```

   Observed success: JSON with `status: ready`, exit `0`, and the configured database/exchange paths. `not_found` can also exit `0`; it is not success for this checklist. Copy the actual database path into the next command.

4. Query local status:

   ```sh
   python3 agentmesh.py --database "/path/to/memory.db" status
   ```

   Observed on an empty test database: zero counts and `outbox`, `pending`, `conflict`, `invalid`, `received` all `0`, exit `0`. Your bootstrap counts can differ. Nonzero conflict/invalid counts require review; zero counts do not prove peer delivery. See [status](../use/status).

5. Try a read-only query:

   ```sh
   python3 agentmesh.py --database "/path/to/memory.db" recall "amber orchard verification" --limit 1
   ```

   Observed on an empty database: `results: []`, `evidence: []`, exit `0`. After the synthetic test record below was ingested, `results` contained its text with `source_type: event`; `evidence` was still empty. An empty query result is not an execution error. A database or invocation error must be fixed before continuing.

6. [Connect your agents](../use/recall#connect-your-agents) after direct recall works. Verify the agent actually invokes the installed command; installing a rule does not guarantee it is loaded.

## Prove a record travels from A to B

Use two already bootstrapped devices with different identities in the same group. Keep both Syncthing instances and workers available. On **A only**, create a private test directory outside the exchange and save this single line as `test.jsonl`, including a newline at the end:

```json
{"type":"message","id":"guide-check","message":{"role":"user","content":[{"type":"text","text":"The amber orchard verification record is synthetic."}]}}
```

This is synthetic OMP-format input, not command output. Use a fresh test phrase if this record already exists. Do not ingest this transcript independently on B.

From A's installed app directory:

```sh
python3 agentmesh.py --database "/path/to/A-memory.db" ingest \
  --root "omp=/absolute/path/to/test-transcripts" --project GuideCheck
```

Observed first-run result, exit `0`: `found: 1`, `processed: 1`, `inserted: 1`, `errors: 0`. A repeated unchanged file is skipped. `errors > 0` exits `1` and needs investigation even if some records were inserted.

Allow A to complete a worker cycle, Syncthing to transfer the packet, and B to complete an import cycle. This takes processing and transfer time plus each worker's configured wait. Do not launch another worker over the running one. On B:

```sh
python3 agentmesh.py --database "/path/to/B-memory.db" recall "amber orchard verification" --limit 1
```

Observed excerpt after two disposable bootstrap databases exchanged the packet locally, exit `0`:

```json
{"results":[{"content":"The amber orchard verification record is synthetic.","project":"GuideCheck","source_agent":"omp","source_type":"event"}]}
```

The local test verified record exchange, not your Syncthing connection. On your devices, finding that distinctive A-only record on B is the acceptance check. Empty results mean delivery/import/query verification is incomplete: inspect A's publication and B's pending/conflict/invalid counts. Do not delete the packet after testing.

Next, [control the worker](../use/worker) to choose ongoing operation and handle failures.
