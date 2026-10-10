# Windows existing-install program installation (development RC.5)

This is a Windows-only CLI installer for **an already initialized installation**.
It installs immutable program copies and optionally registers a per-user logon
launcher. It is not a Windows service, before-login startup, crash supervisor,
GUI, baseline join, signed OTA updater, or security-activation wizard.

Implementation/source fixtures are distinct from native Windows verification.
The native source and frozen gates must run on Windows before a bundle is handed
out. Actual login execution, launcher console appearance, logout survival, and
recurring sync on the operator's host remain a separately approved host gate.
The task's `Hidden` setting controls task visibility, not console suppression.
No reboot or production restart is requested by these instructions.

## Confirmations (RC.9+)

Each command prints one plan (from -> to version, numbered steps, expected time,
rollback) and asks **once**. This replaces the earlier per-step typed words; the
historical INSTALL/UPGRADE/BIND/REPLACE/ENABLE/DISABLE gates below now mean that
single decision.

| Command | Interactive console | Script / CI (not a console) |
|---|---|---|
| `windows-install`, `windows-upgrade`, `windows-autostart` | one-line select `Upgrade now` / `Cancel` (arrow keys, `y`/`n`, Enter; default **Cancel**); `[y/N]` where keys are unavailable | refused unless `--yes` |
| `windows-rollback`, `windows-uninstall` | type the word (`ROLLBACK`, `UNINSTALL`), case-insensitive, with live match colouring; 3 attempts with a "Did you mean" hint | refused unless `--yes --confirm ROLLBACK` (or `UNINSTALL`) |

Esc, Ctrl+C, Enter on an empty word, or 5 minutes without input cancel with
`Cancelled. Nothing was changed.` and exit code 2. `--gui` (used by the click
helpers) shows the same plan in a native Yes/No dialog with **No** as the default and a
result dialog at the end; it is never used by the logon launcher, scripts or `--yes`.
Rollback and uninstall stay console-only. Progress is a single line per stage that
updates in place and ends with a kept `[OK]`/`✔` or `[FAIL]`/`✖` line.

## Install program files only

Extract the development ZIP locally **outside Syncthing**. Keep `agentmesh.exe`
and its adjacent `BUILD.json` together. No Python or Git is required by the frozen
CLI. `Install-AgentMesh.cmd` first performs a read-only plan against the candidate
`%LOCALAPPDATA%\AgentMesh\data\runtime.json`, then asks for `INSTALL`. Missing,
unsafe, or mismatched runtime state is refused; the helper never creates a DB or
runtime, repairs permissions, enables autostart, or stops/starts a worker.

For a custom existing runtime, use PowerShell and its explicit absolute path:

```powershell
.\agentmesh.exe windows-install --runtime 'C:\absolute\existing\runtime.json' --dry-run
.\agentmesh.exe windows-install --runtime 'C:\absolute\existing\runtime.json'
```

The default program root is `%LOCALAPPDATA%\AgentMesh\programs`. Its parent must
already exist; it must be separate from existing app code, DB/data, runtime,
workflow, identity/trust, security state, and exchange. A common AgentMesh
container may contain separate sibling directories. Use `--program-root` to
select another absolute root. Symlink/junction/reparse ancestors are refused.
A preexisting root is refused for initial installation, not adopted or re-ACL'd.
Only newly owned program storage is protected. Existing private state is never
repaired implicitly.

The new owner-only `installed.json` is both installed state and local recovery
descriptor. `--installed-state` may explicitly select it but must name exactly
`PROGRAM_ROOT\installed.json`. It is not part of the distributed ZIP. Version
directories use the RC version plus the **full source SHA**. The binary and copied
BUILD manifest are hash checked on each operation. BUILD metadata verifies
checksum consistency and development-distribution claims, **not trusted
publisher identity**. The descriptor records ownership, file hashes/directory
identity, scope, the exact task snapshot, and incomplete transitions.

The selected program version is not proof of which worker is currently running.
Initial installation leaves the old program and worker alone. Decline or EOF at
the first confirmation, and every dry run, perform no writes. Planning uses an
immutable informational DB snapshot that may exclude WAL; after confirmation,
current authoritative scope must match before mutation. Authoritative SQLite
reads may create normal WAL/SHM sidecars; the installer never copies/restores the
DB or manually deletes its sidecars.

## Opt-in start at login

First prove the existing managed worker stopped. For a legacy/unsigned runtime,
`--legacy-drained` means the operator explicitly approved that the prior legacy
worker was already drained; a diagnostic projection never supplies that approval.
Then independently approve the `ENABLE` gate:

```powershell
.\agentmesh.exe windows-autostart --runtime 'C:\absolute\existing\runtime.json' --enable --interval 60 --timeout 60 --legacy-drained --dry-run
.\agentmesh.exe windows-autostart --runtime 'C:\absolute\existing\runtime.json' --enable --interval 60 --timeout 60 --legacy-drained
```

Missing managed metadata means **unknown/unmanaged**, not proven stopped. For
required/signed policy with no managed record, separately approve that all prior
source/ad-hoc workers exited using `--unmanaged-drained`. The legacy approval
already includes prior unsigned source-worker drain. Both policies also require
the shared cycle lock to be free while changing bindings/removing programs; an
idle cycle lock alone never proves a recurring source worker exited.

Omit `--legacy-drained` for required/signed policy. The flag does not change policy
or create keys. Enabling does **not** start the worker now: only the next matching
user logon triggers `INSTALLED_EXE worker-start` with the exact existing runtime
and approved interval/timeout. Future legacy starts retain the explicit approval.
Disable only the launcher with a separate `DISABLE` gate; an existing worker is
not stopped:

```powershell
.\agentmesh.exe windows-autostart --runtime 'C:\absolute\existing\runtime.json' --disable
```

The launcher is bound to the current SID, InteractiveToken (3), least privilege,
and one logon trigger with that same SID. No password, elevation, SYSTEM account,
registration trigger, restart-on-failure, wake, demand start, or hard termination
is used. Battery blocking/stopping are false, and overlapping launcher instances
use IgnoreNew. The task is not hidden. A task's Running state is **not** worker
health; use `worker-status` and recurring healthy cycles instead. A foreign or
changed task is refused, never overwritten. Every register/remove operation is
followed by exact-target readback. `--task-name AgentMesh-...` can explicitly
select a name, but cannot bypass ownership, SID, or snapshot verification.

## Windowless logon entry (RC.6+)

From RC.6 the bundle also ships `agentmeshw.exe`, a GUI-subsystem launcher, so
Windows allocates no console at logon. It accepts only `worker-start`, runs the
adjacent console `agentmesh.exe` with `CREATE_NO_WINDOW`, and returns its exit
code; the worker lifecycle is unchanged. The logon task targets `agentmeshw.exe`
for versions that include it and `agentmesh.exe` for older versions, so program
rollback to RC.5 restores the previous (console) task entry.

To move an installed RC.5 to RC.6 with autostart already enabled, run
`Upgrade-AgentMesh.cmd` from the extracted RC.6 folder: it plans first, then the
UPGRADE and REPLACE gates request a cooperative stop, rebind the task to the new
version, and start and verify the new worker. No sign-out is needed.

## Onedir program layout (RC.8+)

From RC.8 the Windows program is a PyInstaller **onedir** folder: `agentmesh.exe`,
`agentmeshw.exe` and a shared `_internal/` runtime. Nothing is unpacked at start,
so the logon chain (launcher, `worker-start`, `worker-run`) no longer waits for a
onefile extraction that antivirus rescans on every launch. `BUILD.json` lists
every `_internal/...` file by relative path. The installer copies and hashes the
whole tree, refuses unlisted, linked or unsafe paths, and uninstall removes only
the listed files and their now-empty directories. RC.5-RC.7 flat versions stay
valid in history, so program rollback still works.

## Upgrade, program rollback, uninstall

All commands accept `--dry-run` and require the same explicit existing runtime.
Upgrade from the new extracted bundle, not by replacing a directory in place:

```powershell
.\agentmesh.exe windows-upgrade --runtime 'C:\absolute\existing\runtime.json'
```

`UPGRADE` stages verified files while a healthy worker can continue. A subsequent
`BIND` gate selects the new program/task binding **only with the worker proven
stopped and its lifetime lock drained**. Declining BIND leaves the new version
staged and the old selection unchanged. No implicit start or stop occurs.
For legacy binding, include the separately approved `--legacy-drained` flag.

An explicitly approved live upgrade adds `--replace`. Its distinct `REPLACE`
gate requests nonce-cooperative stop and lock drain, selects the new binding,
including when the selected installed version already matches the candidate
(installed selection does not identify the current running executable),
then starts the exact installed executable and verifies a healthy cycle plus a
subsequent cycle for the same new instance. It never kills a metadata-selected
PID. Timeout or uncertain readback leaves private recovery state and fails closed;
it does not restore SQLite or pretend the old worker is running. Native frozen
live replacement remains an execution gate, separate from the disabled-task
installation smoke.

```powershell
.\agentmesh.exe windows-rollback --runtime 'C:\absolute\existing\runtime.json' --legacy-drained
.\agentmesh.exe windows-uninstall --runtime 'C:\absolute\existing\runtime.json' --legacy-drained
```

`ROLLBACK` selects only a previous intact version owned by this installer. The
initial old ad-hoc/legacy executable is not imported automatically. Rollback is
program/task-only and requires proven stopped execution; it never restores DB,
identity, trust, workflow, runtime, security state, or signing policy, and does
not start a worker. Uninstall requires `UNINSTALL` and proven stopped execution,
removes only the exact owned task and individually verified program files, and
leaves all existing private data/config plus the local ownership/recovery records.
Use an external extracted bundle to uninstall: Windows may refuse removal of an
installer executable that is itself currently executing inside program storage.
Unknown files, changed hashes, uncertain ownership, stale records, changed scope,
and changed task definitions block removal instead of recursive cleanup.

The complete previous task snapshot is retained through the startup phase until
recurring worker health succeeds. If `installed.json` says `recovery_required`,
retain it and all program versions for operator review. Automatic recovery from
partial/hostile/concurrently changed
state is intentionally refused. The saved transition/snapshot identifies what
was attempted; do not delete it, rerun bootstrap/setup-new, regenerate keys, or
restore a DB backup. Installer locking serializes cooperating operator commands;
it is not filesystem/SQLite crash atomicity or a hostile-writer security boundary.

## Verification and Microsoft contracts

Focused fixtures use real disposable files and SQLite with only the task adapter
injected. Production CLI always enforces native Windows. Native COM tests and
frozen smoke require `AGENTMESH_NATIVE_TASK_FIXTURE_ROOT` and isolated
`AgentMesh-Fixture-UUID` names; tasks are registered **disabled**, read back, and
removed without invoking them. Failed/uncertain fixtures must be retained.
The frozen program runs with a System32-only PATH, without Python/Git dependencies.
CI includes these gates before RC.5 packaging; RC.4 assets are not overwritten.

Official Task Scheduler contracts:
- [RegisterTaskDefinition and TASK_CREATE/TASK_UPDATE](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskfolder-registertaskdefinition)
- [Principal.LogonType](https://learn.microsoft.com/en-us/windows/win32/taskschd/principal-logontype)
- [LogonTrigger.UserId](https://learn.microsoft.com/en-us/windows/win32/taskschd/logontrigger-userid)
- [RestartInterval minimum and optional restart policy](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-restartinterval)

The adapter invokes OS-owned absolute Windows PowerShell with `-NoProfile`,
`-NonInteractive`, fixed encoded code, and environment JSON for all names/paths.
It clears inherited PSModulePath case-insensitively and uses OS-owned modules.
Do not disable SmartScreen, endpoint protection, ACL guards, or other OS controls
for an unsigned development binary.
