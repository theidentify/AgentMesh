<picture>
  <source media="(prefers-color-scheme: dark)" srcset="memory-sync/assets/agentmesh-logo-dark.svg">
  <img src="memory-sync/assets/agentmesh-logo.svg" alt="AgentMesh" width="384" height="80">
</picture>

# AgentMesh

Local-first memory sync and cross-platform setup scripts for AI agents,
connecting Hermes, OMP, Codex, and Claude across devices via peer-to-peer
file exchange.

## Status

AgentMesh is in active development. The Python/SQLite sync engine, private
bootstrap packaging, and setup entry points for macOS, Linux and Windows are
implemented. Three independent SQLite peers have been exercised against a
real read-only memory snapshot, with all eight memory tables compared.

This is **not a production replacement** for the existing PostgreSQL memory
workflow and does not install every agent application. Actual Windows-host
and Linux-host installation/service verification remain outstanding. The
staged PostgreSQL mirror is read-only; peer edits do not write back to PostgreSQL.
Reasoning, digest jobs and production retrieval callers have not been migrated.

## Setup

Install Python 3.10+ with SQLite FTS5/JSON support and Syncthing separately,
pair devices and accept a private exchange folder. The wrappers check these
prerequisites but do not run OS package managers or register services.
A bootstrap archive and its checksum manifest must be supplied through that
private exchange; a Git checkout intentionally does not include memories.

| Platform | Entry point supplied in the exchange |
| --- | --- |
| macOS | `sh setup_macos.sh "/absolute/path/to/exchange"` |
| Linux | `sh setup_linux.sh "/absolute/path/to/exchange"` |
| Windows | Double-click `START-WINDOWS.cmd` |

The installer keeps local databases outside the Syncthing folder, verifies
bootstrap checksums, preserves existing initialized databases, and starts a
foreground sync worker. One logical node per `mac`, `windows` and `linux` is
supported; never reuse an identity on another active device.

See [macOS/Linux setup and optional user services](memory-sync/README-SETUP.md)
and [Windows setup](memory-sync/README-WINDOWS.md). Background operation is
optional and separate from installation: generate a macOS LaunchAgent or Linux
systemd user unit with `memory-sync/platform_service.py`, inspect it, then
register it using the documented OS commands.

## AgentMesh Insight

`insight/` is the integrated local, read-only workspace for memory and operations.
It includes Overview, Metrics, Memory Browser, Agents, Pipeline (ingest and
summary), and Sync views, with explicit PostgreSQL-authority versus SQLite-staging
connections. It does not change memory, cron jobs, provider configuration or sync
algorithms, and it does not infer agent or transport liveness from file existence.

```sh
python3 -u insight/server.py --port 0
```

The server prints its loopback URL. Optional runtime paths and a PostgreSQL DSN
are configured outside Git; absent sources are shown as unavailable. See
[Insight setup, privacy boundaries and verification](insight/README.md).

## Architecture

Each device owns its local SQLite database. Immutable change packets travel
through a dedicated Syncthing exchange folder. The receiving script validates
and imports packets, preserving receipts and tombstones and refusing conflicts
rather than silently overwriting independent edits. Local integer-ID ranges
are separated for the three node identities.

Do not synchronize live SQLite database, WAL, or SHM files as a substitute for
record-level sync. Direct Syncthing exchange requires overlapping online time.
See [sync APIs, CLI, conflict handling and limitations](memory-sync/SYNC.md).

## Private bootstrap construction

On a trusted source machine with an existing compatible JSONL snapshot:

```sh
python memory-sync/build_package.py --snapshot /private/baseline.jsonl \
  --exchange /private/syncthing-exchange --group "$SYNC_GROUP_UUID"
```

The exchange must already contain its Syncthing `.stfolder` marker. Keep the
resulting ZIP, manifest, group configuration and change packets private; do
not publish them as GitHub release assets.

## Privacy

This repository contains code, tests, and documentation only. Real memories,
transcripts, databases, bootstrap snapshots, change packets, logs, credentials,
and device-specific deployment files must remain outside Git.

## Brand artwork

The logo, peer-network icon, and terminal mark are original vector geometry and
custom lettering; no stock image, external font, icon pack, or vendor logo is
included. See [artwork provenance and license boundary](memory-sync/assets/PROVENANCE.md).
This does not relicense existing code or claim trademark clearance.

## Development

`memory-sync/` contains the SQLite adapter, packet exchange, read-only
PostgreSQL mirror, pure transcript parsers, setup scripts and tests.
The SQLite and packet-exchange runtime uses the standard library. The optional
PostgreSQL mirror requires `psycopg`; tests require `pytest`.

```sh
python -m pytest -q memory-sync
```
