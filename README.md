# AgentMesh

Local-first memory sync and cross-platform setup scripts for AI agents,
connecting Hermes, OMP, Codex, and Claude across devices via peer-to-peer
file exchange.

## Status

AgentMesh is in active development. The initial SQLite storage prototype has
been exercised against a read-only copy of an existing memory database.
Two-way packet exchange, the staged PostgreSQL mirror, and installation
packaging are being integrated. It is **not yet a complete one-click installer
or a production replacement** for an existing memory workflow.

## Intended architecture

Each device owns its local SQLite database. Immutable change packets travel
through a dedicated Syncthing exchange folder. The receiving script validates
and imports packets, preserving receipts and tombstones and refusing conflicts
rather than silently overwriting independent edits.

Do not synchronize live SQLite database, WAL, or SHM files as a substitute for
record-level sync. Both peers need an overlapping online period for direct
Syncthing exchange.

## Privacy

This repository contains code, tests, and documentation only. Real memories,
transcripts, databases, bootstrap snapshots, change packets, logs, credentials,
and device-specific deployment files must remain outside Git.

The private bootstrap payload belongs only in the paired-device exchange, not
in a GitHub commit, release asset, or issue attachment.

## Development layout

- `memory-sync/`: SQLite adapter, packet exchange, read-only PostgreSQL mirror,
  pure transcript parsers, tests, and Windows bootstrap helpers.
- Hermes integration and the final installer are not yet published here.

Python 3.10+ with SQLite FTS5 and JSON support is required. The SQLite and packet
exchange runtime uses the standard library. The optional PostgreSQL mirror
requires `psycopg`; tests require `pytest`.
