# OS-protected unattended unlock (issue #7): design

Status: approved in conversation on 2026-10-12. Stacked on `feat/encrypted-key-storage` (PR #26, issue #6).

## Goal

Let the unattended worker unlock a passphrase-protected device key (#6) without a console, through an
OS key store bound to the worker's user account. Keep a portable passphrase path. Never fall back to
plaintext or unsigned mode.

## Decisions

- **What the OS store holds:** a random 32-byte wrapping key `K`. It does not hold the passphrase or
  the raw device key.
- **One scheme on both OSes:** the same `identity.json` field, envelope, commands and refusal rules.
  Only the store that holds `K` differs.
- **Mac backend (interim):** `macos-keychain-file`, which runs `/usr/bin/security` against the
  file-based login keychain. Apple TN3137 (2026-09-24) calls the file-based keychain "on the road to
  deprecation". It is not officially deprecated. The user accepted it as an interim backend. The
  planned upgrade is the SecItem API with the data protection keychain, optionally Secure Enclave.
  That needs Developer ID signing, a provisioning profile with `keychain-access-groups`, and an
  app-like bundle (separate issue).
- **Windows backend:** `windows-credential`, `CredWriteW`/`CredReadW`/`CredDeleteW` through ctypes. A
  generic credential with `CRED_PERSIST_LOCAL_MACHINE`, protected by the user's DPAPI. These are
  current Win32 APIs. PowerShell is not used.
- **Not used:** any deprecated API. That includes `SecKeychain*`, `SecAccessCreate` and
  `SecKeychainSetUserInteractionAllowed`.

## Startup context (verified in code)

- **Mac:** the worker is a LaunchAgent in `gui/<uid>` (mac_replace.Launchd). It runs while the user is
  logged in, so the login keychain is available.
- **Windows:** the worker is a scheduled task with `LogonType 3` (InteractiveToken) at user logon
  (windows_task). It runs in the user's logon session, so the user's credentials and DPAPI are
  available.

## Format

`identity.json` gains one optional field:

```
{ <public fields>, "protection": {...#6...}, "os_unlock": {
    "format": "agentmesh-os-unlock-v1",
    "backend": "macos-keychain-file" | "windows-credential",
    "nonce": <12-byte hex>, "ciphertext": <48-byte hex> } }
```

- Encryption: AES-256-GCM under `K`. The associated data is the domain label
  `agentmesh-os-unlock-v1\0`, then the `backend` value, then the canonical public fields. The label
  is distinct from the passphrase envelope's label. Because `backend` is in the associated data,
  changing it is reported as tampering, not as "locked".
- `K` is single-use. Each enable generates a new `K` and encrypts exactly once under it, so a nonce
  is never reused. Anything that re-seals the key later (passphrase change, #8 restore) must generate
  a new `K`.
- `os_unlock` is valid only when `protection` is also present. Without it the file is rejected
  (`invalid private identity`). This means a portable passphrase path always exists.
- The field set and lengths are checked exactly, and `backend` must be a known value. An unknown
  backend is rejected as invalid. A known backend for another OS means locked.
- OS item name: service `agentmesh.identity-unlock`, account `<key_id>` on the Mac, and target
  `agentmesh.identity-unlock/<key_id>` on Windows. Binding the item to `key_id` stops a copied or
  renamed directory from picking up another identity's `K`.

## Backend interface

```
class Backend:
    name: str                       # value stored in os_unlock.backend
    def store(self, key_id, K): ...    # create or replace; raises OSUnlockError(reason)
    def load(self, key_id) -> bytes:   # raises OSUnlockError(reason)
    def delete(self, key_id): ...      # missing is not an error
```

`current_backend()` returns the backend for this OS, or None on unsupported platforms (Linux).
Tests inject a fake backend. Native tests pass a backend bound to a temporary keychain path (Mac) or
a random target prefix (Windows). They never touch the login keychain or real targets.

### macos-keychain-file

- **Store:** run `/usr/bin/security -i` and send
  `add-generic-password -U -s <service> -a <key_id> -w <hex K>` over **stdin**, so `K` is never in
  argv. Do not pass `-A` or `-T`: the item ACL keeps its default creator, `/usr/bin/security`.
- **Load:** `/usr/bin/security find-generic-password -s <service> -a <key_id> -w`. The value comes
  through a stdout pipe and must be exactly 64 hex characters.
- **Delete:** `/usr/bin/security delete-generic-password -s <service> -a <key_id>`.
- **Always name the keychain explicitly.** Store, load and delete all pass the login keychain's
  absolute path as the trailing keychain argument. The path is
  `<pw_dir>/Library/Keychains/login.keychain-db`, where `pw_dir` comes from `pwd.getpwuid(os.getuid())`,
  never from `HOME`. So the default keychain and the search list are never used, and a keychain
  planted at the front of the search list is never read. Tests replace only this path, with a
  temporary keychain, so CI runs the same code path as production.
- Minimal environment: exactly `{'PATH': '/usr/bin:/bin', 'HOME': <pw_dir>, 'LANG': 'C'}`.
- `key_id` is checked inside the backend (exactly 64 lowercase hex characters) before it is used in
  any command line or `-i` script. The `-i` script is a single line, and stdin is closed after it.
- Exit status from `security -i` is advisory, because interactive mode can continue after a failed
  command. Store succeeds only when a load-back returns exactly the stored `K`.
- Timeout: 15 seconds, after which the process is killed. Stdout and stderr are read through pipes
  and are never put into an exception, a log or a report.
- A dialog may still appear. If the login keychain is locked while the user is logged in (password
  changed or reset so it's out of sync, auto-lock settings), `security` can open a SecurityAgent
  unlock dialog, and killing `security` doesn't promise the dialog closes. The interim backend
  therefore does **not** promise "no prompt". It promises that the worker never blocks longer than
  the timeout and never asks more than once per process (see the once-per-process rule under
  Unlock order).

### windows-credential

- `CredWriteW` with `CRED_TYPE_GENERIC`, the target name above, a 32-byte `CredentialBlob`, and
  `CRED_PERSIST_LOCAL_MACHINE`.
- `CredReadW`. Check `CredentialBlobSize == 32` before copying with `ctypes.string_at`, and call
  `CredFree` in `finally`.
- `UserName` is set to the fixed value `agentmesh`, which is not secret.
- ctypes: `WinDLL('advapi32', use_last_error=True)` and `ctypes.get_last_error()`, never
  `GetLastError` directly. Every call declares `argtypes` and `restype`.
- `CredDeleteW`. `ERROR_NOT_FOUND` counts as success.
- Error mapping by winerror:
  - `ERROR_NOT_FOUND` (1168) → missing.
  - `ERROR_NO_SUCH_LOGON_SESSION` (1312) or access denied (5) → refused.
  - Anything else → refused.

## Unlock order

`Security(dir, unlock=None, os_unlock=False)`:

1. A raw `private_key` (legacy) loads as today.
2. `protection` with `os_unlock` requested and an `os_unlock` envelope whose backend matches this OS:
   load `K` and decrypt. On success the key is unlocked.
3. Otherwise, if an interactive `unlock` was given, use the passphrase (#6).
4. Otherwise the key is locked.

Rules:

- **Worker** (`sync_worker.run_once`, `memory_sync` once/watch):
  `os_unlock=True`, no interactive unlock. A failure in step 2 leaves the key locked, and
  `require_unlocked()` then raises the specific OS reason, never a passphrase prompt.
- **Wizard** (`security_wizard.interactive`): `os_unlock=True` plus the interactive unlock. If the OS
  path fails, it asks for the passphrase.
- **Public checks** (`install_adopt.validate`, the diagnostics status) never call the OS store.
- A failure to decrypt `os_unlock` (wrong `K` or tampering) is reported as such. It is never silently
  skipped to the passphrase in unattended code.
- **Once per process.** `run_once` builds `Security` every cycle (sync_worker.py:30). The OS store is
  read at most once per process, and the result is cached in memory: either the unlocked key, or
  the refusal reason. After a refusal the worker doesn't call the store again until it restarts. It
  keeps refusing with the cached reason, so a stalled keychain or a dialog can't repeat every cycle.
- Every `OSUnlockError` is raised `from None`. Its message is only a fixed reason from the allowlist,
  never subprocess output. A `TimeoutExpired` or `CalledProcessError`, which may hold captured
  stdout containing part of `K`, never stays attached as `__cause__` or `__context__`.
- The existing `LOCKED` reason from #6 ("unattended unlock is not available yet") is reworded to
  `passphrase-protected identity is locked; enable OS unlock or unlock interactively`. The matching
  `KEY_REASONS` entry changes with it.

## Commands

All are explicit and operator-run. Nothing enables OS unlock automatically.

- **Locking:** enable and disable hold an exclusive lock on `<security dir>/.os-unlock.lock`
  (runtime_lock.lock with timeout 0) for the whole operation. The "already enabled" check, the store
  and the replace all happen under that lock.
- **`agentmesh identity-os-unlock enable --security-dir DIR`**
  - Requires a protected identity and a supported backend.
  - Asks the passphrase once on an interactive console, generates `K` with `os.urandom(32)`, and
    seals the device key under it.
  - Order: `backend.store` first, then build the new file, then verify it by loading `K` back from
    the store and decrypting. Then the atomic replace (`write_local`), then a reread through
    `Security(os_unlock=True)`.
  - If anything fails after the store, reread `identity.json` and decide from what is actually on
    disk. `write_local` can raise after `os.replace` has already happened, for example when the
    directory fsync fails. If the file holds the new envelope, keep the OS item and report the
    error. Otherwise, delete the OS item it just stored. The file is never left with `os_unlock` but
    no `K`.
  - If `os_unlock` already exists, refuse (`OS unlock is already enabled`).
- **`agentmesh identity-os-unlock disable --security-dir DIR`**
  - Removes `os_unlock` from the file first (atomic replace), then deletes the OS item.
  - If the delete fails, exit non-zero with the fixed reason `OS unlock item could not be removed`.
    A leftover `K` is not harmless: together with an older copy of `identity.json` that still has
    `os_unlock` (a backup, a sync, a rollback), it unlocks the key. Running disable again when
    `os_unlock` is already absent still deletes any leftover item.
  - Needs no passphrase. Removing an unlock path only lowers what can unlock the key.
- **`agentmesh identity-os-unlock status --security-dir DIR`** (read-only)
  - Prints `{"key_id", "protected", "os_unlock": "absent"|"enabled", "backend", "available": bool,
    "reason", "stray_item": bool}`.
  - `stray_item` is true when `os_unlock` is absent but an OS item for this `key_id` still exists.
  - `available` comes from loading `K` and decrypting. `K` and the key are discarded and never
    printed.

New fixed reasons are added to `cli_errors.KEY_REASONS`:

- `OS unlock is not supported on this platform`
- `OS unlock is already enabled`
- `OS unlock item is not available for this account`
- `OS key store refused access`
- `OS key store did not respond`
- `OS unlock could not open the identity: wrong key or damaged file`
- `identity must be passphrase-protected before enabling OS unlock`
- `OS unlock item could not be removed`
- `OS unlock is already in progress`

## Separation from OTA release keys

- Release keys (`release-private.json`, ota_update) never get `os_unlock` and never call the OS store.
- `identity-os-unlock` refuses a file that isn't a device identity. `Security` already rejects
  `release-private.json` as `invalid private identity`.
- A test checks both, and also checks that `ota_update` never imports the OS-unlock module.

## Threats and limits (to add to KEY-PROTECTION.md)

- Any process of the same user can read `K` from both backends (`security` CLI, CredRead). This is no
  stronger than #6's "same-user code while unlocked" limit. With OS unlock enabled, the key is in
  effect always unlocked for that user.
- A copied identity directory alone doesn't unlock on another account or machine; it stays locked.
  But OS backups can carry `K` along:
  - Mac: Time Machine and Migration Assistant copy `login.keychain-db`. That file plus the login
    password recovers `K`.
  - Windows: a profile backup plus the password, or the domain DPAPI backup key, decrypts the
    credential.
  
  So with OS unlock enabled, the identity in such a backup is only as strong as the **weaker** of the
  passphrase and the OS login password. The portable backup is still `identity.json` with
  `protection` plus the passphrase. #8 adds a backup/restore command.
- Squatting on the OS item name from the same account (planting your own `K`) only causes a lockout,
  because decryption fails. That falls under the accepted same-user limit.
- Items for old or rotated `key_id`s are not cleaned up automatically yet. A future `status` will
  list them.
- Items created through the `security` CLI get the Apple tool's ACL/partition defaults. The SecItem
  upgrade must re-create the item rather than read the old one. That migration is planned in the
  upgrade issue.
- The Mac backend is interim, on the file-based keychain. The planned upgrade is the data protection
  keychain or Secure Enclave after signing.
- A keychain that is locked while the user is logged in causes a refusal after at most the timeout,
  once per worker process. A system dialog may appear (Mac interim backend).

## Testing

- **Unit tests (any OS, fake backend):**
  - enable, disable and status
  - unlock order
  - the worker uses the OS path; the wizard falls back to the passphrase
  - missing item, refused access and timeout all lead to refusal with no writes
  - wrong `K` or a tampered `os_unlock` → error
  - a backend for another OS → locked
  - unknown backend or `os_unlock` without `protection` → invalid
  - a failed replace after store → the OS item is deleted and the file is unchanged
  - a stray item is cleaned up
  - no secrets in files, reports or errors, including a fake process that times out after writing a
    `K`-like string: the string appears nowhere in the error, its `__cause__`/`__context__` chain or
    the report
  - store whose exit status says success while load-back fails → treated as failed
  - once per process: after one refusal, later cycles don't call the backend again
  - enable/disable lock: a second concurrent enable is refused
  - `write_local` raising after the replace → the OS item is kept and the file is consistent
  - disable whose delete fails → non-zero; `status` reports `stray_item`; a second disable removes it
  - `backend` value changed in the file → tampering error
  - restart: a fresh process unlocks through the OS path, and still refuses after disable
- **Native CI:**
  - macos-15 with a temporary keychain: store/load/delete, missing item, a locked keychain refusing
    within the timeout without hanging
  - windows-2025 with a random target: store/load/delete and missing item
  - a CI session that can't use Credential Manager reports `OS key store refused access`, which is
    acceptable and recorded
  - frozen smoke: `identity-os-unlock --help` and `status` on a disposable protected identity, plus a
    frozen `worker-run --once` against a disposable strict runtime with OS unlock enabled on the
    temporary keychain (Mac) or random target (Windows)
- **Native hosts** (disposable identity and runtime only, after the user approves sending steps):
  - a worker started from LaunchAgent / the logon task unlocks through the OS path
  - it still works with the screen locked
  - another account refuses
  - a deleted OS item refuses
  - Mac: login keychain locked under a LaunchAgent worker. Record whether a dialog appears and how
    many (expect at most one per process). Password changed so the keychain is out of sync.
    Startup right after reboot or login (LaunchAgent vs keychain unlock timing).
  - Windows: task switched to S4U or "run whether logged on or not" (expect 1312 → refused). Admin
    password reset, where the DPAPI master key is lost (expect refused, and the passphrase still
    works).
  - Identity directory copied to another machine or account → the specific locked reason, and the
    passphrase still works
  - the Windows steps go through the "Windows setup" session

## Out of scope

- Developer ID signing, the .app bundle, the data protection keychain and Secure Enclave on the Mac.
- TPM on Windows.
- Backup/restore commands (#8).
- Changing the passphrase or unprotecting.
- Migrating the real Mac or Windows identities.
- Activating signing.
