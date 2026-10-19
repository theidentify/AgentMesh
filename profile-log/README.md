# Encrypted profile log

Standalone Python 3.11 library for phases 1–2 of the encrypted profile log design.
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

This is a library, not a broker or sync service. It does not implement enrollment,
revocation, grants, keyring persistence, age, OS secure storage, recovery, Shamir,
credential redaction, PII detection, search, migration, compaction or conflict resolution.
Signatures establish authorship, not scope-write authorization. A later broker must apply
access policy, redaction and user-presence rules. There is no automatic promotion to
`common` and no implicit cross-profile operation.

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
