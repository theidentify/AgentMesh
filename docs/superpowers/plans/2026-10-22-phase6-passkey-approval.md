# Phase 6 passkey approval — 2026-10-22

Branch: claude/phase6-passkey-approval, stacked on claude/phase6-status-page
(PR #73 including de-escalation). No commits, deploys, network or npm additions.
Owner principle: the cloud relays, it never grants.

Spec amended first in the status-page section. Implement in surrounding style,
with failing tests before implementation. Use the installed pinned real library
and a test software P-256 authenticator. Python interpreter:
`~/.cache/agentmesh-logvenv/bin/python`.

Choose device-local single-use challenges, not stateless challenges. Finish carries
issuing device_id; a different Tunnel responder refuses with wrong_device and the
page asks to retry. This avoids cross-device replay state and keeps counters local.

Acceptance gates:

- P6-P1: Signed additive credentials/removals validate account authority; revoked,
  write-only, malformed, unknown or removed records cannot authenticate.
- P6-P2: Presence-backed CLI add/list/remove and settings; hashed expiring single-use
  codes; ambiguous prefixes refuse; no sensitive output except initial code once.
- P6-P3: Real-library registration requires code, exact origin/RP/challenge and UV.
- P6-P4: Real-library assertions reject forged/replayed/wrong-bound/expired/removed,
  backwards-counter or missing-UV requests without approval writes; accept 0/0.
- P6-P5: Shared CLI/device approval publishes exactly proposed policy and valid
  signed envelope, with inbox creation and scope authority checked on the device.
- P6-P6: Status-only pages/script, JWT and CSRF, safe rendering, offline and
  wrong-device messages; Worker applies only verified signed approval and never
  stores codes/assertions/credentials or transient profile names.
- P6-P7: Full Python and gateway Node suites pass; README, FORMAT and Phase 6
  REPORT describe decisions, pins, tests and real-authenticator limitations.

Execution order: spec/plan; registry and enrolment tests/implementation; shared
approval and assertion tests/implementation; bridge integration; Worker/browser
tests/implementation; documentation and complete regression runs.

Not verified by software tests: real browsers, Bitwarden, iCloud, Google and
YubiKey ceremony behavior, deployed Cloudflare Access/Tunnel routing.

Implementation notes:

- The exact policy reserves a missing inbox scope ID before the ceremony; creation
  happens only after verification, through the shared CLI/passkey approval function.
- Protected state is consumed before publication. A storage failure can require a
  new ceremony/code, but cannot replay a verified assertion.
- Connector CLI operations and broker requests share the bridge's home lock;
  broker contention returns busy instead of restoring stale counters/challenges.
- The browser keeps options only in memory, clears code input immediately, displays
  the proposed policy as text, and requires a separate confirmation click.

Results: all 28 new Python passkey tests pass. Full Python: 721 passed, 8 skipped,
5 failed (only the five existing local-socket tests denied by this sandbox).
Gateway: 45 passed, no failures/skips. Pins match the installed environment;
`git diff --check` passes and no npm manifests/lockfiles changed. P6-P1 through
P6-P6 are covered; P6-P7's fully green Python requirement needs a rerun in an
environment allowing local socket binding. Real browser and authenticator
validation remains explicitly unverified. See the Phase 6 REPORT for exact
commands and the five affected test names.


## Reviewer follow-up: request latency and lock scope

P6-P8: Passkey request reads use only `FilesystemStorage(home.root/'replica')`:
registry, keyring, connector policies, credentials and removals. The existing
background `sync_control` registry prefix covers both passkey subtrees; a regression
test imports a credential and then its removal through that path. Configured
remote storage is a publication destination only on the passkey request path.
The shared approval function and configured-storage CLI approval semantics remain
unchanged; the bridge calls it against local storage and publishes the resulting
new inbox/control records and policy after releasing local locks.

WebAuthn verification and remote writes do not hold the bridge/home lock. Finish
rechecks the challenge, expiry, credential, authority and counter after reacquiring
the lock, then consumes protected state before local or remote publication. This
prevents concurrent finish calls from approving twice without serializing recall
behind verification or R2 latency. The HTTP handler no longer holds an outer lock
across the complete passkey request; nonce claims remain locked.

TDD: the remote-read-refusal test and paused-request concurrency tests failed
before the fix. Tests now cover all four bridge steps with remote reads forbidden,
missing-inbox publication, background sync, actual HTTP-handler recall during
Event-gated verification/publication, and concurrent registration/approval finishes.
`GATES.md` was deleted as requested; no replacement root ledger is created.

Remote writes can still exceed the Worker's nine-second deadline when R2 connections
are slow. This change removes remote reads and recall-blocking lock holds; it does
not claim to repair the owner's IPv6 connectivity or make remote writes asynchronous.
Final follow-up validation: full Python suite **727 passed, 8 skipped, 5 failed**
(116.23 seconds). Only the same five sandbox-denied local-socket tests fail;
all 34 passkey tests pass. Gateway `node --test`: **45 passed, 0 failed,
0 skipped**. The existing transport fixture now initializes its real RLock because
`handle()` owns nonce locking; its assertions are unchanged. `git diff --check`
passes, `GATES.md` is absent, and no commits were made.


## Enrolment and approval UX

Branch `claude/passkey-enrol-ux` starts at main `074d95d`. No commits or deploys.
The single-page wizard checks secure context, configured status origin and browser
passkey support before continuing. Platform unlock absence or probe failure is a
warning; optional client capabilities are information only. Choose password
manager/device or physical security key before entering the uppercase-normalized
10-character device code (10 minutes, single use), then explicitly create.
Attachment and WebAuthn hints are browser preferences only (unsupported hints are
ignored by browsers); challenge, RP, user, resident key and UV remain device-owned.

Approval shares readiness and explicitly displays the device's client/profiles,
remember target, maximum label and limits before its assertion prompt. Both flows
use fixed friendly errors, safe text rendering, focused steps, live results and a
no-JavaScript explanation. Success links to approval and CLI list/remove guidance.
Labels and enrolled kinds are omitted because the existing protocol does not
return them. The device returns generic denial for invalid/expired/used codes;
the UI explains that possibility without asserting a cause. Dedicated fixed code
mappings are tested for future responses, without broadening the Worker allowlist.

TDD: option preservation, readiness decisions and fixed error tests failed before
implementation. Fake-browser tests exercise both choices, approval policy,
normalization, CSRF rotation, cancellation and offline recovery. Existing JWT,
single-use CSRF, host isolation and exact CSP assertions remain in force. No device
verification, npm dependencies, inline scripts/styles or CSP changes. Readiness
checks browser/device unlock capability; actual trusted-device availability is
established by the existing begin call. Real authenticator/browser UX still needs
manual validation.

UX validation: gateway `node --test`: **57 passed, 0 failed, 0 skipped**.
Full `profile-log/tests` Python suite: **733 passed, 8 skipped, 5 failed**
(113.17 seconds). The five failures are the documented sandbox socket-bind gates:
`test_p3_1_processes`, `test_connections_offline`, `test_real_transport`,
`test_p5_4_concurrent_processes`, and `test_s3_custom_http_endpoint`.
All report `Operation not permitted`; a fully green run requires an environment
allowing local socket binding. `git diff --check` passes.
