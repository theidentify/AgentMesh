# AgentMesh Insight

A local, read-only operational workspace integrated with AgentMesh. Its sidebar,
connection switcher, data browser and inspection drawer follow the operational-tool
layout of Redis Insight, without copying vendor assets or branding.

Python 3.10+ and the standard library are sufficient for SQLite and metrics.
The optional PostgreSQL reader uses the system **libpq** shared library through
`ctypes`; no Python database package or web framework is required. No external
fonts, scripts, analytics, model calls or provider credentials are used.

## Run from the repository root

```sh
python3 -u insight/server.py --port 0
```

The server prints its exact localhost URL and PID. It always binds `127.0.0.1`.
This starts an inspection UI, not an ingest job, summary worker or sync service.
Missing sources are labeled unavailable, not replaced with demonstration data.

For an existing staging database and an optional authoritative PostgreSQL source:

```sh
# Runtime sources must remain outside this repository.
export HERMES_HOME="/private/runtime/hermes"
export INSIGHT_PG_DSN="host=127.0.0.1 port=5432 dbname=memory user=readonly"
python3 -u insight/server.py --home "$HERMES_HOME" \
  --sqlite "/private/runtime/agentmesh/node.db" --port 0
```

Use a least-privilege PostgreSQL account. Supply a libpq **keyword/value** DSN
through the environment, not a command-line argument, browser form or Git file.
Use the operating system's password mechanism if authentication is needed.
`INSIGHT_LIBPQ` can point to an installed libpq shared library if automatic
library discovery is insufficient. Connection and SQL timeouts are three seconds.

`--home` defaults to `HERMES_HOME`, otherwise `~/.hermes`. No SQLite database is
created when an optional path is absent or invalid. The connection picker keeps
**authority** separate from **comparison/staging**. Set
`INSIGHT_PRIMARY_BACKEND=sqlite` explicitly only after a verified local writer and
caller cutover; SQLite is then authority/default and PostgreSQL is read-only
comparison. Without that selector PostgreSQL remains authority and SQLite is
staging. Selecting a backend is not a migration and an unavailable source is
reported, never repaired by a write or a silent query fallback. The local SQLite
database supplies sync metadata independently of the memory browser selection.

## Views and sources

| View | Read-only source and interpretation |
| --- | --- |
| Overview | Connection read checks, item/summary counts, observed agents and cron executions. |
| Metrics | Existing summary accounting ported into `metrics.py`; separate historical, production-source bounded and isolated smoke histories, charts and coverage. |
| Memory Browser | Paginated `memory_items` and `memory_summaries` from the selected backend. Search content, exact project/scope key, kind, status and scope filters. |
| Agents | Aggregated `source_sessions` and `observation_events` metadata, plus content-free Hermes session metadata. Zero source-session rows do not hide agents with recorded events. |
| Pipeline | Exact known OMP ingest and summary cron job IDs, last execution and next scheduled time, coordinator window state, ingest counters/errors and summary checkpoints. |
| Profile log | Content-free `profile-log-status.json` under `--agentmesh-home`: profiles/scopes, agent R/W, devices, migration targets/counts and last apply/catch-up/remote sync outcomes. |
| Sync | Existing AgentMesh `_sync_config`, `_sync_outbox`, `_sync_receipts`, `_sync_diagnostics` and optional `_agentmesh_worker_state` metadata, plus fixed Mac/Windows completed-cycle ACK projections. No packet payloads, group IDs, source paths, owners or diagnostic messages. |

`--agentmesh-home` defaults to `$AGENTMESH_HOME`, otherwise `~/.agentmesh`.
Each relocated item independently prefers its new path when present, falls back
to the legacy path beneath `--home`, and otherwise reports `missing`. API source
labels are `agentmesh`, `legacy`, or `missing`; filesystem paths are not exposed.

| Item | Under `--agentmesh-home` | Legacy under `--home` |
| --- | --- | --- |
| Peer ACKs | `exchange/status/{mac,windows}.json` | `omp-memory/sync/status/{mac,windows}.json` |
| Syncthing exchange | `exchange` | `omp-memory/sync` |
| Coordinator | `jobs/.bounded_digest_state.json` | `omp-memory/.bounded_digest_state.json` |

Hermes `cron/jobs.json`, `cron/usage_audit.jsonl` and `state.db` always stay under
`--home`, even if similarly named files exist under `--agentmesh-home`.

Metrics reads in place: sorted `backups/archive/summary-metrics.jsonl.pre-relocate-*`
history under `--agentmesh-home`, then legacy `omp-memory/summary-metrics.jsonl`
under `--home`, then new `jobs/summary-metrics.jsonl` under `--agentmesh-home`.
Every existing live file participates. Records are de-duplicated by `run_id`,
with the later file winning. The same merge applies separately to
`summary-smoke-test-metrics.jsonl`. Archives are only read; nothing is copied,
moved or chmodded. Corrupt JSON lines fail explicitly.

Repeatable `--metrics-log PATH` replaces the automatic summary-run input list;
isolated smoke remains a separate source. The standalone `metrics.py` CLI also
accepts repeated `--metrics-log` options. Existing legacy live logs remain inputs
because a writer configuration may still point there. Changing that production
configuration is outside Insight. Confirm this read-in-place default before
deployment.
Known integration jobs are ingest `eabd6e41c22d` and summary `6fcf5646d34d`.
A different runtime without those IDs is explicitly reported as unavailable.

The canonical historical join and aggregate semantics are retained locally;
Insight does **not** import a user's home-directory module as its core.
Unknown values stay null, known/sample coverage remains explicit, cached prompt
input is distinguished from uncached input, and costs or workload-matched savings
are not invented. Latest successful LLM activity is separate from idle/export
state. Backlog is last observed in a run, not a live PostgreSQL backlog query.

The migrated metrics view is a same-origin frame inside the Insight workspace,
with shared theme and refresh controls; its accounting filters remain independent
of memory-backend selection. This preserves the existing working chart and
accounting UI without mixing production logs with isolated smoke samples.

### Profile log snapshot

The Profile log tab reads only `profile-log-status.json`, written by
`agentmesh-log apply`, `agentmesh-log migrate catch-up` and the broker sync loop.
Remote sync shows the last successful sync and becomes stale after
`max(3 * interval_s, 600 + 2 * interval_s)` seconds, allowing for throttled status writes. Edit in a terminal
with `agentmesh-log admin` / `agentmesh-log apply`. This is an observed snapshot,
not current broker authority; timestamps and age distinguish stale sections.
The reader never opens broker state, `sync-status.json`, credentials, agent keys, settings or apply audit logs; it never contacts R2.
It refuses symlinks, non-regular files, malformed/wrong-format JSON and files over
256 KiB. It projects allowed fields, caps each list at 500 entries with a visible
truncation notice, and shortens device IDs to 12 characters. Missing values stay
unknown. `GET /api/profile-log` accepts no parameters and uses a 15-second cache;
write methods remain rejected. There are no web editing controls.

### Activity is observation, not liveness

- Recent observation means a recorded timestamp is less than one hour old.
- Stale means an older or future-dated observation. Missing timestamps are unknown.
- A source file, historical open session, worker checkpoint or enabled cron is
  **not proof of a running agent**. No active process/heartbeat probe is configured.
- Cron results are observed last executions, not guaranteed future success.
- Receipts prove local packet imports, not two-way convergence or remote ACKs.
- Sync **Peer Status** independently reads fixed `mac.json` and `windows.json`
  completed-cycle ACKs under the resolved status directory above. It remains visible
  even when the SQLite sync source is unavailable. No arbitrary peer/path input
  is accepted. Status JSON is bounded to 64 KiB and projected through an allowlist.
- ACK timestamps require timezone offsets and display in the browser's local
  timezone. Age is computed at API observation time. Fresh means at most 300 seconds
  old (an observation threshold, not an asserted worker interval); stale does not
  imply offline. Future timestamps are flagged; malformed/missing counters remain
  unknown, not zero. Missing/malformed files and partial reports are explicit.
- Peer counts, ingestion-error counts, sync totals and last-cycle applied/published/
  pending/conflict/invalid counters are last reported values, not live DB queries.
  Summary-cycle counters are separate and unknown if no summary sync occurred.
  Reported `summary_state` is shown as Summary state without inferring progress.
  Reported-error presence includes workflow/mirror failures without exposing their contents.
- A completed-cycle ACK is not proof of current worker liveness, a successful
  workflow, Syncthing connectivity or exact full convergence. Old zero-error ACKs
  describe an old observation, not current health.
- Transport connectivity is a read-only Syncthing REST observation, independent of
  ACKs and SQLite availability. Overview and Sync share the same observation.

### Packet security and pairing observation

The read-only Security / Pairing section is separate from Syncthing transport.
Local `_sync_security` configuration and `_sync_verification` counters are read
directly from SQLite. A configured required policy is not proof that every remote
worker enforces it. Legacy databases show **legacy / not enforced**, not secure.
No recorded verification counters/timestamps means unknown, not zero failures.
Counters count verification attempts (including repeated replay checks), not
distinct rejected packets or successful application transactions.

Peer `agentmesh-security-status-v1` metadata is allowlisted from completed-cycle
status JSON: sanitized ASCII display label, existing slot, reported policy,
pairing state, wizard step/next action, exact probe UUID and reported verification
timestamps/counters. Old ACKs without signing fields show legacy policy and unknown
pairing/verification. Missing or malformed data stays unknown. Full fingerprints,
public/private keys, trust stores, private paths, credentials and raw errors are
never projected. This page has no trust/revoke/disable buttons.

The installer only marks its isolated probe verified after verifying a pinned
signed application receipt for the exact packet UUID/digest. Insight labels this
**reported verified**, because the completed-cycle status JSON itself remains
unsigned telemetry and is not independently authenticated by the dashboard.
It does not establish production recall, fresh liveness or all-peer readiness.
Use the installer CLI and out-of-band full fingerprint confirmation for authority.
Transport completion/encryption cannot establish signature enforcement or trust.

### Syncthing transport observation

Insight discovers the existing `config.xml` in the standard macOS Syncthing,
Linux state or legacy configuration locations. An optional absolute
`INSIGHT_SYNCTHING_CONFIG` path selects a private configuration outside Git.
No key is supplied through the CLI or browser: Insight reads the existing API key
server-side and never projects it. No pairing, folders, settings or services are changed.

Only the folder whose canonical path equals the resolved exchange directory is observed,
and only its paired remote devices are included. Numeric loopback GUI addresses
are required; DNS names, external origins, credentials in addresses, redirects
and proxies are not used. TLS uses normal certificate verification. Missing or
invalid configuration, unauthorized requests and unavailable endpoints remain
explicitly unknown. Responses are capped at 1 MiB, sockets at two seconds and an
observation at six seconds. Device IDs, addresses, paths and raw exceptions never
leave the adapter. Peer names are reduced to Windows or numbered exchange peers.

Read-only endpoints are `GET /rest/system/status` (local identity exclusion),
`GET /rest/system/connections`, `GET /rest/db/status?folder=...` and
`GET /rest/db/completion?folder=...&device=...`. See the official Syncthing REST
endpoint documentation for their semantics. Local pending bytes/items mean data
needed by this Mac; remote completion and pending values belong to that peer.
Connected means every configured exchange peer has an explicit current connected
and unpaused device observation. Missing expected peers remain unknown; explicitly
disconnected or paused devices report disconnected. Paused remote/local folders,
disconnected peers and unknown remote sharing state suppress current completion.
Partial numbers remain null, never zero. File completion is not database
application, successful workflow execution, convergence or remote recall.

The backend shares a 15-second transport cache across memory backends; the browser
refetches every 20 seconds and displays the actual transport observation timestamp.

## Read-only and privacy boundary

- SQLite is opened with URI `mode=ro`, `PRAGMA query_only=ON` and a read transaction.
- PostgreSQL uses `BEGIN READ ONLY` and bound parameters. Connections close without
  committing. There is no migration, arbitrary SQL endpoint or command executor.
- Exact HTTP route allowlist, loopback Host checks, same-origin checks, no CORS,
  no static directory traversal and no arbitrary file paths. Write methods are
  rejected. Security headers restrict frames, scripts, assets and connections.
- Browsing returns bounded durable-memory excerpts, not raw transcripts or arbitrary
  metadata. Explicit detail opens at most 20 linked event excerpts, each bounded
  to 4,000 characters. Durable detail is bounded to 20,000 characters; list excerpts
  to 2,000. Unlinked summary metadata is not fabricated into evidence.
- Credentials, obvious tokens and private keys receive best-effort redaction. All
  rendered prose is escaped and treated as untrusted reference data, not instructions.
  **Redaction is not a guarantee that prose is secret-free.** Use only on a trusted
  local machine. Do not publish runtime screenshots, excerpts, databases or logs.
- Exceptions are sanitized. A source failure is visible without disclosing DSNs,
  prompts, filesystem paths or raw error payloads.
- Backend metadata cache is 15 seconds; browser refresh is 20 seconds. Tables keep
  their overflow inside their panel. Filters use explicit Search and Clear actions.
- No middle-dot separators are used. Information is separated with layout and labels.

Remote access requires the Cloudflare Access configuration below. Never bind
Insight to a public interface. Background operation is not launch-at-login
persistence. Stop the server process to stop Insight. Existing standalone dashboard
instances can remain running until a user deliberately switches to this workspace.

## Verification

```sh
python3 -m unittest discover -s insight -v
node insight/profile_log_qa.mjs
python3 -m pytest -q memory-sync
git diff --check
```

Unit/integration tests use disposable fixtures and cover memory pagination,
parameter validation, literal search, evidence redaction, source isolation,
unknown values, actual SQLite write rejection, HTTP route/Host/Origin/write
rejection, missing backends and cache behavior. They never write configured runtime
memory. Existing AgentMesh tests remain unchanged.

Real-browser QA uses Node's native WebSocket and an **isolated** Chromium instance
with remote debugging enabled, not a copy of a locked user profile:

```sh
node insight/qa.mjs http://127.0.0.1:PORT CHROMIUM_DEBUG_PORT \
  /private/runtime/insight-qa
# Bounded security-only checks, suitable for an unchanged legacy deployment:
node insight/security_qa.mjs http://127.0.0.1:PORT CHROMIUM_DEBUG_PORT \
  /private/runtime/security-qa legacy
```

Use a disposable browser profile outside the repository. The test expects configured,
nonempty PostgreSQL and SQLite memory sources and existing summary metrics; it
checks all seven navigation views, filtering/clearing, paging, explicit details,
connection switching, metrics charts/source tabs, desktop/mobile light/dark,
20-second auto-refresh, overflow and runtime exceptions. Screenshots and the QA
report are written only to the specified external directory. They contain local
operational and memory information and must not be committed.

## Files

- `server.py`: loopback HTTP routes, guards, cache and CLI.
- `adapters.py`: allowlisted read-only metrics, memory and operational projections.
- `libpq_reader.py`: parameterized, read-only PostgreSQL adapter.
- `metrics.py` and `metrics.html`: portable migration of existing summary accounting/UI.
- `index.html`: Insight navigation, browser, operational views and shared theme.
- `syncthing_transport.py`: bounded loopback-only GET transport projection for the exact exchange folder.
- `test_insight.py`, `test_peer_status.py` and `test_syncthing_transport.py`: fixture-based safety and reader tests.
- `qa.mjs`: real Chromium/CDP acceptance checks.
- `security_qa.mjs` and `test_security_status.py`: bounded security/pairing rendering,
  actual legacy observations, read-only counters, private-field exclusion and unknown values.

No database schema, sync algorithm, ingest parser, production cron, provider
configuration or memory is changed by this subproject.

## Remote access via Cloudflare Tunnel

Owner setup (not performed by this change):

1. On the existing tunnel, add public hostname `insight.krisb8.com` pointing to
   `http://127.0.0.1:51877`. Leave `bridge.krisb8.com` and its routing untouched.
2. Create a Cloudflare Access self-hosted application for `insight.krisb8.com`.
   Require an owner-only email OTP policy. Copy the application's AUD.
3. In `~/Library/LaunchAgents/org.agentmesh.insight.plist`, set these entries in
   `EnvironmentVariables` (configuration only, no tokens or secrets):

   ```text
   INSIGHT_REMOTE_HOST=insight.krisb8.com
   INSIGHT_ACCESS_TEAM_DOMAIN=royal-poetry-211d.cloudflareaccess.com
   INSIGHT_ACCESS_AUD=<copied application AUD>
   INSIGHT_ALLOWED_EMAILS=<owner email>
   ```

   Allowed emails are comma-separated and case-insensitive. Use bare hostnames,
   without a scheme, port or path. Set `ProgramArguments[0]` to the absolute,
   expanded path for `~/.agentmesh/venv/bin/python`, which must already have
   `cryptography`. Keep the existing arguments, including port `51877`.
   `/opt/homebrew/bin/python3` currently lacks that package: local access still
   works there, but remote authentication fails closed. No package is installed
   by Insight.
4. Restart the existing launch agent after reviewing the plist:

   ```sh
   launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.agentmesh.insight.plist"
   launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.agentmesh.insight.plist"
   ```

Access at the edge and Insight's own RS256 JWT signature, issuer, audience, time
and email checks are both required. Insight accepts only the assertion header,
never an Access cookie. Missing/invalid configuration or unavailable verification
returns a fixed 403 without authentication details. Tokens/JWTs are never logged
or rendered. Public signing keys are cached in memory for ten minutes; unknown
key IDs trigger at most one rotation refresh per minute. JWKS uses verified HTTPS,
IPv4 before IPv6, a three-second network budget and a 1 MiB response cap; redirects
are refused. System DNS resolution may exceed the socket timeout.

Remote detection includes the configured Host and **any** `Cf-` or `X-Forwarded-`
header, even on a loopback Host. Local loopback requests without these headers
retain local access. Forwarding a request with a loopback Host and stripping all
forwarding headers would erase this distinction: retain the public Host and
Cloudflare headers in the tunnel configuration. The listener remains loopback.

Remote access is metadata-only: GET `/`, `/metrics.html`, `/health`,
`/api/profile-log`, `/api/metrics`, `/api/connections` and `/api/operations`
(counts, timestamps and states only) are allowed after verification. All other paths return 404; HEAD and write methods return 405.
Remote Origin, when supplied, must exactly match `https://insight.krisb8.com`.
The UI hides the Memory Browser and its search controls and never fetches
`/api/memory*` remotely; Overview, Agents, Pipeline, Sync, Metrics and Profile log
work remotely. Metadata can still reveal operational counts and names; keep the
Access policy owner-only. The UI is not the authorization boundary.

Not verified by this change: live tunnel routing, DNS/TLS deployment, the owner's
Access policy/OTP flow, the launch-agent interpreter/restart, or remote browser
connectivity. Local tests use generated RSA keys and injected JWKS, not production
assertions. Snapshot metadata does not prove worker liveness or convergence.
