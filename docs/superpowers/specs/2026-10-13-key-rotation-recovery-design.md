# Lost-key and compromised-key recovery (issue #9): design

Status: approved in conversation on 2026-10-13. Builds on #6 (passphrase protection), #7 (OS unlock) and #8
(backup and restore), all merged into main.

## Goal

Give operators explicit, separate paths for three situations:

| Situation | Path |
|---|---|
| A lost or damaged key, a trustworthy backup, no suspicion of compromise | #8 `identity-restore` (same key) |
| A lost key with no backup, or a key that is unavailable | **rotation** to a new key |
| A suspected compromise | **rotation** to a new key, plus revocation, plus a review report |

**Constraints:**
- History is never silently rewritten, and SQLite data is never rolled back to repair a key.
- A restore never makes a compromised key safe.

## Decisions (made by the user)

1. **Rotation changes only the key.** The new key keeps the same `sender`, `group` and `node`; only
   `key_id` and `public_key` change.
   - The database binding `_sync_security(sender, group_id, node)` stays valid, so no data migration
     is needed.
   - `signed.approve()` already allows a new key in a slot held by the same sender.
2. **Pending outbox packets** (signed by the old key, never published) are **re-signed** with the new
   key. This happens only after the operator types `RESIGN`, and it runs in one transaction.
   - The uuid and body stay the same.
   - Published packets are never touched.
   - Rotation refuses while any pending packet already has a file in the exchange.
3. **Packets already received from a compromised key are reported, never removed automatically.** A
   read-only `compromise-report` lists them for human review. Any fix happens through normal edits,
   which become new packets.

## Facts in the current code this relies on

- **Signing time.** Outbox packets are signed when they are captured (`memory_sync._capture`, which
  calls `security.sign(packet)` before the `INSERT INTO _sync_outbox`) and are stored as canonical
  signed JSON.
- **Unpublished outbox check.** `Security.guard` verifies every unpublished outbox packet with the
  current key and sender.
- **Revoked keys.** `_verify` refuses a revoked key for new packets (`historical=False`). It still
  accepts a revoked key when `verify_received` upgrades an already-committed receipt
  (`historical=True`). So revocation doesn't undo history.
- **Exchange packet files.** Received packets stay as immutable files under
  `EXCHANGE/signed-changes/<node>/`. `_sync_receipts(uuid, checksum)` records which ones were
  committed.
- **Proposal path.** The wizard's pairing proposal lives at `pairing/proposals/<sender>.json`, and
  `publish_proposal` refuses a different file at the same path. Rotation must therefore not reuse
  that path.

## Rotation proposal format

The rotation proposal lives at `EXCHANGE/pairing/rotations/<new_key_id>.json`, written exclusively:

```
{ "format": "agentmesh-key-rotation-v1",
  "old_key_id": <hex>, "new": <PUBLIC_FIELDS of the new key>,
  "reason": "lost" | "compromised" | "planned", "created_at": <ISO-8601 UTC>,
  "signature": <hex Ed25519 by the NEW key over b'agentmesh-key-rotation-v1\0' + typed(all other fields)> }
```

- **What the signature proves:** the signature by the new key only proves possession of the new key
  and that the proposal is intact. It does **not** prove anything about who made it. The old key is
  never used to sign it, because it may be compromised.
- **Authorization comes from people:** at each peer the operator types the new key's full
  fingerprint, which they checked out of band.
- **Reading the proposal:** peers read it with `read_local(private=False)`. They validate:
  - the size (at most 64 KiB) and the exact field set
  - `check_public(new)`
  - `old_key_id` is 64 hex characters
  - the reason is one of the three values
  - the signature

## Commands

All are operator-run, interactive and explicit. They follow the #6–#8 conventions:
- passphrases come only through `key_protection.console_passphrase`
- typed words use the `console_prompt` style
- reports use fixed reasons and contain no secrets
- errors are raised so no lower exception keeps data

### `agentmesh identity-rotate --security-dir DIR --db DB --exchange EXCH --reason {lost,compromised,planned} [--runtime RUNTIME]`

Runs on the device that gets the new key.

1. **Binding.** Read `_sync_security` from `DB` (it must exist, which means signed policy is
   `required`). Read `_sync_config` (node, group).
   - Refuse unless there is exactly one binding row and it matches the config.
   - The binding gives `sender`, `group` and `node`.
   - Refuse a legacy DB with `rotation requires a signed (required-policy) database; there is no key
     to rotate`.
2. **Current identity.** `DIR` may hold:
   - a raw `identity.json`
   - a protected `identity.json` (no unlock is needed; only its public fields are read)
   - no `identity.json` at all (a lost key)
   
   Rules:
   - When an identity exists, its public `sender`, `group` and `node` must equal the binding;
     otherwise refuse.
   - `old_key_id` is taken from that identity. Without one, it is the single **active** entry in
     `DIR/trust.json` for that (group, node, sender). Without either, `--old-key KEY_ID` (64 hex) is
     required.
   - `trust.json` must exist, because peers are needed afterwards. Refuse with `rotation needs this
     device's trust store; restore it from a backup (#8) or re-pair` when it doesn't.
3. **The worker is stopped.**
   - If `--runtime` is given, `worker_lifecycle.status(runtime)` must report a state other than
     `running`. Refuse with `stop the worker before rotating (worker-stop)` otherwise.
   - In every case the operator types `STOPPED` to confirm no other process uses this DB or identity.
   - Then take the DB's worker lock (`runtime_lock.lock(DB.parent / 'worker.lock', timeout=0)`, the
     same lock the cycle uses) and hold it for the rest of the operation. If it's held, refuse with
     `a worker or cycle is using this database`.
4. **Pending outbox.**
   - Count the rows of `_sync_outbox WHERE published=0`.
   - For each one, refuse if `EXCHANGE/signed-changes/<node>/<uuid>.json` or
     `EXCHANGE/changes/<node>/<uuid>.json` exists (a half-finished publish). Reason:
     `pending packet <uuid> is already in the exchange; publish or inspect it before rotating`. The
     uuid is not secret.
   - Every pending packet must parse and carry this binding's `sender`, `group` and `node`.
   - If the count is above 0, show it, and the operator types `RESIGN`.
5. **New key.**
   - Generate a new Ed25519 key in memory.
   - Ask for a new identity passphrase twice. It must be at least 12 characters, and it must differ
     from the old passphrase when the old identity was protected and its passphrase was supplied. If
     no old passphrase was asked for, this check is skipped.
   - The new `identity.json` is sealed in the #6 `protection` form with production N, and has the
     same `sender`, `group` and `node`.
6. **Commit, in this order, so every crash point stays recoverable:**
   1. **Write trust.** Write `DIR/trust.json.pre-rotate` (a hard-link snapshot of the current trust).
      Then write the new trust:
      - the old key's entry gets `revoked: true` (an existing entry; or added from the old public
        fields when the identity file still has them)
      - the new key gets `{**new_public, revoked: False}`
      - every other entry stays unchanged
   2. **Swap the identity.** Write the new `identity.json` into `DIR/.rotate-<uuid>/`, then replace it
      into `DIR/identity.json` with `signed.replace`. Keep the old identity as
      `DIR/identity.json.rotated-<old_key_id[:16]>`:
      - a protected old identity is kept as it is
      - a raw old identity is **not** kept as it is; it is dropped, and the report says so, because
        a compromised raw key should not linger on disk
   3. **Re-sign the outbox.** In one SQLite transaction (`BEGIN IMMEDIATE`):
      - for every pending row, rebuild the signed packet: take the stored packet, remove `signature`,
        `key_id` and `sender`, and call `new_security.sign(...)`
      - check the new packet: uuid, body and checksum unchanged; it verifies with the new key
      - `UPDATE` the stored packet
      
      If any row fails, the whole transaction rolls back.
   4. **Verify.**
      - `Security(DIR, unlock=new passphrase)` loads, `check_self` passes, and `guard(c, EXCHANGE)`
        passes on the DB (this re-verifies the whole unpublished outbox with the new key)
      - read back the outbox through `Security.verify`
   5. **Publish the proposal** exclusively to `EXCHANGE/pairing/rotations/<new_key_id>.json`.
   6. **Clean up.** Remove `trust.json.pre-rotate` and the staging directory. Remove the old key's #7
      OS-unlock item through an injectable backend (`os_unlock.current_backend().delete(old_key_id)`;
      missing counts as success). A failure there is reported, not fatal.
   
   **Rollback on an exception before step 6.4 succeeds:**
   - restore `trust.json` from `pre-rotate`
   - restore `identity.json` from the kept copy (or remove the new one when there was none)
   - the DB transaction never committed
   
   After a **crash**, the next run sees `trust.json.pre-rotate` or `.rotate-*` and refuses with
   `unfinished rotation exists; inspect before retrying`. KEY-PROTECTION.md documents how to finish
   or undo it by hand.
7. **Report:**
   `{"status": "rotated", "old_key_id", "new_key_id", "reason", "resigned": <count>, "proposal": "pairing/rotations/<new_key_id>.json", "os_unlock_item_removed": bool, "old_identity_kept": bool, "next": [...]}`.
   
   `next` lists:
   - on every peer, run `peer-rotate` and check the new fingerprint out of band
   - for `compromised`, run `compromise-report` on every device, this one included
   - create a new backup (`identity-backup create`); backups of the old key are now revoked material
   - optional `identity-os-unlock enable`
   - restart the worker
   
   It never starts a worker or enables signing or OS unlock.

### `agentmesh peer-rotate --security-dir DIR --proposal FILE`

Runs on every other device.

1. Validate the proposal (format and signature).
   - `proposal.new` must have the same `group`, `node` and `sender` as the trust entry for
     `old_key_id`.
   - That entry must exist in `DIR/trust.json`; refuse with `unknown old key` otherwise.
   - Refuse if `new.key_id` is already in the trust: `already rotated` when active,
     `new key is revoked here` when revoked.
   - Refuse if `proposal.new.group` differs from this device's own identity group.
2. Show the old and new fingerprints (first 16 characters), the reason and `created_at`. The operator
   types the **full** new fingerprint. A mismatch refuses with nothing changed.
3. **One trust write:** the old entry gets `revoked: true` (if it isn't already) and the new entry
   `{**new, revoked: False}`. Use the slot rule from `approve`: no other active entry for that
   (group, node) with a different sender.
4. **Report:** `{"status": "peer-rotated", "old_key_id", "new_key_id", "reason", "next": [...]}`. When
   the reason is `compromised`, `next` includes `compromise-report --key <old>`.

### `agentmesh compromise-report --db DB --exchange EXCH --key OLD_KEY_ID [--since ISO]` (read-only)

- Open the DB read-only (`mode=ro`).
- For every file under `EXCHANGE/signed-changes/*/` (every node folder), parse it with the existing
  safe parser and check that it is valid JSON with `key_id == OLD_KEY_ID`.
- Check that `_sync_receipts` holds its `uuid` with the matching checksum, which means it was
  committed here. Use the same receipt digest as `memory_sync`.
- **Report:** `{"key_id", "since", "committed": [{"uuid", "file_mtime", "node", "changes": [{"table", "key", "op"}]}], "not_committed": <count>}`.
  - `changes` is derived from the packet body's change entries. List only table, key and operation,
    **never row contents**.
  - `--since` filters on the file mtime. It's documented as approximate, because it is the local
    exchange mtime, not a signed time.
- Never write, delete or quarantine anything. Unreadable files are counted under `unreadable`.

## Documentation

`memory-sync/KEY-PROTECTION.md` gains a "Lost or compromised keys" section (the runbook):

- **A decision table:** restore (#8) vs rotate, per situation. A restore never makes a compromised
  key safe, and the #8 restore already refuses a key revoked in the local trust.
- **Step-by-step rotation:**
  1. Stop the worker.
  2. Run `identity-rotate`.
  3. Run `peer-rotate` on every peer, with the fingerprint checked out of band.
  4. Run `compromise-report` everywhere.
  5. Restart the workers.
  6. Make a new backup and run the drill.
- **Pending outbox, published packets and historical receipts:** what happens to each. Published
  packets and receipts stay unchanged. Pending packets are re-signed with RESIGN.
- **Key recovery is separate from SQLite data recovery:** never roll back the database to fix a key.
- **Backup destinations and custody:**
  - backup destinations: offline media, not Syncthing, git or cloud sync
  - retention: keep the latest verified backup per key, and delete backups of revoked keys
  - passphrase custody: a password manager or sealed paper, separate from the file
  - periodic restore drills: monthly, and after every rotation
- **Manual completion or undo** of an interrupted rotation.

## Testing

Unit tests use disposable identities and DBs; the fast KDF is test-only.

- **rotate:**
  - a lost key (no identity.json) works from the binding plus the trust; `--old-key` is needed when
    the trust is ambiguous
  - a raw old identity, and a protected old identity
  - **refusals:**
    - a legacy DB
    - a binding that differs from the identity
    - a running worker (fake `worker_lifecycle.status`) and the worker lock held
    - `STOPPED` not typed, `RESIGN` not typed
    - a pending packet already in the exchange
    - a leftover `pre-rotate` or `.rotate-*`
  - the re-signed outbox keeps uuid, body and checksum, and verifies with the new key but not the old
  - published rows are untouched byte for byte
  - **rollback:** inject a failure at each commit step and check that trust, identity and DB return
    to their original state
  - the proposal is written exclusively and passes `peer-rotate` validation
  - the OS-unlock item is removed through a fake backend; a removal failure is only reported
  - the old raw identity is not kept
  - no secret appears in files (other than sealed identities), reports or errors
- **peer-rotate:**
  - success; a wrong fingerprint changes nothing
  - an unknown old key, an already-rotated proposal, a revoked new key
  - a slot conflict, a group mismatch
  - a tampered proposal (each field, and the signature)
  - an oversized proposal
- **End to end with two peers in one test:**
  1. A and B are paired with signed policy.
  2. A rotates and B runs peer-rotate.
  3. A captures and publishes a new change; B receives it.
  4. A packet signed by A's old key and published afterwards is refused at B as revoked.
  5. B's earlier receipts from the old key still verify as historical (`verify_received`).
  6. A's re-signed pending packet is received by B.
- **compromise-report:**
  - lists only committed packets from that key, with table, key and op but no row contents
  - `--since` filters
  - the DB and exchange are unchanged byte for byte afterwards
- **Restore interplay:** a #8 `identity-restore` of a backup of the rotated-away key into a target
  whose trust revokes it is refused (this already works; add a regression test).
- **Frozen smoke:** `--help` for the three commands. Without a console, `identity-rotate` and
  `peer-rotate` refuse and change nothing. `compromise-report` on a disposable DB/exchange returns an
  empty report.
- **Packaging:** the new module goes into `standalone_build.HIDDEN_MODULES` and `build_package.py`,
  and the CI pytest list runs the new tests natively.

## Out of scope

- A new sender (allocation migration).
- Automatic quarantine or rollback.
- Rotating OTA release keys.
- Activating signing.
