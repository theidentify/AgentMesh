# Passphrase-protected identity key (issue #6)

`identity.json` in the security directory normally stores the Ed25519 device key as raw hex
(`private_key`). `agentmesh identity-protect` replaces it with a passphrase-encrypted copy
(`protection`). It is an explicit operator step: nothing migrates an identity automatically.

## Threat model

Protects against:

- copies of the identity directory at rest: backups, disk images, synced or stolen files. Without
  the passphrase, a copy cannot sign packets or ACKs.
- tampering with the stored key or its public fields. Decryption fails closed.

Does **not** protect against:

- code already running as the same user while the key is unlocked. Such code can read process
  memory or capture the passphrase prompt.
- a weak or reused passphrase. Offline guessing is slowed by scrypt but not prevented. The minimum
  is 12 characters; use a long, unique passphrase.
- loss of the passphrase. There is no recovery: the identity has to be replaced through pairing.

## Format

The public fields (`format`, `key_id`, `public_key`, `sender`, `group`, `node`) stay exactly as they
were, so trust entries, pairing and the database binding are unchanged. `private_key` is replaced by:

| field | value |
|---|---|
| `format` | `agentmesh-key-protection-v1` |
| `kdf`, `n`, `r`, `p`, `salt` | scrypt, N=2^17 (about 128 MiB), r=8, p=1, 16-byte random salt |
| `cipher`, `nonce`, `ciphertext` | AES-256-GCM, 12-byte random nonce, 32-byte key + 16-byte tag |

The associated data is a domain label plus the canonical public fields, so the ciphertext cannot be
moved onto another identity. The parameters are bounds-checked (N between 2^14 and 2^20, a power of
two; fixed r and p; exact field set and lengths) before any key derivation, so a corrupted file
cannot force excessive work. A wrong passphrase and a damaged file give the same error by design.
The cryptography comes from the pinned `cryptography` package (`requirements-security.txt`).

## Migration

1. Stop or drain the worker, and keep a private offline backup of the security directory if your
   procedure requires one. The backup holds the raw key, so protect it as well.
2. In an interactive console, run:

   ```
   agentmesh identity-protect --security-dir <security dir>
   ```

   The passphrase is asked twice with no echo. It is never read from arguments, environment
   variables, files or piped input. Without an interactive console the command refuses and
   changes nothing.
3. The protected file is unlocked and checked before an atomic replace, then read back and checked
   again. An interruption leaves either the original or the protected file, never a partial one.
   Output is `{"key_id": ..., "status": "protected"}`, with no secrets.

Refusals (mismatched or short passphrase, already protected, no console) leave the file untouched.

## Worker and restart behaviour

- A protected key starts locked unless an explicitly enabled OS unlock path succeeds or an
  interactive unlock supplies the passphrase. Each worker process reads the OS store at most once
  per identity and caches either the unlocked key or the refusal. Restart after fixing a refusal.
- While locked, public checks, trust and verification still work without accessing the OS store.
  Signing refuses with `passphrase-protected identity is locked; enable OS unlock or unlock interactively`,
  or the specific OS refusal below.
- The unattended worker (`worker-run`, `worker-start`, `memory_sync once/watch`) opts into OS unlock
  and refuses a locked key before sync writes. It never asks for a passphrase and never falls back
  to plaintext or unsigned packets. Under `legacy` policy the worker does not use the identity.
- `wizard-resume` tries OS unlock first, then asks for the passphrase if needed.
- Disabling removes the persisted OS path. A process that already holds the key in memory remains
  unlocked until it exits; stop/restart workers when changing unlock policy.

## Limitations

- On SSDs, copy-on-write and journaling file systems, or after earlier backups, the old raw key
  bytes may remain on disk blocks or in snapshots after migration. If that matters, treat the key as
  exposed and rotate it through pairing instead.
- There is no passphrase change or unprotect command yet.
- The KDF cost is fixed at N=2^17. Unlocking takes about half a second and about 128 MiB of memory.

## OS-protected unattended unlock (#7)

Use these explicit operator commands on a host whose worker runs as the same user:

```
agentmesh identity-os-unlock enable --security-dir <security dir>
agentmesh identity-os-unlock status --security-dir <security dir>
agentmesh identity-os-unlock disable --security-dir <security dir>
```

First protect the identity with `identity-protect`. Enable asks for that passphrase once in an
interactive console, creates a random 32-byte wrapping key K in the OS store, and adds an
`os_unlock` envelope to `identity.json`. The passphrase envelope remains the portable unlock path.
Nothing enables this automatically, and release signing keys are excluded.

On Mac, the worker is a LaunchAgent in `gui/<uid>` while the user is logged in. The interim
`macos-keychain-file` backend invokes `/usr/bin/security` against the account's explicit
`Library/Keychains/login.keychain-db` path, obtained from the account database rather than `HOME`.
Apple TN3137 describes the file-based keychain as “on the road to deprecation”; it is not officially
deprecated. No deprecated keychain API is used. The default keychain and search list are not used.
K goes over stdin, never argv; the item retains the security tool's default ACL.

On Windows, the logon task uses LogonType 3 (InteractiveToken). `windows-credential` uses current
CredWriteW/CredReadW/CredDeleteW APIs for a generic credential with local-machine persistence,
protected by that user's DPAPI. PowerShell is not used. The item is named
`agentmesh.identity-unlock/<key_id>`; on Mac the service is `agentmesh.identity-unlock`, with `key_id`
as its account. Linux has no backend.

The envelope has exactly `format`, `backend`, `nonce`, and `ciphertext`, with format
`agentmesh-os-unlock-v1`, a 12-byte nonce and 48-byte ciphertext encoded as hex. AES-256-GCM binds
the domain label, backend and canonical public identity. Every enable generates a fresh K and seals
once. Future passphrase changes or restore operations must also generate a fresh K. Unknown backends,
malformed fields, and an envelope without `protection` are rejected. A known backend for another OS
stays locked. Wrong K or authenticated-field tampering fails closed.

Enable and disable hold `.os-unlock.lock` exclusively. Enable stores and verifies K before atomic
replacement and verifies the committed file. If replacement reports an error after committing, K is
retained. Disable removes the envelope first and then deletes K, without a passphrase. A deletion
failure is an error: run disable again to remove a leftover item. Read-only status reports `key_id`,
`protected`, `os_unlock`, `backend`, `available`, `reason`, and `stray_item`. It reads the store to
verify decryption or detect a leftover item; it never prints K or the device key.

### Threats and portability

- Any process of the same user can read K using the security CLI or CredRead. With OS unlock enabled,
  the device key is effectively always unlocked for that user. Same-user item-name squatting can
  cause lockout, but cannot cause a different key to decrypt successfully.
- Copying the identity directory alone to another account or machine leaves it locked. OS backups
  can also carry K: Time Machine or Migration Assistant can copy `login.keychain-db`, recoverable
  with the login password. A Windows profile backup plus the password, or the domain DPAPI backup
  key, can decrypt the credential. Such a backup is only as strong as the **weaker** of the
  passphrase and the OS login password. The portable backup remains `identity.json` with `protection`
  plus the passphrase; issue #8 adds backup/restore commands.
- A locked or password-out-of-sync Mac keychain can display a system unlock dialog. The worker OS
  read is capped at 15 seconds and attempted at most once per identity per process, including after
  refusal. Killing the security process does not promise that the system dialog closes.
- Items for old or rotated key IDs are not automatically cleaned up. Status only checks the current
  key ID. A leftover K plus an older identity-file backup containing its envelope still unlocks
  that identity; deletion failures must be resolved.
- Items created by the security CLI use Apple's tool ACL/partition defaults. The planned signed
  SecItem data-protection-keychain or Secure Enclave upgrade must re-create the item rather than
  reuse the old item. It requires Developer ID signing, a provisioning profile with keychain access
  groups, and an app-like bundle. That upgrade is outside this feature.

### Fixed refusals

| Reason | Operator action |
|---|---|
| `OS unlock is not supported on this platform` | Use the matching OS/account or unlock interactively with the passphrase. |
| `OS unlock is already enabled` | Inspect status; disable before explicitly enabling again. |
| `OS unlock item is not available for this account` | Check the account and store; use the passphrase, then disable and re-enable if needed. |
| `OS key store refused access` | Restore the user's logged-in key-store access, then restart the worker. |
| `OS key store did not respond` | Resolve the locked keychain or system dialog, then restart the worker. |
| `OS unlock could not open the identity: wrong key or damaged file` | Inspect/restore a trusted identity backup; use the passphrase path and explicitly recreate OS unlock. |
| `identity must be passphrase-protected before enabling OS unlock` | Run `identity-protect` first. |
| `OS unlock item could not be removed` | Restore store access and run disable again; inspect `stray_item` in status. |
| `OS unlock is already in progress` | Wait for the other enable/disable operation to finish, then retry. |

Native and frozen OS-store tests are CI-only, opt-in, and use temporary keychains or random test
credential targets with cleanup. Never enable native tests on a production workstation. Normal
standalone binaries contain no test-store override; CI builds a separate disposable smoke binary.
