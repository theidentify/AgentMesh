# Portable SQLite consumers (opt-in)

These source adapters prepare a future cutover. Installing or packaging them does
**not** change production authority, caller rules, services, cron, or transport.
PostgreSQL remains authoritative until explicit deployment and verification.
Python 3.10+ with SQLite FTS5/JSON support is required; no PostgreSQL client is
needed by the SQLite commands. Windows behavior still needs a real-host test.

## Read-only recall and context

Run from the installed `memory-sync` source directory, supplying a private local
SQLite database outside any exchange/synchronized folder:

```sh
python recall_memory.py /private/local/memory.db "current constraints" --project demo
python recall_memory.py /private/local/memory.db "deployment" --task DEMO-1
python shared_memory_context.py --database /private/local/memory.db --project demo
python shared_memory_context.py --database /private/local/memory.db --task DEMO-1 --query deployment --json
```

`AGENTMESH_DATABASE` can replace the explicit context database argument. Recall
returns `query_plan`, ranked durable items/summaries, raw fallbacks and original
evidence IDs. Active constraints, decisions, preferences, project/task scope and
stable keys are retained. SQLite and PostgreSQL search tokenization, candidate
membership and tie ordering can differ; do not promise identical ranking.
Context's recent unsummarized rows use exact receipts and batch provenance, not
`id > cursor`. It never silently assumes a legacy scalar checkpoint covered
future peer/low-ID arrivals. Seed only an independently verified current snapshot
with the explicit Phase1 coverage command, on an authorized copy first.

## Deterministic export and disabled retention

```sh
python sqlite_export.py /private/local/memory.db --root /private/staging/OMP-Memory
python retention_audit.py /private/local/memory.db
```

Export includes every event, source-path session, project/task index, active
memory, preferences, summaries, and linked evidence. Names hash the entire
natural identity (not a short native session prefix or numeric ID), preserving
multi-peer IDs and path collisions. Generated source links use `#^event-ID`.
Stored prose is escaped as data, not generated wiki navigation. A full staging
projection is link-checked before publishing, all target paths are preflighted,
and manual notes/symlinks cause a collision failure rather than overwrite.
Individual note replacement is atomic; publication of the whole directory is
**not** transactional. A failed/interrupted publish must be retried before a
scheduler window can succeed. Prior generated notes are not pruned automatically.
The retention command is **audit-only and unconditionally disabled**: it cannot
delete database rows or source files. Coverage alone never permits deletion.

## Bounded fixed-window scheduling

```sh
python digest_scheduler.py /private/local/memory.db --root /private/staging/OMP-Memory \
  --command-json '["/resolver/venv/bin/python","/installed/source/digest_worker.py","--resolver-root","/resolver/source"]' \
  --model gpt-6-luna --conflict-model gpt-6.1-sol \
  --related-chars 45000 --context-chars 80000 \
  --metrics-log /private/summary-metrics.jsonl --boot-id OS-STABLE-BOOT-ID
```

The `45000`/`80000` values are an **explicit measured-snapshot override**, not
relaxed defaults. Defaults remain 28000/60000 and fail closed on overflow without
dropping old rules or truncating source evidence. The resolver command can also
come from `AGENTMESH_DIGEST_COMMAND_JSON`. Never place keys or account data in
workflow configuration or Git. A caller may supply a stable per-OS-boot identifier
using `AGENTMESH_BOOT_ID`; omitting it gives daily-only gating. A process UUID is
not a valid boot identity. No scheduler or startup hook is installed here.

Each due daily/boot/forced window persists its entire eligible event ID manifest,
manifest checksum, export root and bounded/model configuration in local
`_agentmesh_digest_windows`. The numeric highwater is only a bound for those exact
snapshot members. Late lower-ID arrivals wait for the next window, never vanish.
A pending window cannot be widened by `--force`, configuration changes or restart.
Each accepted batch commits separately and remains covered after a later failure.
At most eight batches run per tick by default; remaining work resumes next tick.
Success requires the manifest to drain and a complete verified export. Export
retry makes no model calls; an idle/scheduled tick also makes zero model calls.
A nonblocking portable OS lock prevents overlapping coordinators; the bounded
runner also fences standalone applies. A pending configuration mismatch blocks,
rather than silently changing the authorized window. Reuse the same arguments
on restart. Canonical content-free metrics preserve unknown token/cost values as
null. `gpt-6.1-sol` is only the schema-valid explicit-conflict fallback.

For a future worker integration, set the private workflow object's
`"summary_backend": "bounded"`, `"summarize": true`, and supply `summary` options
matching `digest_scheduler.tick` (`root`, `command`, bounds, metrics path and
optional boot ID). The selector bypasses the old summarizer entirely. Existing
configurations retain legacy behavior; do not enable both production digest jobs.
No caller or Claude hook is installed or invented by this preparation.

## Insight authority and accounting

`INSIGHT_PRIMARY_BACKEND=sqlite` selects SQLite authority/default **only when
explicitly configured**; `pg`/`postgres` select PostgreSQL. Without a selector,
PostgreSQL remains the authority label and SQLite remains staging, even when only
SQLite is available. PostgreSQL stays accessible as read-only comparison after a
future explicit switch. `INSIGHT_PG_DSN` is supplied privately, never committed.
Insight's metrics adapter recognizes both established PG statuses and SQLite
`committed`/`validated` records; this is schema compatibility, not workload parity.
The existing production Insight process need not be restarted during staging.

The private bootstrap builder allowlists these modules and this documentation.
It does not ship live DB/WAL/SHM, runtime configuration, account keys or private
proof artifacts. Baseline archives themselves contain private memory and must
remain private. Production activation still requires writer/caller deployment
checks, no competing legacy summarizer/manual writer, retention disabled,
independent review, rollback backups, and fresh remote application/recall proof.
