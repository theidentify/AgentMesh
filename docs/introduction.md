# What is AgentMesh

AgentMesh makes past work available across agents and devices. Your agent sessions become memory that is encrypted on
the device, shared through a private object store, and recalled by any agent you have granted access, on any of your
devices. This page explains what AgentMesh provides and where to start. No installation is needed yet.

## Keep context across agents and devices

Agent sessions are stored separately, so a decision recorded by one agent on one device is missing from the next
conversation elsewhere. AgentMesh closes that gap in three steps:

1. **Ingest.** Scheduled jobs read the transcripts that Claude Code, Codex and OMP write on this device, summarize new
   turns into memories (decisions, constraints, procedures, preferences, open loops and facts), and write them through
   the local broker. See [Ingest agent sessions](./use/ingest).
2. **Share.** Every memory is a signed, encrypted event in the **profile log**. Each device keeps an encrypted replica
   and synchronizes it with a private bucket in Cloudflare R2 (or another S3-compatible store). The storage provider
   sees only opaque objects.
3. **Recall.** Agents ask the local **broker** for memories, with source IDs as evidence. The broker answers only
   within the scopes each agent was granted and never hands an agent the keys. Hermes, OMP, Codex and Claude Code
   recall through a rule that runs the recall command; Claude and other AI apps can also connect as a remote MCP
   connector.

See [Concepts](./getting-started/concepts) for how these parts fit together.

## Know what setup includes

AgentMesh installs its own programs, not the agents themselves. A device runs:

- the **broker** (`agentmesh_log`, Python 3.11 or later), which holds this device's keys and syncs the profile log;
- the scheduled **ingest** and **summary** jobs, which use the Claude Code CLI to summarize transcripts;
- on a Mac and on Windows, the **supervisor**, which runs the broker and the jobs as one background item, and on a Mac
  the [menu bar app](./use/control-app) that shows their health.

A repository checkout contains code, not your memories. Memory lives only in the encrypted profile log: in each
device's replica and in your bucket.

::: info Upgrading an older device
Before 2026-11, devices ingested into a local SQLite database and some exchanged packets through Syncthing. Both are
retired, and their installers were removed on 2026-11-09. If a device still runs its transcript jobs from the
`memory-sync` directory, follow the [migration runbook](./install/migrate-memory-sync).
:::

## Choose a reading route

![Reading path from Concepts through Prerequisites, Install, Use and Help, with Install highlighted.](/diagrams/reading-path.svg)

1. Read [Concepts](./getting-started/concepts) if the terms are new.
2. Prepare the [prerequisites](./getting-started/prerequisites): Python, a private bucket and its credentials.
3. Follow the [Quick start](./getting-started/quick-start) to create your account on the first device and pair a
   second one. The platform pages ([macOS](./install/macos), [Linux](./install/linux), [Windows](./install/windows))
   cover the details for each system, and [Run in the background](./install/background-service) keeps everything
   running.
4. [Ingest agent sessions](./use/ingest), then [recall memory and connect agents](./use/recall).
5. Use [Troubleshooting](./troubleshooting) and [Privacy](./privacy) when you need help.

For the project scope, see the [repository overview](https://github.com/theidentify/AgentMesh/blob/main/README.md).
Operator procedures and developer details are collected in [Reference](./reference/index).

Next, read [Concepts](./getting-started/concepts).
