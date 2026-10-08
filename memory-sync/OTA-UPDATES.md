# Signed worker updates (opt-in)

This consumer updates only AgentMesh worker code. It does not reboot the OS,
restart Syncthing, alter other profiles, approve peer keys, change summary
providers, or activate signed sync. No production update is implied by publishing
a feature branch. Native Windows operation and ACL durability require separate
verification on an authorized Windows host.

## One-time local bootstrap

Old Windows workers do not contain an OTA consumer. Delivering a release to
Syncthing cannot upgrade them. On that host, stop **only the AgentMesh worker**
and verify it has stopped before installing the consumer from independently
verified source. Do not run the legacy bootstrap launcher afterward: it uses
its own `app` tree and does not follow the version pointer.

Use the existing installation root, with `data/runtime.json`, databases and
`data/workflow.json`. Signing state, if enabled, must be in `identity` inside
that root. Other layouts require explicit operator migration, not silent copying.
Install the pinned dependency in the interpreter that will run the consumer:

```text
python -m pip install -r VERIFIED-SOURCE/requirements-security.txt
python VERIFIED-SOURCE/ota_update.py bootstrap --local LOCAL-ROOT --source VERIFIED-SOURCE
```

Version 0 is copied from verified source, with the existing database, runtime,
workflow, identity and trust left intact. The legacy `app` is not overwritten.
The fixed consumer lives in `LOCAL-ROOT/consumer`; worker releases never update
that trust base. Consumer changes or additions to its frozen file contract
require another explicit maintenance bootstrap/upgrade, not remote code execution.

## Separate release authority

Generate a release identity offline, outside Git and exchange:

```text
python ota_update.py release-key PRIVATE-RELEASE-DIRECTORY
```

A release identity is **not** a sync peer key. Independently verify its public
key fingerprint and release UUID with the operator. Copy its public record via
an authenticated/offline channel, then authorize that exact record locally:

```text
python LOCAL-ROOT/consumer/ota_update.py trust --local LOCAL-ROOT --public RELEASE-PUBLIC.json --fingerprint FULL-SHA256 --release-id RELEASE-UUID
```

Trust is never learned from an arriving manifest, and peer approval does not
grant release authority. Existing release trust cannot be overwritten by this
command. Revocation/rotation is explicit local maintenance; stop the consumer
and remove/replace authority only after independent operator approval. An
approved publisher can ship executable Python code: this is not a sandbox.
Keep the release private key offline and review the exact source before signing.

## Publish and consume

Build from a reviewed source tree, using an integer version strictly higher
than every version already attempted by any recipient:

```text
python ota_update.py build --source VERIFIED-SOURCE --output PRIVATE-STAGING --private PRIVATE-RELEASE-DIRECTORY/release-private.json --version 1
```

Publish only `worker.zip` and `release.json` to
`EXCHANGE/releases/worker/`, with the manifest last and atomic replacement.
No database, key, identity, runtime config or baseline belongs in this release.
Incomplete delivery fails closed and retries on a later polling cycle.

Replace the local worker launch invocation with:

```text
python LOCAL-ROOT/consumer/ota_update.py run --local LOCAL-ROOT
```

The consumer polls at 60 seconds by default, runs bounded worker subprocess
cycles with the existing workflow and security settings, and selects immutable
version directories through one atomic local state record. The OS and other
services remain running. `--once` performs one consumer pass for local proof.
`apply --local LOCAL-ROOT --release PRIVATE-STAGING` performs an isolated/local
manual update only while no supervisor is running; it also executes the new
worker's first real cycle, not just an import check.

## Safety and recovery

- Both updater and every new worker cycle share a kernel-owned lock beside the
  database. Updates wait up to 120 seconds for an active cycle to drain. A second
  supervisor/manual updater fails without touching live state. Unrelated legacy
  writers do not participate in this lock; stop them before bootstrap and do not
  run them against an OTA-managed installation.
- A domain-separated Ed25519 signature binds product, explicit release identity,
  monotonic version, exact archive digest and all file hashes. The installed
  consumer freezes the path set. No package-selected shell command, entrypoint,
  command-line options, dependency install or service registration is accepted.
- Archive size, entry count, exact names, duplicates, symlinks, file type,
  encryption, per-file size, expansion ratio and total expanded size are bounded
  before extraction. Extraction goes to a new private staging directory, then
  an immutable version directory. Live worker files are never overwritten.
- Before switching, all files under managed `data` and `identity` are backed up.
  Every SQLite file is detected by its header **before** interpreting filenames,
  including databases named `primary-wal` or `primary-shm`, and copied with
  SQLite's backup API, with closed handles. Only sidecars belonging to an
  identified SQLite database are skipped; the configured active DB backup is
  mandatory and integrity-checked before activation. Release trust and
  updater state are included. Backups are local/private and retained. Other
  writers and external data stores are outside this updater's guarantees.
- The durable highest-attempted version is burned immediately after full
  manifest/archive authentication, before staging or mandatory backup can fail.
  Active code stays unchanged during these prerequisites. Failed
  releases cannot be replayed or downgraded; publish a higher version to retry.
  Only rollback to the recorded previous local code is allowed. No older remote
  manifest may select a previous version.
- Health checks use a fixed local command: imports, read-only SQLite integrity,
  foreign keys and node/group presence. Then the worker runs one real cycle.
  Failure restores the previous **code pointer**, never an old database over
  new user data. Recovery retains workflow and key state. This contract requires
  releases to preserve backward-compatible DB schema; destructive migrations
  are not supported. A failing cycle may have committed legitimate writes,
  which remain preserved after code rollback.
- Interruptions before final success leave a durable pending marker. On restart,
  recovery rolls back to recorded previous code, rechecks health and never
  decrements the highest version. Repeated recovery is idempotent. Each worker
  checks its expected version against
  the active pointer **under** the shared lock before writing; a waiting stale
  child cannot run after recovery has changed the pointer. An already-running
  orphan cycle may finish; recovery waits for its lock and preserves its writes.
  Process termination recovery is exercised in isolated macOS fixtures, not by
  physically cutting host power. Staging and
  unused backups can remain after interruption; they are not active. Recovery
  after power loss uses atomic state replacement plus fsync on POSIX; native
  Windows/NTFS power-loss behavior has not yet been verified.
- If previous code is unhealthy too, no worker starts. Diagnostics omit memory,
  keys and raw provider errors. Keep local backups for explicit operator repair.
  There is no automatic restore, trust approval, production cutover or OS reboot.

The signing API and pinned `cryptography==50.0.2` were checked against the
publisher's official Ed25519 documentation and release metadata. Dependency
updates and real Windows dependency/ACL checks remain operator-maintenance work.
