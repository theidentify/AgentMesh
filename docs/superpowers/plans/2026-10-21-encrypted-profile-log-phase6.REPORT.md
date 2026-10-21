# Encrypted profile log Phase 6 REPORT

Date: 2026-10-21. Tasks 1–8 implemented in the existing worktree, without a
commit, branch, stash, deployment, network access or package installation.
Reviewed by Claude: see "Reviewer final verification" at the end. Three-OS CI is recorded in the PR.
The plan, whole spec, README, FORMAT, Phase 3/4 plans and reports, and supplied
reference-only R2 plan were read. No code from PR #62 is required here.

## Implementation

- Signed connector controls and scope-bounded policy resolution; gateway key
  rotation; device outbox public keys; per-app secret windows.
- Local per-app broker identities and grants, query-time label filtering,
  bridge-only secret windows, encrypted outbox metadata and logical deduplication.
- Loopback-only signed HTTP bridge, timestamp/nonce checks, policy enforcement,
  encrypted outbox drain, and render-only Mac/Windows service definitions.
- Presence-backed connector CLI and explicit filesystem/S3 storage selection.
  Missing inboxes are created and their metadata is encrypted to authorized peers.
- Worker OAuth approval and revocation, stateless Streamable HTTP MCP, strict
  whoami/recall/remember tools, binding and policy rate limits, signed Tunnel
  requests, and HPKE outbox sealing using public keys only.
- Python/JavaScript vectors, dedicated gate tests, a native Python-to-Node
  lifecycle test and a supplemental in-process transport lifecycle test.
- Node 22 job and Node 22 setup/npm ci in all three Python OS jobs; FORMAT,
  README and placeholder-only Wrangler configuration/runbook.

## Decision: issuer authority

The reviewer settled the original issuer-authority question by applying FORMAT's
scope-grant precedent. This is implemented, not an open owner question.
A granting/widening policy requires a non-revoked, non-write-only issuer with
read grants covering every listed read entry and remember scope at issued_at;
null scopes require the profile root. Creating an inbox requires its root.
Narrowing, including revocation, requires a valid reading device but does not
require authority over the affected scopes. Secret windows require grants over
the policy's read entries current at not_before and cannot exceed 60 minutes.
The CLI refuses insufficient authority before writing. Worker administration
checks the device chain and revocations; the bridge independently resolves scope
relationships and grant authority. D1–D4 remain as confirmed in the plan.

## Gate-to-test map

Python paths below are relative to `profile-log/tests/`. JavaScript gate tests
are in `profile-log/gateway/tests/worker.test.js`; their test names contain the
gate IDs. These are executable assertions, not a claim that the reviewer's
independent mutation campaign or deployed acceptance has run.

| Gate | Dedicated evidence |
| --- | --- |
| P6-1 | JS `P6-1 no data-key/decrypt imports in Worker source`; `test_phase6_end_to_end.py::test_p6_e2e` and `test_p6_e2e_inprocess` inspect storage for live/queued plaintext markers; `test_phase6_integration.py::test_p6_6_policy_recheck` checks ciphertext storage. Wrangler example lists signing/Access secrets and public configuration only. |
| P6-2 | JS registration/PKCE/pending/wrong/expired/reused approval, hashed/rotating token and next-call revocation tests; `test_phase6_integration.py::test_p6_2_revoked_bridge` and both end-to-end variants use a test-only Worker-check-disabled route to prove independent bridge denial. |
| P6-3 | JS token-to-app binding; `test_phase6_broker.py::test_p6_3_broker_boundary` bypasses the bridge and checks broker refusal and audit identity; `test_phase6_records.py::test_p6_3_widening_ignored` and `test_p6_3_revoke_honoured`; `test_phase6_cli.py::test_p6_3_approve_no_writes`. |
| P6-4 | `test_phase6_broker.py::test_p6_4_noninterference` compares results, counts, ranks and errors against the same store without secret items; `test_phase6_integration.py::test_p6_4_window_isolation` and `test_p6_4_bridge_only_window_op`; `test_phase6_records.py::test_p6_4_window_validation` covers forged/revoked/overlong/unauthorized-scope windows; `test_phase6_cli.py::test_p6_4_presence`; both lifecycle variants. |
| P6-5 | `test_phase6_bridge.py::test_p6_5_transport` covers unsigned/unpinned/body/app/timestamp/replay failures; `test_p6_5_loopback_logs` covers binding restriction and no body logging; `test_phase6_records.py::test_p6_5_gateway_rotation`. |
| P6-6 | JS offline ciphertext, listed-recipient decryption and revoked-device exclusion; `test_phase6_integration.py::test_p6_6_drain`, `test_p6_6_policy_recheck`, `test_p6_6_two_devices`; `test_phase6_broker.py::test_p6_6_broker_outbox_dedup`; `test_phase6_vectors.py::test_p6_vectors`. |
| P6-7 | JS `P6-7 timeout and healthy Tunnel connector failover` stops either fake connector and tests both stalled fetch and stalled response body under 10 seconds; `test_phase6_integration.py::test_p6_6_two_devices` uses two independent paired device homes and recalls on both; native and supplemental lifecycle tests queue/drain after offline. Real Tunnel routing remains unverified. |
| P6-8 | JS exact tool inventory, oversized/extra inputs, policy cap, cross-profile denial, fixed errors/preamble and both rate limits; Python bridge/broker boundary tests enforce the local boundary independently. |

## Dependency pins and runtime

Supplied dependencies and lockfile versions are unchanged. No dependencies added.

| Package | Version |
| --- | --- |
| @cloudflare/workers-oauth-provider | 1.2.2 |
| @modelcontextprotocol/sdk | 1.32.1 |
| @hpke/core | 1.9.0 |
| @hpke/dhkem-x25519 | 1.8.0 |
| @hpke/chacha20poly1305 | 1.8.0 |
| zod | 4.6.5 |

`node --test` works directly in gateway. `tests/setup.mjs` registers a test-only
resolution hook for `cloudflare:workers`, backed by the small stub under tests;
production imports are unchanged. Local execution uses Node 26; CI specifies
Node 22. Node 26 emits a register deprecation warning; the Node 22-supported API
is intentional. The OAuth library also reports CIMD disabled; DCR is used here.
Crypto uses WebCrypto Ed25519, hpke-js and existing Python cryptography/age.

## Verification

Final full-run results are recorded below. The interpreter is
`~/.cache/agentmesh-logvenv/bin/python`; pytest uses `-q -p no:cacheprovider`.

| Working directory | Command | Result |
| --- | --- | --- |
| Repository root | `python -m pytest -q -p no:cacheprovider profile-log/tests` | 492 passed, 7 skipped, 4 sandbox binding failures; 59.69 s |
| `profile-log/` | `python -m pytest -q -p no:cacheprovider tests` | 492 passed, 7 skipped, 4 sandbox binding failures; 59.69 s |
| `profile-log/gateway/` | `node --test` | 14 passed, 0 skipped, 0 failed |

Baseline before implementation was 466 passed, 5 skipped and 4 socket-binding
failures in each Python invocation. Existing socket tests are not weakened:
`broker/test_isolation.py::test_p3_1_processes`,
`broker/test_transport.py::test_real_transport`,
`migrate/test_processes.py::test_p5_4_concurrent_processes`, and
`test_storage.py::test_s3_custom_http_endpoint` fail when the sandbox forbids
binding. The new native Phase 6 lifecycle test skips with an explicit bind-denied
reason; its supplemental Python-to-Node transport test runs without sockets.
Three opt-in live R2 tests and two Windows-only junction tests are also skipped
locally. The reviewer must rerun native socket tests outside the sandbox.
`git diff --check` passes. Status contains only allowed paths. No unresolved
security-gate question remains; external acceptance is still pending.

## Deviations and limits

- Signed issued_at precedes the object-path tie-break for latest policy selection,
  preventing replay under a newer object name from superseding a later policy.
  Gateway rotation uses the latest valid signed pin; drain resolves its pin at
  created_at so already queued items survive a legitimate rotation.
- Added an encrypted, device-signed `connector-inbox` metadata record, documented
  in FORMAT. Phase 4's initial pairing metadata is immutable; this additive record
  lets a previously paired authorized device resolve an inbox created afterward.
  Existing Phase 1–5 record formats are unchanged.
- The bridge is a trusted local administrative process: it accesses the protected
  device store to provision app agents, resolve controls and hold its outbox key.
  Recall/write operations still traverse the app's authenticated broker client.
- The approval page uses a Continue action after device approval. Approval is
  browser-bound and includes the signed device assertion. Conditional R2 claims
  enforce single use. Conditional R2 minute slots additionally enforce policy
  rate caps; no application delete operation is exposed. Lifecycle management of
  accumulated gateway-state metadata is an owner operational concern.
- HPKE's deterministic sender randomness is exposed only in a test helper. Python
  writes and verifies the vector fixture using that helper, and both runtimes
  verify the resulting signature and ciphertext interoperability.
- Supplemental IPC end-to-end coverage was added because sandbox binding is
  denied. It uses the same actual Worker, bridge and broker implementations;
  it does not substitute for the native transport acceptance test.
- S3 selection uses the existing backend and normal credential configuration,
  without depending on PR #62's not-yet-merged credential loader. Service files
  only render and install nothing; remote storage arguments must be added using
  the runbook after PR #62 integration.
- Shared-memory recall was unavailable because its configured interpreter was
  missing. Supplied repository documents remained authoritative. No scratch
  ledger or GATES.md was created.
- Not verified: real Cloudflare deploy/R2/KV/Access/rate binding behavior, real
  Tunnel health routing, real Claude app/mobile, Node 22 runtime execution here,
  Windows/macOS installed services, three-OS CI, or PR #62 integration. No live
  home, OS keychain, credentials, installer or running service was touched.

## Owner steps after review and merge

All remain pending; none were performed by this implementation session.

- **D-1:** Confirm PR #62 is live and integrate configured remote credentials.
  D1–D4 are already confirmed.
- **D-2:** Create one named Tunnel and Access service token. Run the rendered
  bridge service and cloudflared connector on Mac, then Windows after rollout.
- **D-3:** Generate the gateway signing key; publish its pin using gateway-init;
  configure the public account pin, storage prefix, R2/KV/rate bindings and
  signing/Access Worker secrets; deploy from gateway.
- **D-4:** Add `{origin}/mcp` to Claude custom connectors and approve its displayed
  code on a trusted device for personal/work reads and personal/inbox writes.
- **D-5:** From Claude mobile, test whoami, recall and marked remember; inspect
  ciphertext storage, sleep the Mac, queue another remember, wake and drain.
- **D-6:** Rehearse the app-specific 15-minute secret window and revoke.
- **D-7:** Add other supported apps with their own approvals and policies.

Rollback: remove the Worker deployment and stop bridges/Tunnel connectors.
Queued ciphertext remains; it can drain if an authorized bridge is restarted.


## Owner update: shared prefix and incremental outbox (2026-10-21)

Implemented the owner-approved storage decision. Worker `R2_PREFIX` defaults to
`v1/` in the Wrangler example and must match device `--s3-prefix v1/`. Only
`gateway/src/storage.js` applies it: every R2 get, put and list is wrapped before
OAuth administration, registry resolution or tool dispatch. Empty is valid;
unsafe characters, leading slashes, `..` and missing trailing slashes are refused.
Python adds no second prefix layer. R2 claims/rate counters now live under
`connectors/gateway-state/`; Phase 6 introduces no other top-level prefix.

The bridge persists `connector_outbox_cursor` (the floor ULID of the maximum
considered ULID timestamp), `connector_outbox_reconciled_at`, and the existing
drained set. Each 60-second incremental scan starts strictly after the floor
ULID ten minutes behind that cursor. Startup and every 24 hours perform full
reconciliation, so arrivals older than the overlap are eventually reconsidered.
The cursor ignores future-dated names; malformed items cannot push it into the
future. Overlap uses the drained set and broker outbox_id deduplication. Control
sync no longer lists outbox/ separately. The additive storage signature uses
exactly `start_after`, is keyword-only, and returns sorted logical keys strictly
greater than the bound. S3 passes its bucket prefix plus the bound as StartAfter;
the existing page_size option remains compatible.

Dedicated evidence:

- `test_phase6_end_to_end.py::test_p6_e2e_prefix` runs the actual Node Worker and
  Python bridge/broker with fake R2 rooted at bucket/ and bridge Storage rooted at
  bucket/v1/. It exercises approval, live/queued remember, recall, drain, secret
  window and revoke, asserting that every bucket object is below v1/ and only
  permitted top-level paths exist there. This uses the supplemental IPC transport
  so the test runs even when native socket binding is prohibited.
- Native `test_p6_e2e[v1]` repeats the prefixed lifecycle over loopback when binding
  is available; `test_p6_e2e[root]` retains empty-prefix coverage.
- `test_phase6_cursor.py::test_p6_cursor` asserts incremental listing bounds,
  persisted progress, late-arrival overlap, daily/startup full reconciliation and
  resistance to future cursor poisoning. `test_p6_start_after` verifies both
  backends and the exact prefixed S3 StartAfter request.
- Node `P6-1/P6-6 R2 prefix confines all accessor reads and writes` covers registry
  devices/revocations/gateway/outbox keys, policies/windows, keyring and outbox,
  paginated list key stripping, empty prefix and invalid prefixes.

Rechecked D3 usage: OAuthAuthorizationServer performs DCR, token exchange,
validation, hashed token storage and rotating refresh tokens; authorization
requires S256. McpServer uses the SDK WebStandardStreamableHTTPServerTransport in
stateless mode, with strict zod schemas. The HPKE CipherSuite uses the three pinned
hpke-js libraries; Ed25519 signing/verification uses WebCrypto. Actual OAuth/MCP/
HPKE tests exercise these paths. Each P6-1 through P6-8 has named dedicated tests
as mapped above; tool-surface assertions additionally cover forbidden resource,
prompt and sampling methods and oversized remember bodies.

Final verification: both full Python invocations completed with **492 passed,
7 skipped, 4 unchanged sandbox binding failures** (59.69 s each). Gateway
`node --test`: **14 passed, 0 skipped, 0 failed**. Seven Python skips comprise
two native E2E variants denied loopback binding, three opt-in live-R2 tests and
two Windows-only junction tests. Both supplemental cross-language lifecycle
variants passed, including the non-empty prefix. `git diff --check` passes;
changes remain within allowed paths. No commits or dependency changes.

## Reviewer follow-up: independent gates and operational fixes

Reviewer-reported results before these fixes: Python **498 passed / 5 skipped**
outside the sandbox, including native socket E2E; Node **14 passed**. The reviewer
ran **33 mutations**, reporting survivors for the bridge profile guard,
policy-derived broker reads, HTTP body logging, and the Worker's own PKCE guard.
The redundant pending.completed guard also survived removal, with the separate
single-use claim protecting that boundary; it remains unchanged. These results
are reviewer-run; this agent did not repeat the entire campaign.

New gate evidence in `tests/test_phase6_review.py`:

| Gate / defect | Test |
| --- | --- |
| P6-3 bridge guard | `test_p6_3_bridge_spy`: rejects out-of-policy profile and historical remember scope without contacting a broker client. |
| P6-3 policy-derived grant | `test_p6_3_provision_boundary`: provisions the real app agent, directly queries the broker for allowed/denied scopes and profiles, and verifies audit identity. |
| P6-5 actual HTTP logging | `test_p6_5_http_logs`: loopback POSTs with valid/invalid signatures; capfd/caplog marker assertions; skips only bind permission denial. |
| Private claim sync | `test_p6_sync_private`: excludes gateway-state in both directions. |
| P6-7 refresh interval | `test_p6_7_sync_interval`: two handled requests sync once, remote revoke takes effect after the interval, drain forces refresh. |
| Remote service arguments | `test_p6_service_remote`: CLI options reach Mac plist and Windows task arguments. |

Node `P6-2 Worker independently refuses missing PKCE before pending state` uses a
permissive authorization API to isolate the Worker guard, asserting no pending
KV entry or displayed code. Production still uses the pinned OAuth server.
Node `P6-8 policy counter uses bounded expiring KV and zero R2 writes` checks that
four calls update one counter with a 120-second TTL and no R2 PUTs.

Local targeted mutation reruns killed widened bridge profiles, widened
provisioned reads, and the removed Worker PKCE guard. The real HTTP logging
mutation needs the reviewer's rerun outside this bind-restricted sandbox.
All temporary mutations were restored. No storage/ files were edited during
this follow-up; the reviewer's PR #65 signature alignment is preserved.

Operational changes superseding the earlier implementation notes:

- Policy caps use one OAUTH_KV get/put per app/minute with TTL 120 seconds.
  Concurrent updates are approximate; the Workers rate-limit binding is the hard
  limit. Admin nonce and approval-completion claims retain atomic conditional
  R2 creates (etagDoesNotMatch:'*'). The R2 rate-slot loop is removed.
- Control sync excludes connectors/gateway-state/ in both directions.
- Each request/drain shares one ConnectorRegistry over the local replica.
  Remote sync runs at most once per Bridge(sync_interval=15) seconds, configurable
  on Bridge, and always at drain start. Bridge-side revocation takes effect within
  the interval on its next request; the Worker refuses immediately from R2.
  Secret-window opening can wait for refresh; expiry remains timestamp-enforced.
  E2E lifecycle tests explicitly refresh newly published controls, while the
  dedicated interval test proves the stale-to-revoked transition.
- render-service accepts filesystem/S3 options and passes them to both rendered
  formats, never credentials. README D-2/D-3/D-4/D-6 examples consistently use
  shared storage with --s3-prefix v1/.

Accepted residual risks:

1. Bridge nonce caches are per-process. A request captured inside Cloudflare
   could be replayed to another bridge or after restart within the ±60-second
   timestamp window, requiring the Access token. This risk is accepted and bounded
   by timestamp validation.
2. secret_read operation authority rests on the bridge agent key file, readable
   by same-OS-user code, matching Phase 4's agent-file trust boundary.

Final follow-up verification:

| Run | Result |
| --- | --- |
| Full Python suite from repository root | 497 passed, 8 skipped, 4 baseline sandbox binding failures; 51.49 s |
| Full Python suite from profile-log/ | 497 passed, 8 skipped, 4 baseline sandbox binding failures; 52.84 s |
| Gateway node --test | 16 passed, 0 skipped, 0 failed; 9.56 s |

Skips: two native lifecycle variants and the new real HTTP logging test cannot
bind in this sandbox; three live-R2 tests remain opt-in; two junction tests are
Windows-only. The four baseline socket failures are unchanged and listed above.
Both supplemental cross-language lifecycle tests pass. `git diff --check` passes.
No commits, dependency changes, storage/ edits, or leftover mutation files.

## Reviewer final verification (Claude, 2026-10-21)

Run on the Mac outside the Codex sandbox, so every socket test ran.

| Run | Result |
| --- | --- |
| Full Python suite from the repository root | 504 passed, 5 skipped (3 opt-in live R2, 2 Windows-only junctions) |
| Full Python suite from `profile-log/` | 504 passed, 5 skipped |
| `node --test` in `profile-log/gateway` | 16 passed |

Mutation campaign (each mutant applied alone to a scratch copy, then the named gate tests run):
36 mutants across P6-1..P6-8, the storage prefix, the gateway-state sync exclusion, the control-sync
interval and the rendered service flags. 34 were killed. The two survivors are the two redundant
single-use layers of the OAuth approval: the `pending.completed`/`pending.approval` flags and the
atomic R2 claim (`etagDoesNotMatch:'*'`). Removing either layer alone is still refused by the other.
Cloudflare documents `If-None-Match: *` / `etagDoesNotMatch:'*'` as an atomic create-once, which
the real R2 binding must honour (not verifiable offline).

The storage `list(prefix, *, page_size=None, start_after=None)` change is byte-identical to PR #65.

Not verified here: a real Cloudflare deployment (Worker, KV, rate-limit binding, R2 conditional
puts), a real named Tunnel with an Access service token and two connectors, and the real Claude
connector UI (web, desktop, iOS, Android). These remain owner steps D-1..D-7.

After merging main (#62, #64): connector and bridge commands build R2 storage through the CLI's
`remote_storage`, so they take `--s3-credentials keychain:SERVICE` and never the boto credential
chain, as remote sync does. `bridge render-service` writes only that Keychain reference into the
service. The S3 `list` merge keeps main's error handling plus `start_after`. Python 612 passed,
5 skipped; node 16 passed.

## Deploy fixes (2026-10-21)

Uncommitted fixes on `claude/phase6-deploy-fixes` from main `62ed30a`:

- Approval CSP allows only `'self'` and the parsed HTTP(S) redirect origin;
  rejects CSP delimiters/wildcards and retains the default-src/frame-ancestors restrictions.
- `/mcp` and both protected-resource metadata paths carry the requested wildcard
  CORS headers on all responses, with unauthenticated 204 OPTIONS. Approval and
  admin routes receive no added CORS; Allow-Credentials is not set.
- Connector admin requests use `User-Agent: agentmesh-log/1` (no package version
  is defined), retaining the redirect refusal and fixed errors.
- macOS bridge plists use `<home parent>/logs/bridge.out.log` and `bridge.err.log`,
  matching the broker's AgentMesh-root log convention. Windows XML is unchanged.

Regression tests failed before implementation and passed afterward. No logging
of approval codes, tokens, or request/response bodies was added.

Validation: the requested full Python command finished with **650 passed,
8 skipped, 5 failed**; all five failures explicitly report sandbox-denied socket
binding (`test_p3_1_processes`, `test_connections_offline`, `test_real_transport`,
`test_p5_4_concurrent_processes`, `test_s3_custom_http_endpoint`). The targeted
Python regression run passed both tests. Gateway `node --test`: **19 passed,
0 failed**. `git diff --check` passed. Live Cloudflare/Claude validation remains
an owner deploy step; no commit or deployment was performed.


Follow-up: live Claude recall returned `agentmesh_offline` because fresh R2
connections on the owner's broken advertised IPv6 path took about 10 seconds,
exceeding the Worker's 9-second bridge timeout.

- Request-time controls now read only the local replica and key store. Recall,
  whoami, and remember use local broker access without remote storage calls.
- `serve()` starts a dedicated control-sync thread, allows up to 250 ms for the
  initial attempt, then starts serving even if sync remains blocked. Subsequent
  attempts run every `sync_interval` (default 15 seconds) after completion;
  failures are swallowed without secret logging and retried on the next tick.
  Explicit `controls(force=True)` and drain retain synchronous refresh.
- Drains serialize with a separate drain lock. Remote control transfer and
  outbox list/get calls hold neither the request lock nor the home lock. Local
  state application reloads the key store after transfer and holds the local
  update locks, preserving concurrent provisioning. Broker writes retain the
  broker's own request serialization.
- Bridge-side revocation and new policies take effect within `sync_interval`
  plus one sync duration when sync succeeds; outages extend this until a
  successful retry. The Worker still refuses revoked apps immediately from R2.

TDD: six regression cases failed before implementation. The adapted interval
case verifies requests never sync; force/drain do. Added tests cover signed
recall/whoami with a raising remote, bounded startup during blocked sync, retry
and background revocation, and requests during blocked control/outbox remote
calls. Targeted result: **10 passed, 1 skipped** (sandbox loopback restriction).
No body/query logging was added. No commit or deployment was performed.

Final follow-up validation on top of `23fe649`: full Python suite
(`python -m pytest -q -p no:cacheprovider profile-log/tests`) completed with
**655 passed, 8 skipped, 5 failed** in 105.38 seconds. All five failures are the
same sandbox-denied socket-binding gates listed above. Both in-process E2E cases
pass; E2E harnesses that bypass `serve()` now explicitly refresh after remote
approval to simulate the next background tick. Gateway `node --test` completed
with **19 passed, 0 failed, 0 skipped**. `git diff --check` passed. Changes remain
uncommitted on `claude/phase6-deploy-fixes`.

## Follow-ups (2026-10-21)

On `claude/phase6-followups` from main `ec7f844`, recall now checks policy
membership at the Worker only for exact 32-character lowercase hex profile IDs.
Other strings reach the bridge unchanged, retaining zod limits. Bridge tests
verify allowed local names succeed and unauthorized/unknown names never contact
the broker. Profile names remain in encrypted device metadata; no names were
added to Worker-visible records. Existing tool descriptions do not claim IDs only.

Connector administration gives exact app IDs precedence over remembered names.
Case-insensitive name resolution excludes revoked apps, and allow-secret also
excludes missing policies. Unknown/ambiguous names return `unknown_app` or
`ambiguous_app` before presence or writes. List output adds the locally remembered
`display_name`, null when absent. README guidance covers both changes.

TDD reproduced the Worker refusal and CLI resolution/list/error-code failures
before implementation; regression coverage also checks exact-ID precedence,
schema limits, and bridge/broker isolation. No body, query, or token logging was
added. No commits or dependency changes.

Final validation: full Python suite (`python -m pytest -q -p no:cacheprovider
--tb=no profile-log/tests`) finished with **675 passed, 8 skipped, 5 failed**
in 98.23 seconds. All five failures explicitly report sandbox-denied socket
binding: `test_p3_1_processes`, `test_connections_offline`,
`test_real_transport`, `test_p5_4_concurrent_processes`, and
`test_s3_custom_http_endpoint`. The initial run also caught missing short test
IDs; those were fixed and the full rerun passes the portability gate.
Gateway `node --test` (TAP reporter): **21 passed, 0 failed, 0 skipped**.
`git diff --check` passes. Changes remain uncommitted.
