# Local control plane: design

Status: draft for owner review, 2026-10-23. Builds on the encrypted profile log
(`2026-10-18-encrypted-profile-log-design.md`), its settings file and admin UI
(`2026-10-20-profile-log-settings-and-admin-ui-design.md`) and Phase 6 passkey
approval (`../plans/2026-10-22-phase6-passkey-approval.md`).

## Problem

On a Mac, AgentMesh runs as nine separate launchd agents: `ingest`, `summary`,
`sync`, `retention`, `profile-log.broker`, `profile-log.bridge`,
`profile-log.catch-up`, `insight` and `insight-tunnel`. Nothing supervises them as
a whole. This shows up as:

- a stuck worker that nobody notices;
- hand-edited plists and the `plutil` array-insert gotcha;
- upgrades and OTA that walk every plist one by one;
- failures spread over several logs;
- a 429 quota failure that repeats on every run.

Keys are the messiest part. There are two stacks:

- **Legacy (memory-sync):** `signed_packets`, `key_rotation`, `identity_backup`,
  `os_unlock`, `key_protection`, `security_wizard`.
- **Current (profile-log):** `identity`, `keyring`, `keys`, `recovery`, `backup`,
  `oskeystore`. `oskeystore` is a port of `os_unlock`.

Nothing answers "which keys exist, how old are they, which devices hold which
grants, and when was the last good backup?"

## Owner decisions (2026-10-23)

- **Language:** Python. The Go rewrite and a Go/Fyne desktop app were considered
  and dropped.
- **Scope:** a local control plane first, one supervisor per host. The API must
  still allow remote use later. No cross-device control in v1.
- **Migration:** move units from launchd to the supervisor gradually: observe and
  control through `launchctl` first, then adopt one unit at a time, each with a
  rollback.
- **Control tiers in v1:** tiers 1, 2 and 3. Tier 3 (keys and identity) is
  included, behind the strongest gates.
- **Keys:** the supervisor manages the key *lifecycle* (inventory, health, expiry,
  ceremonies). It never holds unlocked keys. Ceremonies run as separate
  subprocesses.
- **Platform:** Mac first. OS-specific code sits behind interfaces so Windows can
  follow in v1.1.
- **Insight** stays as it is, a separate read-only web UI.
- **v1 features:** a watchdog with a restart-storm guard, key and device expiry,
  remote approval with Discord notification, maintenance mode, and a quota guard.
- **Desktop UI:** deferred. The CLI is the first client.

## Non-goals (v1)

- Cross-device control, or any request that starts off this host.
- An HTTP or TCP listener on this host.
- The supervisor holding unlocked key material.
- Automatic rotate or revoke. The supervisor proposes, and the owner decides.
- Windows. The v1.1 interfaces are designed for it, but nothing is implemented.
- Retiring the legacy key stack. That is a separate milestone (see Milestones).

## Architecture

A new package, `agentmesh_log/control/`, lives in profile-log. It reuses the
transport, audit, presence, keystore and identity code there. The supervisor runs
as `agentmesh control serve` from the existing venv under `~/.agentmesh`, started
by one new plist, `org.agentmesh.control`, with `KeepAlive`.

The data plane does not change. Ingest, summarize, sync, crypto and keys stay in
the existing Python modules and their tests. The supervisor starts and watches
them. It does not reimplement them.

| Module | Responsibility |
|---|---|
| `units.py` | The unit registry. Each unit has a name, a kind (`job` = scheduled, `service` = long-running), an argv, a schedule, the tier of each action and its watchdog settings. |
| `backends.py` | The `Backend` interface. `LaunchdBackend` uses `launchctl` (`print`, `kickstart`, `bootstrap`, `bootout`). `NativeBackend` runs the unit as a supervised child process. Each unit has exactly one backend at a time. |
| `scheduler.py` | Native units only. Schedules runs, catches up after sleep (one run, never a burst) and prevents overlap. Checks maintenance mode and the quota guard before every start. |
| `health.py` | The watchdog: reads the heartbeats services already write (#92). A unit silent for longer than `watchdog_sec`, after a start-up grace period, is stuck: SIGTERM, then SIGKILL, then restart. The restart-storm guard: more than `burst` restarts within `interval` puts the unit in `failed`, sends one alert and waits for `agentmesh control reset <unit>`. A unit without heartbeats must declare `watchdog: off`. |
| `keys.py` | The key inventory, read-only from metadata, never unlocking anything: identities, devices, agents, scope epochs, grants and backup recency. Expiry policy: `max_age` for device keys, grants and backups, plus a stale-device threshold. It warns ahead of time and *proposes* rotate or revoke when past due. |
| `ceremonies.py` | Runs rotate, backup, restore, revoke, relocate and replace as separate subprocesses through the existing CLI, so unlocked keys live only in that process. |
| `gates.py` | Tier gates and the plan → approve → execute state machine (see below). |
| `approvals.py` | Pending approvals, Discord notification and the remote passkey path. |
| `maintenance.py` | `maintenance on/off`: pauses scheduled jobs, ingest, summary and sync. The broker keeps running so agents can still recall. Upgrade and relocate turn it on themselves. |
| `quota.py` | A daily cap on summarize calls and cost (prices from `insight/pricing.json`, the same accounting as metrics). On 429 (the #89 failure details) it backs off exponentially and alerts once per episode. |
| `api.py` | The control socket (see API). |

Audit goes through the existing `broker/audit.py`. Settings (expiry, quota,
watchdog overrides) go into the existing `~/.agentmesh/settings.json`.

State lives in `~/.agentmesh/control/state.db` (SQLite):

- the backend of each unit;
- `failed` states and restart counters;
- pending approvals;
- quota counters;
- intent records for running ceremonies.

## Threat model

AI agents (Claude, Codex, Hermes) run as the owner's user. A peer-UID check alone
cannot tell the owner from an agent. The broker scopes this out ("Same-user hostile
code is outside this boundary"). The control plane does not. Any operation that
changes authority or touches keys needs **proof of human presence** that same-user
code cannot forge: Touch ID evaluated by the supervisor itself, or a passkey.

The Phase 6 principle carries over: **the cloud relays, it never grants.**

## API

**Transport.**
- `~/.agentmesh/run/control.sock`, mode `0600`, peer-UID check.
- The broker's `UnixServer` and `broker/framing.py` (length-prefixed canonical JSON).
- `PipeServer` gives the same API on Windows in v1.1.

**Messages.**
- Request: `{v: 1, id, op, args}`.
- Response: `{id, ok: true, result}`, or `{id, ok: false, error: {code, message}}`.
- Every op has an explicit argument schema, like the broker's `SCHEMAS`. Unknown
  keys are refused.
- Every request audits the caller's pid and executable path.

| Tier | Operations | Gate |
|---|---|---|
| 0 read | `status`, `units`, `keys.inventory`, `quota.status`, `approvals.list`, `audit.tail` | none |
| 1 routine | `unit.start`, `unit.stop`, `unit.restart`, `unit.reset`, `sync.retry`, `maintenance.on`, `maintenance.off`, `diagnose` | No presence check. Rate-limited (default: 3 restarts per unit per hour) and audited. Agents may call these. |
| 2 devices and authority | `device.approve`, `device.revoke`, `grant.add`, `grant.remove`, `settings.set`, `quota.set`, `unit.backend` | Human presence: local Touch ID **or** a remote passkey. |
| 3 keys and identity | `key.rotate`, `identity.backup`, `identity.restore`, `mac.relocate`, `mac.replace`, `compromise.report` | Local Touch ID **and** a typed confirmation of the operation name. A remote passkey may only forward the request. It never replaces Touch ID. |

**Gate state machine (tiers 2 and 3).**

1. **plan:** run the operation's dry-run or preflight. `plan_digest` = SHA-256 of
   the canonical plan.
2. **pending:** issue an `approval_id`. It expires after 10 minutes (tier 2) or
   5 minutes (tier 3). Notify Discord.
3. **approve:** approval is bound to `plan_digest` and is single-use.
4. **execute:** re-run the dry-run first. If the digest changed, abort with
   `plan_changed` and require a new approval.
5. **concurrency:** one global lock for tiers 2 and 3. Tier 1 locks per unit.

**Presence.**
- `presence.py` already defines the `Presence` protocol
  (`confirm(action, summary)`) with `ConsolePresence`.
- v1 adds `TouchIDPresence`, which implements that protocol through
  `LAContext.evaluatePolicy` (`pyobjc-framework-LocalAuthentication`, pinned like
  the other requirements).
- The **supervisor** raises the prompt. It is a LaunchAgent in the owner's GUI
  session. A client's claim that it passed Touch ID is never trusted.
- Windows Hello is the v1.1 implementation of the same protocol.

**Remote approval.**
- Requests always start on this host.
- A Discord bot sends a notification. The bot uses an outbound gateway connection
  only, and its token lives in the Keychain.
- The notification links to the existing passkey approval page on the status
  gateway (Phase 6: device-local single-use challenges, signed approvals).
- The supervisor fetches the result over an outbound connection.
- Discord is notification only. A compromised Discord account cannot approve
  anything.

**Error codes:**
- `unknown_op`
- `bad_args`
- `forbidden_tier`
- `rate_limited`
- `maintenance`
- `approval_required`
- `approval_expired`
- `plan_changed`
- `busy`
- `failed` (details only in the audit)

## Error handling and rollback

| Case | Behaviour |
|---|---|
| Supervisor crash | `KeepAlive` restarts it. On start it reconciles child processes from pidfiles plus process start time. Ordinary jobs are stopped and restarted. A running ceremony is **never** killed: the supervisor waits for it and reads the ceremony's own result file. |
| Interrupted ceremony | An intent record with no result → `interrupted`. The supervisor alerts and blocks all tier 2 and 3 operations until `agentmesh control ceremony resolve`, which shows that ceremony's own recovery path (relocate rollback, rotation manifest). Key operations are never rolled back automatically. |
| Supervisor unusable | `agentmesh control panic-revert` works without the supervisor. It boots out `org.agentmesh.control` and re-bootstraps the original plists directly. The original plists stay on disk throughout the migration. |
| Discord or gateway down | Fall back to local Touch ID. Notifications queue with bounded retry. Tier 1 is never blocked. |
| Audit write fails | Fail closed: refuse tiers 1–3. Tier 0 still answers. |
| Touch ID unavailable (screen locked, prompt timed out) | Return `approval_required`. Never fall back to a weaker gate. |

## Migration order

Each unit moves alone and runs natively for at least 3 days before the next one
moves. `agentmesh control unit <name> --backend launchd` reverses any step.

1. `retention`
2. `profile-log.catch-up`
3. `ingest`
4. `summary` (with the quota guard)
5. `sync`
6. `profile-log.bridge`
7. `insight`, `insight-tunnel`
8. `profile-log.broker`, last, because every agent's recall depends on it.

## Testing

**Unit tests** use fakes: `FakeBackend`, `FakeClock` (scheduler, watchdog, expiry,
sleep/wake), `FakePresence`, `FakeNotifier` and `FakeGateway`.

**Security tests with mutation gates.** `gates.py`, `approvals.py` and
`TouchIDPresence` must survive mutation testing. Cases:

- approval bound to the digest;
- single use;
- expiry;
- `plan_changed`;
- an agent without presence refused for tiers 2 and 3;
- a remote passkey cannot replace Touch ID at tier 3;
- audit failure fails closed.

**Contract tests:** every op refuses unknown keys, and error codes match this spec.

**Mac integration tests** use real launchd with `org.agentmesh.test.*` labels and
a temporary `AGENTMESH_HOME`. They cover the backend switch, rollback and
`panic-revert`.

**Owner-run host checklist:**
- a real Touch ID prompt;
- a Discord notification arriving;
- passkey approval end to end through the status gateway;
- a real sleep/wake cycle.

## Milestones

1. **Observe and control (tiers 0–1):** the supervisor, `LaunchdBackend`, the API,
   the CLI, the watchdog in observe-only mode, maintenance mode and the quota guard.
2. **Native units:** `NativeBackend`, the scheduler, an enforcing watchdog with the
   restart-storm guard, `panic-revert`, and migration steps 1–4.
3. **Tier 2:** `TouchIDPresence`, gates, approvals (Discord and passkey), device
   and grant operations, and the key inventory with expiry alerts.
4. **Tier 3:** ceremonies with intent records. This needs `--json` output and
   non-interactive entry points for the security wizard (13 `input()` calls today).
5. **Remaining migration:** steps 5–8.
6. **Legacy key retirement:** once `keys.py` sees everything, migrate or archive
   the legacy memory-sync key data, then remove the stack. This gets its own spec.

## Open items for planning

- The source of device last-seen for the stale-device rule. `presence.py` is console
  approval, not device presence. The likely sources are `heartbeats.py` and the
  devices in `profile-log-status.json`. Confirm before building `keys.py`.
- How the Phase 6 approval envelope extends to control-plane operations. It is
  scoped to pairing and policy today.
- The heartbeat coverage of each unit, to decide which units start with
  `watchdog: off`.

## Amendments from milestone 1 planning (2026-10-23)

- The #92 heartbeats are device telemetry, not service liveness. The M1 watchdog is observe-only and uses `launchctl print` plus `ps` runtime: jobs past `max_runtime_s` are `stuck`, and KeepAlive services that are not running are `down`. Service liveness pings move to M2.
- `org.agentmesh.sync` was retired in Phase 7a, so the registry has 8 units and `sync.retry` is dropped.
- In M1 launchd still starts jobs, so maintenance and quota holds are enforced in `memory-sync/job_notify.py` (ingest, summary, retention). They apply only when `AGENTMESH_HOME/control` exists.
- The M1 quota cap counts `llm_api_calls` from `status/last-summary.json`. A cost cap and `quota.set` come later.
- M1 rate-limit counters live in memory. `state.db` arrives with native units in M2.
- The M1 CLI is `agentmesh-log control …`. An audit write failure returns `failed` / `audit_unavailable`.

## Amendments from milestone 2 planning (2026-10-24)

- Backend moves are owner-run CLI commands, `agentmesh-log control adopt|release <unit>`, gated by typed console presence (`Type migrate:`), until Touch ID arrives in M3. They are not socket ops. The supervisor only reads `state.db`.
- M2's native backend runs scheduled jobs only (retention, catch-up, ingest, summary). KeepAlive services move in M5.
- A native run reproduces the plist's argv, cwd, logs, umask and schedule (`StartInterval`, or `StartCalendarInterval` with Minute/Hour/Weekday). Its environment is launchd's base keys plus the plist's `EnvironmentVariables`, without the supervisor's `AGENTMESH_HOME`. `ProcessType=Background` is not reproduced.
- The storm guard counts watchdog kills: more than 3 within 1 h puts the unit in `failed` until `control reset`. It alerts once through job_notify's Discord webhook.
- At start, orphans are sent SIGTERM only when pid plus `ps lstart` match, and their runs are marked `orphaned`.
- `panic-revert` needs no presence.

## Amendments from milestone 2.1 (2026-10-25)

- `adopt` runs `launchctl disable gui/<uid>/<label>` and then `bootout`. Bootout alone left the plist in `~/Library/LaunchAgents` to be loaded again at the next reboot or login, and both launchd and the supervisor then scheduled the unit.
- `release` and `panic-revert` run `launchctl enable`, then `bootstrap` (only if the label is not loaded already).
- Backstop: when the supervisor starts, it disables and boots out every native unit that launchd has loaded, and audits `launchd_reloaded`.
- `status` reports health `conflict` for a native unit that launchd also has loaded. It takes precedence over every other health state.
