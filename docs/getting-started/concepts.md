# Concepts

Understand how AgentMesh keeps memory local while sharing record changes between devices. Begin with [What is AgentMesh](../introduction) for the scope of the application; no installation is needed yet. You will be ready to choose distinct device identities and prepare a private exchange.

## How AgentMesh works

AgentMesh exchanges record changes instead of live database files. Each device keeps its own memory and shares only small record changes, so the picture below is the whole system.

![Your agents feed a local sync worker that keeps a local database and exchanges change packets with other devices through a private Syncthing folder; syncing database files directly is forbidden.](/diagrams/architecture.svg)

Follow the arrows from left to right. **Your agents** (Claude, Codex, Hermes, OMP) write session transcripts as they work. The **sync worker** picks up new sessions, stores them in the local database, **Local database**, and every 60 seconds exports local changes and imports changes from peers. The **exchange folder** is the only thing the devices share: a private Syncthing folder of change packets and status reports. **Other devices** run the same worker against their own local databases.

The exchange folder is highlighted because everything depends on it, and it is also the reason both devices must be online at the same time. The red box is the shortcut people try first: putting the database file itself in Syncthing. Never do that; live SQLite files and their WAL/SHM companions corrupt when copied mid-write.

The rest of this page defines each piece in more detail.

## Device identity

A device identity combines the sync group UUID with one of three allocation slots: `mac`, `windows` or `linux`. These are labels, not operating systems: a group supports at most three devices, each with a different identity. Two Macs, or a Linux source and a Mac destination, can share memory when they use different identities. Platform wrappers select a matching label by default; the Python installer and empty-group setup can select any slot on any OS. Never reuse an identity on another active device or clone initialized local state.

## Exchange folder

The private exchange carries immutable change packets, the bootstrap ZIP and manifest, and peer reports at `status/<node>.json`. Supplied setup files may also live there. Keep the live database and its WAL/SHM files outside this folder; AgentMesh synchronizes records, not database files.

## Bootstrap

The bootstrap ZIP supplies a shared starting snapshot. Its checksum manifest lets the installer check integrity. Both contain private installation material: never publish them, including as GitHub release assets. A Git checkout alone contains no memories.

## Change packets and conflicts

A packet describes record changes. Independent edits to the same row or natural unique key are quarantined instead of silently merged. Other unrelated packets can still apply. See [troubleshooting](../troubleshooting#records-are-quarantined) before attempting recovery.

## Overlapping online time

Direct Syncthing exchange needs both devices online at the same time. Offline writes stay local until transport and workers are available again.

## What AgentMesh does not do

The setup wrappers install the memory-sync application, not Hermes or other agents. They do not run OS package managers or register services. Background operation is a [separate step](../install/background-service). Peer edits do not write back to PostgreSQL.

Read the [sync reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SYNC.md) for protocol details and limitations. When the model is clear, check the [prerequisites](./prerequisites) for your devices.
