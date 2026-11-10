# AgentMesh Insight

A local, read-only status workspace for AgentMesh: the encrypted profile log, the summary jobs and this device's
storage. Python 3.10+ and the standard library are enough. It loads no external fonts, scripts or analytics, and it
makes no model calls.

SQLite retirement R5b removed the memory browser and the SQLite and PostgreSQL readers behind it, along with the old
Syncthing, packet-peer and pairing views. Memory content now lives only in the profile log. Agents read it through
the broker. Insight shows counts only.

## Run from the repository root

```sh
python3 -u insight/server.py --port 0 --agentmesh-home "/private/runtime/agentmesh"
```

The server prints its exact localhost URL and PID. It always binds `127.0.0.1`. It starts an inspection UI, not an
ingest job, a summary worker or a sync service. Missing sources are labelled unavailable, never replaced with
demonstration data.

`--agentmesh-home` defaults to `$AGENTMESH_HOME`, otherwise `~/.agentmesh`. `--sqlite` is still accepted, because
installed launch agents pass it, but it is ignored. `INSIGHT_PG_DSN` is no longer read.

## Views and sources

| View | Read-only source |
| --- | --- |
| Overview | Job health and recent failures, memory counts from the profile log, profile-log backup state, and which AI tools were active recently. |
| Metrics | Summary accounting in `metrics.py`, from `jobs/summary-metrics.jsonl` and its pre-relocation archives. The retired cron digest's audit log (`backups/archive/digest-cron/cron/usage_audit.jsonl`) is the historical baseline. Its old `state.db` session table is no longer read. |
| Agents | Each AI tool's transcripts that the broker summary follows (`jobs/transcript-cursors.json`). It shows the count and the latest file modification time, never a path or content. |
| Pipeline | The ingest and summary launchd jobs (wrapper state and plist interval) and their failures over the last 7 days. It also shows the broker summary's own files: the last transcript scan (`status/last-ingest.json`), the last tick (`status/last-summary.json`), the schedule and quarantine count (`jobs/summary-state.json`), and projects held back for want of a scope (`jobs/summary-unmapped.json`). |
| Sync | Profile-log backup to R2, the devices in the profile log, and how AI apps reach memory. |
| Profile log | Content-free `profile-log-status.json`: profiles and scopes, agent grants, devices, workspaces, last apply and last remote sync. |
| Storage | Disk use under the AgentMesh home, the summarizer's state files, the replica and R2, cost and compute estimates, buffers and network IO. |

Metrics reads in place. Records are de-duplicated by `run_id`, with the later file winning, and corrupt JSON lines
fail explicitly. Repeatable `--metrics-log PATH` replaces the automatic summary-run input list. Unknown values stay
null, and costs are not invented.

### Profile log snapshot

The Profile log tab reads only `profile-log-status.json`, written by `agentmesh-log apply` and the broker sync loop.
The `migration` and `last_catch_up` sections from the SQLite catch-up are not projected, even from an older file.
Remote sync shows the last successful sync. It becomes stale after `max(3 * interval_s, 600 + 2 * interval_s)`
seconds. The reader never opens broker state, credentials, agent keys, settings or apply audit logs, and it never
contacts R2. It refuses symlinks, non-regular files, malformed JSON and files over 256 KiB. Each list is capped at 500
entries, and device IDs are cut to 12 characters.

### Activity is observation, not liveness

- Recent means a recorded time less than one hour old. Stale means an older or future-dated one. A missing time is
  unknown.
- A transcript's modification time, a job's last result or a summary tick is not proof that an agent or worker is
  running now.

## Read-only and privacy boundary

- Exact HTTP route allowlist, loopback Host checks, same-origin checks, no CORS, no directory traversal and no
  arbitrary file paths. Write methods are rejected. Security headers restrict frames, scripts, assets and connections.
- Every API answer is counts, states, labels and times. No memory content, transcript text, transcript path, agent key
  or credential leaves the adapters. Best-effort redaction still runs on every answer.
- Status files are read bounded and only when regular and not symlinked.
- Exceptions are sanitized. A source failure is visible without disclosing paths or raw error payloads.
- Caches last 15 seconds (storage: 10 minutes); the browser refreshes every 20 seconds.

Remote access requires the Cloudflare Access configuration below. Never bind Insight to a public interface.

## Verification

```sh
python3 -m pytest -q insight
node insight/profile_log_qa.mjs
git diff --check
```

The tests use disposable fixtures only and never read configured runtime data.

## Files

- `server.py`: loopback HTTP routes, guards, cache and CLI.
- `adapters.py`: allowlisted read-only projections (jobs, summary status, profile-log snapshot, metrics, memory counts).
- `metrics.py` and `metrics.html`: summary accounting and its UI.
- `storage.py`: storage, cost, compute, buffer and network observations.
- `index.html`: navigation, views and shared theme.
- `remote_access.py`: Cloudflare Access verification for remote reads.
- `deploy_insight.py`: the owner-run release deploy.

## Remote access via Cloudflare Tunnel

Owner setup (not performed by this change):

1. Create a separate remote-managed tunnel named `agentmesh-insight-mac` in the
   Cloudflare dashboard, connected only by this Mac. Move `insight.krisb8.com`
   from `agentmesh-bridge` to this tunnel, pointing to `http://127.0.0.1:51877`.
   Remove that hostname from `agentmesh-bridge`; keep `bridge.krisb8.com` there.
   The bridge tunnel can then have Mac and Windows connectors without
   load-balancing Insight across machines. Install the connector as described below.
2. Keep the existing Cloudflare Access application, owner-only email OTP policy
   and AUD for `insight.krisb8.com`: the hostname has not changed. For a first
   installation, create that self-hosted application and copy its AUD.
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
`/api/profile-log`, `/api/metrics`, `/api/operations`, `/api/log-counts` and `/api/storage`
(counts, timestamps and states only) are allowed after verification. All other paths return 404; HEAD and write methods return 405.
Remote Origin, when supplied, must exactly match `https://insight.krisb8.com`.
Every view works remotely. Metadata can still reveal operational counts and names; keep the
Access policy owner-only. The UI is not the authorization boundary.

Not verified by this change: live tunnel routing, DNS/TLS deployment, the owner's
Access policy/OTP flow, the launch-agent interpreter/restart, or remote browser
connectivity. Local tests use generated RSA keys and injected JWKS, not production
assertions. Snapshot metadata does not prove worker liveness or convergence.


### Device heartbeats

Both the Sync Devices card and Profile log Devices table show the device name,
platform, this-device/revoked state, last seen and last sync outcome/time. A peer
without a readable heartbeat shows **never reported**, including a peer whose
last heartbeat predates this device joining. Local values are live observations;
peer values come from verified, decrypted heartbeats cached for 15 minutes.
These are observations, not proof of liveness or convergence.

Optionally set `"device": {"name": "My Mac"}` in `settings.json` (1–64 printable
characters). Otherwise the broker uses the hostname, removes a trailing `.local`
and truncates it to 64 characters. Platform is detected from Python's platform.
Names/platform are exposed only through the local status projection and the
existing authenticated `/api/profile-log` route. They are sealed inside age
ciphertext in storage; the Worker ignores heartbeat objects. Memory content
continues to stay local in Insight's remote view.

Each device overwrites its signed `registry/heartbeats/<device_id>.json`, sealed
to all non-revoked device age recipients. Publishing is limited to once per
15 minutes, with immediate exceptions when the sync outcome or recipient set
changes. Reads use one LIST and a GET per heartbeat every 15 minutes. Failed
heartbeat operations log only their exception class and do not change sync results.
The cache is in broker memory and resets on restart.

Steady-state R2 cost per device per 30-day month: 96 PUT/day ≈ 2.9k Class A;
96 LIST/day ≈ 2.9k Class A; N × 2.9k Class B GETs. Three devices total ≈ 17k
Class A and 26k Class B, below the stated free tier of 1M Class A / 10M Class B.
Outcome/recipient changes and broker restarts add operations beyond that baseline.

### Install the dedicated Mac connector (owner steps)

These steps change live routing and are intentionally not executed by the tests.

1. In the Cloudflare dashboard create `agentmesh-insight-mac`, and obtain its
   connector token. On this Mac, use Keychain Access to add a generic password
   item with service/name `org.agentmesh.insight.tunnel`, account `agentmesh`,
   and the token as its password, or run
   `security add-generic-password -s org.agentmesh.insight.tunnel -a agentmesh -w`
   with nothing after `-w` so it prompts for the token. Do not paste the token
   into a shell command, plist, repository file or log. Ensure the login Keychain is unlocked and
   allows the `security` command to read this item.
2. Ensure `cloudflared` is already installed. The template PATH covers Homebrew
   on Apple Silicon and Intel. Adjust it if the binary lives elsewhere.
3. Copy `tunnel/run-insight-tunnel.sh` to `~/.agentmesh/services/insight-tunnel.sh`
   (mode 0700), so the service never runs from a git checkout. Copy
   `tunnel/org.agentmesh.insight-tunnel.plist` to
   `~/Library/LaunchAgents/org.agentmesh.insight-tunnel.plist` and replace
   `__HOME__` with your absolute home path, XML-escaping special characters.
   launchd does not expand `~` or `$HOME`. Keep `~/.agentmesh/status/` at mode 0700
   and the copied plist private. The plist contains no token. The first start may
   show a Keychain prompt for `security`; choose Always Allow.
4. Validate and start the connector:

   ```sh
   plutil -lint "$HOME/Library/LaunchAgents/org.agentmesh.insight-tunnel.plist"
   launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.agentmesh.insight-tunnel.plist"
   ```

   The script reads Keychain silently and supplies `TUNNEL_TOKEN` only in the
   environment of `cloudflared tunnel --no-autoupdate run`. Missing token,
   Keychain access or binary produces a fixed error and nonzero exit. RunAtLoad
   and KeepAlive restart it; logs go under `~/.agentmesh/status/`.
5. Move `insight.krisb8.com` to the dedicated tunnel's published application route
   `http://127.0.0.1:51877`, removing it from `agentmesh-bridge`. Keep Insight's
   public Host and Cloudflare headers. Verify owner OTP access, all metadata
   views and remote memory denial. Confirm the bridge still serves its gateway
   route, and only this Mac connects to the Insight tunnel. Keep the existing
   Access app/AUD and Insight server configuration.

Rollback: **first** add `insight.krisb8.com` back to `agentmesh-bridge` with service
`http://127.0.0.1:51877` (remove its dedicated-tunnel route), **then** stop the
connector:

```sh
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.agentmesh.insight-tunnel.plist"
```

The Access app/AUD remain unchanged on rollback. Remove the copied launch-agent
plist if it must stay disabled after the next login. A later Windows Insight
gets a separate hostname such as `insight-<name>.krisb8.com` and its own tunnel;
Windows Insight is not implemented here.

### Storage, costs, compute, buffers and network IO

The Storage tab and authenticated `GET /api/storage` expose only sizes, counts
and fixed operational labels. A single background computation
refreshes every ten minutes. The first request returns `available: false,
pending: true` immediately; refresh failures retain the previous result and expose
only an exception class. Request handlers never walk directories, spawn processes or fetch tunnel metrics. No deletion, vacuum or R2 request is made
by Insight.

Directory accounting uses
one bounded, non-symlink walk (200,000 entries) and reports truncation/unreadable
sources. Reclaimable entries are suggestions only. Orphan snapshot detection is
suppressed on incomplete walks so unseen agent keys are not mistaken for removal.

Ship **storage.py and pricing.json** alongside server.py and the existing Insight
assets in each release. Prices are the plan's 2026-10-22 defaults, with sources
shown in the UI; they were not rechecked online during this offline implementation.
`<agentmesh_home>/insight-pricing.json` merges individual known keys into the shipped
prices. Numeric values must be finite and nonnegative; `as_of` is an ISO date.
An invalid file restores defaults with a visible flag. Energy defaults to 5 W per
CPU core and an unknown electricity price. Optional sibling
`<agentmesh_home>/insight-config.json` accepts `cloudflared_metrics_port` (default
20241). The fixed numeric-loopback `/metrics` fetch refuses redirects, caps its
body at 1 MiB and enforces a two-second deadline. Tunnel rates need two samples;
counter resets yield unknown rates until a new baseline is available.

R2 bytes remain an estimate from replica size and object counts. Legacy status
uses prefix/sync estimates for requests; new broker status supplies measured
PUT/LIST and GET/HEAD counts, including LIST pages, SDK retry sends and heartbeat
traffic. Payload bytes exclude HTTP/TLS overhead. Totals and their epoch survive
restart through `sync-status.json`; every cycle with traffic publishes the totals.
Legacy status files remain valid. Monthly cost and network projections are
estimates, not invoices; unknown inputs never become zero costs.

Summary API-equivalent costs use production bounded-run metrics only, de-duplicated
by run ID. Month-to-date and seven-day windows use UTC. The subscription note is
retained unless a run records `billing_source: api_key`. Separately recorded
`cache_write_5m_tokens` / `cache_write_1h_tokens` are priced at their write rates;
otherwise writes remain part of uncached input. Unknown breakdowns use total input
and are flagged. Unknown models produce unknown costs.

Compute samples fixed launchd labels and exact-name cloudflared via argv, with
five-second subprocess timeouts and one combined ps call. Job aggregates read
bounded launchd log tails; old lines remain readable but missing CPU/wall fields
make affected totals unknown. The job wrapper (`profile-log/agentmesh_log/control/jobrun.py`, formerly
`memory-sync/job_notify.py`) adds monotonic wall time, child user+system CPU deltas and RSS to
the existing content-free JSON line. `ru_maxrss` is the platform's child high-water
mark, not an additive delta; Windows without `resource` records null CPU/RSS.
The estimate of summary waiting time is wall minus CPU, not a measured API trace.

Follow-ups deliberately left outside this change: bridge drain-status persistence,
audit log
rotation (it currently grows forever), and a likely pruning bug leaving encrypted
per-agent index snapshots after agent removal. No plist or live copy is changed.
Per-process nettop remains not measured: its sampling and macOS output are unstable,
and root-owned cloudflared attribution would need sudo. Worker/KV/Access usage
requires the Cloudflare dashboard; Insight uses no provider token.
