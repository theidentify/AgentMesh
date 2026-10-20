# Encrypted Profile Log, Phase 5 (Migration from SQLite) Implementation Plan

> **For agentic workers:** execute in order. The spec is
> `docs/superpowers/specs/2026-10-18-encrypted-profile-log-design.md` (Phase 5, "Storage" constraints, "Reuse and
> retirement", "Events", "Memory broker"); read it fully first, then `profile-log/README.md`, `profile-log/FORMAT.md`
> (broker appendix and Phase 4 records), the Phase 3 and Phase 4 plans and REPORTs, and the live sources this phase
> reads: `memory-sync/schema.sql`, `sqlite_memory.py`, `recall_memory.py`, `sqlite_export.py`, `ingest_sessions.py`,
> `bounded_digest.py` (`apply`), `summarize_memory.py` (`commit_response`) and `platform_service.py`. The spec wins on
> conflict, except where this plan records an owner decision. Stop and report instead of guessing on anything that
> affects a gate below; record other choices under Deviations in the REPORT.

**Goal:** durable memories and summaries in the live SQLite database become signed, encrypted profile-log events,
idempotently and resumably, with a recorded row-to-event mapping. While SQLite stays the authority, the digest
writers dual-write to the broker and a catch-up pass repairs anything the hook missed. `recall_memory.py` can
answer from the broker (durable items and summaries) merged with local SQLite observation events, and falls back
to SQLite when the broker does not answer. All of this is **off unless a bridge config file exists**; without it,
every memory-sync behaviour and output is unchanged. Recall parity is measured on a read-only snapshot of the real
database.

## Owner decisions (2026-10-20)

- **What migrates:** `memory_items` with `status='active'` and the **latest version** of each
  `memory_summaries` `(scope, scope_key)`. `observation_events` (raw transcripts) stay local, as the spec says.
  Events carry only integer observation IDs as provenance, never transcript text. Rows that are not migrated are
  recorded in the mapping with a reason.
- **Profiles and scopes come from an owner-edited mapping file.** A dry run lists project names and row counts
  (never content). Each project goes to `work/common`, `personal/common`, a project scope (`work/<name>`), or
  `skip`. Migration refuses a row whose project is unmapped. Following the spec, the default is the `project`
  body field as a filter inside `common`. A separate project scope is created only where *who may read* differs.
- **Dual-write: a post-commit hook plus catch-up.** After a successful SQLite commit, `bounded_digest.apply` and
  `summarize_memory.commit_response` hand the touched row IDs to the migrator. This is best-effort: it never fails
  or delays the commit. A periodic catch-up compares the whole table with the log and writes whatever is missing
  or changed. Only the configured **authority device** writes migration events (see "Authority").
- **Recall switch: hybrid with fallback.** Durable items and summaries come from the broker. Event fallback and
  evidence come from local SQLite (read-only), ranked with the existing rules. If the broker does not answer, the
  result is the full SQLite answer, marked `backend: "sqlite-fallback"`, with a warning on stderr.
- **Ingest is unchanged.** `ingest_sessions.py` keeps writing raw transcripts into local SQLite. The memory write
  side (digest and summarize) is what dual-writes.
- **Auto-start: an opt-in generated service.** `platform_service.py generate` gains broker units (launchd, systemd
  and a Windows Task Scheduler XML). It only renders files, as today. Installers, upgrades and `sync_worker` do
  not change.
- **Parity:** a SQLite backup-API snapshot in a private temporary directory, migrated into a disposable broker
  home with a file key store. A deterministic generated query set (plus an optional owner query file) runs against
  both paths. The report contains only metrics and integer row IDs. Thresholds are under P5-6.
- **Rollback:** a single flag (`enabled: false` in the bridge config, or delete the file) returns recall and
  writers to pure SQLite. Migrated events stay in the local replica until the owner explicitly runs `broker sync`,
  so a rollback before then deletes the dedicated broker home. After a sync, rollback uses `profile delete`
  (crypto-shred).
- **Real data:** Codex never touches `~/.hermes` or real databases. The reviewer runs parity on a snapshot after
  review, with the owner's approval. The owner runs the real rollout after merge, following the REPORT.

Unchanged: `memory_sync.py`, `sync_worker.py`, `RANGES`, packets, conflict machinery, installers (`install_*.py`,
`windows_install.py`, `setup_*.sh`, `*.cmd`), workflows and `ingest_sessions.py` do not change.
`profile-log/` still imports nothing from `memory-sync/` or `profile-memory/`.

## Facts from the real database (read-only stats, 2026-10-20; counts and names only)

`mac.db` (356 MB): 483 memory_items (409 active, 62 resolved, 12 superseded; `supersedes_id` unused; every row has
a `memory_key`), 33 memory_summaries (29 project, 4 session), 50,505 observation_events, 1,716 memory_sources. Item
scopes: project 337, session 38, global 18, task 16. There are 25 distinct projects, many of which are
`AgentMesh--claude-worktrees-*` names. Item IDs fall in range 0 (78, pre-sync legacy) and the `mac` range (405).
Items are upserted **in place** by `memory_key` (`ON CONFLICT DO UPDATE`), and summaries by
`(scope, scope_key, version=1)`, so a row's content and status change over time. Maximum item length is 1.7 KB;
summaries average 5 KB with a maximum of 36 KB. Item `metadata` holds exact evidence **quotes from transcripts**
(`evidence`, `evidence_history`).

## Threat model additions

- The live database is read only through `file:…?mode=ro` plus `PRAGMA query_only=ON`. The migrator never opens
  it read-write, never creates tables, and takes no writer lock.
- Nothing leaves the device: the migrator, the catch-up pass, the hook and parity have no remote flags and never
  call `sync_once`. The events land in the local replica only.
- Transcript text never enters an event. Item `metadata` is **not** copied (it holds evidence quotes). Only the
  whitelisted fields listed below are.
- Migrated content passes through the broker's detectors: secrets are redacted and labels raised exactly as for
  `remember`.
- Only an agent whose grant has `allow_import` can create events that carry migration provenance. A `remember` or
  `revise` body with the reserved metadata keys is rejected, so ordinary agents cannot forge or retire migrated
  rows.

## Design to implement

### Event body convention for migrated rows (normative; add to FORMAT.md as "Phase 5 migration bodies")

Body object (the Phase 3 convention, with reserved metadata):

```
{"kind": <item kind | "summary">, "content": <text>, "project": <str|null>, "task_ref": <str|null>,
 "confidence": <0..1>,
 "metadata": {"source": {"table": "memory_items"|"memory_summaries", "id": <int>, "scope": <src scope>,
                         "scope_key": <str|null>, "memory_key": <str|null>, "version": <int|null>,
                         "created_at": <src text|null>, "updated_at": <src text|null>},
              "row_hash": <64 hex>, "evidence_event_ids": [<int>, …], "retired": false}}
```

- Items: `kind`, `content`, `project`, `confidence` from the row; `task_ref` = `scope_key` when `scope='task'`,
  else null. `evidence_event_ids` = sorted `memory_sources.event_id` for the item (integers only).
- Summaries: `kind: "summary"`, `confidence: 1.0`. `project` = `scope_key` when `scope='project'`. `task_ref` =
  `scope_key` when `scope='task'`. `evidence_event_ids` = the integer IDs `recall_memory` expands from metadata
  (`event_ids`, `batch_event_ids`, integer `source_event_ids`). String native IDs are dropped.
  **Session-scope summaries are skipped** with reason `not_recalled`, because `recall_memory` never returns them.
- `row_hash` = SHA-256 hex over `typed()` of `{table, id, kind, scope, scope_key, project, content, status,
  confidence, memory_key, version, updated_at, evidence_event_ids, target_scope}`. It is stored only inside the
  encrypted body.
- The event `created_at` is the source `updated_at` (items) or `created_at` (summaries), parsed as UTC
  (`YYYY-MM-DD HH:MM:SS` or ISO-8601). If it cannot be parsed, use the import time and count it as
  `timestamp_fallback`. The broker refuses `created_at` more than 5 minutes in the future.
- **Retirement:** a revision with `metadata.retired: true`, `content: ""` and the same `source`. It is written when
  a row becomes non-active, disappears, is remapped to `skip`, or a summary is replaced by a newer version. Indexes
  never show a head whose body has `retired: true`, in any mode. A remap to a different scope retires the old
  chain and starts a new root in the new scope (cross-scope revisions are denied, as in Phase 3).
- The reserved metadata keys `source`, `row_hash`, `evidence_event_ids` and `retired` are rejected in `remember`
  and `revise` (`invalid_request`).

### Broker changes (`profile-log/agentmesh_log/broker/…`)

- **Grant flag `allow_import`** (`broker grant --allow-import`). It is included in the grant digest. Only such an
  agent may call `import`. It still writes only to its granted write scopes.
- **Op `import`**, with the exact fields `source` (`{table, id}`), `row_hash`, `created_at` (ms), `scope` (name),
  `body` (object, or null to retire). It follows the two-step write: it returns `{"stage":"sign","unsigned":…}`,
  or `{"outcome":"unchanged","id":ULID}` without a sign step. After `sign` it returns `{"id","label",
  "redactions","outcome"}`, where `outcome` is `created`, `revised` or `retired`.
  - **Source ledger:** the broker derives `(table,id) → current head` from every event it can open whose body
    carries `metadata.source`. It builds this at load time and maintains it on ingest and sync. There is no
    separate ledger file, so the replica is the only truth, and a crash after an event persists can never lose
    the mapping.
  - Same `row_hash` as the head: `unchanged`. A different hash in the same scope: a revision of the head. A
    different scope: retire the old head, then create a new root (two sign steps; the response says
    `stage: "sign"` twice; document the sequence). `body: null` on a known source: retire. On an unknown source:
    `unchanged` with `id: null`.
  - **Reservation:** a pending import holds the `(table,id)` reservation for its session. A concurrent import of
    the same source gets the fixed error `busy` (new code), and the client retries. Disconnecting or sending any
    other op releases it.
  - Detection, redaction, labels and audit work exactly as for `remember`. The audit line has
    `op: "import"` and logs no source IDs or hashes beyond counts.
- **Recall `compat` mode** (new optional request field `compat`, bool, default false; CLI `recall --compat`).
  With `compat: true`, selection mirrors `recall_memory.recall` for durable items and summaries:
  - Durable (`source.table == memory_items`): included if `source.scope == 'global'`; or, with no project and no
    task refs, everything; or, with a project, `project == P or scope_key == P`; or `upper(scope_key)` is in the
    task refs. Text match is as today.
  - Summaries (`source.table == memory_summaries`): included only if (`source.scope == 'project'` and
    `scope_key == P`) or (`source.scope == 'task'` and `upper(scope_key)` is in the task refs), with no text match
    requirement, and never dropped by the matched-terms rule.
  - Each result gains `source_type` (`"durable"` or `"summary"`) and `source_ref` (`{table, id}` or null).
    `candidate_counts.summaries` counts summaries. Non-migrated events behave as before (`source_type:
    "durable"`, `source_ref: null`).
  - Without `compat`, recall behaves exactly as in Phase 3, except that retired heads are hidden.
  Add the new columns (`source_table`, `source_scope`, `scope_key`, `retired`) to the index. Bump the snapshot
  schema so old snapshots rebuild.

### Migrator (`profile-log/agentmesh_log/migrate/`)

```
source.py   read-only SQLite reader (mode=ro + query_only), schema check, row → body, row_hash; backup-API snapshot
mapping.py  mapping file load/validate, project → target scope or skip, plan (names + counts only)
run.py      migrate all / rows / catch-up: build intents, call the broker via BrokerClient import + sign, retry busy
ledger.py   mapping report from the broker's ledger (via a new read-only op) and the source rows
```

- **Mapping file** (`agentmesh-migration-mapping-v1`, UTF-8 JSON, strict fields): `global` (target for
  `scope='global'` items), `null_project` (target for items with a null project), `projects`
  (`{name: target}`), `prefixes` (a list of `{prefix, target}`; exact match first, then the longest prefix).
  A target is `"<profile>/<scope path>"` or `"skip"`. Every target scope must exist in the broker home, or the run
  stops before writing anything.
- **CLI** (`python -m agentmesh_log.cli migrate …`, JSON output; nothing prints content):
  - `migrate plan --db DB [--mapping M]`: project names with item/summary counts, and unmapped names. No broker is
    needed. This is the dry run the owner uses to write the mapping.
  - `migrate run --db DB --mapping M --agent-key K`: every in-scope row (create, revise or retire). It is
    idempotent and resumable.
  - `migrate rows --db DB --mapping M --agent-key K --ref memory_items:ID … `: only the named rows (the hook).
    At most 500 refs.
  - `migrate catch-up …`: same as `run`, plus retiring ledger entries whose row is gone, non-active or skipped.
    `run` and `catch-up` share the code; `catch-up` is the name the scheduled unit uses.
  - `migrate report --agent-key K [--db DB --mapping M] --out FILE`: writes the mapping
    `{"table:id": {"event": head ULID, "chain": [ULIDs], "scope": name, "status": created|revised|retired|skipped,
    "reason": …}}` as a 0600 file, refused inside the replica. With `--db`, every in-scope and out-of-scope row
    appears exactly once. The ledger read uses a new op `import_ledger` (`allow_import` only), which returns
    source refs, ULIDs and scope names, never content or hashes.
  - `broker init … --file-keystore` for **disposable homes only**: it is refused unless the home is inside the
    system temp directory or `AGENTMESH_ALLOW_FILE_KEYSTORE=1` is set (tests set it). It prints the recovery
    shares as normal.
- **Run summary** (stdout JSON): counts by outcome, `unmapped` (names only), `skipped` by reason,
  `timestamp_fallback`, `busy_retries`, `errors` (class names only), `secret_labelled`.

### Memory-sync bridge (`memory-sync/profile_log_bridge.py`, new)

Standard library only. It never imports `agentmesh_log`, and it talks to profile-log by subprocess argv (no
shell).

- **Config** at `$AGENTMESH_PROFILE_LOG_BRIDGE`, or else `<database>.profile-log.json`. It has the strict fields
  `format` (`agentmesh-profile-log-bridge-v1`), `enabled`, `recall`, `dual_write`, `authority` (bool),
  `cli_argv` (absolute argv list that starts the profile-log CLI), `agent_key` (recall agent),
  `import_agent_key`, `mapping`, `profile` (the single profile recall queries) and `timeout_seconds` (1–30).
  On POSIX it must be a regular, non-symlink file owned by the user with mode 0600, and every path must be
  absolute. An invalid config disables the bridge with one stderr line and never raises into callers.
- **`notify_rows(database, refs)`:** if `enabled and dual_write and authority`, start `cli_argv + ['migrate',
  'rows', …]` **detached** (stdin/stdout/stderr DEVNULL; `start_new_session` on POSIX, `CREATE_NO_WINDOW |
  DETACHED_PROCESS` on Windows) and return immediately. Any exception is swallowed after one sanitized stderr
  line (class name only). Called by `bounded_digest.apply` and `summarize_memory.commit_response` **after**
  `commit()` returns, with the item and summary IDs they wrote.
- **`broker_recall(plan, pool)`:** runs `cli_argv + ['recall', query, '--compat', '--agent-key', K, '--profile',
  P, '--limit', min(pool,100), --project/--task from the plan]` with a timeout. It returns the parsed results, or
  raises `BrokerUnavailable` on a non-zero exit, timeout, invalid JSON or wrong shape.

### Hybrid recall (`memory-sync/recall_memory.py`)

- With no config, or `enabled`/`recall` false: **byte-identical output and identical SQL** to today (existing
  tests unchanged, plus a golden test).
- Enabled: open SQLite read-only as today, compute the plan and `_infer_project` locally, then query events
  locally as today. Get durable and summary candidates from `broker_recall` instead of SQLite. Map each broker
  result to the existing row shape: `id` = `source_ref.id` (or the ULID string for non-migrated events), plus the
  columns of `memory_items` / `memory_summaries` that `recall_memory` returns today, filled from the body and
  source. `metadata` is `{"profile_log": {"id", "profile", "scope", "label"}}`. Re-score with the local `_score`,
  then sort and select with the existing rule. Evidence comes from local SQLite using `source_ref.id` (durable:
  `memory_sources`; summary: `evidence_event_ids`). The top level gains `backend: "broker"`.
- On `BrokerUnavailable`, return the unchanged SQLite answer plus `backend: "sqlite-fallback"`, with one stderr
  line. The `--backend sqlite|auto` CLI flag (default `auto`) forces SQLite for debugging.

### Parity (`memory-sync/profile_log_parity.py`, new; run with the profile-log interpreter)

`python memory-sync/profile_log_parity.py --db DB --mapping M [--queries FILE] [--sample 200] --out REPORT`:

1. Snapshot DB with `sqlite3.Connection.backup` from a `mode=ro` source into a new 0700 temporary directory.
2. Create a disposable broker home there (`--file-keystore`), with the profiles and scopes the mapping names, an
   import agent and a recall agent. Migrate the snapshot in process (it may import `agentmesh_log` by adding
   `../profile-log` to `sys.path`; this direction is allowed).
3. Queries: deterministic (seed 0). For each of `--sample` active items, the first 3–6 analyzer terms of its
   content; one `"<project> status"` per mapped project; optional owner queries (one per line). Queries never
   appear in the report; it holds per-query HMACs with a random per-run key that is discarded afterwards.
4. For each query, run `recall_memory.recall(snapshot)` (old) and the hybrid against the disposable broker (new),
   with limit 12. Compare the **durable and summary row IDs**. The old side is restricted to rows mapped into the
   queried profile and not withheld as `secret`; withheld and unmapped rows are counted separately.
5. Report (0600 JSON): `overlap_at_12` (mean Jaccard), `top5_hit` (mean fraction of the old top-5 IDs in the new
   top-5), `exact_order` (fraction of queries with an identical ID order), per-query failures as `{hmac, missing
   ids, extra ids}`, counts, timings. **No content, queries or project names.** Delete the temporary directory on
   exit, including on error.

### Generated services (`memory-sync/platform_service.py`)

`generate --unit broker|catch-up` (default stays the sync unit, so existing output is unchanged):
- `broker`: launchd `org.agentmesh.profile-log.broker` (RunAtLoad, KeepAlive, Umask 077), systemd, and Windows
  Task Scheduler XML (logon trigger, restart on failure). It runs `cli_argv broker run --home H` with **no**
  `--sync-interval`.
- `catch-up`: launchd `StartInterval` (default 300 s), a systemd service plus timer pair, and Windows
  time-trigger XML. It runs `cli_argv migrate catch-up …`.
- Rendering only: exclusive create, 0600, absolute-path and control-character validation as today. Nothing is
  loaded or registered.

### Authority

Only one device writes migration events. `authority: true` goes in that device's bridge config (the Mac, which
holds all rows through packet sync). Hooks and catch-up on a non-authority device do nothing. Rows written on
other nodes reach the authority's SQLite through the existing sync and are picked up by catch-up. The README warns
that two authorities would duplicate roots. As a guard, `migrate run/rows/catch-up` refuses when the import
agent's broker already holds migration events written by **another device**, unless `--take-over` is given.

## Ground rules

- Work only in `/Users/krisanathep/Workspaces/AgentMesh/.claude/worktrees/epl-phase5-235b0b`. You may change:
  `profile-log/**`; in `memory-sync/`: `recall_memory.py`, `bounded_digest.py`, `summarize_memory.py`,
  `platform_service.py`, the new `profile_log_bridge.py` and `profile_log_parity.py`, the module/file lists in
  `build_package.py` and `standalone_build.py` (add `profile_log_bridge.py` only), their tests and new test files;
  this plan's REPORT. **Do not touch** `memory_sync.py`, `sync_worker.py`, `RANGES`, packets, signed packets,
  conflict/resolution code, `ingest_sessions.py`, installers, `*.cmd`, `setup_*.sh`, workflows or other docs.
- **Do not commit, push or create branches.** The reviewer commits.
- No network. Interpreters: `~/.cache/agentmesh-logvenv/bin/python` for `profile-log/tests` and for memory-sync
  tests that need `agentmesh_log`. `~/.cache/agentmesh-testvenv/bin/python -m pytest -q memory-sync` for the
  memory-sync suite; tests that need `agentmesh_log` use `pytest.importorskip('pyrage')`. Do not pip install;
  there are no new dependencies.
- **Never read `~/.hermes`, real databases, identities, keychains or transcripts.** Tests build synthetic SQLite
  databases from `memory-sync/schema.sql` in temporary directories, in both WAL and rollback-journal modes.
- TDD: write each failing test first, confirm it fails for the stated reason, then implement.
- Never print, log or put into exceptions any key, plaintext body, query text, transcript text or row content.
- Write text files with an explicit `encoding='utf-8'` (Windows CI uses cp1252).
- Keep every existing test passing and FORMAT v1 event bytes unchanged. Phase 3 recall without `compat` keeps its
  result field set (only retired heads become hidden).
- Sockets: tests that cannot bind in the sandbox fail explicitly; never skip them. The reviewer reruns them.
- No GATES.md or other working files left behind. Match the compact style of the surrounding code.

## Tasks

1. **Source reader and bodies.** `migrate/source.py`: read-only open, schema check, item and summary bodies,
   evidence IDs, row_hash, timestamp parsing, backup snapshot. Tests on synthetic WAL and rollback DBs.
2. **Mapping.** `migrate/mapping.py`: strict file, exact and prefix rules, `skip`, unmapped refusal, `migrate
   plan`.
3. **Broker import.** `allow_import`, the `import` and `import_ledger` ops, the derived ledger, reservation and
   `busy`, retirement, reserved-key rejection, index columns, retired-head hiding, FORMAT appendix.
4. **Recall compat.** `compat` selection and result fields, summaries, candidate counts, snapshot schema bump.
5. **Runner and report.** `migrate run/rows/catch-up/report`, retries, authority guard, run summary,
   `--file-keystore`.
6. **Bridge and hooks.** `profile_log_bridge.py`, config validation, detached notify, hooks in `bounded_digest`
   and `summarize_memory` after commit, hybrid recall and fallback, packaging list entries.
7. **Parity tool and services.** `profile_log_parity.py`; `platform_service.py` broker and catch-up units.
8. **Docs and REPORT.** README "Migration from SQLite" (mapping, plan, run, bridge config, services, rollout on
   the Mac, later Windows, rollback), FORMAT "Phase 5 migration bodies", and the REPORT mapping gates to tests.

## Acceptance gates (each with a dedicated test the reviewer can mutation-test)

- **P5-1 Read-only source:** after `plan`, `run`, `rows`, `catch-up`, hybrid recall and parity against a synthetic
  DB (WAL and rollback-journal), the main DB file's SHA-256 and the set of `sqlite_master` entries are unchanged,
  and no rollback journal appears. The reader's connection refuses a write (`attempt INSERT → readonly error`).
  Swapping `mode=ro` for `rw` in the reader must fail this gate.
- **P5-2 One event per row, recorded:** after `run`, every active item and every latest non-session summary has
  exactly one live chain head carrying its `(table,id)`, and `migrate report --db` lists every source row exactly
  once (migrated or skipped with reason). Resolved/superseded items, older summary versions and session summaries
  appear as `skipped` with the right reasons. There are no duplicate roots per source.
- **P5-3 Idempotent:** a second `run` writes zero events (all `unchanged`). Changing one row's content or
  `updated_at` produces exactly one revision. Setting one row to `resolved` produces exactly one retirement, and
  that row disappears from recall in both modes. A remap to another scope retires the old chain and creates one
  new root. A row that is unchanged after a remap back produces nothing.
- **P5-4 Resumable and concurrent:** inject a failure after k of n events (the process exits). The rerun finishes
  with exactly n heads and no duplicates. Two concurrent `run` processes plus a `rows` hook against one broker end
  with no duplicate roots (reservation and `busy` retry). Removing the reservation must fail this gate.
- **P5-5 Nothing sensitive leaves the device or enters events:** marker strings placed only in
  `observation_events.content`, item `metadata.evidence` / `evidence_history` and summary metadata never appear in
  any decrypted event body. Markers in row content never appear in plaintext under the broker home, replica or
  parity temp directory (P3-5 style, UTF-8 and UTF-16). A generated secret in row content is redacted and labelled
  `secret`. The migrator, hook and parity create no remote storage and never call `sync_once` (spy); the replica
  outbox afterwards equals the number of migrated objects. The parity report contains none of the query, content
  or project-name markers.
- **P5-6 Parity (synthetic, automated):** on a synthetic DB of at least 300 items and 30 summaries with Thai and
  English content, tasks, globals and several projects, the parity tool reports `overlap_at_12 ≥ 0.95` and
  `top5_hit ≥ 0.98`, with the old side restricted as described. The test prints the measured values. Breaking the
  compat global/summary selection must fail this gate. **Real-data parity** uses the same thresholds and is run by
  the reviewer after review (owner-approved, snapshot only). The REPORT has a placeholder section for it.
- **P5-7 Import authority:** an agent without `allow_import` gets `denied` for `import` and `import_ledger`. A
  `remember` or `revise` with reserved metadata keys gets `invalid_request`. An import into a scope outside the
  agent's write grant is denied. A migration event signed by a non-import agent (crafted in the replica) is not
  admitted to the ledger.
- **P5-8 Dual-write hook:** with the bridge enabled and `authority: true`, `bounded_digest.apply` and
  `summarize_memory.commit_response` start exactly one detached `migrate rows` with the IDs they wrote, after
  commit. With the bridge absent or disabled, or `authority: false`, nothing is started. A hook that raises, or a
  CLI that hangs, never changes the commit result or blocks for longer than 1 s (spy and timing). The hook must not
  run when the commit fails or returns `stale`. Catch-up then picks up a row the hook missed.
- **P5-9 Hybrid recall and fallback:** with no config, `recall()` output is identical to a stored golden output
  for a fixture DB (including key order) and has no `backend` key. Enabled against a stub CLI, it returns
  `backend: "broker"`, the existing result key set, broker content and local evidence. A broker that exits
  non-zero, times out, returns malformed JSON or a wrong shape gives the full SQLite answer with
  `backend: "sqlite-fallback"` and one stderr line. An invalid config (bad mode, symlink, relative path, extra
  field) behaves like no config, plus one stderr line.
- **P5-10 Rollback:** setting `enabled: false` restores the golden output and stops the hooks, with no other
  state change. The REPORT documents removing the dedicated home before any `broker sync`, and `profile delete`
  after one.
- **P5-11 Services:** the existing `generate` output is byte-identical for the sync unit. The broker and catch-up
  units for macOS, Linux and Windows render the expected argv (snapshot tests), contain no `--sync-interval`,
  validate paths as today, and refuse to overwrite.

### Whole change

- The full profile-log suite passes from the repository root and from `profile-log/`, and the memory-sync suite
  passes with its usual known baseline failures (list them). The reviewer reruns the socket tests outside the
  sandbox, and the three-OS CI passes.
- End-to-end test: a synthetic DB with the bridge enabled. `migrate run`, a digest `apply` that changes and
  resolves rows (the hook fires), then `catch-up`, then a hybrid recall that matches the SQLite recall on that DB
  for the fixed queries.
- `git status` shows changes only in the allowed files.
- The REPORT maps every gate to tests, lists deviations, and lists the owner steps: write the mapping from
  `migrate plan`; real-data parity (reviewer); the Mac rollout (broker home, scopes, grants, presence window for
  the recall agent's first access, `migrate run`, bridge config, generate and load the units); and the Windows
  rollout after the Windows key-store smoke (bridge with `authority: false`, recall only, once paired).
