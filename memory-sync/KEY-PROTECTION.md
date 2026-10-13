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
  plus the passphrase; use the encrypted backup commands below for portable recovery.
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

## Backup and restore (#8)

Use a separate, long backup passphrase (at least 12 characters). Keep it separately from both the
backup file and the identity passphrase, for example in a password manager with a recovery procedure.
The interactive prompts never accept passphrases from argv, environment variables or files.

```
agentmesh identity-backup create --security-dir <security dir> --output <offline backup file>
agentmesh identity-backup verify --file <backup file> --security-dir <security dir>
agentmesh identity-backup drill --file <backup file> --security-dir <security dir>
agentmesh identity-backup status --security-dir <security dir>
agentmesh identity-restore --file <backup file> --security-dir <new security dir> --exchange <group exchange>
```

`create` unlocks a protected identity with its identity passphrase, never OS unlock. Legacy raw
identities can also be backed up. Confirm the displayed public identity by typing `BACKUP`, then enter
the new backup passphrase twice. It must differ from the identity passphrase when one was used.
Creation refuses overwriting an existing file, verifies the written ciphertext immediately, and
records evidence only after that verification succeeds. A failed readback removes the new backup.

Keep backups on offline media outside Syncthing and git. The location check resolves symlinks and
rejects ancestors containing `.stfolder` or a `.git` file/directory. Syncthing's marker name is
configurable, so this is a best-effort check: inspect your actual sync configuration. Detected iCloud,
OneDrive, Dropbox and Google Drive locations require typing `CLOUD`: uploading an encrypted file
still exposes its metadata and permits offline guessing. Cloud detection cannot discover every
custom sync root. Backup publication uses an atomic no-clobber hard link, falling back to an
exclusive create on filesystems without hard links. POSIX mode 0600 or a Windows private ACL is used
where supported; `permissions: unsupported-by-filesystem` means the medium cannot enforce it.

### Portable format and evidence

The JSON format is `agentmesh-identity-backup-v1`: scrypt (N=2^17, r=8, p=1, random 16-byte salt) and
AES-256-GCM (random 12-byte nonce). The public manifest contains `key_id`, `public_key`, `sender`,
`group`, `node` and UTC `created_at`. **Anyone holding the file can read this metadata.** It is bound
as authenticated associated data and displayed by the commands only after authentication succeeds.
There is no unauthenticated inspect command. Exact fields and bounded KDF/ciphertext sizes are checked
before key derivation. Wrong passphrases and damaged encrypted files produce the same opening error.

The encrypted payload contains the device key, the captured trust object and `trust_captured_at`.
Neither the identity's `protection` envelope nor its `os_unlock` envelope is exported. Decrypted keys
and passphrases stay in memory; every identity written by restore or drill is sealed. Python cannot
reliably wipe secret memory. This retains the same-user-code threat model described above.

`verify` authenticates and validates the whole file. `drill` additionally creates two disposable,
sealed identities, signs and verifies a probe and a signed ACK, then removes both directories even
on failure. It never writes to the exchange, adopts an identity, or needs `FENCED`. Verify and drill
warn for an older backup whose scrypt N is below the production value. `--security-dir` is optional
for these two commands; evidence is recorded only when its public identity matches the backup.

`backup-status.json` is private evidence indexed by identity key ID and encrypted-file SHA-256. It
stores creation, verification and drill timestamps, never paths or secret material. Read-only
`status` reports `none`, `verified` or `drilled`, plus the age in seconds of each timestamp. Evidence
means a check passed at that time; it does not prove the file still exists or is readable. File
existence alone is never evidence. Same-user code could forge this evidence file.

### Restore, fencing and trust

Stop or wipe the old device before restoring its identity. **Never run this identity on two active
devices.** The required exchange must contain `.stfolder` and a status file for the backup's group.
Recent activity (within 15 minutes), future timestamps, or unreadable entries on this node's status,
signed-changes or changes slot refuse restore. The check is repeated immediately before adoption.
It is only a heuristic: **typing `FENCED` is the operator's confirmation and the real control**;
a quiet exchange cannot prove that a disconnected old device is stopped.

The target must be private, outside git, Syncthing and the exchange, and contain no identity. Cloud
targets require `CLOUD`. A leftover `.restore-*` or `trust.json.pre-restore` blocks another attempt:
inspect the interrupted restore and remove its recovery artifacts only after deciding which trust
state to retain. Staging contains only a sealed identity and trust. Trust is adopted first and the
identity last without overwriting an identity; ordinary adoption failures roll trust back. A crash
can leave the private pre-restore trust copy for operator recovery.

Current trust wins. A revoked own key, conflicting own public fields, a newer active key on the same
slot, or current trust from another group refuses the entire restore. Existing peer entries are
retained and revocation from either copy wins. Backup-only revoked peers are imported automatically
as revoked. For each new active peer, type its full 64-character fingerprint to pin it; Enter or a
wrong fingerprint skips it. Slot conflicts are skipped. Skipped peers must be paired again through
the wizard. The captured trust is a historical snapshot: restore sees only local revocations.
Revocations held by remote peers become visible when the required live roundtrip fails.

Restore asks for the backup passphrase and a new identity passphrase twice. The new identity
passphrase must differ from the backup passphrase; the old identity passphrase is unnecessary.
The restored `key_id`, public key, sender, group and node are unchanged. It is read back and checked
under the new passphrase before success. Stale backup evidence is removed from the target.

After restore, run a live signed roundtrip with a real peer using the wizard probe before treating
the device as restored. OS unlock is off. A `stray_os_unlock_item: true` report means an existing OS
item was detected: explicitly run `identity-os-unlock disable` or `enable` to remove or replace it.
Then optionally enable OS unlock, explicitly run `worker-start`, and create a new encrypted backup.
Restore never starts, stops or signals workers, enables OS unlock, touches databases, or writes the
exchange. An offline drill is not a substitute for the live peer check.

A backup cannot make a compromised or revoked key safe. Rotate and re-pair after compromise (#9);
this feature does not recover missing backups or restore SQLite data.

## Lost or compromised keys

### 1. Choose the recovery path

| Situation | Action |
|---|---|
| Lost or damaged key, trustworthy backup, no suspicion of compromise | `identity-restore`; confirm `FENCED` and pass the exchange-activity check. |
| Lost/unavailable key without a usable backup | `identity-rotate --reason lost`; generate a new key. |
| Suspected compromise | Immediately revoke on every peer, then rotate with `--reason compromised`, then review packets everywhere. |
| Planned replacement of a usable key | `identity-rotate --reason planned`. |

**A restore never makes a compromised key safe.** Restore refuses a key revoked in the local trust.
Restoring the same key into an empty trust can succeed locally, but remote peers still refuse its
new packets. A successful local restore is not remote authorization.

### 2. Compromise: revoke first

On **every peer**, close the exposure window immediately, before rotation:

```text
python signed_packets.py --security-dir PEER_DIR revoke --fingerprint OLD_FULL_FINGERPRINT
```

Use the old fingerprint from your trusted records. `peer-rotate` accepts an old key that is already
revoked. Until the approved rotation manifest arrives, unseen and causally pending old-key packets
are refused; this is intentional. Do not reactivate the compromised key to clear that backlog.

### 3. Rotate and approve on peers

1. Stop the worker and every unmanaged cycle on the rotating device. Stop any DB clones elsewhere.
2. On that device, run:

   ```text
   agentmesh identity-rotate --security-dir DIR --db DB --exchange EXCH --reason compromised --runtime RUNTIME
   ```

   Use `lost` or `planned` when appropriate. The database must already use required signed policy;
   rotation never activates signing. Sender, group and node remain unchanged; only the key changes.
   Supply `--old-key OLD_KEY_ID` if the whole security directory was lost. With an identity present,
   its public key ID takes precedence; otherwise `--old-key`, then a single active trust entry is
   used. The old key need not be unlockable or locally active.

   Type `STOPPED` to attest that all copies are stopped. The command holds the database cycle lock
   and, when `--runtime` is supplied, the managed worker lifetime lock. It refuses running workers
   and activity in this node's exchange slot within 15 minutes. The exchange must contain its
   `.stfolder` marker and a status file for the database group. Pending packets already present in
   either `signed-changes/` or `changes/` refuse rotation; inspect or publish them first.

   With `--reason compromised`, review each pending UUID, format, v3 kind, table/operation and
   `key_sha256`; row contents are never shown. Repeat `--drop-pending UUID` to exclude reviewed
   packets from publication and the manifest. This option is only accepted for a fresh compromised
   rotation. A packet referenced by another pending packet's covers/evidence cannot be dropped.
   Dropped packets stay unpublished until finish, then move byte-identically into the local
   `_sync_rotation_dropped(uuid, packet, dropped_at)` archive. The report lists `dropped_pending`.
   If pending packets exist, review the counts per packet format and per table/operation, then type `PUBLISH`.
   Rotation publishes pending v2 and v3 packets exactly as stored, including resolution packets.
   Their bytes, signatures and evidence digests never change; the manifest lists them alongside
   already-published old-key packets. This also works when the old private key is lost.
   Enter a new identity passphrase twice, at least 12 characters, through the interactive console.
   No passphrase flag or environment-variable input is supported. The new identity is always sealed;
   rotation does not enable OS unlock or start a worker.

   The report includes the full `new_fingerprint`, old/new key IDs, `published_pending` count, total
   `manifest_packets` count and `pairing/rotations/NEW_KEY_ID.json`. If `trust_restored` is false, only the new
   self entry exists locally. Re-pin every peer with `signed_packets trust`, using out-of-band
   fingerprints; incoming peer packets are safely refused until then.

3. Read the **full new fingerprint from the rotating device's terminal report** to each peer operator
   by phone or in person. Never obtain the approving fingerprint from the exchange or proposal file.
4. On every peer, run:

   ```text
   agentmesh peer-rotate --security-dir PEER_DIR --proposal EXCH/pairing/rotations/NEW_KEY_ID.json --exchange EXCH
   ```

   Review the short fingerprints, reason, timestamp and manifest count. Type the full new fingerprint
   delivered out of band. A wrong fingerprint changes nothing. A warning about competing proposals
   means multiple files claim to replace the old key: verify with the device owner before approving.
   `competing_proposals` counts other proposal files. Approval revokes the old entry and pins the new
   key, enforcing one active identity key and the allocation-slot rule.

5. For compromise, run this on **every device, including the rotating device**:

   ```text
   agentmesh compromise-report --db DB --exchange EXCH --key OLD_KEY_ID --security-dir DIR
   ```

   `committed` lists matching local receipts, timestamps and table/operation metadata. Row keys are
   represented only by a 16-hex SHA-256 prefix; row contents and raw keys are never emitted.
   `in_manifest` means the UUID and envelope digest (including the signature) match this device's approved manifest; it is null
   without `--security-dir`. Committed packets outside that manifest deserve particular attention.
   `uncommitted` shows other observed old-key files. On the originating device, `forged_candidates`
   identifies own-node files that do not match the outbox byte for byte. These are evidence for
   investigation, not an automatic verdict. This byte check includes v3 files. When the resolution
   store exists, `superseded` means a disposition matches this file's envelope digest;
   `variant_superseded` means a disposition exists for another digest of the same UUID.
   V3 syntax and row validation use `packet_versions`. Missing signed-envelope receipts are counted separately
   from legacy receipts because receipts do not retain key/node attribution. Malformed files increase
   `unreadable`. For v3 packets, the packet and its change summaries include `kind` (`change` or
   `resolution`). When the resolution store exists, every packet also has `superseded: true|false`;
   true requires a committed disposition matching its complete envelope, including its signature.
   A changed envelope cannot inherit the original packet's disposition. This is a read-only inventory,
   not signature authentication. The report never writes, deletes, quarantines or undoes source state.
   Live-WAL inspection uses a disposable private snapshot so even source SHM read marks stay unchanged.
   An incomplete WAL/SHM sidecar pair refuses inspection instead of creating a missing sidecar or
   ignoring uncheckpointed data; inspect or checkpoint the stopped database before retrying.

6. Update remaining pins: re-run the wizard pairing step to update `security_state.peer`, and update
   the profile-memory `SignedChannel` peer fingerprint. Rotation does not rewrite either pin.
7. Restart workers explicitly. If `os_unlock_item_removed` is false, the report prominently identifies
   failed removal of the **old** OS item: remove that fingerprint's item by hand. The existing
   `identity-os-unlock disable` command targets the currently installed identity, so after rotation
   it does not remove the old item. A null result means no supported OS backend was available.
   Optionally run `identity-os-unlock enable` for the new identity before restarting unattended work.
8. Create a new encrypted backup and run a restore drill. Backups of the revoked key are revoked
   material; delete them under your custody policy.

### 4. Resume an interrupted rotation

```text
agentmesh identity-rotate --security-dir DIR --db DB --exchange EXCH --runtime RUNTIME --resume
```

Before the journal exists, there has been no identity/trust/DB replacement. A failure while staging
can leave a private `.rotate-*.manifest`; inspect that leftover if there is no journal to replay.
Once `rotation-journal.json` exists, recovery is **forward only**. Do not roll back trust, identity or
SQLite and do not delete the journal to make the worker start. Ordinary signing and publishing refuse
while the journal exists. The journal contains a sealed new identity and public recovery metadata,
never a raw key or passphrase; its manifest is kept in a separate private staging file.

`--resume` repeats `STOPPED`, the locks, exchange inactivity and pending-path checks before replay.
Only original pending files pinned by this journal may already exist with identical bytes; different
bytes refuse with `pending packet file differs from the outbox; see compromise-report`.
For a journal-bound collision, use `--resume --quarantine-collision UUID` (repeatable).
The command shows the exchange and outbox envelope digests. Type `QUARANTINE` to preserve the
conflicting file at `EXCHANGE/pairing/rotations/quarantine/<uuid>.<digest>.json` and continue
with the authoritative outbox bytes. Cancellation leaves the collision in place. Malformed
envelopes require separate inspection; this option accepts only valid envelope shapes.
Recent activity in unrelated files still refuses. An interrupted temporary write may
be retried only at this journal's exact staging name with bytes that are a prefix of the stored packet.

Resume asks for the new identity passphrase and replays trust, identity, publication of the unchanged
pending packets, verification and proposal publication. All pending flags commit in one DB transaction
after every file is in place. A crash can leave some files present with the flags rolled back; replay
accepts identical files and finishes the transaction. `published_pending` retains the original count.
OS cleanup precedes final journal removal. Restore also refuses rotation journals/staging.

Publication uses journal format `agentmesh-rotation-journal-v2`. An unfinished journal from the older
re-sign implementation is refused as `invalid rotation journal`; inspect that recovery state instead
of deleting or reinterpreting it. Proposals use `agentmesh-key-rotation-v2` and registries use `agentmesh-rotations-v2`.
V1 proposals and registries are refused; their signature-excluding digest is not accepted.
Round-1 journals without drop metadata or a v2 proposal also require separate inspection.

### 5. What happens to packets

| Packet/evidence | Result |
|---|---|
| Pending unpublished v2/v3 packets | Published byte for byte only after `PUBLISH`, then marked published. Listed on the manifest; signatures and evidence digests stay unchanged. |
| Published old-key packets | Unchanged. Peers accept approved contents through the signed manifest and historical signature verification. |
| Historical committed receipts | Unchanged; the historical receipt path continues to validate retained files. |
| Own published files | Seen before signature verification only when file bytes exactly match the stored outbox text. |
| Forgeries already committed | Remain committed; investigate with the report and correct through normal edits. |

The new key signs the small proposal's manifest SHA-256 and count. The separate sorted manifest is
streamed with a 200 MiB ceiling (roughly 1.9 million packets), not parsed as a JSON packet. Peer approval
stores it privately. A revoked-key packet must match its approved UUID/digest and still pass historical
signature verification. Each Security instance caches manifests lazily against the registry's mtime and size;
a changed registry invalidates cached entries, including missing entries. Signing a new forged packet with the stolen key cannot add it to the
approved manifest.

**Approved manifests authorize resolution evidence.** After fingerprint approval, a listed packet
counts as authorized `covers`/`evidence` even if the peer never committed it before revocation. This
includes an old-key v3 resolution packet. UUID/envelope-digest matching and historical signature
verification still apply; rotation never changes packet bytes or evidence references.
Everything outside an approved manifest retains strict revocation: merely held or previously verified
uncommitted packets are refused. Exact committed receipts and superseded dispositions retain their
existing historical-replay rules. No synthetic receipt or blanket trust exception is created.

### 6. Key recovery is separate from SQLite recovery

**Never roll back the database to fix a key.** Rotation preserves the signed database binding and
history. If data also needs repair, investigate and use the normal data-recovery process separately.

### 7. Backup destinations and custody

- Use offline media; do not put identity backups in Syncthing, git or cloud sync.
- Retain the latest verified backup for each active key. Delete backups of revoked keys.
- Keep the passphrase in a password manager or on sealed paper, separate from the backup file.
- Run restore drills monthly, and after every rotation or restore.

### 8. Limits

Forgeries committed before rotation remain committed. The manifest cannot retroactively decide
whether earlier application data was legitimate; the report supplies evidence for human review.

A database clone on another machine cannot be fenced by local locks. `STOPPED` covers the operator's
responsibility for those clones. If a clone publishes different bytes under an existing packet UUID,
publication refuses with an immutable-path collision and receive rejects the changed packet. Inspect
the collision and remove the clone's file only after determining its origin; never overwrite published packets.

`compromise-report --since ISO_UTC` filters exchange file mtimes, which a writer can set. It is an
approximate convenience filter, not a cryptographic time boundary. Unlinking a file is not secure
erasure, and Python cannot reliably wipe secret memory.
