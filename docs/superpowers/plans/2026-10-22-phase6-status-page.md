# Phase 6 status page — 2026-10-22

Branch: `claude/phase6-status-page` from main `630ad11`. Local TDD only; no
commits, deployment, network access or npm dependency changes in this work.

## Acceptance gates and implementation plan

1. P6-S1–S3: generate local RSA assertions and fake JWKS; prove valid login,
   fail-closed validation, no cookie fallback, host/method isolation and headers.
2. P6-S4–S5: prove fixed hourly error counters and honest bridge observations.
3. P6-S6–S8 (Python): prove signed reserved status operation, no policy or broker
   access, replay/signature/time rejection, and drain/cursor metadata.
4. P6-S9–S13: exercise key rotation/expiry/fetch failure, bounded R2 pagination,
   escaped HTML/metadata projection, nonblocking best-effort telemetry, expired
   grant filtering and reusable bounded control reads.
5. Run the full Python suite and gateway `node --test`, leaving existing tests
   untouched. Review docs, dependency diff, branch/base and working-tree hygiene.

Tests were added before implementation: five Node status gates failed against
missing routes/counters; two Python gates failed against unsupported status.
The misuse rejection tests already passed. Follow-up tests cover boundary cases.

## Hosting and owner setup (instructions only; not executed)

The deployed gateway at `https://agentmesh-gateway.<sub>.workers.dev` serves
OAuth and MCP for Claude and ChatGPT. Access can protect that hostname only as a
whole, which would break those clients. Keep it as `ORIGIN` and leave its behavior
unchanged. Keep the one Tunnel hostname `bridge.<domain>` under Service Auth.

1. Attach `status.<owner domain>` to the **same Worker** as a **Workers Custom
   Domain**. The commented `[[routes]]` / `custom_domain = true` example in
   `profile-log/gateway/wrangler.toml.example` documents the route configuration.
2. Create a Zero Trust Access **self-hosted application** for that entire status
   hostname. Enable one-time PIN and an **Allow policy for the owner's email
   only**. Do not apply it to workers.dev, or use a bypass policy.
3. Copy the application's AUD tag and the team hostname into public Worker vars:
   `STATUS_ORIGIN=https://status.<owner domain>`,
   `ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com`,
   `STATUS_ACCESS_AUD=<application AUD>`, and
   `STATUS_ALLOWED_EMAILS=<owner email>`. The last field is a comma-separated,
   case-insensitive allowlist; keep it to the owner's email for this setup.
   There are **no new secrets**. Unset `STATUS_ORIGIN` disables status with 404.
4. Deploy the Worker through the owner's normal release process, then swap/update
   the Mac app so its bridge knows the new `status` operation. Neither step was
   performed as part of this change.
5. Sign in at `/status`; check `/status.json`, live metadata, and isolation in
   both directions. Verify existing OAuth/MCP apps still work on `ORIGIN`.

## Security and data contract

Access is defense in depth. Every supported status request independently verifies
`Cf-Access-Jwt-Assertion`; the Worker never trusts the edge policy alone or falls
back to the `CF_Authorization` cookie. WebCrypto verifies RS256 with the team's
`https://<ACCESS_TEAM_DOMAIN>/cdn-cgi/access/certs`. Public keys are cached in memory
for five minutes; unknown kid refreshes an existing cache. JWKS fetches have a
three-second timeout and refuse redirects. The exact issuer must be
`https://<ACCESS_TEAM_DOMAIN>`, audience must contain `STATUS_ACCESS_AUD`, `exp`
and `iat` must be valid integer dates, and `nbf` is checked when present. Time
checks allow 30 seconds of skew. The signed email must be in the configured
allowlist. Every validation failure is 403 with the same `Forbidden` body.

The status hostname serves GET `/status`, GET `/status.json`, and POST
`/status/action`; all other
paths/methods are 404, including `/mcp`, `/authorize`, `/token`, `/register`,
`/admin/*`, and `/.well-known/*`. Status paths are always 404 on `ORIGIN`.
Responses use `Cache-Control: no-store`, `default-src 'none'`,
`frame-ancestors 'none'`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
and no CORS. No scripts or inline styles are needed; dynamic HTML is escaped.

One live signed gateway-to-bridge probe per load uses the existing nine-second
budget and body `{"tool":"status","arguments":{}}`. The 32-zero app ID is reserved
for status and the strictly narrowing admin operation, not an OAuth app. The normal signature, timestamp and nonce
gates precede dispatch; no policies are resolved and no broker client is created.
The bridge returns only `device_id`, `now_ms`, `last_control_sync_ms`,
`last_drain_ms`, `last_drain_result` (`stored`, `rejected`, `errors`), and
`outbox_cursor_ms`. Sync/drain times are process-local observations and initially
null; the cursor comes from persisted state. A failed drain records one error.

The Worker stores successful status-probe observations at `status:bridge:<device_id>`
with a seven-day TTL. Tool calls write no bridge record (they cannot identify the
responder, and KV writes are budgeted).
Only a probe can identify its responder: the Tunnel may select any healthy device
on each request. The page marks the live responder and reports probe failures as
`offline`, with reason only `offline`, `denied`, or `unavailable`. History is not a
complete device list or evidence that previously seen devices remain online.

Outbox count and oldest/newest age use ULIDs or R2 upload metadata, without reading
outbox content. They do **not** identify pending or drained objects; the page also
shows the answering bridge's drain time/result and cursor. Apps use verified
connector policies: app ID, state, max label, opaque read/remember IDs, limits,
issued time, current grant presence via `listUserGrants`, and registered client
names via `lookupClient`. Secret windows reuse `secretWindow()` and show expiry.
No profile/scope names reach the Worker.

Only `denied`, `invalid_request`, `unavailable`, `agentmesh_offline`,
`rate_limited`, and `invalid_token` are counted. Best-effort background KV
read/modify/write uses `status:err:<code>:<yyyymmddhh>`, TTL 48 hours. The display
sums the current and previous 23 UTC hourly buckets. KV eventual consistency and
concurrent writes can lose counts; telemetry never delays or fails an app request.

R2 lists paginate across `registry/`, `keyring/`, `keys/`, `events/`,
`connectors/`, and `outbox/`, stopping after 10,000 objects. Counts and latest event
`uploaded` time are explicitly partial when capped. Policy/window resolution is
withheld on a partial inventory because missing revocations would be misleading.
App display caps at 100 apps and 10 grant pages each; bridge history caps at 1,000
entries/10 KV pages. Caps are surfaced. Metadata neither proves decryption nor
full replica convergence. Status GETs write only KV observations and CSRF tokens. Actions also use existing
conditional R2 nonce/completion claims. The Worker never signs a policy or window.
Never log or render memory content, ciphertext, tokens, codes, queries or bodies.

## Verification limits

Local fakes and locally generated keys cannot verify live DNS, Workers Custom
Domain attachment, Access PIN delivery or policy configuration, the actual team's
JWKS, production KV timing, Cloudflare routing/failover, or a replaced Mac app.
No live deployment or network verification was performed. Full suite results and
any sandbox limitations are recorded in the Phase 6 REPORT's status amendment.

## Part 1: access-reducing actions (P6-S10 onward)

The cloud relays, it never grants. Access email-OTP is sufficient only for these
three reductions. Each supported POST repeats the Worker's Access JWT validation.
`POST /status/action` exists only on `STATUS_ORIGIN`; no admin API, OAuth or MCP
route is exposed there. No new secrets, dependencies or deployments are needed.

HTML renders escaped, small forms for pending requests (bounded to 100 entries /
10 KV pages), active apps, and active windows. JSON stays metadata-only. The
hidden CSRF field is 32 random bytes encoded as hex; only its SHA-256 lookup key
is stored as `status:csrf:<hash>` with `{sub,email,exp}`, TTL 900 seconds. A
nonempty signed Access `sub` is required for forms and actions. POST requires
exact `Origin`, `Sec-Fetch-Site: same-origin` when supplied, matching signed
sub/email, and an unexpired token. Duplicate fields and extra action arguments
are denied. A conditional R2 claim makes consumption single-use even under
concurrent or stale KV reads; KV is then deleted. The CSP permits forms to self
and no scripts. Auth assertions, OAuth codes/tokens and request bodies never
reach HTML or logs; the hidden anti-CSRF value is the necessary form-only value.

The gateway signs the existing bridge transport with the reserved all-zero app
ID and body `{"tool":"admin","arguments":{...}}`. Exact argument sets are
`{action:"reject",code_hash}`, `{action:"revoke",app_id}` and
`{action:"close_secret",app_id}`. Ordinary app IDs, approval, widening, window
opening and extra arguments are refused on the device. The device refreshes
controls and checks its current account membership. Close does not require
reader status or scope authority; the other actions retain their existing checks.
It writes the existing broker audit format with null agent, `connector_<action>`,
`ok`/`denied`, and no payload fields.

- Reject signs a `connector-admin` envelope containing only `code_hash`. The
  common Worker admin path verifies the pinned device chain, current revocations,
  signature and nonce before claiming the browser completion slot and deleting
  `pending:<code_hash>`. Even a stale approval resurrected in KV cannot complete.
- Revoke requires an existing policy, copies it with state revoked, signs and
  publishes it, then signs the usual revoke envelope. The Worker verifies both
  envelope and referenced revoked record before using the existing grant cleanup.
- Close requires an active policy/window. It signs an additive secret-close
  record, applies it locally, clears broker secret grants through a bridge-only
  operation, and publishes the same bytes remotely. A remote failure returns a
  fixed denial while preserving the local reduction. Both verifiers ignore
  windows containing a valid close timestamp; later windows remain independent.

### Threat notes and failure limits

The browser cannot supply policies, scope authority, timestamps or admin
signatures. The relay response must match the requested action and target before
it can enter the shared admin verifier. A consumed CSRF token remains consumed
on failure; reload before retry. Confirmation is a plain page with a status link.
Revoke/close storage and OAuth cleanup are not one distributed transaction;
partial failure can reduce locally before reporting failure. Existing remote
control polling bounds other devices' observation; there is no claim of an
instantaneous global transaction. R2 claims are durable replay tombstones, using
the existing gateway-state mechanism. KV expiration alone is never the replay
oracle. The settled close-issuer rule is intentionally narrowing-only: both
verifiers accept the signature of any account device non-revoked at `closed_at`,
including a write-only device, without reader or scope-ancestry checks. Revocations
are resolved through the account registry. A signer revoked at closure is ignored;
revocation after a valid close cannot undo that reduction and reopen access.
Closes cannot create, reopen or extend windows. No public ancestry evidence is
needed, and the Worker's former read-grant ancestry logic has been removed.

TDD includes initially failing forms/admin/record tests, shared Python-to-Node
close vectors (write-only signers, revocation timing and no widening), stale
approval resurrection, concurrent
CSRF consumption and remote-publication failure. Final counts and sandbox limits
are recorded in the Phase 6 REPORT. No commit or deploy is part of this work.
