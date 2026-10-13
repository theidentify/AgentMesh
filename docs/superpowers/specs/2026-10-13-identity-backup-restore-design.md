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

- **Manifest.** `manifest` holds public data only, and `created_at` is an ISO-8601 UTC timestamp. It
  sits outside the ciphertext so its format can be checked, but it is **never shown or trusted before
  decryption succeeds**. Restore, verify and drill display it only after authentication. There is no
  `inspect` command.
  - Anyone holding the file can read the group, sender, node and key_id. This is documented as
    metadata exposure.
  - The manifest is checked with `signed.check_public({'format': 'agentmesh-peer-key-v1',
    **{k: manifest[k] for k in ('group', 'node', 'sender', 'key_id', 'public_key')}})`.
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
- **Plaintext stays in memory.** Decrypted backup content (the raw key, passphrases) never touches
  disk. Anything this feature writes to disk is either the encrypted backup or an identity sealed in
  the #6 `protection` form. Python can't wipe memory; this is documented.
- `create` always uses production N = 2^17. `verify` and `drill` warn when a backup's N is lower.
- Backup files are read with `read_local(private=False)`, because removable media may not support
  owner-only permissions.
- A wrong passphrase and a tampered file give the same error: `backup could not be opened: wrong
  passphrase or damaged file`.

## Commands

All are explicit and operator-run. Passphrases come only from `key_protection.console_passphrase`
(an interactive console, no echo). Typed words use the existing `console_prompt` style.

### `agentmesh identity-backup create --security-dir DIR --output FILE`

1. **Location checks** (shared helper `unsafe_location(path)`, also used for the restore target):
   - Resolve the path with `os.path.realpath` first.
   - Refuse if any ancestor holds `.stfolder` (Syncthing).
   - Refuse if any ancestor holds `.git`, whether a directory or a file (worktrees and submodules).
   - Compare case-insensitively on macOS and Windows.
   - **Cloud sync folders** (iCloud Drive / Mobile Documents, a synced Desktop or Documents, OneDrive,
     Dropbox, Google Drive, detected by known root paths and markers): don't refuse, but warn that the
     file will be uploaded and allow offline guessing, and require typing `CLOUD`.
   - Document that the Syncthing marker name can be configured, so the `.stfolder` check is best
     effort.
   
   Refuse if `FILE` exists (no overwrite) or its parent directory is missing.
2. Load the identity. A raw identity loads directly. A protected identity is unlocked with the
   identity passphrase. OS unlock is **not** used: export requires the operator's passphrase.
3. Show the manifest. The operator types `BACKUP`.
4. Ask for a new backup passphrase twice: at least 12 characters, matching, and not equal to the
   identity passphrase when one was used.
5. **Encrypt and write without clobbering:**
   - Write a temp file in the same directory, fsync it, then publish with `os.link(temp, final)`,
     which fails if the file exists.
   - If the filesystem has no hard links (FAT/exFAT, some SMB or cloud volumes), fall back to
     `os.open(final, O_CREAT|O_EXCL|O_WRONLY)`, write, fsync, and delete the file on any failure.
   - Permissions: owner-only (0600 on POSIX, a `windows_acl` private ACL on Windows) when the
     filesystem supports it. When it doesn't (FAT, exFAT), continue and report
     `"permissions": "unsupported-by-filesystem"`.
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

1. Asks for the backup passphrase and validates.
   - Restores into a **disposable** private directory created under the security directory's parent
     (or `os.path.realpath(tempfile.mkdtemp())`), because `no_symlinks` rejects macOS's `/var`.
   - The drill identity is sealed in the #6 form under a random passphrase held only in memory, with
     a low N, which is fine for a random 32-byte secret. No raw key reaches disk.
   - There is no exchange and no FENCED: the drill identity is never adopted, and the drill never
     writes into any exchange or the real security directory, except the evidence file.
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

- `status` shows how old each piece of evidence is.
- Evidence records that a check passed; it doesn't prove the file still exists or is still readable.
- Same-user code could forge `backup-status.json`. That falls within the #6 threat model and is
  documented.

### `agentmesh identity-restore --file FILE --security-dir TARGET --exchange EXCHANGE`

`--exchange` is required. Use the group's accepted Syncthing folder on this machine.

1. **Target:**
   - `TARGET` must be an owner-only directory (created at 0700 if missing) with no `identity.json`.
   - It must pass `unsafe_location` (no Syncthing ancestor or git work tree; a cloud folder needs
     `CLOUD`).
   - It must not be inside, or equal to, `--exchange`.
   - If `TARGET/.restore-*` already exists (a crashed earlier restore), refuse and tell the operator
     to inspect it and remove it.
   - If `trust.json` exists, it is the *current trust*; otherwise there is none.
2. Ask for the backup passphrase and validate fully. Decryption is **in memory**.
   - The staging directory `TARGET/.restore-<uuid>` (owner-only) only ever receives the sealed
     identity and the reconciled trust.
   - Staging is removed in `finally`.
3. **Consistency with the current trust.** Refuse the **whole restore** if any of these holds:
   - The current trust has `manifest.key_id` with `revoked: true`. Reason:
     `this key is revoked in the current trust; rotate instead (#9)`.
   - The current trust holds this key_id with different public fields.
   - The current trust has an **active** entry for the same (group, node) with a different key_id,
     meaning a newer identity already owns this slot.
   - The current trust holds any entry from another group.
   
   The slot rule applies to the self entry too.
4. **Exchange activity (a heuristic; FENCED is the real control).**
   - The exchange must have `.stfolder` and must belong to this group: at least one
     `status/*.json` whose `group` equals the manifest group. Otherwise refuse with
     `exchange does not belong to this group`.
   - Refuse with `the old device looks active: recent exchange activity for this identity` when
     **any** file under `EXCHANGE/status/<node>.json`, `EXCHANGE/signed-changes/<node>/` or
     `EXCHANGE/changes/<node>/` has an mtime within the last 15 minutes **or in the future**. There
     is no key_id filter: any recent file on this node's slot counts. Unreadable entries count as
     activity.
   - Show the operator the newest activity time found for this node.
   - Run the check **again** right before adopting (step 8).
5. Show the manifest and the backup's `created_at` / `trust_captured_at`. The operator types
   `FENCED`.
6. **Peers.** For each backup peer entry other than self:
   - Already in the current trust: keep the current entry; `revoked` = current OR backup. Never
     change a current entry otherwise.
   - **Revoked in the backup and not in the current trust:** imported automatically, with no prompt,
     as `revoked: true`. A revoked entry can only refuse a key, and `approve()` already refuses
     revoked keys, so a compromised peer can't be re-paired later.
   - **Active in the backup and not in the current trust:** show node, sender and the first 16
     characters of the fingerprint. The operator types the **full** 64-character fingerprint to pin
     it, or presses Enter to skip it. A wrong fingerprint skips the peer with a message. Skipped peers
     are listed in the report.
   - **Slot rule:** a backup peer that would give two active entries for one (group, node) with
     different senders is **skipped**, with a message, not pinned.
7. Ask for a **new identity passphrase** twice (at least 12 characters, not equal to the backup
   passphrase). Write `identity.json` in the #6 `protection` form, using `key_protection.seal` with
   production N.
8. **Adopt:**
   - Write `identity.json` and the reconciled `trust.json` into staging, then move them into `TARGET`.
     `trust.json` is written first, with an atomic replace when one exists. `identity.json` is written
     last, as an exclusive create.
   - Read back with `Security(TARGET, unlock=<new passphrase>)` and compare the public fields and the
     key.
   - Remove staging.
   - Before replacing an existing `trust.json`, copy it to `TARGET/trust.json.pre-restore` (private).
     If an **exception** happens before `identity.json` is in place, restore puts `trust.json` back
     from that copy. After a **crash**, the copy stays for the operator, and the next restore refuses
     until it has been reviewed, the same way as a leftover `.restore-*`.
   - On success, delete `trust.json.pre-restore`.
9. Report: `{"status": "restored", "key_id", "pinned": [...], "skipped": [...], "kept_revoked": [...],
   "imported_revoked": [...], "stray_os_unlock_item": bool, "next": [...]}`.
   - `stray_os_unlock_item` comes from the #7 backend `load` for this key_id: true if an OS item
     exists. A true value asks the operator to run `identity-os-unlock disable` (or `enable`, which
     replaces it).
   - `next` lists: run a live signed roundtrip with a real peer (the wizard probe) before treating the
     device as restored; optional `identity-os-unlock enable`; `worker-start`; `identity-backup create`
     for the new passphrase setup.
   - **Limit:** restore only sees revocations in the local trust. Revocations held by peers become
     visible when that live roundtrip fails.
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

- New fixed reasons go into `cli_errors.KEY_REASONS`. The new actions (`identity-backup`,
  `identity-restore`) are added to the key actions in `cli_errors.report`, and the new module to
  `TRUSTED_MODULES`.
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
    - the no-link fallback (simulate `os.link` raising `OSError(EPERM)`) still refuses to clobber
    - a `.git` *file* ancestor → refuse
    - a cloud folder needs `CLOUD`
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
    - works on macOS with temp under `/var/folders`
    - no raw key on disk at any time
    - temp directories are gone afterwards, including after an injected failure
  - **restore:**
    - same key_id, sender, group, node and public key
    - a revoked backup peer is imported as revoked without a prompt, and `approve()` then refuses it
    - a target in `.stfolder`, `.git` or inside the exchange → refuse
    - a leftover `.restore-*` or `trust.json.pre-restore` → refuse
    - the current trust has a newer identity for this slot, the same key with different fields, or
      another group → refuse
    - a future mtime in the exchange → refuse
    - an exchange without this group's status → refuse
    - activity appearing between the first check and adopt → refuse
    - a new identity passphrase equal to the backup passphrase → refuse
    - no file under staging or the target ever holds a raw `private_key` (scan every write)
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
