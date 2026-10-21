# Profile log remote sync to Cloudflare R2 (Mac) — plan

> Status: **plan only.** Nothing in this document has been run on the live Mac. Deploying (D-2 to D-7)
> needs the owner's explicit go-ahead at each **owner checkpoint**. The agent never reads, prints, stores or
> passes R2 credentials; the owner enters them in their own terminal.

## Already decided (not reopened here)

From `docs/superpowers/specs/2026-10-18-encrypted-profile-log-design.md` (approved in #50) and later work:

- R2 is the default S3-compatible backend. The storage interface is `put/get/list` only, with no deletes,
  locks or conditional writes. Objects are already encrypted or signed, so moving to another backend is
  `rclone sync` plus a config change.
- Sizing assumes **1-minute polling**, which fits easily in R2's free tier.
- The Cloudflare Worker gatekeeper is optional and later. Until it exists, the device holds a
  **bucket-scoped** token ("Object Read & Write" on one private bucket). This is the same token shape the
  owner used for the live check on 2026-10-19 (phase 1-2 REPORT: 3 passed, boto3 1.43.114 default settings,
  no checksum overrides).
- Phase 5 rollout rule: no broker sync until the migration is accepted. The owner accepted it on the Mac on
  2026-10-20 (438 rows migrated).
- Windows, the Worker, dual-write and compaction are out of scope here.

## What is still open (this plan)

Facts read on 2026-10-20 (read-only: plist, file counts and interpreter only; no replica contents):

| Fact | Consequence |
|---|---|
| Live `~/Library/LaunchAgents/org.agentmesh.profile-log.broker.plist` runs `~/.agentmesh/venv/bin/python -m agentmesh_log.cli broker run --home ~/.agentmesh/profile-log` with `StandardOutPath`/`StandardErrorPath` under `~/.agentmesh/logs/`. | No remote, so nothing has ever been uploaded. The generator does not emit the log paths: they were added by hand during rollout, and a newly rendered plist must keep them. |
| `~/.agentmesh/venv` has **no boto3**. | `--s3-bucket` fails today with `S3 backend requires optional boto3 dependency`. Install the pin from `profile-log/requirements-s3.txt` (`boto3==1.43.114`). |
| `S3Storage` builds `boto3.client('s3', …)` with the **default credential chain**, and `~/.aws/config` exists (Amazon Q). | Credentials under launchd would be ambiguous or wrong. The broker must receive credentials **explicitly** and never fall back to `AWS_*`, `~/.aws` or instance metadata. |
| `render_profile_units()` (`memory-sync/platform_service.py`) renders only `broker run --home H`. | It needs remote flags, `--sync-interval` and log paths. |
| `Broker.serve()` sync thread: `while not stopped.wait(i): self.sync_once(remote)`. The result is discarded, and an exception (for example from `sync_control` while offline) **ends the thread silently** while the broker keeps serving. | Sync can stop with nothing visible. The loop needs exception handling and a status record. |
| Local replica: events 438, keys 2, registry 7, keyring 12 (459 objects). `sync_control` also pushes `registry/ keyring/ keys/ profiles/ alerts/`. | The first upload is about 459 objects. Control objects are signed public records or age-encrypted, and events and wraps are sealed. The ciphertext-only gate checks every one. |
| `profile-log-status.json` and the Insight Profile log tab come from PR #61 (`agentmesh_log/status.py`, sections `overview/migration/last_apply/last_catch_up`). | `last_sync` is a new section. **I-4 depends on #61 being merged first.** |

## Setup on this Mac (accepted by the owner 2026-10-21)

- **Bucket and token:** reuse the private bucket and the bucket-scoped "Object Read & Write" token the
  owner created on 2026-10-19 (both still active). Live data goes under prefix `v1/`, kept apart from the
  test prefix `agentmesh-live-tests/`. The endpoint account ID is not secret and goes in the plist.
- **Where the token lives:** the login Keychain, service `org.agentmesh.profile-log.r2`, accounts
  `access-key-id` and `secret-access-key`. The owner enters it once at a `security … -w` prompt (D-2). The
  broker reads it with `/usr/bin/security`, using the same pattern as `oskeystore.MacKeychainFile`. It is
  never put in plist env or `~/.aws`, and never taken from boto's default credential chain.
- **Interval:** 60 s, as in the design.
- **Behaviour (not a choice):** a sync failure never stops local recall/remember and never crash-loops.
  AgentMesh never deletes remote objects.

## Implementation (Codex implements, Claude reviews; PR against main)

### I-1 Explicit credentials (`profile-log/agentmesh_log/storage/r2creds.py`, `cli.py`)
- `load_keychain_credentials(service, runner=subprocess.run, keychain=None)` returns
  `(access_key_id, secret)` or raises `StorageError('S3 credentials missing'|'S3 credentials refused')`.
  It validates the shape (R2: 32 lowercase hex / 64 lowercase hex), has a timeout, uses a minimal env
  `PATH=/usr/bin:/bin`, and raises outside the `except` block so no captured stdout is chained.
- CLI: `--s3-credentials keychain:<service>` on `broker run` and `broker sync`. It is **required** whenever
  `--s3-bucket` is given (no default chain). `remote_storage()` passes a `client_factory` that calls
  `boto3.client('s3', endpoint_url=…, region_name='auto', aws_access_key_id=…, aws_secret_access_key=…)`.
  `--s3-endpoint` must be `https://`.
- Tests (fake runner + moto): missing item → `credentials_missing`; malformed value → refused; `AWS_*` env
  and `~/.aws` set to bogus values are ignored (moto asserts the explicit key ID); no credential text in
  any exception, log record or `repr`.

### I-2 Resilient sync loop with status (`broker/service.py`, `status.py`)
- The loop runs **once immediately**, then every `sync_interval`. Every exception becomes an outcome, and
  the thread never exits while the broker runs.
- After each attempt, `status.write(root, last_sync={at, outcome, uploaded, downloaded, invalid, errors,
  outbox, local_objects, remote_objects, interval_s, backend:'s3'})`. It holds counts and identifiers only,
  with no bucket, endpoint, key or object names. Extend `status.valid()` with a strict schema. To avoid
  churn, write only on a change of outcome or counts, or every 10 minutes, whichever comes first.
- One content-free stderr line per outcome change (`profile-log sync: ok uploaded=… outbox=…`).
- Tests: a raising remote keeps the thread alive and records `remote_error`; recovery records `ok`; the
  status schema rejects extra fields; the broker accepts connections while the remote is failing.

### I-3 Plist rendering (`memory-sync/platform_service.py`)
- `render_profile_units(..., remote=None, logs=None)`, where `remote = dict(bucket, prefix, endpoint,
  credentials, sync_interval)`, validated as: bucket `[a-z0-9][a-z0-9-]{1,61}[a-z0-9]`, `https://`
  endpoint, prefix passes `validate_key(prefix=True)`, credentials `keychain:[A-Za-z0-9.-]+`, finite
  interval ≥ 10. With remote set, the broker argv gains `--s3-bucket B --s3-prefix P --s3-endpoint E
  --s3-credentials keychain:S --sync-interval N`. `logs` adds `StandardOutPath/StandardErrorPath`
  (`broker.out.log`/`broker.err.log`). Without `remote`, output is byte-identical to today (the fixture in
  `memory-sync/tests/fixtures/profile_log_units.json` is unchanged).
- `generate … --unit broker` gains `--s3-bucket/--s3-prefix/--s3-endpoint/--s3-credentials/--sync-interval/
  --logs`. It still writes only into a new output directory, never registers or loads anything, and never
  takes credentials as input.
- Tests: plist round-trip with `plistlib`; the argv contains no secret-shaped values; invalid bucket,
  http endpoint or control characters are rejected; the macOS output matches the live plist exactly apart
  from the added flags.

### I-4 Insight (after #61 merges; `insight/`)
- The Profile log tab gets a **Remote sync** card from `last_sync`. If absent: "Off — not configured". If
  `ok`: "Last sync <relative time>, outbox N, local L / remote R". If an error: the outcome in amber or red,
  plus the last ok time. The card shows "stale" when `at` is older than 3× `interval_s`.
- Read-only, as before; Insight never reads credentials or contacts R2.

### I-5 Verification tool (`profile-log/agentmesh_log/verify_remote.py`, read-only)
- Inputs: home, the same `--s3-*` flags, `--markers FILE` (local plaintext probes, see G-5).
- It lists the whole remote prefix once. Only replica prefixes (`events/ keys/ registry/ keyring/ profiles/
  alerts/`) are compared with the local replica. Other remote-only prefixes (Phase 6 `connectors/`,
  `outbox/`, the planned `archive/` tier) are reported as `unknown_remote` and do not fail the run, but
  they are still marker-scanned. It compares the **sha256 of every object** on both sides. It parses every
  `events/` and `keys/` object as a sealed envelope or wrap, and every control object as a signed record.
  It scans every remote byte string for each marker. It does a restore rehearsal: downloads remote events and wraps into
  a disposable empty `FilesystemStorage` under `$TMPDIR`, then verifies and opens events with the
  local keys, compares counts, and deletes the temp directory.
- Output: one JSON line of counts and booleans only (`missing_remote, missing_local, unknown_remote,
  hash_mismatch, unparsable, marker_hits, restored_events, opened_events`). It never prints keys, markers, bodies or
  object names.

### I-6 Encrypted backup tool (`profile-log/agentmesh_log/backup.py`)
Owner policy (2026-10-21): every backup asks the owner first and is always encrypted. Plaintext or silent
backups are not allowed.
- `python -m agentmesh_log.backup create --home H --out FILE --include PATH…` shows a content-free summary and
  asks `Create encrypted backup? [y/N]`. It builds the tar in memory and writes only the age ciphertext
  (0600, exclusive create). Its output is one JSON line with counts and the sha256 of the encrypted file.
- **Recipients: this device's age recipient and the account recovery age recipient**, both in one age file.
  The device recipient makes a same-Mac rollback possible without the recovery kit. The recovery recipient
  keeps the backup usable if this Mac's Keychain or sealed keystore is lost, which is the case a backup
  exists for. Profile/scope recipients are not used: they rotate and grant less than the backup holds. The
  Syncthing/system identity is not used either: it belongs to the legacy sync path.
- `restore --in FILE --to NEW_DIR` decrypts with the device identity into a new 0700 directory, rejecting
  links and `..`. Recovery-identity decryption goes through the recovery kit.

## Acceptance gates

| Gate | Check | Pass |
|---|---|---|
| G-1 Tests | `profile-log` and `memory-sync` suites + new tests, CI on 3 OS | all green; no live network in CI (`AGENTMESH_R2_LIVE` unset) |
| G-2 No credential leakage (code) | grep diff + tests: no `AWS_` fallback, no secret in argv/plist/status/logs/exceptions | test asserts with sentinel secrets |
| G-3 Default unchanged | rendering without `remote` is byte-identical; `broker run` without flags behaves as today | fixture test |
| G-4 Live smoke (owner's terminal) | `AGENTMESH_R2_LIVE=1 pytest profile-log/tests/test_r2_live.py` against the existing bucket/token (values entered by the owner, as on 2026-10-19) | 3 passed, cleanup verified |
| G-5 Ciphertext only | `verify_remote.py` with markers: all profile/scope paths, agent names, owner email, device hostname, 25 random memory snippets of 6+ words from `mac.db` (`--sample-db` / `--sample 25`, kept only in memory; owner-supplied markers in a 0600 file the owner deletes after) | `marker_hits=0`, `unparsable=0` |
| G-6 Heads match | same run | `missing_remote=0 missing_local=0 hash_mismatch=0`; remote events = 438 (+ new since) |
| G-7 Restore rehearsal | same run | `restored_events == local events`, `opened_events == restored_events` |
| G-8 Steady state | 10 min after D-6: status `last_sync.outcome=ok`, `outbox=0`; `remember` a synthetic memory → within 2 intervals `uploaded≥1`, then `outbox=0`; `launchctl print` shows one broker PID, no restarts | all hold |
| G-9 Failure behaviour | owner temporarily renames the Keychain item (or pulls network): recall still works; status shows `credentials_missing`/`remote_error`; restoring it returns `ok` with no broker restart | all hold |
| G-10 Insight | Profile log tab shows the Remote sync card per I-4 for ok, error and stale | screenshot by owner or reviewer |

## Deployment on the Mac (owner at terminal; each step needs a go-ahead)

I-4 waits for #61 to merge. D-1 (bucket and token) is already done: the owner created them on 2026-10-19.

- **D-2 Keychain (owner's terminal).** Enter each value at the prompt:
  ```sh
  security add-generic-password -U -s org.agentmesh.profile-log.r2 -a access-key-id -w
  security add-generic-password -U -s org.agentmesh.profile-log.r2 -a secret-access-key -w
  ```
  The agent may verify **only presence** with
  `security find-generic-password -s org.agentmesh.profile-log.r2 -a access-key-id` (no `-w`; it prints
  attributes, not the secret).
- **D-3 Install boto3** into `~/.agentmesh/venv` (`uv pip install boto3==1.43.114`, the pin in
  `profile-log/requirements-s3.txt`), and replace `~/.agentmesh/app/profile-log/agentmesh_log` with
  `git archive origin/main`, recording the old `REVISION` (today `f812b7d`). No copy of the old app is kept:
  rollback re-deploys that revision from git.
- **D-4 Optional encrypted backup (owner consent).** Stop the catch-up and broker jobs, record the replica
  object count (459), then the owner decides whether to back up. If yes:
  `agentmesh_log.backup create --include ~/.agentmesh/profile-log/replica --include <broker plist>` to an
  encrypted `.age` file (I-6). If no, skip it. Rollback does not depend on it: the first sync downloads
  nothing (the `v1/` prefix is empty, so G-6 expects `downloaded=0`), so the replica is unchanged by D-5,
  and the old plist can be regenerated byte-identically (`generate --unit broker --logs …` without
  `--s3-*`, G-3).
- **D-5 First sync, one-shot (owner's terminal).** `launchctl bootout gui/$UID/org.agentmesh.profile-log.broker`,
  then `agentmesh-log broker sync --home ~/.agentmesh/profile-log --s3-bucket B --s3-prefix v1/
  --s3-endpoint E --s3-credentials keychain:org.agentmesh.profile-log.r2`. Expect
  `uploaded≈459 downloaded=0 invalid=0 errors=0`. Then run this exact command (replace `B`, `E`, `MARKERS_FILE`, and `MAC_DB` with the owner's local values) → **G-5, G-6, G-7**:
  ```sh
  ~/.agentmesh/venv/bin/python -m agentmesh_log.verify_remote --home ~/.agentmesh/profile-log --s3-bucket B --s3-prefix v1/ --s3-endpoint E --s3-credentials keychain:org.agentmesh.profile-log.r2 --markers MARKERS_FILE --sample-db MAC_DB --sample 25
  ```
  If any gate fails, stop: go to rollback R-1 and leave the remote data for analysis.
- **D-6 New plist.** `platform_service.py generate --unit broker … --s3-* … --sync-interval 120 --logs
  ~/.agentmesh/logs --output <new dir>`. `diff` against the live plist shows only the added flags. Then
  install it and `launchctl bootstrap gui/$UID …`.
- **D-7 Watch.** G-8 and G-9, then G-10 in Insight.

## Rollback

- **R-1 Stop syncing (any time).** `launchctl bootout` the broker, regenerate the no-remote plist
  (`generate --unit broker --logs ~/.agentmesh/logs` without `--s3-*`; it matches the pre-D-6 plist), install
  it and `launchctl bootstrap`. To undo D-3, re-deploy the recorded revision with `git archive <old REVISION>`.
  Local recall is unaffected: sync only adds verified objects to the replica. If the owner made the D-4
  backup and the replica must return to its exact pre-sync state, stop the broker, run
  `agentmesh_log.backup restore --in <file>.age --to <new dir>` (the owner is asked first), and swap the
  restored `replica/` in. Restarting rebuilds indexes from the replica.
- **R-2 Revoke access.** Delete the R2 token in the dashboard, then
  `security delete-generic-password -s org.agentmesh.profile-log.r2 -a …` for both items.
- **R-3 Remove remote data (optional, owner only).** Delete the `v1/` prefix or the whole bucket in the
  dashboard. The objects are ciphertext or signed public records, so a leftover copy exposes no memory
  contents, but it does reveal counts, timestamps (ULIDs) and device/profile IDs.
- The code changes (I-1 to I-4) are inert without the new flags, so reverting the PR is not needed for
  rollback.

## What should show afterwards

- `~/.agentmesh/profile-log-status.json` → `last_sync: {outcome:"ok", outbox:0, local_objects==remote_objects,
  interval_s:120, at: < 6 min old}`; the other sections are unchanged.
- Insight Profile log tab → "Remote sync: ok · last <1m ago · outbox 0 · local 459+ / remote 459+".
  Before deploy it shows "Off — not configured".
- `~/.agentmesh/logs/broker.err.log` → one `profile-log sync: ok …` line after start, then only lines on
  outcome changes.
- The R2 dashboard → objects under `v1/events/`, `v1/keys/`, `v1/registry/`, `v1/keyring/`, growing by
  about 1-1.5 KB per new memory.

## Known limits

- The bucket token is account-wide for that bucket. A stolen Mac login session can read and overwrite
  ciphertext, and plain PUT has no overwrite prevention. The Worker gatekeeper (design, optional) is what
  removes this; it is not in scope.
- The Keychain item is readable by any process running as the user (the same boundary as `keystore.sealed`).
- Without a second device there is nothing to download yet. Windows pairing is a later, separate plan.
- **R2 request sizing (Class A: LIST and PUT; 1M free per month).** Since the staged sync (06b681a), each
  sync makes one LIST pass over the 6 replica prefixes. `sync_control` and `Replica.sync_once` read that
  snapshot, so the earlier second `events/`+`keys/` LIST is gone. ListObjectsV2 returns at most 1,000 keys
  per page, so the cost of a pass grows with history: about `ceil(events/1000) + 5` LISTs.

  | Objects (≈ events) | LIST per sync | 1 device, 60 s | 2 devices, 60 s | 2 devices, 300 s |
  |---|---|---|---|---|
  | 459 (today) | 6 | 0.26 M/month | 0.52 M | 0.10 M |
  | 5k | 10 | 0.43 M | 0.86 M | 0.17 M |
  | 18k | 23 | 0.99 M (limit) | 1.99 M | 0.40 M |
  | 30k | 35 | 1.51 M | 3.02 M (≈ $9/month over the free tier) | 0.60 M |

  The table does not include PUTs (about one per new event, plus control objects) or Phase 6 traffic (an
  `outbox/` poll, Worker registry reads, the Windows bridge device). Each of those adds to the same 1M.
  **Requirement:** the separate "Incremental R2 sync listing" task (per-prefix `StartAfter` from the last
  seen ULID, and a negative cache for invalid objects) must land **before a second device is added** or
  before `events/` passes about 5k objects, whichever comes first. Until then a second device should run
  at `--sync-interval 300`.

  **Update 2026-10-21 (#65, incremental listing landed):** a steady-state sync lists `keys/` once, each
  scope prefix after its ULID cursor (10 min margin, capped at now), and the 4 control prefixes once.
  It does a full listing on start and every 24 h. Measured through `Broker.sync_once` at 30k objects
  with 7 scope prefixes: main after #62 used 36 pages per cycle (1.56 M/month at 60 s); #65 uses 12
  pages per cycle (0.52 M/month; about 0.52 M with the daily reconciliation amortized). The cost now
  grows with the number of scopes, not with history, so 2 devices at 60 s are about 1.04 M/month, near
  the free tier. See `docs/superpowers/reports/2026-10-21-replica-incremental-sync.md`.

  **Update 2026-10-21 (owner decision):** the default `--sync-interval` is now 120 s (21,600 cycles per
  month). At 30k objects with #65 that is about 0.26 M Class A per device per month, so two devices stay
  under the free tier. The live Mac broker was switched to 120 s the same day.
