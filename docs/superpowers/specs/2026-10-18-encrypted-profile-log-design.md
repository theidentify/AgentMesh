# Encrypted profile log, device identity and memory broker (next-generation sync): design

Status: approved by the owner on 2026-10-19 (discussed 2026-10-18). Phases 1–2 are implemented in
`profile-log/` (theidentify/AgentMesh#52); later phases are not started. Builds on main
(SQLite authority + Syncthing packet exchange, `memory_sync.py`).

## Motivation

- **Mobile and new devices are hard to add.** Nodes are fixed to `mac`, `windows` and `linux`
  (`memory_sync.RANGES`), each with a 2^40 integer-ID range. A fourth device (for example a phone) needs a
  new range and a coordinated rollout to every peer, which rejects unknown nodes.
- **Syncthing needs overlapping online time.** Peers that are rarely online together fall behind.
- **Identity is per node, not per person, agent or profile.** We want each person's memory clearly
  attributed, profiles that never mix, and the strongest practical security.
- **Local-first is a preference, not a hard requirement**, but the design below keeps it: every device holds
  a full replica, works offline, and the relay only ever sees ciphertext.

## Goals

1. Clear identity: every event says which account, device and agent wrote it, and in which profile.
2. Profiles and selected projects isolated by cryptography, not by query filters.
3. Highest practical security: the storage provider, the relay and a leaked access token cannot read memory.
4. Low user effort: no new passwords, no OAuth for a single user; identity comes from the device the user
   already unlocks.
5. Storage is replaceable (cloud or self-hosted) with no logic change.
6. Local-first: full local replica, offline read and write, relay optional.

## Non-goals (for this design)

- MLS group key agreement (see "Deferred").
- A native mobile app. Phones use AgentMesh through AI apps' remote MCP connectors (see "Mobile and external
  AI apps").
- Server-side search. Recall always runs where keys live.

## Model

### Layers

| Layer | Identified by | Holds |
| --- | --- | --- |
| **Account** | email (a name, not an authority) | account root key, recovery key |
| **Profile** | random ID | profile key; a security boundary ("who can read") |
| **Scope** | random ID, tree under a profile | scope key; `common`, then projects |
| **Device** | Ed25519 keypair in OS secure storage | wrapped scope keys it is granted |
| **Agent** | sub-key certified by its device key | no data keys (see Broker) |

Rules:

- **Email = name, keys = authority.** Email is never sufficient to obtain keys. Store it hashed or inside
  encrypted metadata, never in plaintext on the relay.
- Device ↔ profile is many-to-many. Removing a device never removes a profile.
- **When to create a profile vs a project:** create a new profile or project key only when *who may read*
  differs ("should any agent or device not see this?"). Otherwise use tags, `--project` or `--task`, which
  are filters, not security boundaries.

### Scope tree

```
profile: work
├── common            (tips and general knowledge)
├── project: client-a
│   └── task: migrate-db      (filter only, no key)
└── project: client-b
```

- Each scope has a **random** key, wrapped by its parent's key (not derived), so one branch can be rotated
  alone.
- Holding a parent key opens all descendants; holding `client-a` opens only `client-a`.
- **Cryptographic boundary at profile and project.** Task and tag are filters only. A truly secret task is
  promoted to its own project.
- Object paths in storage use opaque IDs, never project names.

### Read vs write

- **Read** = holds the scope key.
- **Write** = holds only the scope's public key (sealed box) plus its own signing key, so it can encrypt
  but not decrypt. Example: a phone can append to a project inbox without being able to read it.

### Events

- Append-only, named by **ULID**, so no per-node integer ranges and no write/overwrite conflicts.
- Each event is encrypted to its scope key and signed by the writing agent's sub-key.
- Edits are new revisions. Logical conflicts (the same fact revised on two devices) keep both revisions
  and resolve by an explicit rule or user choice.
- Agents write to the **narrowest** scope they are working in by default. Nothing flows into `common`
  automatically; a tip is **promoted** explicitly (copied and re-encrypted).
- Recall defaults to one profile; cross-profile recall must be explicit; cross-profile writes are never
  automatic.

### Sensitivity labels

Orthogonal to scope: `general`, `internal`, `secret`.

- Set **at write time**, from (1) the scope's label set once by the user, (2) automatic detection by the
  broker (secret/PII patterns, for example gitleaks or detect-secrets rules, Presidio), (3) the agent.
- Labels may be raised automatically; lowering requires user confirmation.
- **Credentials are not stored.** On detection they are redacted and replaced by a reference to a password
  manager entry.

## Memory broker

A local daemon on every device (like ssh-agent):

- The only process holding decrypted keys. Agents never receive data keys.
- Agents connect over a local socket; the broker identifies the agent, checks its grants, and answers
  only from scopes the agent may read. Every query is logged.
- Keeps **one local index per agent view**, so agents running as the same OS user cannot read each other's
  scopes through a shared SQLite file.
- Works fully offline: recall from the local index; writes go to an encrypted outbox.
- Must auto-start (extend `platform_service.py`). If it is down, agents cannot recall.
- Optional later: issues short-lived capability tokens (Biscuit/UCAN style) to sub-agents. Tokens verify
  offline; revocation reaches offline devices only on sync, so expiry must be short.

## Identity and user presence

Principle: **the device the user already unlocks is the identity** (the passkey model). Ask only for what the
user already does, never for something to remember.

- Device keys live in Secure Enclave / TPM / OS keychain (extend `os_unlock`). Secure Enclave supports P-256,
  not Ed25519, so the hardware key wraps the Ed25519 signing key.
- **Biometric or device PIN required for:** adding or removing a device, granting or revoking an agent,
  using the recovery key, export, deleting a profile, cross-profile or bulk reads, first access by a new
  agent, reading `secret`-labelled data.
- Not required for routine recall: agents must run unattended.
- One approval opens a short window (for example 15 minutes). Target: prompts about weekly, not daily.
  Log every trigger to tune rules and avoid rubber-stamping.
- No OAuth for a single user. Optional later, one-time email verification (magic link or OIDC) issues a
  signed email ↔ account-key attestation, needed only for sharing profiles with other people or for a remote
  MCP connector endpoint, which must still never hold data keys.

## Device enrollment

1. New device: `agentmesh join` generates its keypair and shows a pairing request.
2. Existing device: receives the request, verifies it, and the user picks which profiles/scopes to grant.
3. Approve (biometric). The approver wraps the granted scope keys to the new device's public key, writes
   them to the keyring, and records the device in a registry signed by the approver.
4. New device downloads the latest snapshot and catches up.

Pairing channels share one approval flow:

| Channel | Use |
| --- | --- |
| **QR code** | primary when one side has a camera; carries the full key fingerprint, so no manual compare |
| **6-digit code** | universal fallback; both screens show the code, the user confirms they match |
| **LAN / mDNS** | offline pairing when both devices are nearby |
| **Bluetooth** | deferred until a native mobile app exists |

Special cases:

- **First device:** creates the account and profile keys and the recovery key.
- **Agents on a device:** the device certifies an agent sub-key automatically; no per-agent approval.
- **Write-only phone:** receives only scope public keys and its own signing key; losing it leaks nothing.
- With a Worker, a new device needs no storage token; it signs requests and the Worker checks the registry.

## Keyring

- Format: **age** (multi-recipient). Wrapped scope keys are stored next to the data.
- Behind a `Keyring` interface so the distribution mechanism can change later (for example MLS).
- **Remove a device:** create a new key epoch for affected scopes, wrap only to remaining devices. Data the
  removed device already downloaded cannot be recalled; full re-encryption is an optional, heavier step.
- **Delete a profile:** destroy its keys (crypto-shredding); storage objects become unreadable.
- **Archive:** stop writes, keep reads.

## Recovery

- The recovery key is the most important secret: losing it (with all devices) loses the data; leaking it
  exposes every profile.
- Split with Shamir, 2-of-3 (for example paper, hardware security key, passkey via WebAuthn PRF where
  supported). Never store it on a daily device or in a synced folder.
- Keep at least two enrolled devices so recovery is rarely needed.
- Using the recovery key alerts every device, forces rotation of all keys and issues a new recovery key.
- A `recovery test` command verifies a backup without touching live data, with periodic reminders.
- Extends `identity_backup.py`.

## Storage

Interface with three operations only: `put` (new immutable object), `get`, `list` (by prefix). No locks,
conditional writes, server events or server-side search, so any backend works.

Backends:

- **Filesystem**, including the existing Syncthing folder (keeps today's transport working).
- **S3-compatible:** Cloudflare R2 (default), Backblaze B2, self-hosted Garage or SeaweedFS.
- Optional dual-write to two backends for redundancy. Migration is `rclone sync` plus a config change;
  objects are already encrypted.

Sizing (one person, 5 agents, 4 devices, ~1,000 events/day at ~1.5 KB): ~45 MB/month storage, ~30k writes,
~120k reads, ~170k lists with 1-minute polling. Well inside R2's free tier (10 GB, 1M Class A,
10M Class B per month as of 2026-10).

Constraints:

- Put **memories and summaries** in the log; keep raw transcripts local to the ingesting device.
- Periodic encrypted **snapshots/compaction** so new devices do not download hundreds of thousands of small
  objects.
- Never sync live SQLite, WAL or SHM files through a cloud drive.

## Cloudflare Worker (optional)

An enhancement, never a requirement: if it is down or removed, devices sync directly with storage or via
Syncthing and lose only these features.

1. **Gatekeeper:** verifies device signatures against the registry; per-device write paths; no deletes;
   instant per-device revocation. Devices hold no bucket-wide token.
2. **Change notification** instead of polling.
3. **MCP gateway for external AI apps** (see "Mobile and external AI apps"): OAuth for each app, recall
   forwarded to whichever trusted device is online, and remember queued in an encrypted outbox when none is.

The Worker must never hold profile or scope keys and must never perform recall.

## Mobile and external AI apps

Amended 2026-10-21 (owner decisions). This replaces the earlier phone-PWA capture idea. The goal is to use
AgentMesh memory from AI apps on any device, including the mobile apps: Claude (claude.ai, Desktop, iOS,
Android), ChatGPT, Gemini, Codex and others. They connect through one standard **remote MCP server**
(Streamable HTTP over HTTPS, OAuth 2.1 per the MCP authorization spec). Nothing is vendor-specific. An app
works as soon as its vendor supports remote MCP connectors.

- **Gateway:** the Cloudflare Worker is the MCP endpoint and the OAuth server. It holds **no data keys**.
- **Recall is answered by any online trusted device**, Mac or Windows. Each device runs a loopback-only bridge
  that reaches the Worker through one Cloudflare Tunnel with a connector on every device. When no device is
  online, recall returns a fixed `agentmesh_offline` answer. Keys never move to the cloud.
- **Remember works at any time:**
  - With a device online, the bridge writes through the broker. The normal detectors, labels and grants apply.
  - Otherwise the Worker seals the request to every bridge device's published outbox key, signs it, and
    stores it in an outbox. The first device online drains it through its broker.
  - The outbox ID makes a drain that runs twice harmless.
- **One identity and grant set per app:**
  - Each app is approved separately. The owner approves with presence on a trusted device after a 6-digit code
    shown during the app's OAuth flow; no passwords.
  - The approval is a signed connector policy record that every device applies. It holds the readable
    profiles, the remember scope (default: each profile's `inbox`) and the limits.
  - Revoking one app never affects the others. Audit entries name the app.
- **`secret` is off by default** for every app. Results labelled `secret` are never returned, and neither is
  their existence or count. The owner can open `secret` for **one app for a limited time** with presence; it
  closes automatically.
- **Cross-profile recall stays off** unless the policy explicitly allows it.
- **Accepted exposure:** whatever an app recalls or remembers is visible to that app's vendor (Anthropic, OpenAI,
  Google), and to Cloudflare in transit through the Worker and the Tunnel. Storage still holds only ciphertext,
  so this exposure is limited to what was actually used through the connector. Prompt injection inside an app
  is the main risk, so default limits stay narrow.
- **Not chosen:** keys held in the Worker or on a VPS, because a cloud compromise would expose all history.
  An always-on home device enrolled as another trusted device remains an option later, and it uses the same
  bridge.

### Status page (2026-10-22 amendment)

The read-only status UI lives only at `STATUS_ORIGIN` (for example
`https://status.<owner domain>`), a Workers Custom Domain attached to the same
Worker. Protect that hostname with a Cloudflare Access self-hosted application,
Allow policy for the owner's email only, and one-time PIN login. Access on the
whole `agentmesh-gateway.<sub>.workers.dev` hostname would break deployed OAuth
and MCP, so `ORIGIN` stays separate; the existing `bridge.<domain>` Tunnel hostname
keeps its Access Service Auth policy.

Access is defense in depth. On every status GET the Worker independently verifies
`Cf-Access-Jwt-Assertion`: WebCrypto RS256 against the team's
`https://<ACCESS_TEAM_DOMAIN>/cdn-cgi/access/certs`, five-minute in-memory public-key
cache and refresh on unknown kid, exact issuer, audience containing
`STATUS_ACCESS_AUD`, expiry/issued-at and any not-before with 30-second skew, and
case-insensitive membership in comma-separated `STATUS_ALLOWED_EMAILS`. No cookie
fallback; any verification failure is a fixed 403. These four new settings are
public vars, with no new secrets. Unset `STATUS_ORIGIN` disables status (404).
Only `GET /status` and `GET /status.json` exist on the status origin; OAuth, MCP,
admin and well-known routes are 404 there. Status paths are 404 on `ORIGIN`.
Responses have no CORS, `no-store`, a script-free default-deny CSP with
`frame-ancestors 'none'`, `X-Frame-Options: DENY`, and no-referrer.

Each load sends one signed `{"tool":"status","arguments":{}}` bridge request
within the existing nine-second budget. The all-zero 32-hex `app_id` is reserved
exclusively for this operation. The bridge verifies signature, timestamp and nonce
as usual, then returns only device ID, current time, last control sync, last drain
time/result and ULID-derived outbox cursor time; it never resolves policies or
creates a broker client. Other uses of the reserved ID and status with real app
IDs are denied. A Tunnel can choose any healthy connector: the UI shows observed
history, not an enumeration, and marks only the live responder. Only status probes
are recorded (tool calls write nothing). Observations expire from KV after seven days.

Both representations expose only metadata: outbox count/ages (never ciphertext
reads, never a pending-count claim), bridge drain metadata, verified app policies
with opaque IDs and OAuth client names/grant presence, active secret-window
expiry, fixed error-code counts for 24 UTC hourly buckets, prefix counts and latest
event upload. Error counters are best-effort, non-atomic KV writes with 48-hour
TTL. R2 scans paginate and stop at 10,000 objects; partial results are labelled,
and policy/window resolution is withheld if control inventory may be incomplete.
HTML escapes every dynamic value. No memory content, ciphertext, tokens, codes,
queries or bodies are logged or rendered. See the
[status plan](../plans/2026-10-22-phase6-status-page.md) for setup and verification limits.

## Local-first variants

- **Default:** encrypted log + relay (this design).
- **Next:** hybrid — Syncthing between nearby devices and R2 for remote ones at the same time; event names
  are ULIDs and content is encrypted, so the same event can arrive by either route.
- **Evaluate later:** CRDT libraries (Automerge, Yjs, cr-sqlite) or local-first frameworks (Evolu, Jazz) if
  concurrent edits prove frequent. Most are TypeScript; check maintenance status.

## Reuse and retirement

- **Reuse:** Ed25519 signing (`signed_packets.py`), `key_rotation.py`, `identity_backup.py`, `os_unlock`,
  `install_join.py`, `recall_memory.py`, ingest, Insight. Check `profile-memory/` (profile-owned knowledge,
  immutable revisions, scoped exchange) as a possible base; not yet reviewed in depth.
- **Retire after migration:** `RANGES` and per-node integer IDs, most of the packet conflict machinery,
  bootstrap ZIP distribution.

## Deferred

- **MLS (RFC 9420):** designed for messaging; forward secrecy conflicts with readable history for new
  devices, and it needs an ordered delivery service (would make the Worker mandatory). If adopted, use it
  only to distribute scope-key epochs, behind the `Keyring` interface. Triggers: sharing profiles with
  other people, many devices, frequent revocation.
- Proxy re-encryption and attribute-based encryption: not planned.
- Bluetooth pairing and a native mobile app.

## Phases

1. Model: account, profile, scope tree, device and agent identity, event format (ULID, signature,
   encryption), labels.
2. Storage interface with filesystem and S3 backends.
3. Broker with per-agent local indexes; recall and ingest unchanged for callers.
4. Enrollment (QR, 6-digit code, LAN), keyring, recovery with Shamir.
5. Migration from the current SQLite databases into events (building on `sqlite_export.py`).
6. External AI apps (including mobile): the MCP gateway Worker with per-app OAuth and policy, device bridges
   behind one Tunnel, and the encrypted remember outbox.
7. Retire `RANGES` and packet conflict machinery; optional hybrid and dual-write.

## Open questions

- Profile taxonomy for the first rollout (work / personal / per client?).
- Exact logical-conflict rule for concurrent revisions.
- Snapshot cadence and format.
- Which secret/PII detectors run in the broker, and their false-positive budget.
- Whether to build on `profile-memory/` or start a new package.
