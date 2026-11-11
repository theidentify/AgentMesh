# One-click join and the local web client: design

Status: draft for review, 2026-11-10. Builds on the
[team workspace design](2026-11-04-team-workspace-design.md) (W0–W4 and W6 merged) and its
[real-use test](../plans/2026-11-08-team-workspace-real-use-test.md). Nothing here is implemented.

## Motivation

Joining a workspace today takes about a dozen CLI commands on the member's side:

- `broker init`
- a remote with R2 credentials obtained out of band
- three or more `broker sync` round trips
- `workspace join --invite`
- `broker grant`
- `presence open`
- installing agent rules by hand

It also takes at least one `invite accept` in a terminal on the admin's side. Presence words fail
with a Thai keyboard layout (all five failures in the cross-machine test). Agents are never told how
to use tasks; the shared-memory rules even forbid writes. Nobody can see where a join stands.

## Goal

- **Member:** click a link → **Join** → passkey (Touch ID / Windows Hello). Nothing else.
- **Admin:** a notification → **Approve** → passkey.
- **Both:** one local web client, on macOS and Windows, that shows each workspace's join state,
  tasks and notes.

The security model of the team workspace design is unchanged. Keys never leave devices. The relay
sees only ciphertext. Nothing is accepted while nobody is watching. There is no central service.

## Decisions (owner, 2026-11-10)

1. **One broker home per workspace**, at `~/.agentmesh/ws/<id8>/` (short, because socket paths are
   limited to 104 bytes on macOS). Each home has its own account, device, broker and remote. This
   replaces "one shared storage location per device" for workspaces. A person's personal homes and
   remotes are never touched by a join.
2. **The local web client is the main client** on both platforms: plain HTML/JS served by the
   supervisor on `localhost`, with no keys in the browser. The macOS Control app keeps the menu-bar
   icon, notifications and an **Open AgentMesh** item. No WASM and no WebRTC (see "Not chosen").
3. **Passkeys replace typed presence words** in the client: Touch ID on macOS, Windows Hello on
   Windows. The CLI keeps typed words.
4. **The invite link carries no storage credentials.** It carries two presigned URLs: one PUT for
   the join request, one GET for the answer.
5. **Admin approval issues a per-member storage credential**, sealed to the member's device and
   placed in the answer slot. A pluggable **issuer** creates it: Cloudflare R2, AWS IAM, MinIO and
   other S3-compatible stores, none (shared folder), or manual. Removing a member deletes their
   credential. See "Pluggable issuers and join channels".
6. **The client shows join state without unlocking.** Task titles, owners, handoffs and notes need
   a passkey unlock, as the Memory window does today.
7. **One new agent key per workspace**, plus a separate rules file that teaches the task loop.
8. **The recovery kit is not required at join time.** The client reminds the member until it is
   saved. Losing a workspace home costs a re-invite, not data the company cannot recover.
9. **Storage is split by home: each home writes only its own area and reads everyone's.** A
   workspace's objects live under `homes/<device>/` (one prefix per home, or one bucket per home
   where the provider cannot scope to a prefix, as on R2). A member's credential can write only its
   own area, so no member can delete or overwrite another's objects. The workspace's provider
   account is used for nothing else, so "read every bucket in the account" means "read this
   workspace". See "Per-home storage".
10. **Local-first holds for onboarding too.** The issuer and the join channel only carry things.
    Who may join is decided only by signatures checked on devices and by a person's passkey. Swapping
    the issuer or the channel never weakens that. A team with no cloud at all (a shared folder, or
    LAN only) can still join with one click.

## Flow

```
Admin client                    Workspace bucket (S3-compatible)      Member client
------------                    ---------------------                 -------------
Invite people → passkey
  invite entry (existing W3)
  presign PUT  join/req/<slot>
  presign GET  join/ans/<slot>
  link ─────────────── out of band (chat, email) ──────────────────▶  click link
                                                                      Join → register passkey
                                                                      new home, account, device
                                   ◀── PUT join/req/<slot> ────────── request bundle
notification "Bob asks to join"
Approve → passkey
  check use (existing)
  member entry + key grants
  issuer: create credential
  seal creds to Bob's device
  PUT join/ans/<slot> ───────────▶
                                   ── GET join/ans/<slot> (poll) ───▶ unseal, store creds in OS store
                                   ◀════════ normal broker sync ════▶ keys, tree, agent key, rules
                                                                      "Ready"
```

### The link

- `agentmesh://join#<base64url(wire(token))>`. The fragment keeps the token out of HTTP logs when a
  browser opens a fallback page. It is still a secret: whoever holds it can submit a request, which
  the admin must then approve.
- Token v2 fields:
  - The v1 fields: `workspace`, `invite`, `secret`, `owner_account`, `owner_fingerprint`, `role`,
    `expires_at`.
  - `owner_record`: the owner's public account record. The joiner can verify the fingerprint before
    it has any storage access.
  - `remote`: the workspace's storage, which is not secret. Either `filesystem` (a shared or
    synced folder, by path hint), or `s3` with `provider`, `endpoint`, `region`, `bucket`, `prefix`
    and `path_style` (needed by MinIO and some self-hosted stores).
  - `channel`: how the request and answer travel (see "Join channels"). For the default `storage`
    channel, `request_url` and `answer_url` are SigV4 presigned PUT and GET URLs, valid until
    `expires_at`. SigV4 allows at most 7 days, which matches the invite limit, and every
    S3-compatible store accepts them.
  - `label`: optional. The name the admin typed, shown on both sides.
- **One link, one slot, one person.** Bulk invites generate N links, for example as a CSV. A link
  with several slots would let two joiners overwrite each other's request, because the PUT URL
  cannot check for an existing object.
- The URLs are presigned with a separate "invite signer" credential, never with the issuer
  credential. Where the issuer can create one, it is limited to `join/` (or to the bucket on R2).
  Revoking the signer kills every outstanding link of that workspace at once (the emergency stop).
  Revoking one invite still uses the existing signed revocation.

### The join request

The member's device has no storage access yet. Everything `workspace join --invite` would publish
therefore goes into one request object, PUT to `request_url`:

- the account record and device certificate;
- the profile recipient (`publish_recipient`);
- the invite use, signed by the device and by the invite key (existing `use_record`);
- the device's sealing public key for the answer.

Size is capped at 64 KiB by the admin's reader. A presigned PUT cannot enforce a size, so a
too-large or malformed request is ignored and shown as `invalid`.

### Approval

The admin's supervisor lists `join/req/` (it has full storage credentials) and shows each request
as pending in the client and as a notification. **Approve** with a passkey then:

1. Runs the existing `check_use` and member-entry logic of `invite accept`, for this one request
   only. A new `invite accept --request SLOT` replaces "accept everything pending".
2. Writes the bundled records to their normal paths and grants the invite's scopes (`regrant`, or
   `grant_write` for contributors).
3. Asks the workspace's issuer for a credential named `agentmesh-<ws8>-<device8>`. It can write only
   the joining home's area (`homes/<device>/`, or its own bucket on R2, which the issuer creates
   first) and read every home's area of this workspace. The issuer returns the credential and an
   opaque `handle` for revoking it later. Credentials are per device, because each home is one
   device.
4. Seals `{access_key_id, secret_access_key}` to the request's sealing key and writes it to the
   answer slot through the join channel. It also deletes the request.
5. Records the issuer kind and `handle` in a signed `registry/storage-credentials/<ws>/<account>.json`.
   Any admin device that holds the issuer credential can then revoke it later.

**Reject** writes a signed refusal to the answer slot, so the member's client stops waiting and
says why.

**The issuer credential** lives in the admin's OS store as `keychain:` / `wincred:`
`org.agentmesh.ws-<id8>.issuer`. It is loaded only inside an approve or remove action, after the
passkey check. An admin device without it can still approve membership. The member then gets
`waiting_for_storage` until an issuer device, or a manual entry, supplies the storage part.

**Removing a member** (`member --role none`) also revokes their storage credential. Without an issuer
credential on that device, or with the `manual` issuer, the result reports
`storage_credential_not_revoked`. The client keeps showing it as an open item until someone marks it
done.

## Per-home storage

### Layout

Today every writer puts objects at shared paths (`events/<profile>/<scope>/<id>.json`, `keys/…`,
`registry/…`) in one remote. A workspace remote instead holds one area per home:

```
<prefix>/homes/<device_id>/events/<profile>/<scope>/<id>.json
<prefix>/homes/<device_id>/keys/…
<prefix>/homes/<device_id>/registry/…
<prefix>/homes/<device_id>/head.json        # random generation, rewritten after each upload (the only mutable object)
<prefix>/join/req/<slot>, join/ans/<slot>   # written by the admin side, see "Join channels"
```

On R2 an area is a bucket (`<ws8>-<device8>`) instead of a prefix. The paths inside an area are
unchanged. The local replica keeps its current flat layout: the remote adapter maps
`homes/<device>/X` ⇄ `X` on download and writes only under its own area on upload.

### Sync

- **Upload:** only to this home's area. A key found in any area counts as present, so objects
  received from others are never uploaded again.
- **Download:** the union of every area. Objects are immutable and verified by signature exactly as
  today, so the order and the source do not matter.
- **No author-area check (changed while implementing, 2026-11-11).** The draft also refused an
  object whose author differs from its area. Two things decided against it:
  - **One-click join breaks it.** The admin's device uploads a joiner's request records, such as
    its device certificate and invite use. The joiner has no credential at that point. Those
    records then sit in the admin's area, and the joiner never re-uploads them.
  - **The check adds no trust.** A copy elsewhere is the same signed object, so readers already
    verify it as in the flat layout.

  What the layout must give is that a credential cannot delete or overwrite another home's
  objects, and that holds.
- **Known homes** are the `homes/` folders of the remote, found with one delimiter listing (S3
  `Delimiter='/'`, or the folder's sub-directories) at each listing. A new member's area is read as
  soon as it exists. A removed member's area stays readable, and the existing verification rules
  decide which of its events still count.
- **Cost and `head.json`.** Listing every area on every listing grew with the number of homes:
  408 LIST requests per sync at 50 homes, about USD 79 per device per 30 days on R2. So each home
  rewrites `homes/<device>/head.json` with a random generation after every upload. Filesystem
  storage allows that one mutable object, like heartbeat slots.
  - **At the start of each sync attempt**, a reader finds the areas with one folder listing and
    reads every head with a GET (Class B).
  - **It re-lists only areas whose generation changed**, plus areas with no head. Every other
    listing is answered from the last one, including a shorter prefix answering a longer one.
  - **One wrapper per device and remote** lives for the whole process, so `broker run`'s
    per-attempt S3 client keeps the cache.
  - **Measured** with `tests/broker/measure_per_home_cost.py` (30,000 events, one new event, 60 s
    polling):

    | Homes | LIST/cycle | GET/cycle | USD/30 days per device (Class A+B) |
    |---:|---:|---:|---:|
    | flat | 8 | 1 | 1.57 |
    | 1 | 9 | 3 | 1.80 |
    | 5 | 9 | 7 | 1.86 |
    | 20 | 9 | 22 | 2.09 |
    | 50 | 9 | 52 | 2.56 |

- **Maintenance:** compaction or pruning, where any exists, touches only the home's own area.

### What it fixes

| | Shared layout (W2–W4) | Per-home layout |
|---|---|---|
| A member deletes others' objects | possible wherever the credential is bucket-wide | impossible: write covers only its own area |
| Removing a member | revoke credential; their old objects stay unless someone deletes them | revoke credential; their area stays read-only for everyone |
| Who wrote this object | signature only | signature only (the area is not a proof) |
| R2 | needs a bucket per workspace and still allows DoS between members | a bucket per home in a dedicated account; no DoS between members |

`folder` remotes use the same layout, but nothing enforces it: anyone who can write the folder can
write any area, as with the filesystem backend today.

### Scope

- The per-home layout applies to **workspace homes created by this design** only.
- Existing personal homes and the shared-layout test workspaces keep the flat layout. A home records
  its layout in `remote.json` (`"layout": "per-home"` or `"flat"`), and nothing migrates
  automatically.

## Pluggable issuers and join channels

Two small interfaces keep onboarding independent of any one vendor. Both are chosen per workspace in
signed workspace settings (`storage.issuer`, `join.channels`), so every device agrees on them.

### Issuers

An issuer has three operations, all run only on an admin workstation after a passkey:

- `create(name, own_area, workspace_areas)` returns `{access_key_id, secret_access_key, handle}`.
  The credential writes `own_area` and reads `workspace_areas`.
- `revoke(handle)`.
- `capabilities`: `area` (`prefix` or `bucket`) and `expiry`.

| Issuer | How it creates a member credential | Area | Issuer credential it needs | Status |
|---|---|---|---|---|
| `manual` | The admin pastes a key pair into the Approve sheet, made in the provider console to the rule above. | provider-dependent | none | J1 |
| `none` | No credential. The remote is a shared or synced folder (filesystem backend, Syncthing). | prefix, unenforced | none | J1 |
| `cloudflare-r2` | Creates the home's bucket, then an account API token with two policies: Bucket Item Read on all buckets in the (dedicated) account, Bucket Item Write on the home's bucket. Key ID = token `id`, secret = SHA-256 of `value`. | bucket | Account API Tokens Edit, R2 bucket create | J2 |
| `aws-iam` | IAM user with an inline policy: `s3:GetObject`/`ListBucket` on `PREFIX/*`, `s3:PutObject` on `PREFIX/homes/<device>/*`; plus an access key. | prefix | IAM user and access-key management for one path | J2 |
| `minio` | Service account with the same two-statement policy (MinIO admin API). | prefix | MinIO admin user limited to that bucket | J2 |
| `s3-iam` | Generic IAM-compatible API (for example Wasabi, Ceph RGW), same shape as `aws-iam`. | provider-dependent | provider-specific | later |

`manual` credentials that cannot follow the rule, such as a single bucket-wide key, work, but they
give up the protection of decision 9. The client then marks the workspace "shared write" in red.

- Provider claims (prefix scoping, token APIs) are checked per provider in J2 before an issuer is
  marked supported. For this draft only these Cloudflare facts were checked: tokens can be created
  by API, scoped per bucket or account, and derive S3 keys as above. A single R2 token holding two
  policies (read all, write one bucket) is still unverified.
- **Key formats generalize.** `r2creds.py` accepts only R2's 32/64-hex pair today. It needs a
  per-provider kind: AWS uses a 20-character key ID and a 40-character secret, and MinIO accepts
  free-form keys within bounds. The credentials reference stays `keychain:` / `wincred:`, and
  secrets still never touch argv or disk.
- **No issuer API in the provider?** Use `manual`. The admin creates the key in the provider console
  and pastes it once into the Approve sheet. The member's side stays one click.

### Join channels

A channel carries the request to an admin device and the sealed answer back. It never decides
anything: both objects are signed or sealed, and the admin still approves with a passkey.

| Channel | How | Needs internet | Notes |
|---|---|---|---|
| `storage` (default) | Presigned PUT and GET slots in the workspace bucket. | yes | Works with any S3-compatible store. |
| `folder` | Request and answer files in a `join/` folder of the shared folder remote. | no | For `none`-issuer workspaces. The folder must be writable by joiners, which is true of the remote itself. |
| `lan` | The existing pairing LAN flow (QR or 6-digit code on the admin's screen) carries the request and answer between two devices on the same network. | no | Both people in one place; the code doubles as an out-of-band check. |
| `file` | The client saves the request as a file to send by chat, and the admin's client opens the answer file the same way. | no | Last resort. Two manual transfers, still no typing. |

A link may list several channels. The member's client tries them in order, so a link made in the
office offers `lan` first and `storage` second.

**After joining, nothing depends on the channel or the issuer.** The member's home syncs with the
remote like any other device. Recall, tasks and notes work offline against the local replica.

### The member's device after approval

1. The client waits for the answer on its channel. For `storage`, it polls `answer_url` every 5 s for
   2 minutes, then every 60 s until `expires_at`. It unseals the answer and stores any credentials
   in the OS store as `keychain:` / `wincred:` `org.agentmesh.ws-<id8>`.
2. It writes the remote to the home's `remote.json`:
   - `filesystem`, or `provider`, `endpoint`, `region`, `bucket`, `prefix` and `path_style`;
   - `layout: "per-home"`, and the bucket naming rule on R2;
   - the credentials reference, never the secret.

   `broker run` and `broker sync` read `remote.json` when no remote flags are given.
3. The supervisor registers and starts a broker unit for the home, `broker@<id8>`, on launchd or
   Task Scheduler, the existing backends.
4. After the first sync brings the keys and the tree, it creates the workspace agent key. The key
   reads and writes the invite's scopes for a member, and only writes for a contributor. It never
   has `allow_secret`.
5. It opens the agent's `first_access` window and installs the workspace rules.
6. It sets the join state to `ready`.

The member's own home is created with one placeholder private profile, `local`, because
`BrokerState.create` needs at least one. Nothing is written to it.

## Join state

Each home keeps `joins.json`, a content-free record per workspace:

- `workspace`, `label`, `role`, `invite`, `state`, `updated_at`;
- `reason`, set when the state is `failed` or `rejected`;
- `recovery_saved`.

States: `requested → waiting_for_admin → (waiting_for_storage) → syncing → ready`, or `rejected`,
`expired` and `failed`. A supervisor op, `workspace.joins` (tier 0), returns the list for every home.
No unlock is needed, because it holds no titles and no memory.

## The local web client

### Serving

- The supervisor serves the client on `http://localhost:<port>`. The port is fixed at install time
  and kept in `settings.local.json`, because WebAuthn binds to the origin, port included.
- **Session:**
  - Control app, the tray and `agentmesh open` open the client with a one-time token in the URL
    fragment.
  - The page exchanges it for an `HttpOnly`, `SameSite=Strict` cookie.
  - Requests without the cookie get only a "open from the menu" page.
- **Request checks:**
  - `Host` must be `localhost:<port>`.
  - `Origin` must be `http://localhost:<port>` on every state-changing request.
  - No CORS headers; a strict CSP with no inline scripts and no external resources.
- Every page action is an ordinary supervisor op, audited with the client as caller, exactly like
  the Control app. The client has no authority of its own.

### Passkeys

- The client reuses `passkeys.py`. Today it is fixed to an HTTPS RP ID with a dot (Insight's remote
  host), so it needs a `localhost` profile:
  - RP ID `localhost`;
  - expected origin `http://localhost:<port>`;
  - each credential stored per home.
- **Each approval binds its challenge to the action:** for example, a hash of the request slot,
  account and scopes, as `connector.approve` does with its code hash. The page shows the same
  summary the challenge covers.
- **Exact origin checks matter.** Every localhost app shares RP ID `localhost`. A page on another
  port could ask for an assertion, but its `clientDataJSON.origin` carries the other port and is
  refused.
- **Join is the first registration.** A fresh home has nothing to protect yet, so registering the
  first passkey at join is trust on first use. The real checks are the owner fingerprint in the link
  and the admin's approval.
- **The CLI fallback** still asks for typed words. It shows "switch your keyboard to English" when
  the input contains non-ASCII characters.

### Screens

| Screen | Locked (no unlock) | Unlocked (passkey, idle-locks after 5 min) |
|---|---|---|
| Workspaces | each home: label, role, join state, last sync, recovery reminder | — |
| Tasks | counts by status, live and expired leases, conflicts | titles, owners, `next`, `handoff`, `blocked_on` |
| Notes | count "for me" | note text, author |
| Admin | pending requests (label, role, request time), storage credentials to revoke | Approve / Reject; invites: create link, list, revoke |
| Agents | per home: agent key present, rules installed | — |

Unlocked reads go through the broker with the workspace agent key, so grants, labels and audit
apply. `secret` content is never shown in the client.

## Agent rules

- **Files:**
  - Claude: `~/.claude/rules/agentmesh-workspace.md` and its `command.json`.
  - OMP and Codex: `~/.agents/skills/agentmesh-workspace/SKILL.md`.
- Each file has a managed block. Text outside the block is preserved. A file without the marker is
  refused, never overwritten.
- **One table per workspace:** label, agent key path, and the project folders mapped to it. Folders
  are picked in the client, and the default is none. An agent in an unmapped folder uses no
  workspace.
- **The task loop:**
  - Before starting, run `tasks --mine`, then `tasks --status open`.
  - Claim the task with a lease.
  - Renew the lease while working.
  - On stopping, write a `handoff` and set the task to `open` or `review`.
  - Use `note` for questions to other members.
  - After a conflict, the loser stops and writes nothing further on the task.
- **Write limits:** only task and note ops. The rules state plainly that this overrides the
  shared-memory rule's "do not write memory" for those two ops only. `remember` stays off for agents
  (see open questions).
- **Trust:** recalled `pending` items are hypotheses, not facts (already in the team workspace
  design).
- The existing `agentmesh-shared-memory` rules and their generator, which is not in this repository,
  are not changed.

## Not chosen

- **Credentials in the link.** A leaked link would give bucket write before any approval.
- **One shared storage credential per workspace.** Removing anyone would mean rotating it for everyone.
- **Several remotes in one home.** This is cleaner in the long run, but it is a large change to
  sync and keyring code. Separate homes give the same isolation now.
- **WASM crypto in the browser.** Keys would live in browser memory or IndexedDB instead of the OS
  store. WASM may fit later for a browser-only contributor, which holds only public keys.
- **WebRTC.** It needs a signaling server and usually TURN, which is a central service. Agent
  coordination is asynchronous through the log by design.
- **A hosted web client.** It would contradict "no central service" and "keys only on devices".
- **One vendor's API as a hard dependency.** Cloudflare is one issuer among several. A workspace with
  no issuer API, or no cloud at all, still joins in one click through `manual`, `folder` or `lan`.

## Known limits

- **A member can still fill or wipe their own area.** Deleting their own objects hides them from
  members who have not synced yet, but cannot touch anyone else's. Quotas already cap event counts.
  Object versioning, where the provider has it, keeps deleted objects recoverable.
- **Sync cost grows a little with the number of homes:** one head GET per home per sync (USD 2.56
  per device per 30 days at 50 homes, against 1.57 flat). An area without a head, written by an
  older client, is listed on every sync.
- **The issuer credential is powerful.** On Cloudflare it can mint any token in its account. On AWS
  and MinIO it can be limited to one path, but it still creates credentials. A stolen admin device
  with an unlocked OS store can use it. That is why it is loaded only after a passkey, the provider
  account is dedicated, and an IP condition is recommended where the provider offers one.
- **Presigned PUT has no size or overwrite control.** Hence the 64 KiB reader cap, one slot per link
  and the signer kill switch.
- **`folder` and `none` trust the folder's own access control.** Anyone who can write the shared
  folder can submit requests or delete objects, exactly as with the filesystem backend today.
- **The admin must be online to approve.** This is a deliberate choice (2026-11-10).
- **Passkey on a server-class device.** There is none; servers still approve nothing.

## Phases

| Phase | Content |
|---|---|
| **J1** | Token v2 and link encoding; `remote.json`; request and answer objects; `storage`, `folder` and `file` channels; `manual` and `none` issuers; per-provider credential kinds in `r2creds.py`; `invite accept --request`; the CLI `workspace join-link` (one command, resumable, writes `joins.json`). Usable on Windows today. |
| **J1b** | Per-home storage: the remote adapter's area mapping (`storage/areas.py`), union download, folder discovery, `--layout per-home` in remote flags, `remote.json` and links. Per-home `head.json` generations and a listing cache, measured at 1, 5, 20 and 50 homes. Done 2026-11-11. R2 bucket-per-home areas come with the `cloudflare-r2` issuer (J2). |
| **J2** | Issuer interface with `cloudflare-r2`, `aws-iam` and `minio`, each verified against the provider first; signed credential records; removal revokes; invite signer and kill switch. |
| **J3** | Supervisor: one broker unit per workspace home, the `workspace.*` ops, notifications. |
| **J4** | Local web client: serving, session, `localhost` passkeys, the screens above; `agentmesh://` handlers (`CFBundleURLTypes` on macOS, `HKCU\Software\Classes` on Windows); the `lan` channel over the existing pairing flow. |
| **J5** | Workspace agent rules and folder mapping. Can land right after J1. |
| **J6** | Control app reduced to the menu bar, notifications and Open AgentMesh. Memory window moves to the client. |

## Open questions

1. Should agents get `remember` in workspace scopes when `review.staged` is on? Their writes would
   wait for review anyway.
2. Should the Memory window move to the web client in J6, or stay native on macOS?
3. Answer polling: is 60 s after the first 2 minutes acceptable, or should the admin side also nudge
   through a chat adapter?
4. ~~Lease renewal~~: answered. Any `task update` by the holding agent while the task is held sets
   `lease_until` to now + lease (`tasks.successor`), so the rules renew with an update.
5. Should a workspace home share the member's existing Insight snapshot, or keep its own beside the
   home (current behaviour for homes outside `~/.agentmesh`)?
