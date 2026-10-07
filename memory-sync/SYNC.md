# SQLite immutable exchange sync

`memory_sync.py` implements two-way, offline-safe row exchange for the copied
SQLite backend. Standard library only. PostgreSQL remains the production source:
this module neither connects to nor writes PostgreSQL. It is **not** a port of
reasoning/digest jobs, an installer, or a UI. Deployment, scheduling, real-data
bootstrap verification and Windows-host testing belong to the parent deployment.

## Bootstrap and run

Keep each writable SQLite database **outside** the Syncthing folder. Create both
from the **same consistent, uninitialized baseline**, preserving all original
primary keys and values. Use SQLite's backup API rather than copying an open WAL
database's main file. Initialize each clone separately; do not clone a database
already initialized as the other node.

```sh
python memory_sync.py init /local/mac.db --node mac --group 00000000-0000-4000-8000-000000000001
python memory_sync.py init C:/local/windows.db --node windows --group 00000000-0000-4000-8000-000000000001
python memory_sync.py once /local/mac.db /paired/exchange
python memory_sync.py watch /local/mac.db /paired/exchange --interval 60
python memory_sync.py status /local/mac.db
```

All commands print JSON. `watch` prints one JSON object per iteration, continues
on folder/database availability errors, and exits cleanly on Ctrl-C. `init`,
`once`, and `status` return exit status 1 for unrecoverable invocation/database
errors. Quarantined packets are reported in `once` JSON, not converted into a
nonzero exit status. `--interval` accepts a positive number of seconds.
The SQLite database must already exist with the sibling backend's schema.
Initialization is idempotent for the same node/group; changing either is rejected.
One logical writer per identity `mac`, `windows` and `linux` is supported. Do not reuse a node identity on another active device.

Publication creates `EXCHANGE/changes/NODE/UUID.json`. Transport only this folder,
not database files, WALs or local sync state. Temporary publication files have a
`.tmp` suffix and are ignored by receivers. The exchange path should point at the
intended mounted/paired location; this code cannot distinguish a disappeared
mount from a new directory and will create missing directories when permitted.

## Python API

Each `db` argument accepts a filesystem path. Functions open and close their own
SQLite connections, except `allocate_id`.

| Function | Return / behavior |
| --- | --- |
| `initialize(db, node, group_id)` | `{"node": ..., "group_id": ...}`; initialize baseline shadow, revisions, counters, guards and local ledgers atomically |
| `capture(db)` | `{"changes": N, "packets": 0 or 1}`; full eight-table diff, durable shadow/outbox commit, no folder required |
| `publish(db, exchange)` | `{"published": N, "unavailable": bool}`; optional `error`; recover unpublished committed packets, fsync temp file, atomic rename, then mark published |
| `receive(db, exchange)` | `{"applied": N, "pending": N, "conflict": N, "invalid": N, "unavailable": bool}`; capture local edits before importing, retry deferred dependencies, quarantine failures |
| `cycle(db, exchange)` | `{"capture": capture_result, "publish": publish_result, "receive": receive_result}` |
| `allocate_id(connection, table)` | Next reserved integer ID; caller must have an active transaction, normally `BEGIN IMMEDIATE`; reservation commits/rolls back with caller ingestion |
| `status(db)` | `node`, `group_id`, unpublished `outbox` count, `received` packet count, and `pending`/`conflict`/`invalid` diagnostic counts |

`applied` counts packets committed during that invocation. Diagnostic counts are
current stored **file-path counts**, not row counts or unique packet counts;
duplicated failing files can therefore produce multiple diagnostics. Receipts
are deduplicated by packet UUID. Status does not scan for newly arrived files;
run `receive`/`once` first. Sent outbox records are retained even though `outbox`
reports only unpublished records.

Sibling ingestion must use explicit IDs within its existing write transaction:

```python
from memory_sync import allocate_id

connection.execute('BEGIN IMMEDIATE')
ident = allocate_id(connection, 'memory_items')
connection.execute(
    'INSERT INTO memory_items(id,kind,scope,content) VALUES(?,?,?,?)',
    (ident, 'fact', 'global', 'content'),
)
connection.commit()
```

Allocation supports `source_sessions`, `observation_events`, `ingestion_errors`,
`memory_items`, and `memory_summaries`, not composite/text-key tables. It uses a
durable per-table counter and considers existing IDs **only within the local
range**, never resetting or trusting `sqlite_sequence`:

- Legacy PostgreSQL IDs: positive integers below `2**40`.
- Windows local IDs: `[2**40, 2**41)`.
- Mac SQLite local IDs: `[2**41, 3 * 2**40)`.
- Linux SQLite local IDs: `[3 * 2**40, 4 * 2**40)`.

Insert guards reject implicit/default and out-of-local-range IDs. Mac additionally
allows **explicit** new legacy-range IDs for the parent's read-only PostgreSQL
mirror. Such mirroring must preserve source IDs and never write PostgreSQL.
Imported peer IDs bypass the local insert guard only inside the packet
transaction. Updates to primary keys are rejected; represent a key change as an
explicit delete/insert instead. Existing baseline IDs remain unchanged.

## Tables and protocol

All eight tables are included, without filtering machine-local cursor/path data:

| Table | Key |
| --- | --- |
| ingestion_cursors | source_path |
| ingestion_errors | id |
| observation_events | id |
| memory_summaries | id |
| memory_items | id |
| source_sessions | id |
| memory_sources | memory_id, event_id |
| summary_state | consumer |

The exact packet envelope is:

```json
{
  "format": "omp-memory-changes-v1",
  "group": "<canonical group UUID>",
  "node": "mac",
  "uuid": "<canonical packet UUID>",
  "checksum": "<SHA-256 of canonical JSON body>",
  "body": [
    {
      "table": "memory_items",
      "key": [1],
      "revision": "<new UUID>",
      "parent": "baseline:<content SHA-256 or prior UUID>",
      "row": {"<all schema columns>": "<typed values>"}
    }
  ]
}
```

For a new key `parent` is null; for deletion `row` is null (a retained tombstone).
Baseline revisions are `baseline:` plus SHA-256 of canonical
`[table, key-array, typed-row]`. Metadata is decoded JSON, not double-encoded SQL
text. Canonical JSON uses UTF-8, sorted object keys, compact separators, and
rejects non-finite numbers. Packet validation rejects duplicate JSON object keys,
unknown envelope/change/row columns, invalid key shapes/types, invalid UUIDs,
foreign group/format/node, checksums, and new IDs outside the sender range.
SQLite additionally enforces its constraints and all foreign keys.

## Safety and conflicts

- Capture uses `BEGIN IMMEDIATE`. The full diff, new revisions, shadow updates
  and complete packet are committed to the local outbox before publication.
- File publication is fsynced and atomic; POSIX additionally fsyncs the directory.
  A crash after the DB commit or after rename but before the published flag is
  recovered from the durable outbox. An existing identical file is accepted;
  a different file at that immutable name raises an error rather than replacing it.
- Capture happens before incoming apply, and again under the packet write lock,
  so an offline or intervening local edit participates in causality checks.
- One packet is one transaction, including row changes, shadow revisions and
  receipt. Child links are deleted before event RESTRICT deletions; deferred
  foreign keys permit self-reference changes within the packet. Off-packet
  cascades that would erase another peer's concurrent rows are rejected.
- Known divergent ancestry, baseline mismatch, constraint collisions and
  concurrent edits roll back the **whole packet**. No silent last-writer overwrite.
- Unknown UUID ancestry or not-yet-arrived FK targets remain `pending`. Receivers
  retry pending packets while a pass makes progress, including reversed packet
  order. A target already known/deleted is a conflict, not an assumed future row.
- Invalid/conflicting/pending diagnostics are committed separately in
  `_sync_diagnostics(path, kind, message)`. Failed packets receive no receipt.
  Unrelated packets continue. A later successful retry clears that file's diagnostic.
- Imported revisions directly update the shadow, preventing echo. Tombstones,
  revision history, receipts and sent outbox records are retained indefinitely.
- The engine never deletes sent or received `.json` files. Only its own temporary
  publication files are cleaned up. A missing already-marked-published file is
  not automatically reconstructed; preserve the exchange or restore the retained
  outbox deliberately during operator-led recovery.

Inspect diagnostics with a read-only SQLite tool:

```sql
SELECT path, kind, message FROM _sync_diagnostics ORDER BY path;
```

There is no automatic conflict-resolution or garbage-collection command. Keep
backups before any operator-led reconciliation/rebootstrap. Natural-key collisions
between independently inserted rows are quarantined; IDs are not remapped and
rows are not deduplicated by content. Differing bootstrap data is not merged.
Replication of shared cursor/consumer keys can deliberately produce conflicts
when both machines ingest or summarize the same source independently.

## Boundaries and verification

SHA-256 detects accidental body corruption; it is **not authentication**. Packets
contain real memory data in plaintext. Trust and restrict access to the paired
Syncthing exchange; transport security does not make an untrusted folder safe.
No SQL triggers are installed in PostgreSQL and no live PostgreSQL data/config is
changed. No clock-based ordering, network server, external dependencies, installer,
UI, Windows service or reason/digest integration is included.

Full capture is an eight-table scan and keeps decoded rows in memory. Each new
packet also verifies a complete resulting row view to detect unannounced cascades.
Retained, already-received packets use the receipt fast path without a full scan
per file. Concurrent large ingestion may wait for these SQLite write transactions.
Changes made and undone entirely between scans have no net row diff and are not
an audit trail. Exchange files, outbox and history grow without pruning.

Run the real-SQLite test suite with the deployment's Python:

```sh
python -m pytest test_memory_sync.py -q
```

Tests cover all eight tables between clones, both-direction inserts/updates,
links/deletes/cascades, typed metadata, no echo, repeated initialization and identity
rejection, explicit allocation after high peer IDs, crash/publication recovery,
reversed ancestry and FK dependency chains, duplicate files, offline edits,
whole-packet conflicts, unrelated continuation, invalid/partial/foreign packets,
unique-key conflicts, retained tombstones, and all four CLI commands including a
live watch subprocess. Tests use disposable databases under Hermes scratch; they
do not touch production databases or the actual Syncthing folder. Real-data clone
and actual Windows/deployment verification must be reported separately.
