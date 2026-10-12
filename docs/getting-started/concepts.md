# Concepts

## How AgentMesh works

AgentMesh never moves a database between machines. Each device keeps its own memory and shares only small record changes, so the picture below is the whole system.

![Your agents feed a local sync worker that keeps mac.db and exchanges change packets with other devices through a private Syncthing folder; syncing database files directly is forbidden.](/diagrams/architecture.svg)

Follow the arrows from left to right. **Your agents** (Claude, Codex, Hermes, OMP) write session transcripts as they work. The **sync worker** picks up new sessions, stores them in the local database, **mac.db** on a Mac, and every 60 seconds exports local changes and imports changes from peers. The **exchange folder** is the only thing the devices share: a private Syncthing folder of change packets and status reports. **Other devices** run the same worker against their own `windows.db` or `linux.db`.

The exchange folder is highlighted because everything depends on it, and it is also the reason both devices must be online at the same time. The red box is the shortcut people try first: putting the database file itself in Syncthing. Never do that; live SQLite files and their WAL/SHM companions corrupt when copied mid-write.

The rest of this page defines each piece in more detail.

## Node

A node is one logical writer: `mac`, `windows` or `linux`. Each identity can belong to only one active device in a sync group. Never reuse an identity on another active device or clone initialized local state.

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
