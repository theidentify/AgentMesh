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

- A protected key is **locked** in every new process unless an interactive unlock supplies the
  passphrase. An unlock never persists: after a process exit or restart the key is locked again.
- While locked, public checks, trust and verification of peer packets still work. Signing refuses
  with `passphrase-protected identity is locked; unattended unlock is not available yet (requires
  OS-protected key access)`.
- The unattended worker (`worker-run`, `worker-start`, the `memory_sync once/watch` CLI) refuses a
  locked key **up front, before any write**, on the first start and on every restart. There is no
  plaintext or unsigned fallback. Under `legacy` policy the worker does not use the identity, so
  protecting it changes nothing there.
- `wizard-resume` asks for the passphrase once per run, and only when the identity is protected.
- Unattended unlock through Keychain or DPAPI is issue #7. Until it exists, do not protect the
  identity of a host whose worker runs signed (`required` policy), because that worker will stop
  signing.

## Limitations

- On SSDs, copy-on-write and journaling file systems, or after earlier backups, the old raw key
  bytes may remain on disk blocks or in snapshots after migration. If that matters, treat the key as
  exposed and rotate it through pairing instead.
- There is no passphrase change or unprotect command yet.
- The KDF cost is fixed at N=2^17. Unlocking takes about half a second and about 128 MiB of memory.
