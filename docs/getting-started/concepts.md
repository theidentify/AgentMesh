# Concepts

## How AgentMesh works

Each device keeps a local SQLite memory database. A worker exports record changes into a private Syncthing exchange folder and imports changes from other devices. Agents can then recall memory from their local database.

```text
[Mac: mac.db] --packets--> [Syncthing exchange folder] <--packets-- [Windows: windows.db]
                                      ^
                                      |-- packets -- [Linux: linux.db]
```

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

Read the [sync reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SYNC.md) for protocol details and limitations.
