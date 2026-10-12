# Encrypted identity backup and verified restore (issue #8): design

Status: approved in conversation on 2026-10-13. Builds on #6 (passphrase protection, `key_protection.py`)
and #7 (OS unlock, `os_unlock.py`), both merged into main.

## Goal

An explicit, encrypted, portable backup of a device identity, and a restore that preserves the same
identity (key_id, public key, sender, group, node) only after validation. The backup must never make a
revoked key trusted again, and must never let the same identity run on two active devices.

Out of scope (#9): key rotation after compromise, recovery without a backup, SQLite data restore.

## Decisions (made by the user)

1. **A separate backup passphrase.** The whole backup is encrypted under a new backup passphrase. The
   device key inside is stored without the #6 envelope. Restore asks only for the backup passphrase,
   then forces a new identity passphrase. This works for raw (legacy) and protected identities alike.
2. **Peers are confirmed one by one.**
   - This device's own trust entry is always restored.
   - Current revocations win, and nothing is ever un-revoked.
   - A peer that exists only in the backup is pinned only after the operator types its full
     fingerprint. Otherwise it is skipped and re-pairs through the wizard.
   - If the current trust has revoked this device's own key, restore refuses.
3. **Exchange check plus a typed FENCED.**
   - Restore refuses if the exchange shows activity for this key or sender within 15 minutes.
   - In every case the operator types `FENCED` to confirm the old device is stopped or wiped.
   - After restore, OS unlock is off and the worker is not started.

## File format

One JSON file, `agentmesh-identity-backup-v1`:

```
{ "format": "agentmesh-identity-backup-v1",
  "manifest": { "key_id", "public_key", "sender", "group", "node", "created_at" },
  "kdf": "scrypt", "n": 131072, "r": 8, "p": 1, "salt": <16-byte hex>,
  "cipher": "aes-256-gcm", "nonce": <12-byte hex>, "ciphertext": <hex> }
```

- **Readable manifest.** `manifest` can be read without the passphrase, by `inspect`, `status` and the
  restore preview. It holds public data only, and `created_at` is an ISO-8601 UTC timestamp.
- **Associated data:** `b'agentmesh-identity-backup-v1\0' + signed.typed(manifest)`. Editing the
  manifest makes decryption fail.
- **Plaintext:** typed JSON
  `{"private_key": <32-byte hex>, "trust": <trust.json object at backup time>, "trust_captured_at": <ISO>}`.
- **Validation before the KDF:**
  - KDF parameters are bounds-checked (N a power of two in [2^14, 2^20], r = 8, p = 1).
  - The field set is exact.
  - The ciphertext is at most 1 MiB.
  - The manifest is checked with `signed.check_public`-style rules: hex lengths, a canonical UUID,
    a node from `SLOTS`, and `key_id == sha256(public_key)`.
- **Validation after decryption:**
  - `private_key` derives `public_key`.
  - The trust object passes `read_trust`'s schema rules.
  - The trust contains this device's own entry, equal to the manifest's public fields.
- `os_unlock` and `protection` are never in the backup.
- A wrong passphrase and a tampered file give the same error: `backup could not be opened: wrong
  passphrase or damaged file`.

## Commands

All are explicit and operator-run. Passphrases come only from `key_protection.console_passphrase`
(an interactive console, no echo). Typed words use the existing `console_prompt` style.

### `agentmesh identity-backup create --security-dir DIR --output FILE`

1. Refuse if `FILE` resolves inside a Syncthing folder (any ancestor holds `.stfolder`) or a git work
   tree (any ancestor holds `.git`). Refuse if `FILE` exists, which means no overwrite. Refuse if the
   parent directory is missing.
2. Load the identity. A raw identity loads directly. A protected identity is unlocked with the
   identity passphrase. OS unlock is **not** used: export requires the operator's passphrase.
3. Show the manifest. The operator types `BACKUP`.
4. Ask for a new backup passphrase twice: at least 12 characters, matching, and not equal to the
   identity passphrase when one was used.
5. Encrypt. Write with `O_EXCL`, owner-only (0600 on POSIX; `windows_acl` private on Windows). The
   write is atomic: a temp file in the same directory, then rename without replacing.
6. **Verify immediately:** reread the file, decrypt it with the same passphrase, and compare the key
   and public fields with the live identity. On failure, delete the new file and report.
7. Record the evidence (see Status).
8. Report `{"status": "created", "key_id", "file_sha256", "verified_at"}`. No path is echoed beyond
   what the operator gave.

### `agentmesh identity-backup verify --file FILE [--security-dir DIR]`

- Asks for the backup passphrase, then does the full validation above.
- If `--security-dir` is given and its identity's `key_id` equals the manifest, it also checks the key
  against the live identity (unlocking it is not needed: the public key is compared) and records the
  evidence.
- It writes nothing except the evidence record.

### `agentmesh identity-backup drill --file FILE [--security-dir DIR]`

1. Asks for the backup passphrase, validates, and restores into a **disposable** private temp
   directory. There is no exchange and no FENCED, because the drill identity is never adopted.
2. Creates a disposable peer identity in the same group on another slot, and pins the two to each
   other.
3. **Signed roundtrip:**
   - The restored key signs a probe packet with `Security.sign`.
   - The peer verifies it with `Security.verify`.
   - The peer signs an ACK with `security_wizard.signed_ack`.
   - The restored side verifies it with `security_wizard.verify_ack`.
4. Wipes both temp directories in `finally`.
5. Records `drill_at` in the evidence when `--security-dir` matches.
6. Reports `{"status": "drill-passed", "key_id", "file_sha256", "drilled_at"}`.

### `agentmesh identity-backup status --security-dir DIR` (read-only)

Reports the evidence for this identity's `key_id`:
`{"key_id", "backups": [{"file_sha256", "created_at", "verified_at", "drilled_at"}], "state"}`.

`state` is one of:
- `none`: no evidence.
- `verified`: at least one backup has `verified_at`.
- `drilled`: at least one backup has `drilled_at`.

A file's existence never counts as evidence.

### `agentmesh identity-restore --file FILE --security-dir TARGET --exchange EXCHANGE`

`--exchange` is required. Use the group's accepted Syncthing folder on this machine.

1. **Target:** `TARGET` must be an owner-only directory (it is created at 0700 if missing) with no
   `identity.json`. If `trust.json` exists, it is the *current trust*; otherwise there is none.
2. Ask for the backup passphrase and validate fully. Decrypt into a staging directory
   `TARGET/.restore-<uuid>` that is owner-only.
3. **Own key revoked:** if the current trust has `manifest.key_id` with `revoked: true`, refuse with
   `this key is revoked in the current trust; rotate instead (#9)`.
4. **Exchange activity:** refuse with `the old device looks active: recent exchange activity for this
   identity` when either of these was modified within the last 15 minutes:
   - `EXCHANGE/status/<node>.json`
   - any file under `EXCHANGE/signed-changes/<node>/` or `EXCHANGE/changes/<node>/`
   
   The exchange must have `.stfolder`. Use file mtimes. Reading content is not required; for
   `signed-changes`, also check that the packet `key_id` matches before counting it. Unreadable or
   ambiguous files count as activity.
5. Show the manifest and the backup's `created_at` / `trust_captured_at`. The operator types
   `FENCED`.
6. **Peers.** For each backup peer entry other than self:
   - Already in the current trust: keep the current entry; `revoked` = current OR backup. Never
     change a current entry otherwise.
   - Not in the current trust: show node, sender and the first 16 characters of the fingerprint. The
     operator types the **full** 64-character fingerprint to pin it (copied with `revoked` = the
     backup value), or presses Enter to skip it. Skipped peers are listed in the report.
   - Never call `signed.approve` for a key that is revoked anywhere, and keep `approve`'s slot rule.
     Two active entries for one (group, node) with different senders are refused.
7. Ask for a **new identity passphrase** twice (at least 12 characters). Write `identity.json` in the
   #6 `protection` form, using `key_protection.seal` with production N.
8. **Adopt:**
   - Write `identity.json` and the reconciled `trust.json` into staging, then move them into `TARGET`.
     `trust.json` is written first, with an atomic replace when one exists. `identity.json` is written
     last, as an exclusive create.
   - Read back with `Security(TARGET, unlock=<new passphrase>)` and compare the public fields and the
     key.
   - Remove staging.
   - If anything fails before `identity.json` is in place, `TARGET` is left as it was (any original
     `trust.json` is restored).
9. Report: `{"status": "restored", "key_id", "pinned": [...], "skipped": [...], "kept_revoked": [...],
   "next": ["identity-os-unlock enable (optional)", "worker-start", "identity-backup drill"]}`.
   - It does not enable OS unlock.
   - It does not start or stop any worker.
   - It does not touch any database.

## Status evidence

The evidence lives in `DIR/backup-status.json`: a private file written with `write_local`.

```
{ "format": "agentmesh-backup-status-v1", "key_id": <hex>,
  "backups": { <file_sha256>: {"created_at", "verified_at", "drilled_at"} } }
```

- Only `create`, `verify` and `drill` write it, and only after they succeed for this exact `key_id`.
- No paths, passphrases or keys are stored in it.
- `Security` and `read_trust` must tolerate this extra file in the security directory. Restore treats
  a stale one as absent: the target never holds one from before, and it is not copied from the
  backup.

## Safety rules

- Secrets (private key, passphrases, backup plaintext) never appear in argv, environment variables,
  logs, errors or reports. Errors use fixed reasons, added to `cli_errors.KEY_REASONS`, and are raised
  `from None` where a lower exception could carry data.
- Every new file and directory is owner-only. Staging and drill temp directories are removed in
  `finally`.
- No network, no Syncthing writes, no git operations.
- Restore never starts, stops or signals a worker, never touches a database, and never enables OS
  unlock or signing.

## Testing

- **Unit tests** (disposable identities, a fast KDF through an injectable `n` that is test-only):
  - **create:**
    - round trip; manifest equals the live identity
    - refusal inside `.stfolder` or `.git`
    - no overwrite
    - mismatched or short passphrases
    - the backup passphrase equal to the identity passphrase is refused
    - `BACKUP` not typed
    - no console
    - a raw identity can be backed up
    - a protected identity needs its passphrase
    - OS unlock is not used
  - **open:**
    - wrong passphrase
    - tampering of the ciphertext, nonce or each manifest field
    - corrupted KDF parameters rejected before any KDF work
    - an oversized ciphertext
    - trust missing the self entry
  - **verify and status:**
    - evidence is written only on success
    - status reads `none`, `verified`, `drilled`
    - file existence alone is never `verified`
  - **drill:**
    - the signed roundtrip passes
    - temp directories are gone afterwards, including after an injected failure
  - **restore:**
    - same key_id, sender, group, node and public key
    - the new identity passphrase works and the old one is not needed
    - own key revoked → refuse
    - recent `status/<node>.json` or packet → refuse
    - old activity → allowed
    - `FENCED` not typed → refuse
    - a target with `identity.json` → refuse
    - peers: revocation wins, nothing is un-revoked, a typed full fingerprint pins, Enter skips, a
      wrong fingerprint skips with a message
    - an interrupted adopt leaves the target unchanged
    - staging is removed
  - no secret in any report, error or file other than the encrypted backup and the protected identity
- **Restart:** a restored identity loads in a fresh process with the new passphrase. Combined with #7,
  `identity-os-unlock enable` works on it.
- **Frozen smoke (native CI):**
  - `identity-backup --help`, `identity-restore --help`
  - `status` on a disposable identity
  - without a console, `create` and `restore` refuse and change nothing
- The packaging lists include the new module (`HIDDEN_MODULES` and `build_package.py`).

## Documentation

`memory-sync/KEY-PROTECTION.md` gains a "Backup and restore" section:
- the format
- the commands
- where to keep the backup (not in Syncthing or git, offline media)
- passphrase custody (store the backup passphrase separately from the file and from the identity
  passphrase)
- the staleness of trust
- FENCED and the two-device rule
- that a restore never makes a compromised key safe (#9)
