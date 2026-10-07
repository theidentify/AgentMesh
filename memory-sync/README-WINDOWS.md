<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/agentmesh-logo-dark.svg">
  <img src="assets/agentmesh-logo.svg" alt="AgentMesh" width="384" height="80">
</picture>

# AgentMesh — Windows setup

1. Keep Syncthing running and wait until **OMP Memory Exchange** is Up to Date.
2. Install Python 3.10+ if `py -3 --version` or `python --version` does not work.
3. In the exchange folder, double-click **START-WINDOWS.cmd**. Do not unzip the
   private bootstrap into the exchange folder yourself.
4. The installer verifies package and snapshot checksums, creates the app under
   `%LOCALAPPDATA%\AgentMesh\app` and the local database under
   `%LOCALAPPDATA%\AgentMesh\data\windows.db`, and begins a 60-second sync loop.
5. Keep the terminal window open. Closing it stops memory import/export, although
   Syncthing can continue transferring files. Windows auto-start is not configured
   by this initial launcher. Run the same CMD again after login to resume safely.

Alternative command from this folder:

```text
py -3 bootstrap_windows.py --exchange .
```

For installation plus a single sync pass without a persistent console:

```text
py -3 bootstrap_windows.py --exchange . --once
```

The worker publishes `status/windows.json` containing platform, counts and sync
health so the paired Mac can verify that installation actually ran. Status does
not include memory contents, credentials or local database paths.

To ingest a local transcript into the synced SQLite database after installation:

```text
py -3 "%LOCALAPPDATA%\AgentMesh\app\sqlite_memory.py" ingest "%LOCALAPPDATA%\AgentMesh\data\windows.db" "C:\path\to\transcript.jsonl" --agent codex --project your-project
```

Use `omp`, `codex` or `claude` for `--agent`. Automatic discovery/ingestion of all
Windows agent sessions is not yet configured. The copied pure parsers are bundled;
PostgreSQL and psycopg are not required for Windows SQLite ingestion or syncing.

## Important boundaries

- This is the AgentMesh SQLite exchange, not a complete Hermes Windows installer.
- On Mac, PostgreSQL remains the production reader. A read-only mirror feeds its
  changes into the Mac SQLite copy. Windows edits sync into Mac SQLite, **not back
  into production PostgreSQL**. Reason/digest callers have not been cut over.
- Two-way additions and changes are tested, but independent edits to the same row
  or natural unique key are quarantined. A zero process exit code does not mean
  there are no conflicts; inspect `status/windows.json` and `status/mac.json`.
- The initial implementation supports one logical node per `mac`, `windows` and
  `linux` identity. Do not reuse an identity across multiple active writers.
- Keep live SQLite DB/WAL/SHM, PostgreSQL dumps and credentials outside the exchange.
- The ZIP includes private memory data. Never publish it to GitHub or share it with
  an unrelated device. File hashes detect corruption, not malicious replacement.
- Both devices need an overlapping online period for direct exchange. Offline
  writes remain local and synchronize when transport and workers are available.
- Verify Windows installation on the Windows host; macOS tests are not proof that
  Windows command execution, firewall and local ACLs are correct.
