# Windows profile-log rollout (DESKTOP-DOJ622I)

Date: 2026-10-24. Status: draft plan, nothing done beyond W0.

Goal: make the Windows PC a second profile-log device, the same as the Mac.
The steps are: install the runtime under `%USERPROFILE%\.agentmesh`, pair it
with the Mac, sync through R2, and serve agent recall from the local broker.
After that, retire the packet-sync worker (0.3.1).

## Where things stand

- **Mac** (authority device): packet sync was retired on 2026-10-22 (Phase 7a).
  The broker syncs to R2 every 120 s (bucket `agentmesh`, prefix `v1/`).
  Recall reads work+personal from the broker. Since PR #65, R2 listing is
  incremental, which is the precondition for adding a second device.
- **Windows**: the 0.3.1 worker still runs from logon task
  `\AgentMesh-9b6b3962c37aa75426290216` (C:\Users\krisb\.omp_memory). Since
  the Mac stopped exporting packets it receives nothing new. The 0.3.2 upgrade
  is abandoned; leave its Syncthing `.tmp` files alone.
- **W0 done 2026-10-24**: the OS key-store smoke passed (Credential Manager
  round-trip, no leftover item). Python 3.13.13 works; every pinned dependency
  has a cp313 wheel, so 3.11 is not required. Scratch clone:
  `C:\Users\krisb\agentmesh-smoke` (main 48e8988).

## Code gaps found while planning (block W3 and later)

| # | Gap | Evidence | Fix |
|---|---|---|---|
| G1 | R2 credentials load only from the macOS Keychain. | `storage/r2creds.py` accepts only `keychain:<service>` and runs `/usr/bin/security`. | Add a Windows Credential Manager backend, e.g. a `wincred:<service>` reference (or `keychain:` resolved per OS) with two generic credentials, `<service>/access-key-id` and `<service>/secret-access-key`. Reuse the `WindowsCredential` ctypes pattern from `oskeystore.py`, with the same refusal and missing errors and the same size checks. It must never fall back to the boto credential chain. Needs tests with an injected fake and a Windows CI pass (the matrix already has `windows-2025`). |
| G2 | There is no Windows service definition for the broker. | The Mac broker plist was rendered by hand (R2 sync plan). `bridge/services.py` renders a Task Scheduler XML only for the bridge. | Add `render` support for a broker task: a logon trigger, InteractiveToken, LeastPrivilege, restart on failure. Argv is `python -m agentmesh_log.cli broker run --home … --sync-interval 120 --s3-*`. Render only; the owner imports it with `schtasks /Create /XML`. Use `pythonw.exe` so no console window appears. |
| G3 | The recall wiring for Windows agents is unverified (resolved: direct broker call, see decision 3). | `memory-sync/profile_log_bridge.py` reads `<db>.profile-log.json` next to a SQLite database, and agent rules call `agentmesh.py --database <db> recall`. | Rules call `agentmesh-log recall --compat` directly (existing flag, `cli.py:78`). Add a renderer for the Windows `agentmesh-shared-memory.command.json`. |
| G4 | Windows text I/O. | Phase 4 note: CI failed on cp1252. | Any new file I/O uses `encoding="utf-8"`. Set `PYTHONUTF8=1` in the task environment as a guard. |

G1, G2 and the G3 command renderer go in one small PR. Per the usual split, Codex implements them and Claude
reviews them with mutation gates. 

## Owner decisions (answered 2026-10-24)

1. **Profiles**: the same as the Mac, so **work + personal**. W3 approves
   every scope the Mac's `settings.json` declares for both profiles, including
   `personal.scopes.inbox`.
2. **R2 credential**: **mint a new R2 API token** for Windows, scoped to
   bucket `agentmesh` with object read/write. The Mac token stays as it is.
   Revoking either token leaves the other device syncing.
3. **Recall path (G3)**: **direct broker call**. Windows agent rules run
   `agentmesh-log recall QUERY --compat --agent-key <key file> --limit N`.
   Windows has no SQLite DB and no `<db>.profile-log.json`. The W1 PR adds
   a renderer for the Windows `agentmesh-shared-memory.command.json` (argv
   only, run without a shell) and checks that the `--compat` output reads the
   same way as Mac recall for agents.

## Phases

Every phase lists a check that must pass and a rollback. Commands that change
the host are run by the owner in their own PowerShell window. The Windows
Claude session's terminal sends Ctrl-C to interactive commands, and `apply`,
`join` and presence prompts need console input with the keyboard set to English.

### W1 — Code (G1, G2, G4)

- Branch `claude/windows-profile-log`: PR with the Credential Manager R2
  backend, the broker task renderer, and tests on all three CI OSes.
- **Check**: CI is green on `windows-2025`. On the real PC, a credential
  smoke (like `smoke_os_keystore.py`) stores and loads a disposable R2-style
  pair and deletes it.
- **Rollback**: none needed; nothing deployed.

### W2 — Install the runtime under `%USERPROFILE%\.agentmesh`

Layout mirrors the Mac:
- `app\profile-log` holds a `git archive` of the pinned merge SHA (not a live
  clone).
- `venv\` is Python 3.13, installed with
  `pip install --only-binary :all: -r requirements.txt -r requirements-s3.txt`.
- `bin\agentmesh-log.cmd` is a wrapper.
- `profile-log\` is the broker home, `logs\`, `status\`, `settings.json`.

Steps:
1. Create the tree and the venv, then install the pinned app.
2. The owner mints the Windows R2 token in the Cloudflare dashboard, then stores it in Credential Manager with the G1 helper. The owner
   types it in; it never goes into a file or chat.
3. Write `settings.json` with packet sync disabled and the bridge not
   authority, with `dual_write: false`. Windows never imports from SQLite;
   the Mac stays the only authority.
4. Leave the old worker untouched.

- **Check**: `agentmesh-log settings validate` is ok, and
  `python -c "import agentmesh_log"` loads from `app\profile-log`.
- **Rollback**: delete `%USERPROFILE%\.agentmesh` (nothing is keyed yet).

### W3 — Pair as the second device

1. Windows: `agentmesh-log join --home %USERPROFILE%\.agentmesh\profile-log --s3-bucket agentmesh --s3-prefix v1/ --s3-endpoint <R2> --s3-credentials <ref>`.
   It prints the request ID and a QR code, then waits up to 10 min.
2. Mac (owner at the console): `agentmesh-log pair approve <request> --scope <decided scopes> --s3-…`
   Compare the approver fingerprint shown on the Mac with the one Windows asks
   for, then compare the 6-digit pairing code on both screens. If they differ,
   stop: that is `pairing_mismatch`, and nothing is granted.
3. The device key lands in Credential Manager (`agentmesh.profile-log/<device id>`)
   and `keystore.sealed` in the broker home.

- **Check**: `device list` on both machines shows two active devices with
  matching fingerprints. `cmdkey /list` shows exactly one
  `agentmesh.profile-log/…` item.
- **Rollback**: Mac `device remove <windows id>` (rotates the affected keys).
  Windows: delete the broker home and its Credential Manager item.

### W4 — Broker, sync and recall on Windows

1. Run `broker sync` once by hand and expect it to pull all events for the
   granted scopes. Then run `verify_remote` and expect ok.
2. Render the broker task (G2), have the owner import it, and log off and on
   once. Check that the named pipe `\\.\pipe\agentmesh-broker-<id16>` exists
   and that `sync-status.json` is ok.
3. `broker grant --name recall --read <scopes>` creates a local agent key.
   Install the rendered direct-recall command JSON (decision 3). Then install the Windows rule and command JSON
   for Claude Code and Codex on that PC.
4. `remember` a synthetic memory on Windows and confirm it reaches the Mac
   within two sync cycles. Do the same in the other direction.

- **Check**: recall from Windows returns rows with backend `broker`. Insight
  on the Mac shows the Windows heartbeat (platform `windows`) and the R2 IO
  counters. The Mac's R2 class-A calls per cycle stay roughly flat.
- **Rollback**: disable the broker task. Recall falls back to nothing (no
  stale-packet fallback), so if needed point the agent rules back at the old
  DB temporarily.

### W5 — Retire the 0.3.1 worker (no earlier than 7 days green after W4)

1. Disable then delete logon task `\AgentMesh-9b6b3962c37aa75426290216` and
   stop the worker.
2. Back up `C:\Users\krisb\.omp_memory`. Owner policy: ask consent first, use
   age encryption, and never upload plaintext to R2. Then move the folder into
   `%USERPROFILE%\.agentmesh\backups\archive\`. Don't delete it.
3. Remove the stale `installers\AgentMesh-0.3.2` Syncthing folder share, then
   delete the scratch clone `C:\Users\krisb\agentmesh-smoke`.

- **Check**: the agent rules on Windows no longer reference `.omp_memory`,
  and recall still works after a reboot.
- **Rollback**: re-enable the logon task and restore the folder from the
  archive. Packet sync on the Mac is retired, so this only restores old,
  read-only recall.

## Out of scope

- The control-plane supervisor on Windows (M1/M2 are Mac and launchd only).
- Windows Hello presence (console presence only for now).
- LAN pairing.
