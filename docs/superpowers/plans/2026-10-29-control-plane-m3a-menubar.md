# Control plane milestone 3a (menu bar app, Touch ID backend moves) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents are available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the owner a native macOS menu bar app for the supervisor. It shows every unit's health, backend and last exit, and it runs the tier 1 actions (start, stop, restart, reset, maintenance). It can also move a unit between launchd and the supervisor. That move needs human presence, and the **supervisor** proves it with its own Touch ID prompt, never the app.

**What M3a is:**
- The first slice of spec milestone 3: Touch ID presence (a supervisor-run Swift helper) and one tier 2 op, `unit.backend`.
- A SwiftPM menu bar app in `control-app/`. It is a plain client of `control.sock`, with no authority of its own.

**Not in M3a (later M3 slices):** the plan → approve → execute state machine with `plan_digest`, Discord and passkey approvals, device and grant ops, `settings.set` and `quota.set`, and the key inventory.

**Spec:** `docs/superpowers/specs/2026-10-23-local-control-plane-design.md`. **Previous plans:** `2026-10-23-control-plane-m1.md` and `2026-10-24-control-plane-m2.md`, both merged (#107, #108) and live on the Mac.

---

## Design decisions made for M3a (read first)

1. **The supervisor raises Touch ID; the app never claims it.**
   - Same-user code can forge any client message, so a client that says "the user passed Touch ID" proves nothing (spec, Threat model).
   - `unit.backend` makes the supervisor run its own child process, `agentmesh-presence`, which calls `LAContext.evaluatePolicy` in the owner's GUI session (`org.agentmesh.control` is a LaunchAgent in `gui/<uid>`, and its children inherit that session).
   - The supervisor reads the verdict from the exit code of a child that it started itself. No client can inject that answer.
   - The app only sends the request and shows the result.
2. **Touch ID or the Mac password (owner's choice, 2026-10-29).**
   - The policy is `LAPolicyDeviceOwnerAuthentication`: the standard macOS sheet, with Touch ID first and "Use Password…" as the fallback. It works with the lid closed and when a fingerprint does not read.
   - This is weaker than biometrics only, which was the first draft. Anything that can enter the Mac password into the system sheet passes, including an agent with screen control that knows the password. The owner accepted that risk for convenience.
   - The rate limit, the busy lock and the audit in decision 3 still apply.
   - LocalAuthentication has no public API that says whether the owner used Touch ID or the password, so the audit cannot tell the two apart. That was found while building Task 3, and the first draft's `la_method` field is dropped.
   - `presence_unavailable` now means LocalAuthentication itself cannot evaluate (no password set, or the sheet cannot be shown). The typed-console `control adopt|release` path stays unchanged.
   - Spike note: with `LAPolicyDeviceOwnerAuthenticationWithBiometrics`, the default sheet still shows "Enter Password…", which returns -3 `userFallback`. That no longer matters under this policy.
3. **Touch ID confirms a person, so the prompt must name the intent.**
   - The reason string is the same summary that `migrate.adopt`/`migrate.release` print today: unit, label, the argv it will run, and the undo command. It also includes the caller's executable from `peer.py`, which is informative but spoofable.
   - An agent can still trigger a prompt and hope for a reflexive touch. To limit that:
     - tier 2 is rate-limited to **3 prompts per 10 minutes**, counting denials;
     - while one prompt or move is in flight, every other tier 2 request gets `busy`;
     - every request, approval, denial and outcome is audited.
4. **`unit.backend` reuses `migrate.adopt` and `migrate.release`.**
   - They already take a `console` with `confirm(action, summary)`. `HelperPresence` implements that same `Presence` protocol by running the helper.
   - Inside the supervisor, `adopt`'s liveness `call` is replaced by `lambda *a: {'ok': True}`.
   - The M2 rule "the supervisor never changes a backend itself" is amended: it may now, but **only** after its own Touch ID approval. The CLI path is unchanged.
5. **The move runs off the request thread.**
   - `adopt` can wait up to 600 s for a launchd job to go idle.
   - The request thread prompts for Touch ID (≤ 60 s). After approval it starts one worker thread and answers `{accepted: true, move_id}` at once.
   - The move's state is in memory as `pending | waiting | ok | <error code>`, plus `started_at`. `status` reports it per unit as `move`, and `moves.get` (tier 0) reads one by id.
   - If the supervisor restarts mid-move, the move is gone from memory. The audit row (`requested` with no outcome) still records it, and `state.db` is always consistent because `set_backend` is one statement.
6. **`state.db` from two threads.**
   - `Store` opens its connection with `check_same_thread=False` and autocommit, and the ticker uses it already.
   - The move worker writes through the same `Store`, under a new `store.lock` (`threading.Lock`). The ticker's writes take the same lock. This is one small change in `store.py`.
7. **Touch ID lives in a Swift helper, not in Python.**
   - `agentmesh-presence` is a second executable in the `control-app/` SwiftPM package. The venv gains no dependency (pyobjc was considered and rejected on 2026-10-29).
   - It is installed to `~/.agentmesh/bin/agentmesh-presence`, ad-hoc signed.
   - **Contract.** The summary is read from stdin (≤ 4 KiB UTF-8), never argv, so it does not show in `ps`.

     | Invocation | Exit code |
     |---|---|
     | `agentmesh-presence available` | 0 when biometrics can be evaluated, 2 otherwise |
     | `agentmesh-presence confirm --action <a>` | 0 approved, 1 denied or cancelled, 2 unavailable, 3 timed out (60 s), 64 usage error |

     Stdout is one JSON line, `{"result": "...", "la_error": <int|null>}`, kept for audit only. The exit code is the verdict.
   - **The supervisor's side** is `HelperPresence` in `control/presence_helper.py`:
     - Before every run it checks the helper path: a regular file (no symlink), owned by the owner, not group- or world-writable, in a directory with the same properties.
     - It runs the helper with a fixed argv, an empty environment plus `HOME`, and a 75 s subprocess timeout, after which it kills the helper.
     - Any exit code other than 0, or any failed check, is a refusal.
   - **Trust.** The helper is as trusted as the rest of `~/.agentmesh` (venv, app checkout). Same-user code that can rewrite it can also rewrite the supervisor, and the spec already accepts that. The path checks stop casual replacement through a symlink or a loose mode, not a determined same-user attacker.
   - Python tests use a fake `runner`. Swift tests cover argument parsing and the exit-code mapping, using a stubbed `LAContext` protocol.
8. **The app is a thin client.**
   - SwiftPM executable `AgentMeshControl`, a SwiftUI `MenuBarExtra` with `.window` style.
   - It calls `status` every 5 s while its menu is open and every 30 s when it is closed, and `audit.tail` on demand.
   - It has no state of its own except "launch at login" (`SMAppService.mainApp`).
   - It is ad-hoc signed and installed to `~/Applications/AgentMesh Control.app` by `control-app/bundle.sh`. No Xcode project and no App Store.
9. **One wire contract, tested from both sides.**
   - A frame is a 4-byte big-endian length, then UTF-8 JSON (`broker/framing.py`), one request per connection.
   - `profile-log/tests/control/fixtures/status.json` is a real `status` response with the live values anonymized.
   - The Python test asserts that `op_status` produces exactly those keys. The Swift test decodes the same file.

## Conventions

The same as M2, plus:
- `$PY` is `~/.cache/agentmesh-logvenv/bin/python`. If that venv is missing, create it from `profile-log/requirements*.txt` first.
- Swift: Swift 6 language mode, macOS 14 deployment target, and no third-party packages. Run `control-app/test.sh`, not plain `swift test`: a CommandLineTools-only toolchain does not search `plugins/testing` for the swift-testing macros. Build with `swift build -c release` from `control-app/`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File structure

| File | Responsibility |
|---|---|
| Create `profile-log/agentmesh_log/control/presence_helper.py` | `HelperPresence`: path checks, runs `agentmesh-presence`, exit code → verdict. |
| Modify `.../control/layout.py` | Add the `presence_helper` path (`~/.agentmesh/bin/agentmesh-presence`). |
| Create `control-app/Sources/agentmesh-presence/main.swift`, `Presence.swift` | The presence helper: `available`, `confirm --action`, Touch ID or Mac password (`deviceOwnerAuthentication`), 60 s timeout. |
| Create `.../control/moves.py` | `Moves`: one move at a time, worker thread, in-memory states, tier 2 rate limit. |
| Modify `.../control/ops.py` | `unit.backend` (tier 2), `moves.get` (tier 0), and `move` in `status` rows. |
| Modify `.../control/store.py` | `lock` around writes. |
| Modify `.../control/native.py` | Take `store.lock` for writes. |
| Modify `.../control/cli.py` | Serve wiring: build `HelperPresence` and `Moves`. |
| Create `profile-log/tests/control/test_helper.py`, `test_moves.py`, `test_wire_contract.py` | Tests. |
| Create `profile-log/tests/control/fixtures/status.json` | Shared wire fixture. |
| Create `control-app/Package.swift` | Executables `AgentMeshControl` and `agentmesh-presence`, a shared test target. |
| Create `control-app/Sources/AgentMeshControl/ControlClient.swift` | Unix socket, framing, request and response. |
| Create `control-app/Sources/AgentMeshControl/Models.swift` | `Decodable` status, unit, move and audit rows. |
| Create `control-app/Sources/AgentMeshControl/StatusModel.swift` | `@Observable` poller, action dispatch, error text. |
| Create `control-app/Sources/AgentMeshControl/MenuView.swift`, `UnitRow.swift`, `AuditView.swift`, `App.swift` | UI. |
| Create `control-app/Tests/AgentMeshControlTests/*.swift` | Framing, fixture decode, error mapping. |
| Create `control-app/bundle.sh` | Build both products. Install the `.app` (Info.plist with `LSUIElement`) and the helper (mode 0700), ad-hoc signed. |
| Create `control-app/README.md` | Build, install, uninstall, what the app may and may not do. |
| Modify the spec | M3a amendments. |

---

### Task 1: Owner-run spike — the helper's Touch ID sheet from a LaunchAgent

This must pass before Task 3. It answers the one open question: does a child of a LaunchAgent show the `evaluatePolicy` sheet?

- [x] **Step 1:** In `scratchpad/spike/`, write a ~30-line Swift file. It evaluates `LAPolicyDeviceOwnerAuthenticationWithBiometrics` with the reason "AgentMesh spike", waits up to 60 s on a semaphore, prints `{"ok":bool,"la_error":code}` and exits 0 or 1. Build it with `swiftc -O spike.swift -o spike` and sign it with `codesign -s - spike`.
- [x] **Step 2:** Run it from the terminal and touch the sensor.
- [x] **Step 3:** Run it as a one-shot LaunchAgent: a temporary plist with `RunAtLoad` and `KeepAlive=false`, loaded by `launchctl bootstrap gui/$(id -u)` (not `launchctl submit`, whose jobs are KeepAlive and would prompt again after every exit). Touch the sensor, read `/tmp/spike.log`, then run `launchctl bootout gui/$(id -u)/org.agentmesh.spike`.
- [x] **Step 4:** Record both results in the PR.
  - If the LaunchAgent run shows no sheet, or fails with `LAErrorNotInteractive` (-1004), stop here.
  - The fallback design: the supervisor asks the running menu bar app's own helper process, over an XPC Mach service registered by a LaunchAgent with `LimitLoadToSessionType=Aqua`. Amend this plan before you go on.

**Result (2026-10-29, macOS 27, built-in Touch ID, `biometryType` 1): passed.**

| Run | `ppid` | `xpc_service` | Result |
|---|---|---|---|
| Terminal, default sheet | shell | — | `denied`, -3 `userFallback`: the default sheet offers "Enter Password…" |
| Terminal, `localizedFallbackTitle = ""` | shell | — | `approved` in 5.1 s |
| LaunchAgent (`gui/<uid>`, RunAtLoad, no KeepAlive) | 1 | `org.agentmesh.spike` | `approved` in 2.1 s |

A LaunchAgent's process shows the system sheet and gets the verdict, so the XPC fallback is not needed. Two earlier -3 results, after the fallback button was hidden, came with a sensor touch. Treat them as failed matches: the helper maps every non-approval to a refusal anyway.

### Task 2: `store.lock`

**Finding (2026-10-29):** `Store` already holds `self.lock` around every statement (`_one`, `_all`, `_run`), and nothing in `control/` touches `store.db` directly. No code change is needed.

- [x] Add `test_two_threads`: 300 `set_backend` calls and 300 runs from two threads, with no errors and no lost rows.
- [x] Mutation check: the test still passes with the lock removed (5 of 5 runs), because Python's `sqlite3` serializes calls on one connection. It is a regression guard for the move worker's usage, not a lock gate. Keep the lock, which costs nothing and protects multi-statement reads.
- [x] Commit `test(control): state.db from the move worker and the ticker`.

### Task 3: `agentmesh-presence` and `HelperPresence`

**Done (2026-10-29).**
- The helper is a library, `PresenceCore`, plus the `agentmesh-presence` executable. There are 10 Swift tests, and the `timeout` test runs `confirm` on its own thread, so a missing timeout fails the test instead of hanging the suite.
- Mutation gates:
  - Swift: drop the timeout → `timeout` fails after 5 s. Always approve → `deny` fails.
  - Python: treat exits below 3 as approval → 2 failures. Drop `safe()` → 6 failures. Inherit the environment → 1 failure.
- The real binary was checked without a prompt: `available` → `{"result":"available"}`, exit 0. Bad argv, an action outside the list, or a 5000-byte summary → exit 64.

**Swift helper** (`control-app/Sources/agentmesh-presence/`):

```swift
// Presence.swift — Touch ID, or the Mac password, evaluated in a child of the supervisor (deviceOwnerAuthentication).
// The exit code is the verdict; anything but 0 is a refusal.
import Foundation
import LocalAuthentication

protocol Evaluator { func canEvaluate() -> (Bool, Int?); func evaluate(_ reason: String, _ reply: @escaping @Sendable (Bool, Int?) -> Void); func invalidate() }

enum Verdict: Int32 { case approved = 0, denied = 1, unavailable = 2, timedOut = 3, usage = 64 }

final class ResultBox: @unchecked Sendable { private let lock = NSLock(); private(set) var ok = false; private(set) var code: Int?
    func set(_ ok: Bool, _ code: Int?) { lock.lock(); self.ok = ok; self.code = code; lock.unlock() } }

let actions: Set<String> = ["migrate"]   // mirrors presence.ACTIONS entries that may reach Touch ID

func confirm(action: String, summary: String, evaluator: Evaluator, timeout: TimeInterval = 60) -> (Verdict, Int?) {
    guard actions.contains(action), !summary.isEmpty, summary.utf8.count <= 4096 else { return (.usage, nil) }
    guard evaluator.canEvaluate().0 else { return (.unavailable, evaluator.canEvaluate().1) }
    let done = DispatchSemaphore(value: 0); let box = ResultBox()
    evaluator.evaluate("AgentMesh \(action): \(summary.prefix(400))") { ok, code in box.set(ok, code); done.signal() }
    guard done.wait(timeout: .now() + timeout) == .success else { evaluator.invalidate(); return (.timedOut, nil) }
    return box.ok ? (.approved, nil) : (.denied, box.code)
}
```

`main.swift`:
- parses `available` or `confirm --action <a>`, and any other argv → 64;
- reads stdin up to 4097 bytes, refusing anything over 4096;
- wraps `LAContext` as an `Evaluator`;
- prints one JSON line and `exit(verdict.rawValue)`.

**Python side** (`control/presence_helper.py`):

```python
"""The supervisor's presence: it runs its own agentmesh-presence child and trusts only that child's exit code.
Any failed path check, timeout or non-zero exit is a refusal. Summaries go over stdin, never argv."""
import os
import stat
import subprocess
from ..presence import ACTIONS

TIMEOUT_S=75

def safe(path):
    """Owner-only regular file in an owner-only directory, no symlinks."""
    try: info,parent=os.lstat(path),os.lstat(os.path.dirname(path))
    except OSError: return False
    for i in (info,parent):
        if i.st_uid!=os.getuid() or stat.S_IMODE(i.st_mode)&0o022: return False
    return stat.S_ISREG(info.st_mode) and stat.S_ISDIR(parent.st_mode)

class HelperPresence:
    def __init__(self, path, *, runner=subprocess.run, timeout_s=TIMEOUT_S):
        self.path,self.runner,self.timeout_s=str(path),runner,timeout_s

    def run(self, argv, data=b''):
        if not safe(self.path): return None
        try: return self.runner([self.path,*argv],input=data,capture_output=True,timeout=self.timeout_s,
                                env={'HOME':os.environ.get('HOME','')}).returncode
        except (OSError,subprocess.SubprocessError): return None

    def available(self): return self.run(['available'])==0

    def confirm(self, action, summary):
        if action not in ACTIONS: return False
        return self.run(['confirm','--action',action],summary.encode('utf-8'))==0
```

- [ ] **Swift tests** (`PresenceTests.swift`) use a stub `Evaluator`:
  - approve → 0; deny → 1; unavailable → 2;
  - a reply that never comes → 3, and `invalidate` is called;
  - an unknown action, an empty summary or an oversized summary → 64, with no `evaluate` call.
- [ ] **Python tests** (`test_helper.py`) use a fake `runner` and a `tmp_path` helper file:
  - exit 0 → True; exits 1, 2, 3 and 64 → False;
  - a timeout or an `OSError` → False;
  - a symlink, a group-writable file or directory, or a missing file → False and the runner is not called;
  - the summary arrives on stdin and is not in argv; the environment is exactly `{HOME}`;
  - an action outside `ACTIONS` → False and no run.
- [ ] **Mutation gates:**
  - Treat any exit code below 3 as approval → the deny test fails.
  - Drop `safe()` → the symlink test fails.
  - In Swift, drop the semaphore timeout → the timeout test fails.
- [ ] Commit `feat(control): Touch ID through a supervisor-run Swift helper`.

### Task 4: `Moves` and `unit.backend`

**Done (2026-10-29), with a simpler design than the original draft below.**
- **No split.** `migrate.adopt` and `migrate.release` are unchanged. `Moves` runs all of one on a worker thread and passes a `Gate` as its `console`.
  - `Gate.confirm` calls the real presence, then wakes the request thread with the verdict.
  - The request thread waits for whichever comes first, the verdict or the worker finishing early (`unsupported_plist`, `not_native`, ...), for up to `DECIDE_S=90`. No verdict in that time means `presence_required`.
- **States:** `deciding` → `moving` → `ok` or an error code. Finished moves are kept for 1 h.
- **Mutation gates:**
  - The gate ignores the verdict → 3 failures.
  - No busy check, no rate limit, a timeout that approves, or skipping `available()` → 1 failure each.
- **Client consequence:** a `unit.backend` request can take up to 90 s, so the Swift read timeout in Task 6 is **100 s**, not 70 s.

`ops.py` additions:

```python
'unit.backend':(2,frozenset({'unit','to'})),'moves.get':(0,frozenset({'move_id'})),
```

`moves.py`:
- `Moves(layout, store, presence, *, adopt=migrate.adopt, release=migrate.release, clock=time.monotonic, thread=threading.Thread)`.
- `request(name, to, caller)`, in order:
  1. Validate: `to` is in `('native','launchd')` and the unit is `native_ok`. Otherwise `bad_args`.
  2. If a move is in flight, raise `busy`.
  3. Rate limit: more than 3 prompts in the last 600 s → `rate_limited`.
  4. If `presence.available()` is False → `presence_unavailable`.
  5. Call the adopt/release function with `console=presence` (which raises the prompt), wrapped so the Touch ID step runs on the request thread and the wait and move run on the worker.
- Split `migrate.adopt` into `adopt_check` (validation, spec read, summary) and `adopt_apply` (wait, disable, bootout, `set_backend`); split `release` the same way. The CLI calls both in order, so its behaviour is unchanged and its tests still pass.
- Outcomes:
  - Denied → audit `presence_denied` and raise `presence_required`.
  - Approved → store `{move_id: {unit, to, state:'waiting', started_at}}`, start the worker, and return `{accepted:True, move_id}`.
  - The worker sets `state` to `ok` or to the error code it returns, and audits the outcome.
- `get(move_id)` returns that entry or `unknown_move`. Finished moves are kept for 1 h.
- `status` rows gain `move: {move_id, to, state}` or `null`.

- [ ] Tests (`test_moves.py`) use `FakeLaunchd`, a fake presence and an inline `thread`:
  - approve → native, audit `requested`/`ok`;
  - deny → `presence_required`, backend unchanged;
  - a second request while one is in flight → `busy`;
  - the fourth prompt within 10 min → `rate_limited`, including after denials;
  - `presence_unavailable`;
  - a release that waits for an open run;
  - an unknown unit, or a unit that is not native-capable → `bad_args`;
  - the CLI `adopt`/`release` tests unchanged and passing.
- [ ] Mutation gates:
  - Calling `adopt_apply` before `confirm` returns → the deny test fails.
  - Removing the busy check → the busy test fails.
- [ ] Commit `feat(control): Touch ID-gated unit.backend and moves.get`.

### Task 5: Serve wiring and the wire contract

**Done (2026-10-30).**
- `control serve` builds `Moves(layout, store, HelperPresence(layout.presence_helper), launchd=launchd)` and passes it to `Control`. `test_serve_evicts_reloaded_units_first` checks the wiring.
- `fixtures/status.json` comes from the live supervisor on 2026-10-30 (8 units, catch-up native). Pids, runtimes and job times are fixed values. The `move` column is added by hand, because the live supervisor predates it, and catch-up carries an example move so the nested keys are pinned.
- `test_wire_contract.py` checks the envelope, the result, `maintenance`, `quota`, the unit names in order, every row's keys and the `job` and `move` keys.
- Mutation gates: renaming a `move` key or the `move` column → the test fails.

- [ ] In `control serve`, build `HelperPresence(layout.presence_helper)` and `Moves(...)` and pass them to `Control`.
- [ ] Add `fixtures/status.json`, a real `status` response with pids, timestamps and reasons replaced by fixed values.
- [ ] `test_wire_contract.py`: an `op_status` built from `FakeBackend` has exactly the fixture's key sets at every level (unit rows, `job`, `move`, `maintenance`, `quota`).
- [ ] Commit `feat(control): serve Touch ID moves; pin the status wire contract`.

### Task 6: Swift client and models

**Done (2026-10-30).**
- **Layout:** a library target, `ControlCore`, in three files: `Framing.swift`, `ControlClient.swift` and `Models.swift`. The app executable in Task 7 imports it. Decoding uses `.convertFromSnakeCase`.
- **Errors:** `describe()` maps every error code from `ops.py`, `moves.py` and `migrate.py` to readable text, with a "Refused: <code>." fallback.
- **Tests:** 18 Swift tests.
  - Framing: a `socketpair` round trip, a big-endian header, length 0, length over 1 MiB, `UInt32.max`, and a short read.
  - Client, against a fake supervisor on a real Unix socket: request shape, result, server error code, mismatched id, bad JSON, closed without a reply, a hung supervisor timing out, no supervisor, and `moveTimeout > 90`.
  - Models: the shared fixture, an unknown health value, and an audit row.
- **Live check:** run `AMC_LIVE=1 ./test.sh --filter liveStatus`, which is opt-in and calls only the read-only `status`. It passed against the pre-M3a live supervisor, whose rows have no `move` key. `move` is optional, so the app also works before the supervisor is upgraded.
- **Mutation gates:** dropping the id check, setting `moveTimeout` to 70 s, or dropping the 1 MiB cap → each makes a test fail.

- `ControlClient`:
  - `connect` to `~/.agentmesh/run/control.sock` with `socket(AF_UNIX, SOCK_STREAM)`;
  - write a 4-byte big-endian length, then the JSON body;
  - read the 4-byte length, reject 0 or > 1 MiB, then read exactly that many bytes;
  - 100 s read timeout (`SO_RCVTIMEO`) for `unit.backend`, which covers the supervisor's 90 s decision wait, and 10 s for every other op;
  - the request is `{v:1, id:UUID, op, args}`;
  - the response maps to `Result<JSON, ControlError(code, message)>`.
- `Models`: `Decodable` `Status`, `UnitRow`, `JobInfo`, `Move`, `Maintenance`, `Quota` and `AuditRow`. Unknown health strings decode to `.unknown(String)` instead of failing.
- [ ] Tests:
  - framing round-trip against an in-test `socketpair`;
  - an oversized length is refused;
  - `status.json` (copied by the test from `profile-log/tests/control/fixtures/` via `#filePath`) decodes;
  - an unknown health value decodes.
- [ ] Commit `feat(control-app): socket client and status models`.

### Task 7: Menu bar UI

**Built and owner-checked (2026-10-30).**
- 09:59:56: `unit.start catch-up` from the app ran native run 298, which exited 0 (75 unchanged).
- 10:00:32 and 10:00:35: `maintenance.on` (reason "testing"), then `maintenance.off`.
- The audit records all of these with `caller_exe` set to the app binary.
- Follow-ups from the first screenshot are fixed: runtimes read `1h 24m`, and units that are not installed have no menu.
- Still open for later: native units show no last-run time, because `status` has no such field.
- **Testable logic** is in `ControlCore/Presentation.swift`, covered by 9 tests: severity and the menu bar icon, the actions offered per row, the move text, and the job age.
- **The app** is the `AgentMeshControl` executable: `StatusModel`, `MenuView`, `UnitRowView` and `AuditView`.
- **Deviations from the draft below:**
  - **No `@State`.** On a CommandLineTools-only toolchain the macOS 27 SDK's `@State` macro cannot load: the `SwiftUIMacros` plugin is missing. View state lives in the `@Observable` `StatusModel` instead, because `ObservationMacros` is available.
  - **Move confirmation** is an inline panel in the row, not a sheet, because sheets are unreliable inside a `MenuBarExtra` window. It shows a generic text ("…will ask for Touch ID or your Mac password. Approve only if you started this move."). The app cannot know the argv. The supervisor's own sheet shows the full `migrate` summary.
  - **`missing` is neutral** (grey) everywhere. This is simpler than an expected-units list, and retention and insight-tunnel are not installed on this Mac.
  - **The menu bar icon** is a fixed mark, "A mesh": three nodes and a hub. The owner picked it on 2026-10-30 from four designs previewed live in the menu bar.
    - Healthy: the mark alone, as a template image, so the system tints and inverts it.
    - Something needs a look: an orange badge (failing, stuck) or a red one (failed, down, conflict).
    - Supervisor unreachable: the mark fades and an empty ring replaces the badge.
- **Launched unbundled** with `swift build && .build/out/Products/Debug/AgentMeshControl`: no errors, and no audit rows (status is tier 0). Launch at login only works from the bundled app (Task 8) and says so.

- **Menu bar icon:** worst health across units. Green covers `idle`/`running`. Amber covers `failing`/`stuck`. Red covers `failed`/`down`/`conflict`. `missing` counts only for units that are expected (the not-installed set is listed in the README). The icon is grey when the socket is unreachable.
- **Unit row:**
  - name, a health dot, a `native`/`launchd` badge, last exit, and a relative time from `job.at`;
  - a running pid and runtime;
  - a progress line while `move` is not null.
- **Row menu:**
  - Start, Restart and Stop (jobs only), and Reset (only when the unit is `failed`);
  - "Move to supervisor…" or "Move to launchd…", only when the unit is `native_ok`. Before sending `unit.backend`, the app shows a confirmation sheet with the same summary text the Touch ID prompt will show, then says "Touch ID prompt is from the AgentMesh supervisor".
- **Footer:** a maintenance toggle (asks for a reason), quota calls today, "Audit…" (a window with the last 100 `audit.tail` rows), "Launch at login", and Quit.
- **Errors:** the code is shown inline on the row, with readable text for `rate_limited`, `busy`, `presence_required`, `presence_unavailable` (plus "use `agentmesh-log control adopt` in Terminal"), `maintenance` and `unit_failed`.
- [ ] Build and run locally. Owner check: one start of `catch-up`, one maintenance on/off, and the audit window shows both.
- [ ] Commit `feat(control-app): menu bar status, actions and audit`.

### Task 8: Bundle, README and spec amendments

- `bundle.sh`:
  - runs `swift build -c release`;
  - assembles `AgentMesh Control.app/Contents/{MacOS,Info.plist}` with `LSUIElement=true` and bundle id `org.agentmesh.control-app`;
  - runs `codesign -s - --force` on both products;
  - copies the app to `~/Applications`, refusing to overwrite a running instance;
  - installs `agentmesh-presence` to `~/.agentmesh/bin/` (directory 0700, file 0700) through a temp file and `mv`, so the supervisor never runs a half-written helper.
- [ ] README: build, install and uninstall. Spell out that the app holds no authority, that it only calls the socket, and that Touch ID comes from the supervisor.
- [ ] Spec: add "Amendments from milestone 3a", covering decisions 1–5 and 7 above, including the owner's choice of password fallback in decision 2. Replace the spec's `pyobjc-framework-LocalAuthentication` line with the helper, and mark `unit.backend` as implemented.
- [ ] Run the full suites: `$PY -m pytest -q -p no:cacheprovider profile-log/tests` and `swift test`.
- [ ] Commit, push, and open the PR.

### Task 9: Owner-run deploy (after merge)

- [ ] Run `git checkout --detach <merge>` in `~/.agentmesh/app/AgentMesh`, using the drift-guarded script from the 2026-10-29 deploy.
- [ ] Run `control-app/bundle.sh` from the checked-out app. It installs both the app and `~/.agentmesh/bin/agentmesh-presence`.
- [ ] Restart the supervisor: `launchctl kickstart -k gui/$(id -u)/org.agentmesh.control`.
- [ ] Open the app.
- [ ] Verify:
  - every unit shows, and `catch-up` is `native`;
  - "Move to launchd…" on catch-up raises a **system** Touch ID sheet naming AgentMesh. Cancel it, and expect `presence_required` plus an audit `presence_denied` row;
  - move ingest only on its migration date (no earlier than 2026-11-01, after 3 days of clean catch-up runs).
- [ ] Rollback: quit the app, check out the previous revision, kickstart the supervisor, and remove `~/.agentmesh/bin/agentmesh-presence`. `control panic-revert` still works without the supervisor.
