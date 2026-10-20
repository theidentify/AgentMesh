# Encrypted profile log

Standalone Python 3.11 library and Phase 4 local memory broker for the encrypted profile log design.
It models local accounts, profiles, scope trees and sensitivity labels; certifies devices
and agents; seals, verifies and opens signed HPKE events; and stores opaque objects in
filesystem or S3-compatible storage. It imports nothing from `memory-sync` or `profile-memory`.

## Tests

Use the prepared environment; no installation is needed:

```sh
# From the repository root
~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests
# From profile-log/
~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider tests
```

The requirements files pin cryptography 50.0.2, optional boto3 1.43.114,
pytest 8.4.2 and moto 5.2.4, plus pyrage 1.4.0, shamir-mnemonic 0.3.0 and segno 1.6.6. Filesystem storage uses only the standard library;
boto3 is loaded only when constructing an S3 client. Tests use temporary directories,
moto and explicit dummy credentials. The HTTP acceptance test binds only to `127.0.0.1`;
it needs permission to bind loopback sockets and fails explicitly if a sandbox denies it.
No cloud access or real credentials are needed.

## Minimal use

Run from `profile-log/` (or place this directory on `PYTHONPATH`):

```python
from agentmesh_log.model import Account, Profile, ScopeTree, Label
from agentmesh_log.identity import DeviceIdentity, AgentIdentity, Registry
from agentmesh_log.events import seal_event, verify_event, open_event, event_path
from agentmesh_log.storage.filesystem import FilesystemStorage

account = Account(email='local-only@example.test')
device = DeviceIdentity()
registry = Registry(account.public_record())
registry.add_device(device.certificate(account))
agent = AgentIdentity(device, account.account_id)
work = ScopeTree(Profile('work'))
work.add(work.root.scope_id, 'common', 'common')
project = work.add(work.root.scope_id, 'project', 'client-a', Label.internal)
key = work.keys[project.scope_id]

# A phone needs only this write key and its own certified signing identity.
data = seal_event(key.write_key, agent, {'memory': 'An example'},
                  label=project.default_label, tags=['example'], task=None)
store = FilesystemStorage('./disposable-example-store')
store.put(event_path(verify_event(data, registry)), data)
payload = open_event(data, registry, key)
assert payload['body'] == {'memory': 'An example'}
```

Names and email stay local. `Profile` is the root `Scope` (its `profile_id == scope_id`).
`Scope.parent` is the parent's opaque scope ID. `ScopeTree` owns the current read keys,
all locally retained epochs, and current wrap records; agent objects contain no data keys.
`rotate(scope_id)` replaces that scope epoch and re-wraps its direct children; store any
old wrap records you need before rotation. Retained epochs can still read historical data.
A parent reads a descendant by unwrapping each edge, then opening with the descendant key.
`wrap_key(child_read_key, parent_read_key)` authenticates wraps with a parent-private-key-derived
HMAC before HPKE decryption; a write-only phone cannot forge a new child epoch. Selecting the
current epoch and authorizing rotation remain Phase 4 keyring responsibilities.

Labels are explicitly supplied to `seal_event`: the caller uses
`resolve_label(scope.default_label, detected, agent_label)` to select the maximum.
On revisions, supply the known `previous_label` to enforce confirmed lowering.
The library cannot infer a prior encrypted event's label from its ULID alone.

## Storage

`put(key, bytes)`, `get(key)` and sorted `list(prefix)` are the complete interface.
Filesystem publication uses a fsynced temporary file and an exclusive hard link;
identical re-put is harmless and different bytes raise `ObjectExists`.
Symlinks and Windows reparse points are refused. POSIX directory descriptors guard
read/write traversal where supported; other platforms use checked paths. The root and
its ancestors must be managed by a trusted local user. Windows permissions follow the
host's permission model; POSIX files are created with mode 0600.

`S3Storage(bucket, prefix='', endpoint_url=None, region_name='auto', client=None,
client_factory=None)` supports R2, B2, Garage and SeaweedFS-style endpoints through boto's
normal client configuration. An injected client/factory can configure path-style addressing.
S3 uses plain PUT and can overwrite: immutability depends on unique event ULIDs, unique wrap
keys and, later, the Worker. Backend prefixes are isolated directory-like namespaces.
No conditional writes, deletes, locks or server events are used.

## Boundaries

The core event/storage APIs establish authorship, not scope-write authorization.
Use the Phase 3 broker below for grants, revocation, detection, search and revision
head tracking. Phase 4 adds enrollment, age grants, OS secure storage, recovery,
SLIP-39 shares, key-store migration and console presence. Compaction and explicit
conflict resolution remain deferred.
There is no automatic promotion to `common` and no implicit cross-profile operation.

See [FORMAT.md](FORMAT.md) for normative wire rules and the implementation REPORT under
`docs/superpowers/plans/2026-10-19-encrypted-profile-log-phase1-2.REPORT.md` for gate evidence.

## Live Cloudflare R2 check

This is an opt-in integration check that you run yourself. The default suite skips it;
CI must leave `AGENTMESH_R2_LIVE` unset and never runs the live check. Offline moto tests
exercise the harness without cloud access. No dependencies need to be installed in the
prepared environment.

1. Create a dedicated **private** R2 bucket with no public access.
2. Create an R2 API token with **Object Read & Write**, scoped to that bucket only.
3. Set the dedicated variables below. The client receives these credentials explicitly;
   it does not fall back to your other `AWS_*` credentials. Once live mode is enabled,
   missing configuration fails the test instead of skipping it. The endpoint must start
   with `https://`.

```sh
export AGENTMESH_R2_ENDPOINT='https://<account>.r2.cloudflarestorage.com'
export AGENTMESH_R2_BUCKET='<private-test-bucket>'
# Enter the access key ID at the prompt without echo or shell history:
read -rs AGENTMESH_R2_ACCESS_KEY_ID; export AGENTMESH_R2_ACCESS_KEY_ID
# Enter the secret at the prompt without echo or shell history:
read -rs AGENTMESH_R2_SECRET_ACCESS_KEY; export AGENTMESH_R2_SECRET_ACCESS_KEY

AGENTMESH_R2_LIVE=1 ~/.cache/agentmesh-logvenv/bin/python -m pytest -q -s -p no:cacheprovider profile-log/tests/test_r2_live.py

unset AGENTMESH_R2_ACCESS_KEY_ID AGENTMESH_R2_SECRET_ACCESS_KEY
```

Do not enable shell tracing (`set -x`) when entering credentials. The test suppresses SDK
logging, sanitizes client/cleanup errors and prints one summary line containing only
operation counts and elapsed time. `-s` makes that finalizer output visible.

The check uses a fresh `agentmesh-live-tests/<ULID>/` prefix and covers random-byte
roundtrips, missing keys, nested/sorted listing, pagination with `page_size=2`, identical
re-put, and three encrypted `work/client-a` events including a write-only phone. It checks
that sibling keys cannot open events and fetched raw objects expose none of the tested
private payload metadata or account email. Plain S3 PUT does **not** provide exclusive
create: identical re-put is checked, not an overwrite-prevention guarantee.

On normal completion, including test assertion failures, a module finalizer deletes only
objects under that run's full prefix and verifies it is empty, leaving no test objects
behind. It refuses broad/invalid prefixes, checks returned keys before deletion, and uses
batches of at most 1,000. Cleanup failures fail the run. A killed process or loss of access
can prevent cleanup; in that case remove only the abandoned run prefix from the dedicated
bucket. The library's Storage interface still has no delete operation; cleanup is test-only.

`S3Storage.list(prefix, page_size=None)` accepts an optional keyword page size. With a
value such as `2`, it configures boto's paginator while still returning all matching keys
sorted; leaving it unset preserves the backend's default page size.

## Broker

Phase 3 adds a local memory broker, per-agent RAM-only SQLite FTS5 indexes,
mutual device/agent authentication, secret/PII detection, encrypted index snapshots,
a local encrypted replica and explicit synchronization. The earlier library APIs
remain usable independently. Caller rewiring, auto-start and biometric presence
are not implemented. Phase 4 administration is described below.

Run these commands with the prepared interpreter from `profile-log/` (replace
`H` with a new disposable, private directory):

```sh
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker init --home H --profile work --profile personal
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker scope add --home H work/client-a
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker grant --home H --name assistant --read work/client-a --write work/client-a
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli presence open --home H --class first_access
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker run --home H
```

The grant command returns `{"agent_key":"..."}`. Give that file only to the
intended agent. In another process:

```sh
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli remember --agent-key AGENT_KEY --scope work/client-a 'A synthetic memory'
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli recall 'synthetic memory' --agent-key AGENT_KEY --scope work/client-a
```

Agents receive no scope keys. Reads cover descendants of granted scopes; writes
cover only explicitly granted scopes. Recall defaults to the grant's default
profile. Repeat `--profile` and `--scope` to select profiles/scopes; any unreadable
scope denies the entire request. `--project` filters body metadata and grants no
access. Cross-profile recall requires `--allow-cross-profile` on the grant;
secret results require `--allow-secret`. `common` always requires an explicit
write scope. Revisions are available through `BrokerClient.write(revises=...)`.
Concurrent revision heads remain visible as a conflict with alternative ULIDs.

`broker revoke --home H AGENT_ID` applies on the next request, including existing
connections. Administrative commands take an exclusive PID lock at `H/.lock`.
A crashed administrator may leave that lock behind; verify that no administrator
is running before manually removing it. The broker rechecks state contents and
mtime on each request and rebuilds indexes when grants change. Each new event is
decrypted once and added only to authorized per-agent indexes. Recall selects
candidates in SQL before decoding/scoring. Replica object hashes are read once at
startup and maintained in memory as writes and sync downloads arrive. Existing
objects are immutable; out-of-band replica edits require restarting the broker.
Revision components update incrementally, including when children arrive before
their parent. Snapshots retain authorized revision rows and are flushed on clean
broker close (or explicit `flush_indexes()`), rather than after each write. A
missing, invalid or stale snapshot rebuilds from the encrypted replica. A crash
may lose index work, but not an already-persisted event.

Local profile names must be unique, as must sibling scope names. Both CLI and
state/tree APIs reject duplicates; loading ambiguous state is also refused.

For the offline performance benchmark, run from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 ~/.cache/agentmesh-logvenv/bin/python profile-log/tests/broker/bench_broker.py
```

It creates disposable data using two agent grants, populates 1,000 then 5,000
events, and reports 20-sample write and broad-recall median/max latencies. Writes
include remember+sign, encryption, replica persistence and audit fsync. The query
matches every event; timing excludes population and shutdown snapshot flushing.

Writes and recall do not need a remote. To synchronize explicitly:

```sh
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker sync --home H --filesystem REPLICA_DIRECTORY
```

The optional S3 form uses `--s3-bucket`, `--s3-prefix` and `--s3-endpoint`.
`broker run --sync-interval S` requires the same explicit remote flags; automatic
synchronization is off by default. No service or daemon is installed. Only invoke
remote synchronization when you intend to access that remote. `Replica.outbox()`
counts local event/wrap objects absent from the last successful remote listing.
Downloaded events must pass certificate/signature and object-path verification.

Secrets are replaced by `[REDACTED:<kind>]` and raise the event label to `secret`;
PII remains intact and raises it to `internal`. Audit JSONL records request outcomes,
IDs and detector kinds/counts, with a keyed HMAC of query text. It never stores
query, body, tags or task text. An audit append/fsync failure returns `unavailable`.
Audit append-only behavior is conventional; tamper evidence is deferred.

**Key-store boundary:** existing Phase 3 homes use a plaintext 0600 `keystore.json`
until explicitly migrated. Phase 4 initialization uses `keystore.sealed` protected by
a random key in the OS store and discards the account root private key. This protects
copied files, not malicious code running as the same OS user. Agent signing-key files
remain local and contain no scope keys. Python does not promise physical RAM wiping;
crypto-shredding here removes capabilities from the persisted state and active broker
indexes, not copies already made by an attacker or retained in backups.

POSIX broker directories are 0700, files and the Unix socket are 0600, unsafe roots
and symlinks are refused, and peer UID must match. Windows uses named pipes with
byte-only messages and the default pipe DACL; local administrators can still
connect, and certificate authentication remains required. Windows file access
follows host ACLs rather than POSIX mode bits. Socket tests use short temporary
paths and fail explicitly if the sandbox denies binding; they are never skipped
for that reason. The reviewer must run those tests outside the sandbox and on
Windows/Linux CI.

## Devices and recovery

Run commands below from `profile-log/` using the prepared interpreter. Administration
is explicit; no installer, caller, service or auto-start configuration is changed.
Native OS storage currently supports macOS login Keychain and Windows Credential
Manager. Tests inject a fake backend; Linux has no native backend in this phase.

Initialization prints three 2-of-3 SLIP-39 shares once as `recovery_shares` in the JSON
result. `--out RECOVERY_KIT` instead writes separate 0600 files. The destination must
be outside the broker home and replica. Print and store the shares separately. Keep a
trusted copy of `H/replica/registry/account.json` with the kit: recovery on a fresh home
uses this public account record to authenticate the storage registry. Do not trust an
account record newly fetched from an untrusted relay at recovery time.

Each device has an Ed25519 signing key and independent random age identities for each
profile, sealed locally in its OS-protected state. The generic device age recipient is
retained as a device binding; it cannot decrypt profile grants. Profile names and scope
trees travel only inside signed, profile-encrypted metadata.

```sh
# A: create the account and scopes, then publish control objects to shared storage.
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker init --home A --profile work --profile personal --out KIT
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker scope add --home A work/client-a
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker sync --home A --filesystem SHARED

# B: leave this running while A approves the displayed request ID.
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli join --home B --filesystem SHARED

# A: select exactly the scopes B should receive (repeat --scope as needed).
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli pair approve REQUEST_ID --home A --filesystem SHARED --scope work/client-a
```

Compare A's approver fingerprint on B using a trusted screen/channel. Compare the
six-digit codes on both devices and type `yes` only if they match. A QR payload is
`agentmesh-pair:v1:REQUEST_ID:COMMIT_HASH`; the approver's `--fingerprint COMMIT_HASH`
checks its full hash instead of numeric comparison. Requests expire after ten minutes
and are single-use. The commit binds eight fresh profile recipient slots; the result
assigns a different slot to each granted profile. B publishes the signed bindings and
discards unused identities. At most eight profiles can be granted in one pairing.
A grant to `work/client-a` does not include its parent, siblings, `common` or `personal`.

For a later profile, obtain its opaque ID from the granting device's public registry
paths. B must first publish its own random recipient; A cannot invent it:

```sh
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli pair recipient PROFILE_ID --home B --filesystem SHARED
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli pair regrant B_DEVICE_ID --home A --filesystem SHARED --scope personal
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker sync --home B --filesystem SHARED
```

Regrant fails if that signed recipient is absent. It includes the selected scopes and
descendants, plus metadata for a new profile. Existing metadata objects are immutable;
new scope topology on an already paired peer is not automatically refreshed in this
phase. Regrant after recovery uses the device's existing profile recipient and transfers
new epochs. `device list --home A` shows known device IDs and revocation status.

```sh
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli device remove B_DEVICE_ID --home A --filesystem SHARED
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli profile archive work --home A --filesystem SHARED
```

Removal revokes B's agents and rotates every scope it could read. Other devices must
sync before learning revocation. B can still read old data it already had; its retained
keys cannot read later events. Archive refuses writes while retaining historical recall.

Record timestamps (`issued_at`, `created_at`) are self-declared by signers, not trusted
proof of when a record was created. A revoked device can backdate a grant of a key it
already held and redistribute that old key. Revocation does not retract old data or
prevent that redistribution. It cannot obtain the replacement keys from surviving
devices' grants or make a backdated epoch supersede the post-revocation rotation:
a successor must not predate its parent, and the rotation is dated after revocation.
A backdated branch from an older epoch loses to the newer rotation; a purported
successor dated before that rotation is rejected. These checks do not provide trusted
timestamps for historical records.

```sh
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli profile delete work --home A --filesystem SHARED --confirm-name work --share-file KIT/share-1.txt --share-file KIT/share-2.txt --out NEW_KIT
```

Deletion requires typed profile-name confirmation, a fresh console confirmation and two
current recovery shares. They authorize master recovery rotation without keeping a
recovery signing key on daily devices. It destroys that profile's local age identity,
epoch keys and index snapshots; other devices do the same on sync. Surviving profiles
remain readable, and replacement shares restore only surviving profiles. The command
warns before proceeding: **anyone holding two old recovery shares can still decrypt the
deleted profile's old recovery object and grants in storage. Destroy those paper shares,
or remove the old objects by hand, to eliminate this exception.** Storage deletion is
never automatic. Backups or offline devices can retain old keys until separately handled.
Keep the newly printed shares before destroying the old set.

```sh
# One-shot verification: no presence window is opened or consumed.
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli recovery test --home A --filesystem SHARED --share-file KIT/share-1.txt --share-file KIT/share-3.txt

# Recovery after losing all devices, using the trusted account record from the kit.
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli recovery use --home FRESH --filesystem SHARED --account-file KIT/account.json --share-file KIT/share-1.txt --share-file KIT/share-2.txt --out REPLACEMENT_KIT
```

Omitting `--share-file` prompts twice without echo. One share is insufficient. Recovery
test only appends one presence-log line; state, replica, indexes, audit and storage stay
byte-identical. Recovery use restores historical roots and descendants, publishes a
signed alert, rotates all accessible scope epochs and the master recovery secret, and
prints new shares. Old shares fail verification against the current recovery record.
Other devices see alerts through `whoami.alerts` and synchronization results, and need
`pair regrant` for new epochs. Share replacement preserves historical readability.
`recovery create` is only for a root-bearing legacy home that has no recovery record;
normal Phase 4 init and migration already create recovery material.

Presence windows default to 15 minutes; explicit `presence open --minutes N` accepts
integer durations from 1 through 15 and expires after exactly N minutes. Windows are separate for `device`, `agent`, `recovery`,
`export`, `profile`, `cross_profile`, `secret_read`, and `first_access`. Console approval
requires a TTY and the displayed action word. Recovery test/use and delete require
one-shot approval. The daemon never prompts: open the necessary window explicitly.

```sh
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli presence open --home A --class first_access --minutes 15
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli presence open --home A --class secret_read --minutes 15
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli presence status --home A --class secret_read
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli keystore migrate --home LEGACY --to os --out KIT
```

Cross-profile and secret reads still require their agent grant flags. Without the
corresponding window the broker returns `presence_required`. Every trigger is logged
without query text or keys in `presence.jsonl`. Migration deletes the plaintext store
only after the sealed state round-trips successfully and the root key has been dropped.

Owner-only native smoke (never run by pytest), on both Mac and Windows:

```sh
~/.cache/agentmesh-logvenv/bin/python tests/smoke_os_keystore.py
```

Use the equivalent prepared Python path on Windows. The script asks permission, creates
one disposable native key-store entry, checks a sealed round-trip, and removes the entry.
Run the full suite outside the socket-restricted sandbox and on all three OSes; a denied
socket bind is an explicit test failure. Leave live R2 mode disabled for offline tests.

## Migration from SQLite

Migration is opt-in and offline. SQLite remains the authority; raw observation events
and transcript evidence stay there. Only active durable items and every non-session
summary row migrate (summary versions are range-allocated IDs, not an order). Item metadata is discarded; provenance contains integer observation
IDs only. Detectors redact secrets and raise labels before signing. Migration, hooks,
catch-up and parity never synchronize remote storage.

Start with `python -m agentmesh_log.cli migrate plan --db DB` using the profile-log
interpreter. It prints project names and counts, never row content. Write an owner-reviewed
UTF-8 mapping, for example (replace these synthetic names):

```json
{
  "format": "agentmesh-migration-mapping-v1",
  "global": "work/common",
  "null_project": "personal/common",
  "projects": {"Example": "work/common", "Excluded": "skip"},
  "prefixes": [{"prefix": "Example--worktrees-", "target": "work/common"}]
}
```

Exact projects win over the longest matching prefix. Unmapped projects stop the run
before writes. Task summaries without a project use `null_project`. Prefer `project`
as a filter within `common`; create a separate scope only for a distinct reader set.
Every non-skip target must already exist and be in the import agent's write grant.

On the authority Mac, initialize a dedicated broker home with the normal OS key store,
save its recovery kit, add any mapped scopes, and grant a dedicated importer
`--allow-import` with all mapped write scopes. Grant a separate recall agent only the
necessary read access and ordinary minimum write scope required by the grant CLI;
never give it `--allow-import`. Open the `first_access` presence window before each
agent's first request. Start `broker run --home HOME` without `--sync-interval`.
Use the CLI's existing presence, scope and grant commands described above.

```sh
python -m agentmesh_log.cli migrate plan --db DB --mapping MAPPING
python -m agentmesh_log.cli migrate run --db DB --mapping MAPPING --agent-key IMPORT_KEY
python -m agentmesh_log.cli migrate report --db DB --mapping MAPPING --agent-key IMPORT_KEY --out NEW_REPORT
python -m agentmesh_log.cli migrate catch-up --db DB --mapping MAPPING --agent-key IMPORT_KEY
```

A second run is unchanged; changed rows revise, resolved/deleted/skipped rows retire,
and remaps retire the old scope's chain before creating the new root. Runs resume from
the encrypted replica after interruption, including interruption between the two remap
signatures. Reports contain source IDs, chain IDs, scope names, status and reasons, never
content or hashes. Report output is exclusive-create, mode 0600, outside the replica.
`migrate rows` accepts up to 500 repeated `--ref memory_items:ID` or
`--ref memory_summaries:ID` arguments.

Use exactly one authority device. Two authorities can duplicate roots before seeing
each other's events. The migrator refuses a broker that already holds foreign-device
migration events unless the owner explicitly supplies `--take-over`. Peer provenance
uses **device-level trust**: a paired same-account device with a valid, non-revoked
chain at event time is trusted. Local provenance requires an agent that currently has
or previously held `allow_import`; the private state retains those agent IDs even after
revocation. This permits historical recall after restart without distributing local
grants. **A compromised paired device can forge or retire migrated rows in scopes whose
keys it holds**, just as it can forge ordinary events there. No replicated import-grant
record is introduced; event time uses the signed ULID, not the source row timestamp,
and has the existing historical timestamp limitations described above.

Create `<DB>.profile-log.json` (or set `AGENTMESH_PROFILE_LOG_BRIDGE`) as an owner-only,
non-symlink regular file, mode 0600 on POSIX. All paths must be absolute:

```json
{
  "format": "agentmesh-profile-log-bridge-v1",
  "enabled": true,
  "recall": true,
  "dual_write": true,
  "authority": true,
  "cli_argv": ["/absolute/venv/bin/python", "-m", "agentmesh_log.cli"],
  "agent_key": "/absolute/home/agents/recall.key",
  "import_agent_key": "/absolute/home/agents/import.key",
  "mapping": "/absolute/mapping.json",
  "profile": "work",
  "timeout_seconds": 5
}
```

The prepared interpreter must resolve `agentmesh_log`; an absolute Python plus an
absolute CLI script is also accepted. Invalid config disables the bridge and emits one
sanitized warning. Digest and summary commits spawn a detached `migrate rows` after
commit; missed hooks are repaired by catch-up. Hybrid recall merges broker durable and
summary candidates with local observations and evidence, preserving the SQLite row
shape and adding `backend: "broker"`. Failure returns the full SQLite answer with
`backend: "sqlite-fallback"` and one warning. `recall_memory.py --backend sqlite` forces
the original path. No config means unchanged SQL and output.

Generate services with the memory-sync interpreter; generation never registers them:

```sh
python memory-sync/platform_service.py generate --unit broker --platform macos --database DB --home HOME --output NEW_BROKER_DIR
python memory-sync/platform_service.py generate --unit catch-up --platform macos --database DB --home HOME --output NEW_CATCHUP_DIR --interval 300
```

Use absolute paths. The new output directories must not exist. Outputs are 0600
launchd plists, systemd service/timer files (`--platform linux`), or Task Scheduler XML
(`--platform windows`). Review the argv, then load/register them yourself. Broker units
have no automatic remote sync. Existing sync service generation is unchanged.

Before rollout, the reviewer runs parity on an owner-approved snapshot using the
profile-log interpreter:

```sh
python memory-sync/profile_log_parity.py --db SNAPSHOT --mapping MAPPING --sample 200 --out NEW_PARITY_REPORT
```

Optional `--queries FILE` reads UTF-8 queries, one per line. The tool makes a backup-API
snapshot in a disposable 0700 directory, with 0600 files, then creates a disposable
file-keystore broker. The snapshot and its SQLite sidecars are the sole plaintext
exception approved by the owner on **2026-10-20**. Everything is removed on success or
error. The report contains metrics, integer IDs and per-run HMACs, never query/content
or project names. Required thresholds: overlap at 12 ≥ 0.95 and top-five hit ≥ 0.98.
`broker init --file-keystore` is for disposable homes only: system temporary directories,
or an explicitly set `AGENTMESH_ALLOW_FILE_KEYSTORE=1`; use the OS key store for rollout.

After the Windows native key-store smoke and pairing, configure Windows for recall
with `authority: false` and `dual_write: false`. It reads paired events; hooks and
catch-up do nothing there. Existing packet sync brings other devices' SQLite rows to
the Mac authority for catch-up. Raw transcript ingest remains unchanged everywhere.

Rollback: set `enabled: false` or delete the bridge config. This immediately restores
SQLite recall and stops notifications; disable the generated units too. Before any
`broker sync`, remove the dedicated migration home when ready to discard its local
replica. After synchronization, use `profile delete` with its recovery/presence procedure
and the documented old-share/offline-backup limitations. Disabling the bridge alone
does not delete encrypted events or revoke already distributed keys.

## Settings and admin UI

Settings are per-device desired state. The broker authorizes requests only from its
protected grants; editing a settings file never changes a running agent's access.
`apply` prints the complete proposed change before requesting console presence for
widening. The prompt is one-shot, even when an agent presence window is open.
Widening means a new scope or agent, an added read/write scope or flag, any
migration mapping change (it can move rows into a scope other agents read), and any
bridge change other than switching `enabled`, `recall`, `dual_write` or `authority`
off (a new `cli_argv`, database, profile, agent or timeout changes which program runs
with which agent key). Narrowing and removal require no prompt. A mixed change is approved once and applied
together. Initialization and recovery-kit handling still use the existing CLI.

The root is `$AGENTMESH_HOME`, defaulting to `~/.agentmesh` (Windows:
`%USERPROFILE%\.agentmesh`). Its layout is:

```text
settings.json                  user declarations
settings.local.json            device overrides
profile-log/                   initialized broker home
agents/<name>.key              agent signing files created by apply
migration-mapping.json         rendered migration routing
apply.jsonl                    names, change kinds and outcomes only
```

Use a POSIX 0700 root and 0600 regular files owned by the current user. Symlinks,
wrong owners, unsafe modes, unknown or duplicate JSON keys, and key material are
refused with `invalid_settings`. Files are UTF-8. Windows owner-only ACL validation
is outside this implementation; the owner must protect the directory with their
account's ACL. Settings never contain signing keys, recovery shares or row content.
No settings or agent keys are synced. This feature does not move SQLite, packet
identities, services or any existing memory-sync runtime.

### Settings schema v1

Every top-level section is optional. Omitting a section leaves that area unchanged.
Within a declared `agents` object, missing managed names mean removal; renaming is
shown as removal plus creation. Explicit profile/scope omission is reported as an
unsupported deletion. A profile's root is implicit in its `scopes` object. Scope
paths there may be relative to the profile (`common`, `client-a/notes`) or fully
qualified (`work/common`); agent and routing scope references are fully qualified.
Scopes may also reference profile roots (`work`). Existing profiles must already
have been initialized. Creating profiles, deleting profiles/scopes and changing a
scope's label are unsupported by apply.

```json
{
  "$schema": "agentmesh-settings-v1",
  "profiles": {
    "work": {"scopes": {"common": {"label": "general"}, "client-a": {"label": "internal"}}}
  },
  "agents": {
    "recall": {"read": ["work/common"], "write": [], "default_write": null,
               "allow_secret": false, "allow_cross_profile": false, "allow_import": false},
    "importer": {"read": [], "write": ["work/common"], "default_write": null,
                 "allow_secret": false, "allow_cross_profile": false, "allow_import": true}
  },
  "migration": {
    "global": "work/common", "null_project": "work/common",
    "projects": {"Example": "work/common"},
    "prefixes": [{"prefix": "Example--worktrees-", "target": "work/common"}]
  },
  "routing": {"allowed_scopes": ["work/common", "work/client-a"]},
  "bridge": {"enabled": false, "recall": true, "dual_write": true, "profile": "work",
             "timeout_seconds": 10, "recall_agent": "recall", "import_agent": "importer"}
}
```

Labels are `general`, `internal` or `secret`. Agent names match `[a-z0-9-]{1,32}`.
Agent read/write lists default to empty, flags to false, and `default_write` to null.
A non-null default must belong to the write set. Read-only agents are supported.
With no default write, the default recall profile is the first profile represented
by lexically sorted grant scope names, or the first initialized profile for an empty
grant. Broadening the read/write sets or enabling any grant flag requires presence.
Key files are exclusively created; an existing file is an error and is never replaced.
Revocation retains historical import-agent provenance.

Migration targets are existing scopes (including profile roots) or `skip`. The
effective migration section needs `global`, `null_project`, `projects` and `prefixes`;
no destination is inferred. Exact project routes take precedence over longest-prefix
routes. Unmapped projects retain the existing migration refusal behavior.

Only `settings.local.json` may add these bridge fields:

```json
{
  "bridge": {
    "database": "/absolute/path/to/database.db",
    "authority": true,
    "cli_argv": ["/absolute/venv/bin/python", "-m", "agentmesh_log.cli"]
  }
}
```

The database path is absolute. CLI argv accepts either an absolute interpreter plus
`-m agentmesh_log.cli`, or an absolute interpreter plus an absolute CLI script.
Timeouts are 1–30 seconds. A configured bridge needs database, CLI argv, profile and
both named agents. Enabled bridges also need a migration mapping. Its default flags
are enabled=false, recall=true, dual_write=true, authority=false, timeout=10.
Apply renders the bridge's exact existing schema to `<database>.profile-log.json`,
with key paths pointing to `agents/<name>.key`; it never opens the database to render
this file. Disabling preserves the file and renders `enabled: false`. Partial bridge
updates retain previously applied bridge fields. A bare initial `enabled: false`
needs no database or output file.

```sh
python -m agentmesh_log.cli settings validate
python -m agentmesh_log.cli settings show
python -m agentmesh_log.cli apply --dry-run
python -m agentmesh_log.cli apply
```

`settings show` prints the merged user and local configuration with `$schema`, never
key contents. Objects merge per key; lists replace. Precedence is user local over
user. Project layers can affect only project routing, as described below.

Dry runs perform **zero writes**, including no audit or presence-log writes. Every
non-dry-run apply with a safely writable audit destination appends exactly one
0600 `apply.jsonl` record, including declines and no-ops. A declined widening leaves
the protected state and all agent key files unchanged; presence itself also logs its
outcome. A second unchanged apply prints `no changes`, does not prompt and leaves the
protected state byte-identical. Unsafe audit paths are refused, never repaired.
Unsupported changes stop the entire apply with exit 1 unless
`--allow-unsupported-report` is supplied; that option reports them and applies only
the supported remainder. Ordinary I/O failures roll back state, key files, local
keyring publications and rendered outputs. This is not a cross-file, power-loss
atomicity guarantee; after a crash, inspect files and rerun a dry run before retrying.

### Trusted project routing

A repository may contain `.agentmesh/settings.json` and
`.agentmesh/settings.local.json`. Both accept only this routing shape:

```json
{"project": {"name": "Example", "scope": "work/client-a"}}
```

The project local file overrides the shared file per key. The resulting trusted
project route overrides user-local and user migration mappings for that project
name only. Neither project file can contain grants, agents, bridge options or
profiles. Project files are checked out by git (usually 0755/0644), so they must be
regular, non-symlink, owned by you and not group- or world-writable; trust pins their
exact bytes instead of a 0600 mode. Review them, then explicitly trust the folder in a terminal:

```sh
python -m agentmesh_log.cli project trust /absolute/repository
```

Trust uses one-shot presence and pins the canonical folder path and a SHA-256 hash
of both files, with length-delimited file boundaries. Changed bytes require trusting
again. Only existing scopes in user-level `routing.allowed_scopes` are accepted.
Untrusted, changed, invalid or disallowed routing is ignored with a stderr warning;
it does not stop migration. Apply includes routes from remembered trusted folders;
`--project-dir DIR` can additionally name folders to evaluate. Multiple folders
are processed in canonical-path order, making duplicate project-name routing
predictable; prefer a unique project name per trusted repository.

All five migration operations accept `--settings` instead of `--mapping`, and reject
using both. Settings mode loads this device's initialized broker state for trusted
routes. The original `--mapping` form remains available, including plan without a
broker. Repeat `--project-dir` as needed:

```sh
python -m agentmesh_log.cli migrate plan --db DB --settings --project-dir REPOSITORY
python -m agentmesh_log.cli migrate run --db DB --settings --agent-key IMPORT_KEY
python -m agentmesh_log.cli migrate rows --db DB --settings --agent-key IMPORT_KEY --ref memory_items:1
python -m agentmesh_log.cli migrate catch-up --db DB --settings --agent-key IMPORT_KEY
python -m agentmesh_log.cli migrate report --db DB --settings --agent-key IMPORT_KEY --out NEW_REPORT
```

### Terminal administration

`python -m agentmesh_log.cli admin` uses curses when available and stdout is a TTY;
otherwise it uses numbered menus. `--menu` always selects menus. Windows installs
`windows-curses==2.4.2`; absence of curses does not prevent menu operation.

The overview shows profiles/scopes, enforced agent grants (opaque IDs for older unmanaged grants), device revocation
status and migration project counts. The grid edits read/write lists and creates
scopes or named agents. Mapping shows current and proposed destinations, with a
retired/re-created/newly-created row preview when the import agent and broker can
provide the ledger. Otherwise it explicitly marks the preview unavailable. Only
names, counts and source references enter the screen model; no content is displayed.
The menu mapping editor also accepts `@global` and `@null` for those explicit routes.

In curses, `1`–`4` select screens, arrows move, space toggles, left/right or `r`/`w`
select a grid permission, `n` opens a new-scope dialog, `a` creates an agent, `s` saves
and `q` exits. On mapping rows, Enter edits a target. On the pairing screen, `i`
enters the request ID, space selects scopes and `p` approves. Pair approval displays
the approver fingerprint and delegates the existing six-digit comparison and scoped
grant flow; empty scope selections are refused. `admin --filesystem PATH` selects
the shared filesystem used by pairing; otherwise pairing uses the local replica.

Edits remain in memory until Save. Save writes only the **user** layer atomically,
then invokes the same apply command in the terminal. Curses temporarily releases
the terminal for presence and pairing prompts. Local overrides remain authoritative;
use `settings show` and the apply diff to check their effect. Saving a mapping renders
the desired routing; use the existing explicit migration/catch-up operations to move
rows. No service is installed and no remote synchronization starts automatically.
