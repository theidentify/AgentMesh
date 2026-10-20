# Encrypted profile log

Standalone Python 3.11 library and Phase 3 local memory broker for the encrypted profile log design.
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
pytest 8.4.2 and moto 5.2.4. Filesystem storage uses only the standard library;
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
head tracking. Enrollment, age, OS secure storage, recovery, Shamir, migration,
compaction, explicit conflict resolution and user-presence checks remain deferred.
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
remain usable independently. Caller rewiring, enrollment, auto-start, keychain
storage, biometric presence and migration are not implemented.

Run these commands with the prepared interpreter from `profile-log/` (replace
`H` with a new disposable, private directory):

```sh
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker init --home H --profile work --profile personal
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker scope add --home H work/client-a
~/.cache/agentmesh-logvenv/bin/python -m agentmesh_log.cli broker grant --home H --name assistant --read work/client-a --write work/client-a
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

**Phase 3 security limitation:** the 0600 `keystore.json` contains account/device
private keys, all scope epochs, the index master key and audit key. Malicious code
running as the same OS user can read this file or another agent's key file directly.
This phase does not protect against that attacker. A separate broker OS account
or OS keychain ACLs are future work. Account-root storage on a daily device is
also temporary. Phase 4 moves key storage to the OS keychain and adds user-presence
checks for first access, secret reads and cross-profile recall.

POSIX broker directories are 0700, files and the Unix socket are 0600, unsafe roots
and symlinks are refused, and peer UID must match. Windows uses named pipes with
byte-only messages and the default pipe DACL; local administrators can still
connect, and certificate authentication remains required. Windows file access
follows host ACLs rather than POSIX mode bits. Socket tests use short temporary
paths and fail explicitly if the sandbox denies binding; they are never skipped
for that reason. The reviewer must run those tests outside the sandbox and on
Windows/Linux CI.
