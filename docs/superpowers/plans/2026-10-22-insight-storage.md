# Insight storage panel

Owner request (2026-10-22): "เราให้ insight ขึ้นสรุป stat ด้วยสิ ขนาดไฟล์ ไรพวกนี้ใน sqlite r2" — have Insight show a storage summary: file sizes and stats for SQLite and R2.

Everything here is display only and read-only: Insight never deletes, vacuums or writes.

## Data (new `insight/storage.py`, endpoint `GET /api/storage`)

Compute everything in a background thread, cache it for 10 minutes, and serve the cache.

- **Request path:**
  - never runs `dbstat`;
  - never walks directories;
  - while the cache is empty, returns `{"available": false, "pending": true}` and starts one computation.
- **Concurrency:**
  - only one computation runs at a time;
  - a failed computation keeps the previous cache and records `error`, a class name only.

1. **Staging SQLite.** Use the `--sqlite` path, normally `~/.agentmesh/memory-sync/data/mac.db`. Open it read-only through the existing `adapters.sqlite_ro`.
   - File sizes of `mac.db`, `-wal` and `-shm`.
   - `page_size`, `page_count` and `freelist_count`. Reclaimable bytes = `freelist_count * page_size`.
   - Top 15 tables and indexes by size, via `SELECT name, SUM(pgsize) FROM dbstat GROUP BY name`.
     - When `dbstat` is unavailable (OperationalError), set `dbstat: false` and give row counts for the tables instead.
   - Row counts for `observation_events`, `memory_items`, `memory_summaries` and `summary_state`; missing tables give null.
   - Group sizes for names matching `_sync_%` and `_pg_%` (or `pg_%` mirrors, if present). Label them `retired: true`, note "removed in 7b".
2. **Profile log, local**, under `<agentmesh_home>/profile-log/`:
   - `replica/`: total bytes and file count, split by top-level prefix (`events`, `keys`, `registry`, `connectors`, other).
   - `index/`: total bytes.
   - `audit.jsonl`: size.
   - Only sizes and counts. Never file names below these fixed directories.
3. **R2.** Add no LIST calls.
   - **Object count:** `remote_objects` from `profile-log/sync-status.json`.
   - **Bytes:** an estimate, `replica bytes * remote_objects / local_objects`, labelled `estimate: true`. The replica mirrors remote.
   - **Monthly ops estimate** from `interval_s`:
     - syncs per month = `30*86400/interval_s`;
     - LIST calls per sync ≈ the number of `events/<profile>/<scope>/` prefixes in the replica + 1 (`keys/`), and every LIST is Class A;
     - Class A per month ≈ syncs × lists + uploads (the PUTs are small: use the `uploaded` average if present, else 0);
     - Class B ≈ downloads.
     - Show the result as an estimate next to the free tier (1M Class A, 10M Class B).
4. **`~/.agentmesh` breakdown.** Fixed categories only; each is a total size plus a count where useful:
   - `memory-sync/` (data and app)
   - `profile-log/`
   - `exchange/` (retired, the owner can prune it)
   - `logs/`
   - `backups/`
   - `jobs/`
   - `venv/`
   - `versions/`
   - `app/` (current release plus `profile-log.old-*` count and size, which are reclaimable)
   - `status/` Insight releases: count, total, and the size of releases other than the running one (reclaimable)
   - `gateway-*` deploy directories: count, total, and everything except the newest (reclaimable)
   - Also give a grand total and a short `reclaimable` list of `{category, bytes, note}`.
   - Use `os.scandir` with `follow_symlinks=False` and never follow symlinks.
   - Bound the walk at 200k entries; set `truncated: true` beyond that.
5. **Remote.** Add `/api/storage` to `remote_access.ALLOWED_PATHS`; sizes and counts are metadata. The response contains no file names except the fixed category labels above and the SQLite table names, which are schema, not content.

## UI

- Add a new **Storage** tab in `insight/index.html`, after Profile log, with an icon in the nav style. Include it in `remoteViews`.
- Show:
  - **Summary cards:** `~/.agentmesh` total, SQLite file (DB + WAL), profile log local, R2 objects / ≈ bytes.
  - **SQLite panel:**
    - sizes;
    - the reclaimable free pages;
    - the top-tables table, with retired rows labelled "removed in 7b";
    - row counts;
    - if `dbstat` is unavailable, a note plus the row-count fallback.
  - **Profile log panel:** the replica split, index and audit sizes.
  - **R2 panel:** object count, estimated bytes, monthly ops estimate vs free tier, and the sync interval.
  - **Disk breakdown:** a table of categories sorted by size, with reclaimable rows highlighted. The note says "Insight never deletes anything; remove these yourself if you want the space back."
  - "Computing… (first load)" while the result is pending.
  - "Last computed <time>" and "refreshes every 10 minutes".
- Human-readable sizes (KB, MB, GB). Escape everything.

## Gates (each with a test that fails if the property breaks)

- **ST-1:** the cache works — two requests within 10 minutes trigger one computation (count calls with an injectable clock and a computing function). The first request returns pending without blocking.
- **ST-2:** read-only. The SQLite connection is opened through `sqlite_ro` (mode=ro), and the test asserts a write fails. No file in the walked tree is opened for writing (patch `open`/`os.open` to assert read flags only, or compare mtimes and contents before and after).
- **ST-3:** when `dbstat` is unavailable, the result has `dbstat: false` with row counts, and the UI renders it (Node harness, like `test_sync_view.py`).
- **ST-4:** `/api/storage` is in `ALLOWED_PATHS`, and the remote tests cover it. No other allowlist entries change.
- **ST-5:** the walk does not follow symlinks (a symlink to a big directory is not counted) and is truncated at the bound.
- **ST-6:** R2 ops estimate math for a given `interval_s` and prefix count.
- **ST-7:** the UI escapes table names and labels (a malicious table name in a fixture).

## Out of scope

- Deleting or vacuuming.
- Extra R2 calls.
- Changes to profile-log code.

## Costs section (addendum: "แล้วก็แสดงพวก cost ต่าง ๆ" — "and show the various costs too")

This is a **Costs** section on the same Storage tab. Every figure is labelled "estimate".

### Prices

Prices live in `insight/pricing.json`. It ships in the release and is non-secret and editable. An optional owner override at `<agentmesh_home>/insight-pricing.json` replaces individual keys.

- Strict validation: numbers ≥ 0, ISO date strings.
- A bad file falls back to the defaults and shows "pricing file invalid".
- The UI shows "prices as of <date>" next to the source URLs.

Defaults, checked 2026-10-22 from the official pages:

- **R2 Standard** (https://developers.cloudflare.com/r2/pricing/):
  - storage $0.015 per GB-month;
  - Class A $4.50 per million;
  - Class B $0.36 per million;
  - free tier 10 GB-month, 1M Class A, 10M Class B per month;
  - egress free.
  - ListObjects and PutObject are Class A; GetObject and HeadObject are Class B; DeleteObject is free.
- **Anthropic API** (https://platform.claude.com/docs/en/about-claude/pricing), USD per million tokens:

  | Model | input | cache write 5m | cache write 1h | cache read | output |
  |---|---|---|---|---|---|
  | `claude-haiku-5-5`, prompts ≤ 100k tokens | 0.10 | 0.125 | 0.20 | 0.01 | 0.50 |
  | `claude-haiku-5-5`, prompts > 100k tokens | 0.50 | 0.625 | 1.00 | 0.05 | 2.50 |
  | `claude-sonnet-5-5` | 2.00 | 2.50 | 4.00 | 0.10 | 10.00 |
  | `claude-opus-5-5` | 4.00 | 5.00 | 8.00 | 0.20 | 20.00 |

  - A model missing from the table gives cost `null`, shown as "unknown" (never 0).
  - Haiku's long-context tier applies per run when `input_total_tokens > 100000`.

### 1. R2 cost

- **Storage:** GB-month from the estimated R2 bytes (the Storage section).
- **Class A per month:** syncs × LIST pages + PUT uploads.
  - LIST pages per sync = the number of prefixes the incremental listing touches. Use the replica's `events/<profile>/<scope>/` count + 1; label it estimated.
  - Uploads: from the last sync's `uploaded`, scaled per day if no better data exists; label it estimated.
- **Class B per month:** from downloads.
- Compute $ above the free tier per category, then a total. Today it is almost certainly $0.00; show that, with the headroom percentage.

### 2. Summary job (Claude)

- **Source:** reuse what `/api/metrics` already reads (`metrics.report` / the bounded runs). Insight already has per-run `model`, `input_uncached_tokens`, `cache_read_tokens`, `output_tokens` and `input_total_tokens`.
- **Cost per run:** `uncached × input + cache_read × cache read + output × output`.
  - Treat cache-write tokens as part of uncached input unless they are recorded separately.
  - When the breakdown is unknown, use `input_total` at the input price and flag it.
- **Show:**
  - this calendar month so far;
  - the last 7 days;
  - a 30-day projection from the last 7 days;
  - a split by model.
- **Big note:** "API-equivalent cost. The summary job runs on your Claude subscription, so this is not billed per token; it shows what the same usage would cost on the API." The job hits subscription 429s that are shared with Claude Code. If runs record an API key source, adapt; otherwise keep the note.

### 3. Cloudflare Worker, KV and Access

- No local counters exist. Show "see Cloudflare dashboard" plus the free-tier facts in `pricing.json`, as text only.
- No API token or any new secret. No network calls from Insight.

### Gates

- **ST-8:** the cost math from fixed inputs:
  - R2 above and below the free tier;
  - Haiku in both tiers;
  - a mixed-model month;
  - the 30-day projection.
- **ST-9:** a missing model price or a missing R2 figure gives `null`, shown as "unknown" (never 0).
- **ST-10:** an override file merges, and an invalid override falls back to the defaults with a flag.
- The UI renders the Costs section with the API-equivalent note (Node harness).

## Compute (second addendum: "แสดง cost ในการใช้ resource ต่าง ๆ ด้วยสิ compute time" — "show the cost of the various resources too, compute time")

This is a **Compute** part of the Costs section. Every figure is an estimate.

### 1. Local services

The services are:
- broker (`org.agentmesh.profile-log.broker`)
- bridge (`org.agentmesh.profile-log.bridge`)
- Insight (`org.agentmesh.insight`)
- cloudflared (a system daemon; find it by exact process name `cloudflared`)

How to sample them:
- Get each PID from `launchctl list <label>` for the user agents, and `pgrep -x cloudflared` for cloudflared.
- Then run one `ps -o pid=,time=,rss=,etime= -p <pids>`. That gives cumulative CPU time, RSS and elapsed time.
- Run it inside the background computation, as subprocess argv with no shell and a 5 s timeout. No psutil.
- **Show:** CPU-seconds, CPU-seconds per day since start, and RSS in MB.
- A service with no PID shows "not running", never 0.
- Parse `ps` time formats strictly: `[[dd-]hh:]mm:ss[.cc]`.

### 2. Per-run jobs

Jobs are wrapped by `memory-sync/job_notify.py`, and the live copy is identical to the repo.

**Change `job_notify.run`:**
- Measure the wrapped child: wall seconds (monotonic), and child CPU from the `resource.getrusage(RUSAGE_CHILDREN)` delta (user+sys) and `ru_maxrss`.
- Add `wall_s`, `cpu_s` and `maxrss_mb` (rounded) to the per-run JSON line it already prints. That line lands in `~/.agentmesh/status/launchd-<job>.log` and holds no content.
- Keep every existing field and behaviour.
- Add a test in `memory-sync/test_job_notify.py`.
- Note in the PR that this file is shared with memory-sync. Phase 7a (#84) is merged; check that no open PR touches `job_notify.py`.
- Old lines without these fields still parse.

**Insight reads the same logs** that `adapters.job_failures` already reads (extend it or add a sibling). For each job:
- runs per day;
- wall and CPU time for today, the last 7 days and a 30-day projection;
- the average CPU share, `cpu_s / wall_s`.
- For the summary job, "waiting on the Claude API" ≈ `wall_s - cpu_s` (labelled approximate). Use the metrics `duration_seconds` where it adds detail.
- Catch-up: `org.agentmesh.profile-log.catch-up` is not wrapped by `job_notify`, so show "not recorded". Don't change its plist in this PR; list it as a follow-up.

### 3. Worker

Show "see Cloudflare dashboard (requests, CPU ms)". No token, no network.

### 4. Energy (optional, one line)

`CPU-hours × watts_per_core` (default 5 W) `× price_per_kwh` (default: null). With a null price, show kWh only. Both live in `pricing.json` under `energy`, and the line is labelled as a rough estimate.

### Gates

- **ST-11:** the `ps` time parsing and the per-day math, using fixed inputs.
- **ST-12:** a missing process shows "not running" (null), not 0.
- **ST-13:** `job_notify` records `wall_s`, `cpu_s` and `maxrss_mb`; old log lines without them still parse; and the totals and projection math hold on a fixture log.
- **ST-14:** no subprocess runs on the request path. Only the background computation runs `ps` and `launchctl`, through argv.

## Buffers, leftovers and network IO (addenda: "มันมีพวก buffer อะไรไหม เอามารวมด้วย" — "are there any buffers? include them too", and "network io ต่าง ๆ" — "all the network IO")

### A. Buffers and queues

**What every buffer shows:** its size or count, the oldest item's age where known, and a warning when it is stuck (the age is over a threshold, or the count grew across the last two computations). A missing source shows "unknown".

1. **R2 upload outbox.** Source: `profile-log/sync-status.json` `outbox`. It is stuck when the outbox is above 0 for more than 3 syncs; use the age of the `at` field.
2. **Gateway remember outbox.**
   - Source: the bridge status (`last_drain` and the pending count), if a status file exists. Find it in `profile-log/agentmesh_log/bridge/`.
   - If there is none, show "unknown", and list "bridge writes drain status" as a follow-up.
   - No R2 LIST from Insight.
3. **Summary backlog.**
   - Count: pending events not yet summarized, from the coordinator state `pending_ids` length (`layout()['coordinator']`), or the metrics `events_pending_after`.
   - Age: the oldest pending event time, if cheap. One indexed `MIN(occurred_at)` over the ids is fine; otherwise unknown.
4. **Ingest lag, per source:**
   - the newest `observation_events.occurred_at`;
   - the `ingestion_cursors` max `updated_at`;
   - the time since the ingest job's last ok run (`job-ingest.json`).
   - Don't read transcript files.
5. **Catch-up waiting rows.** Use the `profile-log-status.json` `last_catch_up` counts (outcomes/skipped). If there is no "waiting" count, show "unknown" and list it as a follow-up. Don't open the ledger here.
6. **Staging WAL.** The `mac.db-wal` size, warning above 64 MB.
7. **Broker audit log.**
   - The `audit.jsonl` size and growth per day. Store the previous size and time in memory between computations; until two samples exist, use (size / (now − the first line's timestamp)) when cheap.
   - Note: "grows forever, no rotation". List "audit log rotation" as a follow-up and report it in the PR.

### B. Reclaimable leftovers

Display only, never delete. Each entry is a category, its bytes and a note.

- `gateway-*` deploy dirs: all except the newest by mtime. The note says "keep the one wrangler deploys from".
- `exchange/`: "packet sync retired (Phase 7a)".
- `memory-sync/data/baseline.jsonl` and `baseline.jsonl.gz`: "old packet baseline".
- `memory-sync/data/agentmesh.std*.log`: old worker logs.
- `status/insight-release-*`: all except the running one. Read the running release from the server's own `__file__` directory.
- `app/profile-log.old-*`.
- `versions/`.
- **`profile-log/index/*.snap` with no matching `profile-log/agents/<id>.key`:** "index snapshots for removed agents". Verified today: 6 snapshots against 4 agent keys. They are per-agent encrypted snapshots, not duplicates. Show the count and bytes only, and report this as a likely pruning bug in the PR. Don't fix it here.

### C. Network IO (per day and a 30-day projection; estimate flags where needed)

1. **R2.** Add counters in profile-log. Insight cannot see this traffic otherwise.
   - In the broker's remote storage path (`StagedRemote` / `remote_storage`), count PUT/LIST (Class A) and GET/HEAD (Class B) requests and the bytes up/down for each sync cycle.
   - Add `bytes_up`, `bytes_down`, `class_a` and `class_b` to the sync row, and running totals (`total_*` since `totals_since`) to `sync-status.json`. Persist the totals across restarts by reading the previous file.
   - Update `sync_status_valid` and `status.synced` / `valid()` to match. Old files without these fields stay valid.
   - Insight derives per-day rates from the totals and `totals_since`, and replaces the earlier estimate with these exact counts when present.
2. **Cloudflare Tunnel.** cloudflared serves Prometheus metrics on `127.0.0.1:20241` (verified: `cloudflared_tunnel_total_requests`, `cloudflared_tunnel_response_by_code`).
   - Fetch `http://127.0.0.1:20241/metrics` from the background computation only: loopback, a 2 s timeout, a 1 MB cap, and no redirects.
   - Configure the port in `pricing.json`'s sibling config, or in an `insight` block of settings; default 20241.
   - Parse only the needed counters: total requests and responses by code, plus any `*_bytes_*` tunnel counters if they exist.
   - Per-day rates come from two samples, kept in memory.
3. **Claude API (summary job).** Requests (`llm_api_calls`) and tokens from metrics. Bytes are not recorded, so say so.
4. **Per-process `nettop`:** skip. On macOS it needs sampling over time, its output format is unstable, and it can't attribute the root-owned cloudflared reliably without sudo. Say this in the UI as "not measured".

All of this is content-free, and `/api/storage` covers it remotely.

### Gates

- **ST-15:** buffer stuck warnings, using fixed inputs; a missing source shows "unknown".
- **ST-16:** reclaimable detection, using a fixture tree (gateway dirs, insight releases, orphan snapshots).
- **ST-17:** the broker byte and request counters, the running totals across a restart, and that old `sync-status.json` files stay valid (profile-log tests).
- **ST-18:** cloudflared metrics parsing; a non-loopback URL is refused; an oversized or slow response is handled.
- **ST-19:** the remote allowlist includes only `/api/storage` as new.

### Sequencing

- This PR is stacked on the multi-device PR #92, because both touch `broker/service.py` and `insight/index.html`.
- It is one PR, titled "Insight storage, costs, compute, buffers and network IO".
