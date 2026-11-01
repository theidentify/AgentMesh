# What is AgentMesh

AgentMesh makes past work available across agents and devices through local memory and an encrypted, shared profile log. This page helps you decide where it fits and choose a route through the guide; no installation is needed yet, but review the [prerequisites](./getting-started/prerequisites) before setting up a device. By the end, you will know what AgentMesh provides and where to start.

## Keep context across agents and devices

Agent sessions are stored separately, so a decision recorded by one agent on one device may be missing from the next conversation elsewhere. AgentMesh ingests OMP, Codex and Claude session transcripts into a local SQLite **staging** database on each device. **Catch-up** then imports durable memory from staging into the **encrypted profile log**, which every paired device synchronizes through private object storage (Cloudflare R2). Agents recall memory, with source evidence, through a local **broker** that enforces each agent's grant. See [Concepts](./getting-started/concepts) for how these parts fit together.

Earlier versions exchanged record changes directly between devices as packets through a private Syncthing folder. That **packet sync** is off by default since Phase 7a and remains only for rollback; pages that apply only to it say so at the top.

## Use this guide to set up and use memory

Hermes uses recall through this integration; its transcripts are not ingested. Recall rules are instructions telling an agent how to invoke the local read-only query. This guide is for people using Hermes, OMP, Codex or Claude who want to install AgentMesh, connect recall rules and check that memory is available.

The device setup pages ([macOS](./install/macos), [Linux](./install/linux), [Windows](./install/windows)) create the local database that ingestion uses. Creating a profile-log account, pairing a device and granting agents are owner procedures today: follow the [profile-log runbooks](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#devices-and-recovery). On a Mac, the [menu bar app](./use/control-app) shows the background jobs and the keys and devices this profile holds.

## Know what setup includes

AgentMesh installs its own programs, not the agents themselves. The setup wrappers do not install Python or Syncthing through an OS package manager (they still check for Syncthing, which carries traffic only when packet sync is enabled); background services are a separate step. A repository checkout contains code, not your memories: memory lives in each device's staging database and, encrypted, in the profile log.

For the project scope, see the [repository overview](https://github.com/theidentify/AgentMesh/blob/main/README.md). Operator procedures and developer details are collected in [Reference](./reference/index).

## Choose a reading route

![Reading path from Concepts through Prerequisites, Install, Use and Help, with Install highlighted.](/diagrams/reading-path.svg)

Start with **Concepts** to understand staging, the profile log and the broker. The **Prerequisites** page explains how to prepare Python and Syncthing. **Install**, the highlighted step, sets up this device. **Use** takes you through ingestion, recall and, on a Mac, the menu bar app. **Help** covers troubleshooting and protecting your data.

1. Read [Concepts](./getting-started/concepts) if the terms are new.
2. Prepare the [prerequisites](./getting-started/prerequisites).
3. Install on [macOS](./install/macos), [Linux](./install/linux) or [Windows](./install/windows), then pair the device with the profile log using the [owner runbook](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#devices-and-recovery). The broker keeps device keys in the macOS Keychain or Windows Credential Manager; Linux has no native key store for it yet.
4. [Ingest agent sessions](./use/ingest), then [recall memory and connect agents](./use/recall).
5. Use [Troubleshooting](./troubleshooting) and [Privacy](./privacy) when you need help.

Next, read [Concepts](./getting-started/concepts) to see how staging, catch-up, the profile log and the broker work together.
