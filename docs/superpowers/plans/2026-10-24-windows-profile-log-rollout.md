# Windows profile-log rollout (DESKTOP-DOJ622I)

Date: 2026-10-24. Status: W0 done; W1 implemented and reviewed (19/19 mutation gates killed); native Windows verification pending.

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
| G1 | R2 credentials load only from the macOS Keychain. | `storage/r2creds.py` previously accepted only `keychain:<service>`. | W1 adds explicit OS dispatch: `keychain:` on macOS, `wincred:` on Windows, neither on other OSes. Windows uses two generic credentials, `<service>/access-key-id` and `<service>/secret-access-key`, storing exactly 32/64 lowercase ASCII hex characters. `r2-credentials store/check --ref wincred:<service>` prompts with getpass, validates before writes and verifies load-back. Native API is injectable; boto always receives explicit keys. Owner-only `smoke_r2_wincred.py` removes both disposable targets. Windows CI/native smoke remain owner/reviewer checks. |
| G2 | The Windows broker task lacks interactive principals, persistent restart and log redirection. | `render_profile_units` already renders a broker task; its restart count was 3 and it lacked a Principals block. | W1 adds matching Principal/Actions IDs, InteractiveToken, LeastPrivilege and RestartOnFailure Count 999 for the broker. Windows `logs` appends `--log-dir`; broker run opens UTF-8 append logs before startup and tolerates absent pythonw streams. Catch-up settings and macOS/Linux output stay unchanged. Render only; the owner imports it with `schtasks /Create /XML` and supplies `pythonw.exe`. |
| G3 | Windows agents need direct broker recall without a SQLite database. | Existing rules call `agentmesh.py --database <db> recall`. | W1 adds `install_agent_rules.py --broker-agent-key <absolute path> --python <absolute path>`, excluding `--database` and `--app-dir`. The command manifest contains `[python, -m, agentmesh_log.cli, recall, --compat, --agent-key, key]`; POSIX/PowerShell instructions append the query as one argument. Database-mode output is unchanged. The W1 report documents material differences in direct compat rows, evidence and backend metadata. |
| G4 | Windows text I/O. | Phase 4 note: CI failed on cp1252. | Any new file I/O uses `encoding="utf-8"`. Task Scheduler has no per-action environment, so explicit encodings are the only guard. |

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

### W1 — Code (G1, G2, G3; UTF-8 I/O from G4)

- Branch `claude/windows-profile-log-w1`: PR with the Credential Manager R2
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

- **Check**: direct `recall --compat` from Windows returns rows (it has no top-level `backend` field and `evidence` is always `[]`; see the W1 report). Insight
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
