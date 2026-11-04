# Team workspace and shared task state for multiplayer agents: design

Status: draft for owner review, 2026-11-04. Builds on the encrypted profile log
([2026-10-18](2026-10-18-encrypted-profile-log-design.md)), the memory tree
([2026-11-02](2026-11-02-memory-tree-design.md)) and the local control plane
([2026-10-23](2026-10-23-local-control-plane-design.md)). Nothing here is implemented.

## Motivation

AgentMesh today serves **one person** with many devices and agents. A company wants several
people, each with their own agents, to work in one shared workspace:

- Agents of different people should recall the same project decisions and constraints.
- Everyone, human or agent, should see **who is doing what right now** on a task, and hand work
  over without retelling it.
- Each person's personal memory must stay theirs.

Two gaps block this:

1. **No person identity.** Grants are per agent on a device (`broker/grants.py`), and every
   device belongs to the one account. There is no member, role or membership change.
2. **No shared working state.** Durable memory keeps knowledge (`fact`, `decision`,
   `constraint`, `preference`, `procedure`, `open_loop`, `summary`) with `active / superseded /
   resolved / deleted` status, and the head logic picks the current revision. Task progress
   exists only as raw `task_transition` events. There is no keyed record saying "task X is
   claimed by Alice's Claude, blocked on Y, next step Z".

## Goals

1. A **workspace** (company or team) that several people's accounts join, with roles.
2. Every event attributable to a **member**, not only to a device and agent.
3. A **shared task state** record that agents read and update, with claims, leases and handoff.
4. Keep what already holds: keys never leave devices, the relay sees only ciphertext, local-first
   offline operation, agents never hold data keys, nothing flows across profiles automatically.

## Non-goals (v1)

- Real-time messaging between agents. The log is eventually consistent; async task notes and chat
  adapters are covered below.
- Strong, server-enforced locks by default (an optional Worker-backed claim is Phase W5).
- MLS. Membership uses multi-recipient age wraps behind the existing `Keyring` interface.
- Merging a person's personal profile into the workspace. Promotion stays explicit.
- Mobile-native apps. Members on phones use the existing MCP gateway path.

## Model

### Layers (additions in bold)

| Layer | Identified by | Holds |
| --- | --- | --- |
| Account | email (name only) | account root key, recovery key; **one per person** |
| **Workspace** | random ID; a profile with a membership registry | profile key, scope tree |
| **Member** | account ID inside a workspace | role, scope grants |
| Scope | random ID under the workspace profile | scope key |
| Device | Ed25519 keypair | wrapped scope keys it is granted |
| Agent | sub-key certified by its device | no data keys |

A workspace is an ordinary profile whose scope keys are wrapped to devices of **more than one
account**. A person keeps their own `personal` (and private `work`) profiles beside it; the
existing rule "recall defaults to one profile, cross-profile recall is explicit" separates them.

### Member identity

- Joining a workspace requires a one-time **email ↔ account-key attestation** (OIDC or magic
  link), the step the profile-log design deferred "for sharing profiles with other people".
  The attestation is signed and stored in the workspace registry; email remains a name, not an
  authority.
- Event authorship gains `member` (account ID) next to `device_id` and `agent_id`. Readers verify
  the chain account → device certificate → agent certificate → event signature, all of which
  already exist; the member field is the account the device certificate names.

### Roles

| Role | Read | Write | Membership |
| --- | --- | --- | --- |
| owner | all scopes | all scopes | add/remove admins, rotate, delete workspace |
| admin | granted scopes | granted scopes | add/remove members and contributors |
| member | granted scopes | granted scopes | none |
| contributor | none | sealed-box inbox of granted scopes | none |

- A contributor maps to the existing write-only design (scope public key only). Fits CI bots,
  contractors and external agents.
- Membership changes are signed records in the workspace registry. Removing a member or raising
  a role is **Tier 2**: it needs presence on an admin device. Removing an owner, or any change
  to the owner set, needs **two owners** (quorum), so one stolen device cannot take the workspace.

### Membership changes

- **Add:** an admin approves a pairing request from the new member's device (existing QR /
  6-digit / LAN flow), choosing scopes. The admin's device wraps those scope keys to the device.
  The member's other devices are enrolled by the member, but only for scopes already granted
  to that member (a member cannot widen their own grant).
- **Remove:** new key epoch for every scope the member could read, wrapped only to remaining
  devices. As today, data already downloaded cannot be recalled; full re-encryption is optional.
- **Recovery:** the workspace recovery key is Shamir-split among owners (for example 2-of-3),
  never held by a single person.

## Shared task state

### Record

A new durable kind `task_state`, one keyed chain per task in the project scope that owns it:

- `memory_key = "task/<TASK-REF>"`. The `/` keeps it outside ordinary keys, as `summary/` does.
- Updates are `revise` events on the current head, so `broker/heads.py` yields the current state.

```json
{
  "kind": "task_state",
  "task": "AM-142",
  "title": "Retire packet RANGES",
  "status": "in_progress",
  "owner": {"member": "<account-id>", "agent": "<agent-id>"},
  "lease_until": 1793750400,
  "next": "Run r5b once the 2026-12-03 window opens",
  "blocked_on": [],
  "open_loops": ["<memory_key>", "..."],
  "handoff": null,
  "sources": ["<ULID>", "..."]
}
```

Statuses: `open → claimed → in_progress ⇄ blocked → review → done`, plus `abandoned`. `done` and
`abandoned` map to the existing `resolved` status, so the bounded summary drops them.

### Claims and leases

The log has no global order, so claims are **advisory with a deterministic tie-break**:

1. An agent claims by revising the head from `open` (or from an expired lease) to `claimed`,
   with `lease_until` at most 4 hours ahead (configurable).
2. Two concurrent claims produce two heads; `heads()` already reports `conflict: true`. The winner
   is the claim with the **lowest ULID**. Every agent applies the same rule, so all converge after
   sync. The loser must not continue work and should write nothing further on the task.
3. The owner renews the lease while working. An expired lease makes the task claimable again; the
   new claimer must read the last `next` and `handoff` first.
4. Ending work writes `handoff` (what is done, what is next, where the branch or PR is) and sets
   `open` or `review`.

Agents must re-read the head after sync before acting on a claim. The window in which two agents
both believe they own a task equals the sync delay; W5 can close it where that matters.

### Tree and recall

- The memory tree's existing `tasks/<TASK-REF>/INDEX.md` page shows the current `task_state` plus
  its open loops and recent decisions, with `stale` when newer events exist.
- New read-only broker op `tasks` (scope, status filter, owner filter) returns current heads, so
  "what is open in client-a" and "what is my agent holding" need no free-text recall.
- The project `INDEX.md` lists active tasks by owner.

### Write permissions

- Any member with write on the scope may create a task and claim an `open` one.
- Only the current owner, or an admin, may revise a claimed task before its lease expires. The
  broker rejects the write locally; peers also ignore an out-of-rule revision when computing state.
- Contributors may append a `task_request` to the inbox; a member turns it into a task.

## Activity

Operational liveness (which agent process is running now) stays out of memory, as Insight already
does. A workspace view in Insight shows, per project: active tasks and owners, expired leases, and
conflicts. It reads only scopes the viewing device can read.

## Agent-to-agent messages and chat channels

### Between agents

Agents coordinate **asynchronously through the log**, not by direct chat:

- Task-scoped messages are a new kind `task_note` (question, answer, review comment), tagged with
  the task and optionally `to: <member|agent>`. They are ordinary events, so they are encrypted,
  signed, attributed and recallable later.
- `handoff`, `blocked_on` and `next` in `task_state` carry the structured part.
- Delivery latency equals sync latency (seconds to minutes). Real-time negotiation between agents
  stays a non-goal; agents needing that run in one orchestrator on one device.

### Teams, Slack, Discord, LINE

These are chat surfaces, not AI apps with remote MCP, so they connect through a **channel adapter**:

- An adapter is a bot (Slack app, Teams bot, Discord bot, LINE Messaging API webhook) running on a
  **trusted device** of the workspace, enrolled as an agent with its own grant. It never runs in the
  vendor cloud with keys and never sends keys to the Worker.
- Each channel maps to one scope (for example `#client-a` → `project: client-a`). The adapter's
  grant is that scope only; by default it is a **contributor** (write-only inbox) or a member with
  `max_label=internal`, so `secret` content never reaches a chat vendor.
- Inbound: a message mentioning the bot becomes a `task_request`, `task_note` or a recall query.
  The sender's chat identity must be linked to a member (one-time link via the attestation flow);
  unlinked senders are treated as contributors.
- Outbound: recall answers, task status (`tasks` op) and lease-expiry or conflict notices. Only
  content at or below the channel's label leaves the device. Whatever is posted is then stored by
  the chat vendor under its own retention; the adapter states this when a channel is linked.
- If no trusted device is online, the adapter is offline too; a hosted adapter would need the
  Worker to hold keys, which this design rejects.

## Access control

### What enforces what

| Boundary | Enforced by | Holds against |
| --- | --- | --- |
| Scope key | cryptography (age wraps) | everyone without the key: relay, Worker, other members, removed devices (for new epochs) |
| Agent grant (`read`, `write`, `max_label`, `allow_secret`, `allow_cross_profile`) | the broker on the member's own device | that member's own agents and adapters only |
| Role and task-write rules | registry signatures; peers ignore out-of-rule events | members writing with valid keys |

The broker is not a boundary against the person who owns the device: they hold the scope keys
their devices were wrapped. So **inside a workspace, a member can read everything in every scope
granted to them**, whatever their agents' grants say.

### Invariants

1. **Anything a member must not read goes in a scope they are not granted.** No per-record ACL,
   per-agent filter or `to:` field substitutes for this. Tags and tasks stay filters only.
2. **Scope names and the scope tree are encrypted per scope.** A member sees only the names of
   scopes they can read; the registry lists other scopes by opaque ID only.
3. **A grant never widens itself.** Members enroll their own devices only for scopes already
   granted; agent grants are a subset of the member's scopes; adapter grants are a subset of the
   linking admin's scopes.
4. **Effective permission of a delegated request = intersection.** For a chat adapter or MCP
   app acting for a sender: adapter grant ∩ sender's member grant ∩ channel's scope. An unlinked
   sender gets contributor rights only. This prevents the adapter acting as a confused deputy.
5. **Lowering a label needs a scope admin's presence**, not the writing member's.
6. **Revocation is forward-only.** Removal rotates the epoch; data already read stays read.
   Admins are told this in the removal dialog.
7. **Pages and summaries never cross scopes.** The tree writer already cites only non-secret,
   non-page events of the page's own scope (`page_floor`); the workspace keeps that rule and
   adds: a summary visible in scope A never cites scope B, even for an owner who reads both.
8. **Secrets stay device-only.** `secret` content is never sent to an adapter, an MCP app or an
   embedding host, and `allow_secret` windows stay presence-gated per device.

### Known leak paths to close before W2

- **Chat channel membership drift.** Slack/Teams/Discord/LINE membership changes (guests,
  shared channels, exports) are invisible to AgentMesh. Adapters post only at or below
  `internal`, re-check the sender per request, and refuse channels with external guests unless
  an admin marks the scope `general`.
- **Embedding host.** Hybrid recall sends text to an embedding host. In a workspace that host sees
  every member's queries and content; it must be a workspace-trusted device or hybrid stays off.
- **Local audit only.** Each device audits its own broker. Admins cannot see another member's
  recalls; this is inherent (invariant 1), not a bug to hide.
- **Metadata.** The relay sees object counts, sizes and timing per opaque scope ID.

## Deployment without a central cloud service

Owner decision (2026-11-04): no central cloud control service for now. The workspace runs on
devices only; the cloud keeps its current role (ciphertext storage and, where already deployed, the
MCP gateway relay). Storage stays behind the existing interface, so a company may instead use the
filesystem backend or a self-hosted S3-compatible store with no logic change.

The "centre" of a workspace is an optional **workspace server**: an always-on device enrolled like
any other, granted workspace scopes only (never a person's personal profile). It runs the broker,
the summarizer and tree writer, chat adapters and MCP recall for the team, and its own local
control plane. It approves nothing for others: membership and grant changes are signed on an admin
device and only distributed through the log.

## Device classes

Today's code assumes a Mac in several places. The workspace must also accept other devices, so each
device record carries a **class** and its **capabilities**, signed at enrollment. Approvers see
them, and grants are limited by them.

| Class | Examples | Holds | Presence | Limits |
| --- | --- | --- | --- | --- |
| workstation | macOS, Windows, Linux desktop | read/write scope keys | local (Touch ID, Windows Hello, console) | none beyond the member's grants |
| server | headless Linux, Mac mini, Windows Server | workspace scope keys | **none locally**; tier 2 comes as a signed approval from an admin workstation; tier 3 needs someone at the console | no `allow_secret`; cannot approve devices or members; no personal profiles |
| write-only | phone, IoT, kiosk | scope public keys only | none | contributor rights; losing it leaks nothing |
| ephemeral | CI runner, dev container, cloud VM per job | nothing long-lived | none | never enrolled as a device; gets a short-lived agent certificate from a host device, or acts as a contributor |

Remote AI apps through MCP are not devices; they keep the existing per-app OAuth policy.

### Platform gaps

| Capability | macOS | Windows | Linux |
| --- | --- | --- | --- |
| OS key store (`oskeystore.py`) | login keychain file | Credential Manager (DPAPI) | **none** (`current_backend()` returns `None`) |
| Hardware-bound key | not yet (Secure Enclave planned) | not yet (TPM planned) | not yet (TPM planned) |
| Presence | console, Touch ID helper | console only; **Windows Hello missing** | console only |
| Service units (`platform_service.py`) | launchd | Task Scheduler | systemd user units |
| Control plane backend | launchd + native | **missing** (designed for v1.1) | **missing** |

Before W2 ships, Linux needs a key store (for example libsecret on desktops, `systemd-creds` with a
TPM on servers) and a server-class presence path. Windows needs a control-plane backend; Windows
Hello is wanted for workstations but not blocking.

## Many members, each with their own bots

### Quotas per member and per agent

The control plane's quota guard counts per host. In a workspace, one looping bot could flood a
shared scope. Each device's broker therefore enforces, per agent and per member, a daily cap on
writes and bytes per scope (defaults in workspace settings, overrides per role). Because a member
controls their own device, peers also apply the cap when building indexes and pages: events past a
member's cap for the day are kept but marked `over_quota`, excluded from default recall and pages,
and shown to admins in Insight.

### Staged writes and promotion

Agent writes to a shared scope default to **staged**:

- A staged event carries `review: "pending"` and is visible to recall only with an explicit
  `include_pending` flag; pages and summaries never cite it.
- A member with write on the scope (or a reviewer the scope names) **promotes** it by writing a
  signed `review` event (`approved` / `rejected`, reference to the source ULID). Promotion is a
  person's act: it requires console or OS presence on a workstation and cannot be done by an
  agent grant.
- Per scope, admins may allow trusted agents to write `durable` directly, for example the tree
  writer on the workspace server, or low-risk kinds such as `task_note`.
- `task_state` updates are not staged; they are governed by the claim rules.

### Provenance in recall

Every recall result reports `author` (member, agent, device class) and `review` (`approved`,
`pending`, `direct`). Default recall ranks human-written and approved items above unreviewed agent
output, and agents are instructed (through the shared-memory rules) to treat unreviewed agent
claims as hypotheses, not facts. An event derived from another agent's unreviewed event inherits
`pending` until its source is approved.

### Bulk onboarding with signed invites

Approving devices one by one does not scale when many people join at once. An admin issues a
**signed invite**:

- Fields: workspace, scopes, role (member or contributor), `max_uses`, `expires_at` (at most 7
  days), optional email domain for the attestation step.
- The admin's device pre-wraps nothing. A joining device presents the invite plus its email
  attestation; any admin device online verifies both and wraps the scope keys. With no admin
  device online the join waits; the workspace server may verify but may not wrap (server class
  cannot approve).
- Invites are revocable, and every use is audited. Raising a role still needs a separate Tier 2
  approval.

## Security notes

- Workspace and personal profiles never share keys. An agent granted both still recalls one by
  default; writing personal content into the workspace is an explicit promotion.
- Task records follow sensitivity labels; a `secret` task title or note is redacted in tree pages
  as today.
- The Worker and relay still see only ciphertext and opaque paths, including for task records.
- Every membership change, claim conflict and admin override is audited locally.

## Phases

| Phase | Content | Writes |
| --- | --- | --- |
| **W0** | `task_state` kind, claim/lease rules, `tasks` op, tree page, single account. Useful today for several agents of one person. | yes |
| **W1** | Member field on events, email attestation, workspace registry with roles. | yes |
| **W2** | Multi-account keyring wraps, add/remove member with epoch rotation, owner quorum, Shamir among owners; device class and capabilities in device records; Linux key store. | yes |
| **W3** | Contributor inboxes and `task_request`; staged writes, review and promotion; provenance in recall; per-member quotas; signed invites. | yes |
| **W4** | Insight workspace view; MCP gateway exposes `tasks` to remote apps under existing per-app policy. | no |
| **W5** | Deferred (needs cloud): strong claim via a conditional write on an opaque per-task claim object. | yes |
| **W6** | `task_note`; channel adapters (Slack first, then Discord, Teams, LINE) with chat-identity linking. | yes |

W0 needs no identity work and can ship first.

## Open questions

- Task reference source: own IDs, or mirror an external tracker (Linear, GitHub issues)?
- Default lease length, and whether a lease can outlive an offline device.
- Should `review` require a different member than the owner?
- Admin override of a live lease: always allowed, or only after the lease expires?
- Workspace billing and storage: one R2 bucket per workspace, or per owner?
