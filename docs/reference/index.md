# Reference

Find the repository documents behind this guide: full command references, operator procedures and design records.
Start with [What is AgentMesh](../introduction) if you have not set up a device yet.

## Users

| Document | Use it for |
| --- | --- |
| [Encrypted profile log](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md) | Every broker command: init, pairing, grants, `apply` and the settings schema, sync, recall modes, presence, backup and recovery |
| [Insight](https://github.com/theidentify/AgentMesh/blob/main/insight/README.md) | The read-only dashboard, its sources and its remote access |
| [Menu bar app](https://github.com/theidentify/AgentMesh/blob/main/control-app/README.md) | Building, installing and removing AgentMesh Control, and what it may and may not do |

## Operators

| Document | Use it for |
| --- | --- |
| [Devices and recovery](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#devices-and-recovery) | Pairing, regrants, removing devices, archiving and deleting profiles, recovery |
| [Settings and admin UI](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#settings-and-admin-ui) | `settings.json`, `settings.local.json`, `apply` and the terminal admin (`agentmesh-log admin`) |
| [Hybrid recall](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#phase-8-hybrid-recall) | The embedding host, its tunnel and the evaluation scripts |
| [Team workspaces](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#workspace-registry-team-workspace-w1) | Workspace roles, invites, shared tasks, review, quotas and the Slack adapter |
| [Connecting AI apps](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#connecting-ai-apps) | The MCP gateway, connector approval and the owner deployment runbook |
| [SQLite retirement](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#sqlite-retirement) | What R5b removed and what is left |
| [Migrate from memory-sync](../install/migrate-memory-sync) | Moving an older device's transcript jobs into the profile-log package |
| [Control plane design](https://github.com/theidentify/AgentMesh/blob/main/docs/superpowers/specs/2026-10-23-local-control-plane-design.md) | The supervisor, its tiers and its milestones |

## Developers

| Document | Use it for |
| --- | --- |
| [Wire format](https://github.com/theidentify/AgentMesh/blob/main/profile-log/FORMAT.md) | Normative rules for events, wraps and registry objects |
| [Design records](https://github.com/theidentify/AgentMesh/tree/main/docs/superpowers) | Specs, plans and reports for each phase. They are history: where they disagree with this guide or the READMEs, the current code wins. |

Next, return to the [Quick start](../getting-started/quick-start).
