# Concepts

::: warning Retired installer
Parts of this page describe the memory-sync installers and runtime: `setup_macos.sh`, `bootstrap_windows.py`,
`install_agent_rules.py`, `agentmesh.py`, packet sync and the SQLite database. These were removed in SQLite retirement
R5b (2026-11-09). Until this guide is rewritten, set up a device's broker with the
[profile-log README](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md). Move an existing
device's transcript jobs with the [memory-sync migration runbook](../install/migrate-memory-sync).
:::

Understand how AgentMesh keeps memory on each device while sharing it, encrypted, through the profile log. Begin with [What is AgentMesh](../introduction) for the application's scope; no installation is needed yet. You will know what each part does and which pages describe the legacy packet sync.

## How AgentMesh works

Memory moves through four parts. Each runs on the device, and only encrypted objects leave it.

1. **Staging database.** Ingestion reads supported OMP, Codex and Claude transcripts into a local SQLite database. Raw observations and transcript evidence stay there; they are never uploaded.
2. **Catch-up.** Imports durable memory items and summaries from staging into the profile log, routing each project to a scope through the migration mapping. A project the mapping does not cover is held back until the owner picks a scope.
3. **Profile log.** An append-only log of signed, encrypted events, stored in private S3-compatible object storage (Cloudflare R2). Every paired device keeps a local encrypted replica and synchronizes it in the background. The storage provider sees only opaque objects.
4. **Broker.** A local service (`org.agentmesh.profile-log.broker` on macOS) that holds this device's keys, verifies the log and answers agents' recall and remember requests. When the broker is not running, recall returns an explicit error instead of reading another source.

Settings, agent key files and the staging database are never synchronized. Each device has its own.

## Profiles, scopes and grants

A **profile** (for example `work` or `personal`) is a tree of **scopes** such as `work/common` or `work/client-a`. Each scope has its own encryption keys, so a device or agent can read only what it was granted.

- **Devices** join by pairing: an existing device approves the request after you compare a six-digit code on both screens, and grants exactly the scopes you select. A device can be revoked later.
- **Agents** never hold scope keys. Each has a key file and a grant: which scopes it may read, which it may write, and whether it may see secret-labelled memory. The broker checks the grant on every request.
- **Recovery.** Creating the account prints a recovery kit of three shares, any two of which restore it. Keep them apart, and outside the AgentMesh folder.

The [profile-log reference](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#devices-and-recovery) has the pairing, grant and recovery commands.

## The supervisor and the menu bar app (macOS)

On a Mac, the AgentMesh supervisor (`org.agentmesh.control`) runs the scheduled jobs — catch-up, ingest and summary — and watches the broker and the other background services. The [menu bar app](../use/control-app) is its client: it shows each job's health, asks where a new project's memory should go, and lists the keys and devices this profile holds. Moving a service between launchd and the supervisor needs Touch ID or your Mac password, which the supervisor asks for itself.

## Legacy packet sync

Before Phase 7a, devices exchanged record changes directly as immutable packets through a private Syncthing folder. `packet_sync.enabled` now defaults to false. The code remains so that an owner can roll back, and some setup steps (device identities, the exchange folder) still come from it. The rest of this section describes that model; pages that apply only to it carry a notice at the top.

### How packet sync works

Each device keeps a local database and exchanges record changes. The diagram summarizes the main components of packet sync.

![Supported transcripts feed the worker and local database; the worker exchanges packets with other devices through a private folder, while live database-file sync is unsafe.](/diagrams/architecture.svg)

**Your agents** can use recall rules; built-in session ingestion supports OMP, Codex and Claude. When ingestion is enabled, the **sync worker** stores supported sessions in the **Local database**, then exports and imports record changes through the **Exchange folder**. **Other devices** have separate databases and distinct device identities. Hermes supports recall through this integration, but its sessions are not ingested by this workflow.

The highlighted exchange folder carries packets and reports. Direct Syncthing exchange between two devices requires overlapping online time. The red box shows an unsafe shortcut: copying live SQLite database files and their WAL/SHM companions during writes can produce an inconsistent or corrupted copy. WAL and SHM are SQLite's write-ahead log and shared-memory sidecar files.

A fresh bootstrap enables default ingestion and starts the worker immediately unless you pass `--no-ingest`. Use that flag to [review and limit roots before enabling ingestion](../use/ingest#before-the-first-bootstrap-cycle); synchronization still runs. An empty group starts with ingestion disabled. By default the worker waits 60 seconds **after each completed cycle**. Processing time and transport delay add to that wait; it is not a one-minute freshness guarantee.

### Device identity

A device identity combines a sync group UUID with one of three values: `mac`, `windows` or `linux`. These labels are independent of the operating system. When explaining `--node`, its value is the **node slot**; the `<node>` in `status/<node>.json` means that same value. Elsewhere this guide uses **device identity**.

A group supports at most three active devices with different identities. Two Macs and one Linux device can use the three different identities; a fourth device cannot join the same group under this model. Platform setup wrappers choose a default identity, while the Python installer can select an alternative on any OS. Ask the group administrator which identity is assigned to each device, including offline devices, and inspect existing runtimes. Never clone initialized local state or reuse an identity on another active device.

### Exchange folder and bootstrap

Use a dedicated private exchange folder for each sync group: a group UUID does not isolate several groups sharing one folder. The private exchange folder carries immutable change packets, peer reports at `status/<node>.json`, and supplied bootstrap/setup files. Keep the live database and its WAL/SHM files outside it. The bootstrap ZIP supplies a shared starting snapshot; its manifest checks integrity. A repository checkout contains code, not memories. An unsigned group created by current `setup-new` can [add a second and third device through origin-issued invitations](../install/new-group#adding-another-device). Joining replays the empty-baseline group’s retained packets; it does not copy a database. Lost or expired invitations permanently consume their reserved identities.

### Conflicts affect whole packets

![Device A edits a row and captures a change packet; the exchange carries it to device B, which applies it and records a receipt when the parent matches, or holds the whole packet on conflict or a missing predecessor.](/diagrams/packet-lifecycle.svg)

**1 · Edit a row** changes local memory on device A. **2 · Capture** compares it with the saved synchronized view (the shadow), records the previous revision as `parent`, assigns a new revision UUID and puts all changes found in that capture into one packet. **3 · Exchange folder**, highlighted, holds the published immutable file while Syncthing transfers it. **4 · Receive: check parent** compares each change’s parent with B’s current revision. **5 · Match → apply + receipt** applies the changes and records a receipt in B’s database after validation succeeds. On the other arrow, **Mismatch → no overwrite** holds the entire packet: an independent edit can cause a conflict; an unknown predecessor can leave it pending.

The diagram shows ordinary changes; activated packets can express multiple parents and explicit resolutions. The example’s `v1` and `v2` stand for revisions, not literal database values. Parent matching is one check among packet, signature (when signed), uniqueness and referential-integrity checks. A known but different parent or a mismatched baseline is a conflict, not a missing predecessor to wait for. Publication uses `changes/<node>/<uuid>.json`, or `signed-changes/<node>/<uuid>.json` for signed packets; `<node>` is the node slot and `<uuid>` is the packet’s UUID. A packet carries changed rows and their ancestry, not a complete database copy. Receipts prevent applying an accepted packet again.

Never delete packets to reclaim space. A missing intermediate packet can leave later changes **pending**; a missing newest packet can cause **silent loss on a peer** with no later dependency to reveal it. Published packets are not automatically recreated. Keep the original files and [preserve evidence if packets are missing](../troubleshooting#exchange-packets-were-deleted).

A packet can contain several changed records. If one conflicts with an independent edit or a natural unique key, the **entire packet** is quarantined: unrelated records inside it do not apply either. Later packets depending on it may remain pending. Other independent packets can still apply during that cycle, but a managed worker stops after a [permanent failure](../use/worker#respond-to-failures-and-timeouts), an error requiring review rather than automatic retry. [Inspect the failure](../troubleshooting#records-are-quarantined) before restarting.

After every device is upgraded and the operator [activates the new protocol](../use/resolve-conflicts#activate-once-after-upgrading-every-device), a supported same-lineage conflict can be resolved explicitly, including update versus deletion. A resolution packet records the chosen rows and complete covered history, preserving non-conflicting companion rows from the held packets. It does not silently choose the newest edit or repair different baselines, natural-key collisions or foreign-key relationships. Conflicting independent resolutions require another decision; [receive and verify the result, then explicitly restart workers](../use/resolve-conflicts#receive-on-peers-and-restart).

### Plan for growth

**Capture** compares local records with the last synchronized view and packages their changes. It scans all eight memory tables and keeps decoded rows in memory. Applying new packets also checks a complete resulting row view; receive scans retained packet files. Packet files live under `changes/<node>/` or `signed-changes/<node>/` in the exchange folder. Receipts (accepted packet identifiers/checksums), history (record revision ancestry) and outbox records (outgoing packets and publication state) live in the local SQLite database as `_sync_receipts`, `_sync_history` and `_sync_outbox`. All are retained without automatic pruning. Watch disk use and cycle duration as memory grows; do not delete exchange packets to reclaim space. A missing newest packet can leave undetected missing data, while a missing predecessor can leave later changes pending.

## What setup does not include

AgentMesh installs its own programs, not Hermes or other agent applications. Setup does not run OS package managers or enable a background service. PostgreSQL is not required for this guide's SQLite path; if you use the optional PostgreSQL mirror, peer changes do not write back to PostgreSQL. See the [sync reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SYNC.md) for protocol details.

Next, prepare the [prerequisites](./prerequisites) for your chosen setup route.
