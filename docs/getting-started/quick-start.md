# Quick start

Install from a supplied private bootstrap, check sync and run your first recall query. Complete the [prerequisites](./prerequisites) and have the group administrator assign an unused device identity first. You will verify the worker and, with a second device already in the group, test that a record reaches its destination.

## Choose your starting point

- **Received a private bootstrap:** use the checklist below. The person administering the group supplies the files and identity assignment; these are not public release assets.
- **Starting alone from GitHub:** use [New sync group](../install/new-group) for local ingestion and recall, then [invite a second device](../install/new-group#adding-another-device) if the group is eligible. After join completes, use the same A → B test below.

::: info
Output labels distinguish disposable macOS runs from examples derived from code. JSON blocks may show only the fields discussed; private values are redacted. Windows and OS-service behavior still needs verification on the target host.
:::

## Check, install and query

1. Check Python from a terminal:

   macOS/Linux:

   ```sh
   python3 -c "import sys,sqlite3; c=sqlite3.connect(':memory:'); c.execute('create virtual table t using fts5(x)'); print(sys.version.split()[0]); print('FTS5: ok'); print('JSON:',c.execute(\"select json_valid('{}')\").fetchone()[0])"
   ```

   Windows Command Prompt:

   ```bat
   py -3 -c "import sys,sqlite3; c=sqlite3.connect(':memory:'); c.execute('create virtual table t using fts5(x)'); print(sys.version.split()[0]); print('FTS5: ok'); print('JSON:',c.execute(\"select json_valid('{}')\").fetchone()[0])"
   ```

   Output (run on macOS)

   `3.14.8`, `FTS5: ok`, `JSON: 1`, exit `0`. A traceback or a version below your route's minimum means stop and fix [Python setup](./prerequisites#install-and-check-python).

2. In Syncthing, confirm the supplied private exchange folder is **Up to Date**, install with `--no-ingest` on [macOS](../install/macos), [Linux](../install/linux) or [Windows](../install/windows). Keep the foreground worker open for the checks below. Then [review roots and explicitly enable ingestion](../use/ingest#before-the-first-bootstrap-cycle); the flag does not disable sync or manual ingestion.
3. Open a **second terminal** in the installed `app` directory and check the runtime:

   macOS/Linux:

   ```sh
   python3 agentmesh.py inspect-install --runtime "/path/to/runtime.json"
   ```

   Windows Command Prompt:

   ```bat
   py -3 agentmesh.py inspect-install --runtime "C:\path\to\runtime.json"
   ```

   Output (run on macOS)

   JSON with `status: ready`, exit `0`, and the configured database/exchange paths. `not_found` can also exit `0`; it is not success for this checklist. Copy the actual database path into the next command.

4. Query local status:

   macOS/Linux:

   ```sh
   python3 agentmesh.py --database "/path/to/memory.db" status
   ```

   Windows Command Prompt:

   ```bat
   py -3 agentmesh.py --database "C:\path\to\memory.db" status
   ```

   Output (run on macOS)

   On an empty test database: zero counts and `outbox`, `pending`, `conflict`, `invalid`, `received` all `0`, exit `0`. Your bootstrap counts can differ. Nonzero conflict/invalid counts require review; zero counts do not prove peer delivery. See [status](../use/status).

5. Try a read-only query:

   macOS/Linux:

   ```sh
   python3 agentmesh.py --database "/path/to/memory.db" recall "amber orchard verification" --limit 1
   ```

   Windows Command Prompt:

   ```bat
   py -3 agentmesh.py --database "C:\path\to\memory.db" recall "amber orchard verification" --limit 1
   ```

   Output (run on macOS)

   On an empty database: `results: []`, `evidence: []`, exit `0`. After the synthetic test record below was ingested, `results` contained its text with `source_type: event`; `evidence` was still empty. An empty query result is not an execution error. A database or invocation error must be fixed before continuing.

6. [Connect your agents](../use/recall#connect-your-agents) after direct recall works. Verify the agent actually invokes the installed command; installing a rule does not guarantee it is loaded.

## Prove a record travels from A to B

Use two successfully bootstrapped or invitation-joined devices with different identities in the same group. Keep both Syncthing instances and workers available. On **A only**, create a private test directory outside the exchange and save this single line as `test.jsonl`, including a newline at the end:

```json
{"type":"message","id":"guide-check","message":{"role":"user","content":[{"type":"text","text":"The amber orchard verification record is synthetic."}]}}
```

This is synthetic OMP-format input, not command output. For a repeat test, create a new transcript file with a unique message ID and a fresh test phrase. Use that phrase in the recall query on B; do not overwrite the previously ingested test file. Do not ingest this transcript independently on B.

From A's installed app directory:

macOS/Linux:

```sh
python3 agentmesh.py --database "/path/to/A-memory.db" ingest \
  --root "omp=/absolute/path/to/test-transcripts" --project GuideCheck
```

Windows Command Prompt:

```bat
py -3 agentmesh.py --database "C:\path\to\A-memory.db" ingest --root "omp=C:\absolute\path\to\test-transcripts" --project GuideCheck
```

Output (run on macOS)

First run, exit `0`: `found: 1`, `processed: 1`, `inserted: 1`, `errors: 0`. A repeated unchanged file is skipped. `errors > 0` exits `1` and needs investigation even if some records were inserted.

Before watching B, confirm A reports `inserted: 1` and `errors: 0` for the new record. If `inserted` is zero, check the file, message ID, root and [ingestion cursor](../use/ingest#inspect-errors-and-cursors); waiting for sync cannot deliver a record that was not inserted.

Allow A to complete a worker cycle, Syncthing to transfer the packet, and B to complete an import cycle. This takes processing and transfer time plus each worker's configured wait. Do not launch another worker over the running one. On B:

macOS/Linux:

```sh
python3 agentmesh.py --database "/path/to/B-memory.db" recall "amber orchard verification" --limit 1
```

Windows Command Prompt:

```bat
py -3 agentmesh.py --database "C:\path\to\B-memory.db" recall "amber orchard verification" --limit 1
```

After two disposable bootstrap databases exchanged the packet locally, exit `0`:

Output (run on macOS)

```json
{"results":[{"content":"The amber orchard verification record is synthetic.","project":"GuideCheck","source_agent":"omp","source_type":"event"}]}
```

The local test verified record exchange, not your Syncthing connection. On your devices, finding that distinctive A-only record on B is the acceptance check. Empty results mean delivery/import/query verification is incomplete: inspect A's publication and B's pending/conflict/invalid counts. Do not delete the packet after testing.

Next, [control the worker](../use/worker) to choose ongoing operation and handle failures.
