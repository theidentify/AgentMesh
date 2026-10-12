# What is AgentMesh

AgentMesh makes past work available across agents and devices through local memory and private sync. This page helps you decide where it fits and choose a route through the guide; no installation is needed yet, but review the [prerequisites](./getting-started/prerequisites) before setting up a device. By the end, you will know what AgentMesh provides and where to start.

## Keep context across agents and devices

Agent sessions are stored separately, so a decision recorded by one agent on one device may be missing from the next conversation elsewhere. AgentMesh ingests OMP, Codex and Claude session transcripts into a local SQLite database, exchanges record changes through a private Syncthing folder, and lets agents recall memory with source evidence. Each device keeps its own database; the shared folder carries change packets.

## Use this guide to set up and use memory

Hermes uses recall through this integration; its transcripts are not ingested. Recall rules are instructions telling an agent how to invoke the local read-only query. This guide is for people using Hermes, OMP, Codex or Claude who want to install AgentMesh, connect recall rules and check that memory is available. If you already received a private bootstrap, take the [quick start](./getting-started/quick-start). If you are creating an empty group, follow [New sync group](./install/new-group); eligible unsigned groups can then [invite a second and third device](./install/new-group#adding-another-device) from the origin.

## Know what setup includes

AgentMesh installs the memory-sync application, not the agents themselves. The setup wrappers do not install Python or Syncthing through an OS package manager; background services are a separate step. A repository checkout contains code, not your memories, and direct device-to-device exchange needs overlapping online time.

For the project scope, see the [repository overview](https://github.com/theidentify/AgentMesh/blob/main/README.md). Operator procedures and developer details are collected in [Reference](./reference/index).

## Choose a reading route

![Reading path from Concepts through Prerequisites, Install, Use and Help, with Install highlighted.](/diagrams/reading-path.svg)

Start with **Concepts** to understand local memory and sync. The **Prerequisites** page explains how to prepare Python and Syncthing. **Install**, the highlighted step, sets up this device. **Use** takes you through status checks and recall. **Help** covers troubleshooting and protecting your data.

1. Read [Concepts](./getting-started/concepts) if the terms are new; otherwise start with your setup route above.
2. Prepare the [prerequisites](./getting-started/prerequisites).
3. With a supplied bootstrap, install on [macOS](./install/macos), [Linux](./install/linux) or [Windows](./install/windows). Starting alone, create a [new local group](./install/new-group) instead.
4. [Check status](./use/status), then [recall memory and connect agents](./use/recall).
5. Use [Troubleshooting](./troubleshooting) and [Privacy](./privacy) when you need help.

Next, choose [Quick start](./getting-started/quick-start) if you received a private bootstrap, or [New sync group](./install/new-group) to start locally and add devices through eligible unsigned invitations. Read [Concepts](./getting-started/concepts) first if these terms are new.
