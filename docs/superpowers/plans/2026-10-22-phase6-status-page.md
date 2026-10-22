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

The status hostname serves only GET `/status` and GET `/status.json`; all other
paths/methods are 404, including `/mcp`, `/authorize`, `/token`, `/register`,
`/admin/*`, and `/.well-known/*`. Status paths are always 404 on `ORIGIN`.
Responses use `Cache-Control: no-store`, `default-src 'none'`,
`frame-ancestors 'none'`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
and no CORS. No scripts or inline styles are needed; dynamic HTML is escaped.

One live signed gateway-to-bridge probe per load uses the existing nine-second
budget and body `{"tool":"status","arguments":{}}`. The 32-zero app ID is reserved
exclusively for status, not an OAuth app. The normal signature, timestamp and nonce
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
full replica convergence. Status performs no R2 writes; only telemetry writes KV.
Never log or render memory content, ciphertext, tokens, codes, queries or bodies.

## Verification limits

Local fakes and locally generated keys cannot verify live DNS, Workers Custom
Domain attachment, Access PIN delivery or policy configuration, the actual team's
JWKS, production KV timing, Cloudflare routing/failover, or a replaced Mac app.
No live deployment or network verification was performed. Full suite results and
any sandbox limitations are recorded in the Phase 6 REPORT's status amendment.
