<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/agentmesh-logo-dark.svg">
  <img src="assets/agentmesh-logo.svg" alt="AgentMesh" width="384" height="80">
</picture>

# AgentMesh

Local-first, end-to-end encrypted memory and coordination for AI agents (Claude, Codex, Hermes and others) across
your devices, synchronized through an R2 bucket that only ever sees ciphertext.

**User guide:** https://theidentify.github.io/AgentMesh/

## Status

AgentMesh is in active development. Each device runs a broker (`profile-log/`) that holds the profile log: signed,
encrypted events for memory, summaries, tasks and team workspaces. Agents reach it through scoped agent keys.
Brokers replicate through R2. The transcript summary (`profile-log/agentmesh_log/ingest/`) reads your own Claude and
Codex transcripts and writes durable memory through the `summarizer` agent. Ingestion is personal-only: it refuses a
team workspace home and never writes transcripts into workspace scopes.

The original SQLite staging database and its Syncthing packet exchange are retired. The last of their code was
removed in SQLite retirement R5b (2026-11-09). See [the profile-log README](profile-log/README.md#sqlite-retirement).

## Profile-owned memory prototype

`profile-memory/` is a separate opt-in experiment with profile-owned knowledge,
immutable revisions, permission-aware retention and evidence, keyword/semantic/
hybrid retrieval and authenticated scoped semantic exchange. It does not activate
production services, migrate existing databases or change agent profiles.
See [prototype setup, CLI, demo and trust boundaries](profile-memory/README.md).

## Setup

Install and operate the broker as described in the [profile-log README](profile-log/README.md). On a Mac the
[control plane](#control-plane-macos) runs the summary jobs; on Windows the same supervisor runs them from a logon
task. Each job runs through `python -m agentmesh_log.control.jobrun`, which applies maintenance and quota holds and
sends failure alerts. Devices set up before memory-sync's retirement migrate with
[the memory-sync migration runbook](docs/install/migrate-memory-sync.md).

## AgentMesh Insight

`insight/` is the local, read-only status workspace. It has Overview, Metrics, Agents, Pipeline, Sync, Profile log and
Storage views, built from the profile-log status snapshot, the summary jobs' own status files and this device's
disk. It shows counts, states and times, never memory content, and it changes nothing.

```sh
python3 -u insight/server.py --port 0
```

The server prints its loopback URL. See
[Insight setup, privacy boundaries and verification](insight/README.md).

## Control plane (macOS)

On the Mac, the AgentMesh supervisor (`org.agentmesh.control`, in `profile-log/agentmesh_log/control/`) runs and
watches the background units. Ingest and summary run as its native, scheduled jobs; the broker and other
services stay under launchd and are observed. Requests arrive on a local socket and are audited:

- **Tier 0** reads status, the audit log and the key and device inventory (`keys.inventory`), which never opens a key file.
- **Tier 1** starts, stops, restarts and resets units and toggles maintenance mode, within rate limits.
- **Tier 2** moves a unit between launchd and the supervisor, or routes a new project to a scope. The supervisor asks
  for Touch ID or the Mac password itself; no client can assert it.

`agentmesh-log control` is the terminal client; its `panic-revert` restores the original launchd units without the
supervisor. AgentMesh Control, a menu bar app in `control-app/`, shows unit health, asks where a new
project's memory should go, notifies on failures, and lists keys, devices, passkeys, recovery and backups. Approvals
through Discord or a passkey and device and grant operations are not built yet. See the
[menu bar app](control-app/README.md) and the
[control plane design](docs/superpowers/specs/2026-10-23-local-control-plane-design.md).

## Architecture

Each device's broker keeps an encrypted replica of the profile log and syncs it through R2. Agents recall and write
through the local broker, which enforces their grants (work and personal profiles, scopes, labels). If the broker is
down, the agent gets an explicit error. The broker summary (`profile-log/agentmesh_log/ingest/digest.py`)
reads transcripts directly, keeps its cursors in `jobs/`, and writes summaries through the `summarizer` agent. On macOS the
[control plane](#control-plane-macos) schedules ingest and summary.

## Privacy

This repository contains code, tests, and documentation only. Real memories,
transcripts, databases, bootstrap snapshots, change packets, logs, credentials,
and device-specific deployment files must remain outside Git.

## Brand artwork

The logo, peer-network icon, and terminal mark are original vector geometry and
custom lettering; no stock image, external font, icon pack, or vendor logo is
included. See [artwork provenance and license boundary](assets/PROVENANCE.md).
This does not relicense existing code or claim trademark clearance.

## Development

`profile-log/` holds the broker, its CLI, the control plane and the transcript summary (`agentmesh_log/ingest/`, standard
library only). Tests require the pinned `profile-log/requirements-dev.txt`.

```sh
python -m pytest -q profile-log/tests
```

`memory-sync/` holds only compatibility shims until every device has migrated; see its README.
