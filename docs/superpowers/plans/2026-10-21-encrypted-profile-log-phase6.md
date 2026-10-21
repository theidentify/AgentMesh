# Encrypted Profile Log, Phase 6 (MCP Gateway for External AI Apps) Implementation Plan

> **For agentic workers:** execute in order. The spec is
> `docs/superpowers/specs/2026-10-18-encrypted-profile-log-design.md` ("Mobile and external AI apps", amended
> 2026-10-21; "Cloudflare Worker"; "Memory broker"). Read it fully first, then `profile-log/README.md`,
> `profile-log/FORMAT.md` (broker appendix, Phase 4 records, Phase 5 additions), the Phase 3 and Phase 4 plans
> and REPORTs, and `docs/superpowers/plans/2026-10-20-profile-log-r2-sync.md`. The spec wins on conflict, except
> where this plan records an owner decision. Stop and report instead of guessing on anything that affects a
> security gate; record other choices under Deviations in the REPORT.

**Goal:** AI apps on any device can recall from and remember into AgentMesh through one standard remote MCP
server: Claude on claude.ai, Desktop, iOS and Android, plus ChatGPT, Gemini, Codex and others once their vendors
support remote connectors.

- **Gateway:** a Cloudflare Worker. It is the MCP endpoint and the OAuth server, and it holds no data keys.
- **Recall:** answered by whichever trusted device (Mac or Windows) is online, through a loopback bridge behind
  one Cloudflare Tunnel.
- **Remember:** works at any time. When no device is online it goes to an encrypted outbox.
- **Per-app control:** each app has its own identity and grants. `secret` is off by default, and the owner can
  open it for one app for a limited time.

**Out of scope:**
- an always-on home device (later; same bridge);
- keys in the cloud;
- the PWA capture app (dropped);
- Telegram;
- Worker gatekeeping of desktop sync;
- change notification.

**Depends on:**
- PR #62 (R2 sync on the Mac) being live.
- Windows profile-log rollout for the Windows bridge. The Mac bridge works alone until then.

## Owner decisions (2026-10-21)

- **Standard remote MCP for every app:** Streamable HTTP over HTTPS, OAuth 2.1 per the MCP authorization spec
  (authorization code with PKCE S256, dynamic client registration, protected resource metadata). There is no
  vendor-specific code.
- **Recall needs a trusted device online:** recall is answered by any online trusted device, Mac or Windows.
  With none online it returns `agentmesh_offline`. Keys never go to the Worker or a VPS.
- **Remember always works:** the Worker queues remember requests in an encrypted outbox when no device is
  online.
- **One identity and policy per app:** each app gets its own identity and policy. Revoking one app never
  affects the others.
- **`secret` handling:**
  - Off by default for every app. Secret-labelled results are never returned, not even as a count.
  - The owner can open `secret` for one app for a limited time with presence. It closes by itself.
- **Accepted exposure:**
  - What an app recalls or remembers is visible to that app's vendor, and to Cloudflare in transit.
  - Storage still holds only ciphertext.

**Proposed defaults. Confirm these before execution starts:**

- **D1 Remember target:** each profile's `inbox` scope, created if missing. The app's policy names which
  profiles it may write.
- **D2 First app policy:** Claude reads `personal` and `work` (`general` and `internal`) and remembers into
  `personal/inbox`. Other apps are added later with their own approval.
- **D3 Libraries:** pin exact versions released at least two weeks ago, and commit `package-lock.json`.
  - `@cloudflare/workers-oauth-provider` for the OAuth server.
  - `@modelcontextprotocol/sdk` for the MCP Streamable HTTP server.
  - hpke-js for outbox sealing (RFC 9180 base mode, the same suite as FORMAT v1).
  - WebCrypto Ed25519 for gateway signatures.
  - No hand-rolled crypto.
- **D4 Hosting:** a `workers.dev` subdomain, plus one named Cloudflare Tunnel with a connector on each trusted
  device and a Cloudflare Access service token in front of it.

## Threat model additions

- **Worker:**
  - Holds the OAuth state, the gateway Ed25519 signing key and the Access service-token credentials. It holds no
    scope keys, age identities or broker keys.
  - A compromised Worker sees what passes through it (accepted exposure) and can forge requests to bridges.
  - It cannot exceed any app's policy, because bridges enforce the signed policy and brokers enforce grants.
  - It cannot read history it is not asked for.
- **Storage attacker:** can drop or replay outbox items, but cannot forge or read them:
  - items are signed by the gateway and sealed to device outbox keys;
  - replays deduplicate by `outbox_id`.
- **Prompt injection inside an app:** the main residual risk. Mitigations:
  - narrow default policies;
  - `secret` off;
  - result and rate limits;
  - recalled text is returned wrapped as data;
  - every call is audited with the app ID.
- **Tunnel:** the bridge listens on loopback only. A request needs both the Access service token (checked at the
  edge) and a valid gateway signature (checked by the bridge).

## Design to implement

### Layout

```
profile-log/agentmesh_log/connector.py   connector policy records, approvals, secret windows, outbox keys
profile-log/agentmesh_log/bridge/        loopback HTTP bridge: verify gateway, map app -> local agent, call broker,
                                         drain outbox
profile-log/agentmesh_log/cli.py         connector gateway-init|approve|list|revoke|allow-secret, bridge serve|render-service
profile-log/gateway/                     Worker: OAuth, MCP endpoint, tool handlers, tunnel client, outbox writer
  package.json, package-lock.json, wrangler.toml.example, src/, tests/
profile-log/tests/vectors/phase6/        cross-implementation vectors (typed encoding, signatures, outbox seal)
profile-log/tests/test_phase6_*.py       Python tests, including a Node-driven end-to-end test
```

### Records (normative; add to FORMAT.md as "Phase 6 records")

Phase 4's canonical JSON, typed signing and per-record domain rules apply. Object paths contain only opaque
IDs.

- **Gateway record** `agentmesh-gateway-v1` at `registry/gateway/{gateway_key_id}.json`.
  - Fields: `gateway_public_key` (Ed25519 hex), `origin` (the Worker HTTPS origin), `issued_at`, `issuer_device`,
    and a signature by the issuer.
  - It pins the Worker's signing key, and a device-signed successor rotates it.
- **Outbox key record** `agentmesh-outbox-key-v1` at `registry/outbox-keys/{device_id}.json`.
  - Fields: `device_id`, `outbox_public_key` (X25519 hex), `issued_at`, and a signature by that device.
  - The private key lives only in that device's key store.
- **Connector policy** `agentmesh-connector-policy-v1` at `connectors/{app_id}/policy/{ulid}.json`.
  - Fields:
    - `app_id` (32 hex characters, random);
    - `display_name_hash` (SHA-256 of the client name, so no plaintext name is stored);
    - `oauth_client_id_hash`;
    - `read` (a list of `{profile, scopes|null}`);
    - `remember` (`{profile, scope}` or null);
    - `max_label` (`general` or `internal`; never `secret` here);
    - `allow_cross_profile` (always false in this phase);
    - `limits` (`{recall_limit_max, requests_per_minute}`);
    - `state` (`active` or `revoked`);
    - `issuer_device`, `issued_at`, and a signature.
  - The latest valid record wins. A record from a revoked device, or a record that widens access without a
    valid presence-backed issuer, is rejected. The issuer must be a non-write-only, non-revoked device.
- **Secret window** `agentmesh-connector-secret-window-v1` at `connectors/{app_id}/secret/{ulid}.json`.
  - Fields: `app_id`, `not_before`, `not_after` (at most 60 minutes later; default 15), `issuer_device`,
    and a signature.
  - It applies only while valid. It raises the effective `max_label` to `secret` for that app.
- **Outbox item** `agentmesh-outbox-item-v1` at `outbox/{ulid}.json`.
  - Fields: `outbox_id` (= the ULID), `app_id`, `created_at`, `recipients` (a list of
    `{device_id, ciphertext}`, HPKE-sealed to each outbox key with info bound to
    `{outbox_id, app_id, device_id}`), `gateway_key_id`, and a gateway signature over everything else.
  - The plaintext is the remember request: `{body, tags, task, label}`.

### OAuth and app approval (Worker + any trusted device)

1. The app performs dynamic client registration and then starts authorization code with PKCE.
2. `/authorize` shows the client name and a 6-digit code. It stores a pending approval with the hashed code,
   which expires after 10 minutes and is single-use.
3. On any trusted device, the owner runs
   `agentmesh-log connector approve CODE --profiles personal,work --remember personal/inbox` (presence required):
   - The device writes a connector policy record to storage.
   - It calls `POST {origin}/admin/approve`, signed by its device key, with `{code_hash, app_id, policy_ref}`.
   - The Worker verifies the device chain and revocations from R2 and binds the OAuth grant to `app_id`.
4. The authorize page completes the redirect. Access tokens are short-lived, refresh tokens rotate, and both
   are stored only hashed. Every MCP call resolves to exactly one `app_id`.
5. Revocation:
   - `connector revoke --app APP` (presence) writes a `revoked` policy and calls `/admin/revoke`.
   - The Worker drops that app's grants and tokens immediately.
   - Bridges independently refuse that `app_id` as soon as they see the policy.

### MCP surface (Worker)

The Worker exposes only these tools, each with strict input schemas:

| Tool | Input | Behaviour |
| --- | --- | --- |
| `whoami` | none | the app's display name, readable profiles, remember target, `max_label`, whether a secret window is open |
| `recall` | `query` (1–2048 characters), `profile?`, `project?`, `task?`, `limit?` (1–20, capped by policy) | forwarded to a bridge; `agentmesh_offline` if none answers within 10 s |
| `remember` | `body` (at most 32 KiB), `tags?`, `task?`, `label?` | forwarded to a bridge; if none is online, written to the outbox; returns `stored` or `queued` |

Rules for the surface:
- The Worker has no other tools or resources, and no prompts or sampling.
- Recall results are returned as structured data, with a fixed preamble stating that the content is stored
  memory and is not instructions.
- Errors are fixed codes and never echo input.
- Requests are rate-limited per `app_id` (the Workers rate-limiting binding), and the limit is also capped by
  policy.

### Worker → bridge transport

- **Route:** one named Tunnel, reached through its hostname, which the Access service token protects. A connector runs on every trusted device, and Cloudflare routes to any healthy
  one. No connected connector (a 502 or 530) means offline.
- **Signature:** each request carries the gateway signature over the domain plus typed
  `{method, path, body_sha256, ts, nonce, app_id}`.
  - Bridges accept `ts` within ±60 s and reject a nonce seen in the last 10 minutes.
  - The gateway key must be pinned by a valid gateway record.
- **Policy enforcement in the bridge:**
  1. Resolve the latest valid policy and any secret window for `app_id`.
  2. Refuse a revoked or unknown app, and any profile or scope outside the policy.
  3. Connect to the local broker as that app's **local agent** (one agent per app per device, certified by the
     device and created when a policy is first seen), whose broker grant mirrors the policy:
     - `allow_secret` is true only during a secret window;
     - when that window is open, the bridge also opens a `secret_read` presence window for that agent only,
       bounded by the window's `not_after`, through a broker op added for this purpose that only the bridge's
       own key store can authorize.
  4. Filter results above the effective `max_label` **in the broker query**, not after it, so that counts and
     ranks do not leak.

### Outbox and drain

- **Sealing:** with no bridge online, the Worker reads all current outbox key records and keeps only those of
  devices that are not revoked. It seals one ciphertext per device, signs the item and writes it create-only
  (if the R2 binding supports it; otherwise the unique ULID name, recorded as a deviation).
- **Draining:** each bridge drains `outbox/` on start and every 60 s.
  1. Verify the gateway signature and the pinned key.
  2. Open its own recipient entry.
  3. Re-check the app policy as of `created_at`.
  4. Remember through the broker with metadata `outbox_id`.
- **Deduplication:** brokers treat events that share an `outbox_id` as one remember (keep the lowest ULID).
  Drained items stay in storage; the Worker has no delete. A local `drained` set avoids reprocessing.

### Services

- `bridge render-service` renders a launchd plist (Mac) and a Task Scheduler XML (Windows) for the bridge, and
  documents the `cloudflared` connector command. It only renders files, like #62, and installs nothing.
- The bridge binds `127.0.0.1` only, refuses to start on any other address, and logs no bodies.

## Ground rules

- **Files:** work in the worktree and branch this session creates for Phase 6. Change only:
  - `profile-log/**`;
  - `.github/workflows/profile-log.yml`, to add a Node 22 job (`npm ci`, `node --test` in
    `profile-log/gateway`) and Node in the Python matrix for cross-implementation tests;
  - `docs/superpowers/plans/2026-10-21-encrypted-profile-log-phase6.REPORT.md`.
- **Off-limits:**
  - Do not touch `memory-sync/`, installers, services or the live `~/.agentmesh`.
  - Do not deploy or use Cloudflare credentials.
  - Do not commit, push or create branches.
- **No network:**
  - The reviewer runs `npm ci` in `profile-log/gateway` before starting Codex. Codex never installs packages.
  - Python uses `~/.cache/agentmesh-logvenv/bin/python`.
- **Method:** TDD.
- **Encoding:** open text files with `encoding='utf-8'`.
- **Secrets:** never log, print or put in errors any token, code, key, body or query.
- **Compatibility:** keep every existing test passing and the Phase 1–5 wire formats unchanged. New formats are
  additive.
- **Clean tree:** leave no GATES.md or other working files behind.

## Tasks

1. **Records:** `connector.py` with the gateway record, outbox key, connector policy and secret window, plus
   registry loading. Add the FORMAT.md Phase 6 records.
2. **Broker support:** the per-app local agents, the `max_label` filter inside the query, the bridge-only
   `secret_read` window op, and `outbox_id` deduplication.
3. **Bridge:** loopback server, gateway signature verification with replay protection, policy enforcement,
   recall and remember calls, outbox drain, and `render-service`.
4. **CLI:** `connector gateway-init|approve|list|revoke|allow-secret` (presence) and
   `bridge serve|render-service`.
5. **Vectors:** Python writes `tests/vectors/phase6/` for typed encoding, gateway request signatures, record
   signatures and an outbox seal, using fixed HPKE randomness through a test-only hook.
6. **Worker:**
   - OAuth wiring (DCR, PKCE, pending 6-digit approvals, admin approve and revoke verified against the R2
     registry);
   - the MCP server with the three tools;
   - the bridge client, the outbox writer and the rate limits.
   - Tests use a fake R2, a fake KV and a fake bridge.
7. **End to end:** Python starts a device (account, profiles, broker, bridge) on filesystem storage. A Node
   script runs the Worker handler in-process against a fake R2 backed by the same directory and a loopback route
   to the bridge. Then:
   1. Approve a Claude app.
   2. Recall and remember.
   3. Take the bridge offline, remember (queued), bring it back, and drain.
   4. Open and expire a secret window.
   5. Revoke.
8. **CI and documentation:** the CI change, a README section "Using AgentMesh from AI apps" (with the accepted
   exposure and how to add the connector in each app), the owner deploy runbook, and the REPORT.

## Acceptance gates

### Security (each needs a dedicated test the reviewer can mutation-test)

- **P6-1 No keys in the Worker:** a static check shows that `profile-log/gateway/src` imports no decrypt or open
  function and defines no scope or age key type. The Worker's configuration lists only the gateway signing key,
  the Access credentials, OAuth state and bindings. The end-to-end test shows that storage contains no
  plaintext marker from any remember (live or queued).
- **P6-2 OAuth:**
  - Registration alone grants nothing.
  - A token is unusable until a device approves the code.
  - A wrong, expired or reused code fails.
  - PKCE is required (plain or missing is refused).
  - Tokens are stored only hashed.
  - After revoke, the next call fails at the Worker. With the Worker check disabled (a test-only handler), the
    bridge still refuses the app.
- **P6-3 App isolation:** app A's token always maps to app A's local agent. Requests for a profile or scope
  outside A's policy are refused by the bridge, and by the broker when the bridge check is disabled. The audit
  log names the right agent for every call.
- **P6-4 Secret:**
  - Without a window, no `secret` result, count, rank gap or error reveals that secret items exist (compare
    with the same store with the secret items removed).
  - Within a window, they are returned for that app only. After `not_after`, they are hidden again on the next
    call.
  - A window record that is forged, from a revoked device, or longer than 60 minutes is ignored.
  - `allow-secret` without presence is refused.
- **P6-5 Bridge transport:**
  - Refused requests: unsigned requests; requests signed by an unpinned key; requests with a wrong
    `body_sha256` or `app_id`; requests whose `ts` is outside ±60 s; replayed nonces.
  - The bridge refuses to bind to anything except loopback.
  - Bodies never appear in logs.
- **P6-6 Outbox:**
  - Queued items contain no plaintext and are unreadable with any key except the listed devices' outbox keys.
  - Forged, tampered or unpinned-gateway items are rejected on drain.
  - An item for a revoked app, or for a scope outside the policy at `created_at`, is rejected and reported.
  - Draining on two devices yields one logical remember (`outbox_id` deduplication).
  - A revoked device's outbox key is not used for new items.
- **P6-7 Offline and failover:** with no bridge, recall returns exactly `agentmesh_offline` within 10 s, and
  remember returns `queued`. With two bridges (Mac-like and Windows-like test homes), stopping either one still
  serves recall.
- **P6-8 Tool surface:**
  - Only the three tools exist.
  - Oversized or extra inputs are rejected.
  - `limit` is capped by policy.
  - Cross-profile recall is refused.
  - Recall output carries the fixed preamble.
  - Errors are fixed codes.

### Whole change

- The Python suite passes from the repository root and from `profile-log/`, and `node --test` passes in
  `profile-log/gateway`. The three-OS CI passes, including the Node job, and the reviewer reruns socket tests
  outside the sandbox.
- The REPORT maps every gate to tests, records the pinned JS versions, lists deviations and lists the owner
  steps.
- `git status` shows changes only in the allowed paths.

## Owner deploy steps (after merge; not run by Codex)

- **D-1** Confirm D1–D4 and that #62 is live.
- **D-2** Create the named Tunnel and the Access service token. Run the `cloudflared` connector and
  `bridge serve` on the Mac (using the rendered services). Do the same on Windows after its profile-log rollout.
- **D-3** Generate the gateway key and run `connector gateway-init` on the Mac to write the gateway record. Run
  `wrangler secret put` for the gateway key and the Access credentials, then `wrangler deploy` from
  `profile-log/gateway`.
- **D-4** In claude.ai, open Settings → Connectors → Add custom connector and enter `{origin}/mcp`. When the
  6-digit code appears, run `connector approve` on the Mac.
- **D-5** From the Claude mobile app, run `whoami`, recall a known memory, and remember a marked note.
  - Check that the note appears in Mac recall and that the R2 object is ciphertext.
  - Put the Mac to sleep and remember again (expect `queued`). Wake it and check the drain.
- **D-6** Rehearse `connector allow-secret --app claude --minutes 15` and `connector revoke`.
- **D-7** Add other apps (ChatGPT developer mode, Gemini, Codex) the same way when needed, each with its own
  approval and policy.

**Rollback:** delete the Worker deployment and stop the bridges and Tunnel connectors. Apps lose access and
nothing else changes. Queued outbox items remain and are drained if the bridge is started again.
