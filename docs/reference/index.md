# Reference

Find the repository reference for details beyond the task-based user guide. Start with [What is AgentMesh](../introduction) if you have not chosen a setup path yet. You will be able to distinguish user instructions from operator procedures and developer material, and consult each linked document for its verification limits.

## Users

| Document | Use it for |
| --- | --- |
| [Sync](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SYNC.md) | Understand record exchange, identity limits, operator protocol activation and resolution commands. |
| [macOS and Linux setup](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-SETUP.md) | Review installation paths, optional user services and the unsigned invitation/join contract. |
| [Windows setup](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-WINDOWS.md) | Run the bootstrap launcher and interpret its progress. |
| [Insight](https://github.com/theidentify/AgentMesh/blob/main/insight/README.md) | Configure the read-only dashboard and understand its observations. |

For the task sequence, use [Resolve conflicts](../use/resolve-conflicts): activate after upgrading peers, preview and confirm a choice, receive it and explicitly restart workers.

## Operators

| Document | Use it for |
| --- | --- |
| [Windows program installation](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/WINDOWS-INSTALLATION.md) | Manage existing program installations, confirmations and sign-in startup. |
| [Workflow](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/WORKFLOW.md) | Configure ingestion, primary summarization, exchange and recall. |
| [OTA updates](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/OTA-UPDATES.md) | Set up opt-in signed worker updates and recovery. |
| [Signed sync](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md) | Understand packet authentication, pinned peer trust and strict revocation for uncommitted resolution evidence. |
| [Key protection, OS unlock and identity backup](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/KEY-PROTECTION.md) | Protect device keys, configure account-bound unattended unlock, and create, verify, drill or restore encrypted identity backups with fencing and trust checks. |
| [Security wizard](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SECURITY-WIZARD.md) | Review explicit identity, pairing and activation steps. |
| [Mac replacement](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/MAC-REPLACEMENT.md) | Plan operator-led program replacement and rollback. |

For the user procedure, see [Back up and verify the device identity](../install/security#back-up-and-verify-the-device-identity) and [Restore only after stopping the old device](../install/security#restore-only-after-stopping-the-old-device). Identity recovery does not restore SQLite data.

## Developers

| Document | Use it for |
| --- | --- |
| [SQLite writers](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SQLITE-WRITERS.md) | Understand native ingestion and bounded writer readiness. |
| [SQLite consumers](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SQLITE-CONSUMERS.md) | Understand recall, exports and explicit backend selection. |
| [Standalone builds](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/STANDALONE-BUILD.md) | Build native CLI bundles and review platform verification gates. |
| [Profile-owned memory prototype](https://github.com/theidentify/AgentMesh/blob/main/profile-memory/README.md) | Explore the separate opt-in profile memory experiment. |

Next, use [Quick start](../getting-started/quick-start) for a supplied bootstrap, or [New sync group](../install/new-group) to begin without one and [add eligible peers by invitation](../install/new-group#adding-another-device).
