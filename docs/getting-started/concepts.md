# Concepts

Understand how AgentMesh keeps memory encrypted on each device while sharing it between your devices and agents.
Begin with [What is AgentMesh](../introduction) for the scope; no installation is needed yet.

## How AgentMesh works

Memory moves through four parts. All of them run on the device, and only encrypted objects leave it.

1. **Transcripts.** Claude Code, Codex and OMP write each session as JSONL files on the device. AgentMesh reads them
   but never uploads them.
2. **Summary.** A scheduled job summarizes new turns with the Claude Code CLI and writes the resulting memories
   through the broker as the `summarizer` agent. A migration mapping routes each project to a scope. A project
   without a route is held back until you choose one. See [Ingest agent sessions](../use/ingest).
3. **Profile log.** An append-only log of signed, encrypted events. Each device keeps a local encrypted replica and
   synchronizes it with a private bucket in Cloudflare R2 (or another S3-compatible store). The provider sees object
   names, sizes and timing, but no content, scope names or labels.
4. **Broker.** A local service (`org.agentmesh.profile-log.broker` on a Mac) that holds this device's keys, verifies
   every event, keeps per-agent search indexes in memory and answers agents' `recall` and `remember` requests over a
   socket (a named pipe on Windows). When the broker is not running, recall fails with an explicit error; there is no
   other source to fall back to.

## Profiles, scopes and labels

A **profile** (for example `work` or `personal`) is a tree of **scopes** such as `work/common` or `work/client-a`.
Each scope has its own encryption keys, so a device or an agent can read only what it was granted.

Every memory carries a **label**: `general`, `internal` or `secret`. The broker masks secrets it detects
(`[REDACTED:<kind>]`) and raises the label to `secret`; personal data raises it to `internal`. Secret memory is
returned only to agents granted it, and only while a presence window is open.

## Devices

- **The first device** creates the account with `broker init`. It prints a **recovery kit** of three shares, any two
  of which restore the account. Keep them apart, outside the AgentMesh folder.
- **Another device** joins by pairing. It shows a request; a device that already holds the keys approves it after you
  compare a six-digit code on both screens, and grants exactly the scopes you select.
- **Removing** a device revokes its agents and rotates every key it could read. It keeps what it already read, but
  cannot read anything written later.

Device keys are sealed with a key held in the OS key store: the macOS login Keychain, Windows Credential Manager, or
the Secret Service on a Linux desktop.

## Agents and grants

Agents never hold scope keys. Each has a **key file** (`~/.agentmesh/agents/<name>.key`) and a **grant**: which scopes
it may read, which it may write, and flags such as `allow_secret` or `allow_summarize`. The broker checks the grant on
every request. Reads cover the granted scopes and their descendants; writes cover only the scopes named.

You declare agents, scopes and the migration mapping in `~/.agentmesh/settings.json` and run `agentmesh-log apply`.
Apply shows the full change first, and asks you to confirm at the console before anything widens access.

## Presence

Some actions need you at the keyboard: pairing, granting secret access, recovery, an agent's first request, exporting
memory. The CLI asks you to type the action word, or you open a short **presence window** (15 minutes at most) with
`agentmesh-log presence open --class CLASS`. On a Mac the supervisor asks for Touch ID or your Mac password instead.

## The supervisor and the menu bar app

On a Mac and on Windows, the AgentMesh supervisor (`org.agentmesh.control`) runs the broker and the scheduled jobs
(`ingest`, `summary`, `retention`) as one background item, restarts services that exit, and applies maintenance mode
and the summary's daily quota. On a Mac, the [menu bar app](../use/control-app) is its client: it shows each job's
health, asks where a new project's memory should go, and lists the keys and devices this profile holds.

## Team workspaces

A **workspace** is a profile shared with other people's accounts, with signed member roles, shared tasks and optional
review of agent writes. Workspace homes live under `~/.agentmesh/ws/<id>/`, apart from your personal home, and
ingestion never writes into them. Setup is an owner procedure; see the
[workspace reference](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#workspace-registry-team-workspace-w1).

## What is retired

Earlier versions kept a SQLite staging database on each device, imported it into the profile log with a catch-up job,
and exchanged packets directly between devices through Syncthing. Packet sync was turned off in Phase 7a, and SQLite
retirement R5b removed the database, the packet code and the installers built around them (`setup_macos.sh`,
`bootstrap_windows.py`, `install_agent_rules.py` and `agentmesh.py`) on 2026-11-09. Memory the old importer wrote is
still in the profile log and still verifies.

Next, prepare the [prerequisites](./prerequisites).
