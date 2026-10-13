# Lost-key and compromised-key recovery (issue #9): design

Status: approved in conversation on 2026-10-13 and revised after the security review the same day.
Builds on #6 (passphrase protection), #7 (OS unlock) and #8 (backup and restore), all merged into main.

## Goal

Give operators explicit, separate paths for each situation:

| Situation | Path |
|---|---|
| A lost or damaged key, a trustworthy backup, no suspicion of compromise | #8 `identity-restore` (same key; fenced with FENCED and the exchange check) |
| A lost key with no backup, or a key that is unavailable | **rotation** to a new key |
| A suspected compromise | **immediate revocation on every peer**, then **rotation**, then the review report |

**Constraints:**
- History is never silently rewritten, and SQLite data is never rolled back to repair a key.
- A restore never makes a compromised key safe.

## Decisions (made by the user)

1. **Rotation changes only the key.** It keeps the same `sender`, `group` and `node`; only `key_id` and
   `public_key` change. The DB binding `_sync_security(sender, group_id, node)` stays valid, so no data
   migration is needed.
2. **Pending (unpublished) outbox packets** signed by the old key are **re-signed** with the new key.
   This happens only after the operator types `RESIGN`.
   - The uuid and body stay the same.
   - Published packets are never touched.
3. **Packets already received from a compromised key are reported, never removed automatically**, by a
   read-only `compromise-report`.

## Facts in the current code this relies on (verified)

- **Outbox signing.** Outbox packets are signed at capture time (`memory_sync._capture`). Published
  rows stay in `_sync_outbox` with `published=1`.
- **The receive order.** `memory_sync.receive` runs these checks in order:
  1. `_received_signed`, which uses committed receipts only
  2. `security.verify(..., historical=False)`
  3. `_seen`, which matches this node's own outbox
  
  A device's **own** published packets have no receipt. So once its old key is revoked locally, they
  would fail in step 2, be logged as `invalid`, and `sync_worker.permanent_failure` would fail every
  cycle. **This design must fix that (R1).**
- **Peers.** After revocation, a peer refuses every old-key packet it has not already committed:
  - packets not delivered yet
  - packets still `pending` on causality
  
  Later packets that depend on them stay pending forever. **This design must fix that (R2).**
- **What can sign.** `check_self` re-reads the trust on every sign, so writing the trust first fences
  any process that holds the old key unlocked.
- **Existing CLI.** `signed_packets revoke --fingerprint` and `signed_packets trust` (approve) already
  exist.
- **Proposal path.** The wizard's pairing proposal path is `pairing/proposals/<sender>.json`, so
  rotation uses its own path.

## Protocol changes

### R1: own packets are recognized before the signature check

In `memory_sync.receive`, when `p['node']` equals this database's node:
1. Look up `_sync_outbox` by `p['uuid']`.
2. If the stored packet equals `p` exactly, treat it as seen (`continue`) **before** any signature
   verification. It's byte-identical to what this device signed and published itself.
3. A local-node packet that doesn't match keeps today's behaviour: `unknown packet claiming local node`
   (`invalid`).

This also holds for packets the device re-signed: the outbox row then holds the new-key version, and
any old-key file with the same uuid can't exist, because rotation refuses that case (see the fence).

### R2: legitimate old-key packets are accepted through a signed manifest

- **What the manifest is.** The rotation proposal carries a **manifest**: every `(uuid, receipt_digest)`
  pair for the **published** outbox rows signed by the old key. It is signed (with the rest of the
  proposal) by the **new** key.
- **What peers do with it.** After the operator approves the new fingerprint, `peer-rotate` stores the
  manifest in `DIR/rotations.json` (a private file written with `write_local`):
  `{"format": "agentmesh-rotations-v1", "rotations": {<old_key_id>: {"new_key_id", "approved_at", "packets": {<uuid>: <receipt_digest>}}}}`.
- **How receive uses it.** When `security.verify` refuses because the key is revoked, receive checks
  that file. If `p['key_id']` has an entry and `packets[p['uuid']] == receipt_digest(p)`, the packet is
  verified **historically** against the old public key (`Security._verify(..., historical=True)`
  through a new public method, `verify_rotated(p, group, folder, rotations)`) and then applied as
  usual. Anything else stays refused.
- **Why this matters for compromise.** An attacker can sign with the stolen key, but can't get a forged
  packet onto the manifest: the manifest comes from the real device's own outbox and is approved by
  fingerprint. So peers keep every legitimate packet and still refuse forgeries made after the theft.
- **Limit (documented).** A forgery made **before** the rotation and already committed by a peer stays
  committed. That is what `compromise-report` is for.
- **Size.** `rotations.json` and the proposal are limited to 16 MiB. A manifest entry is about 110
  bytes.

### Single active key per identity

After both `identity-rotate` and `peer-rotate`, a trust store has **exactly one** active entry for each
(group, node, sender). This is enforced, not just checked by convention.

## Rotation proposal

The proposal is written exclusively to `EXCHANGE/pairing/rotations/<new_key_id>.json`:

```
{ "format": "agentmesh-key-rotation-v1", "old_key_id": <hex>, "new": <PUBLIC_FIELDS of the new key>,
  "reason": "lost" | "compromised" | "planned", "created_at": <ISO-8601 UTC>,
  "manifest": {<uuid>: <receipt_digest>, ...},
  "signature": <hex Ed25519 by the NEW key over b'agentmesh-key-rotation-v1\0' + typed(all other fields)> }
```

- **What the signature proves:** the new key's signature proves possession of the new key and that the
  proposal is intact, including the reason and the manifest. Authorization comes **only** from the
  operator typing the full new fingerprint.
- **Where that fingerprint must come from:** the rotating device's terminal report, delivered out of
  band (phone, in person). **Never** read it from the exchange.
- **Validation:** read with `read_local(private=False)`. Check:
  - the size limit and the exact field set
  - `check_public(new)`
  - the hex lengths, and the uuid and digest formats in the manifest
  - the reason value
  - the signature

## Commands

All follow the #6–#8 conventions:
- passphrases only through `key_protection.console_passphrase`
- typed words in the `console_prompt` style
- fixed, secret-free reasons, added to `cli_errors.KEY_REASONS`; the new actions are key actions; the
  new module goes into `TRUSTED_MODULES`
- errors raised so that no lower exception keeps data
- no worker is started, and signing and OS unlock are never activated

### `agentmesh identity-rotate --security-dir DIR --db DB --exchange EXCH --reason {lost,compromised,planned} [--old-key KEY_ID] [--runtime RUNTIME] [--resume]`

Runs on the device that gets the new key.

**Preparation (nothing changes):**

1. **Leftovers.** Refuse if `DIR` holds any of these, **unless** `--resume` is given:
   - this feature's journal or staging (`rotation-journal.json`, `.rotate-*`)
   - #8's leftovers (`.restore-*`, `trust.json.pre-restore`)
   
   #8 `identity-restore` is extended to refuse `rotation-journal.json` and `.rotate-*` too.
2. **Binding.** Read `_sync_config` and `_sync_security` from `DB` (opened read-only for this step).
   - Exactly one binding row, matching the config.
   - Refuse a legacy DB with `rotation requires a signed (required-policy) database; there is no key
     to rotate`.
3. **Old key.** Read the public fields of `DIR/identity.json` directly with `parse(read_local(...))`,
   **not** through `Security`, because the old key may already be revoked.
   - If present, `sender`, `group` and `node` must equal the binding.
   - `old_key_id` comes from, in this order: the identity file; `--old-key`; or the single active trust
     entry for this (group, node, sender).
   - Every **pending** outbox packet must carry `old_key_id`. Any other key_id in the pending outbox
     refuses with `pending outbox is signed by an unexpected key`.
4. **Trust.**
   - If `DIR/trust.json` exists, it is used.
   - If it is missing (the whole security directory was lost), rotation still proceeds when the
     binding exists and `--old-key` is given. The new trust then holds **only** the new self entry; the
     old public key is unknown, so no revoked entry can be written.
   - The report then says that peers must be re-pinned on this device with `signed_packets trust`, using
     out-of-band fingerprints. Until that's done this device verifies no peers, so its worker refuses
     incoming signed packets, which is safe.
5. **Fence.**
   - `--runtime` given: `worker_lifecycle.status(runtime)` must not be `running`.
   - The operator types `STOPPED`.
   - Acquire, with `timeout=0`, and hold to the end:
     - `runtime_lock.lock(Path(DB).absolute().parent / 'worker.lock')`, the cycle lock
     - when the runtime is known, the managed worker's `lifetime.lock` in its control directory
       (`worker_lifecycle.control(config)` / `'lifetime.lock'`)
   - Refuse with `a worker or cycle is using this database` if either is held.
   - Run #8's `exchange_activity(EXCH, own)` check (no activity in this node's slot in the last 15
     minutes). Refuse with its reason.
   - Document that a DB clone on another machine can't be fenced. That is the operator's
     responsibility, and is covered by `STOPPED`.
6. **Pending outbox.** For every `published=0` row:
   - the packet must parse
   - it must carry this binding's sender, group and node, and `old_key_id`
   - it must verify historically against the old public key when that key is known (from the identity
     file or the trust)
   - there must be **no** `EXCHANGE/signed-changes/<node>/<uuid>.json` (and no `changes/` file)
     
     Refuse with `pending packet <uuid> is already in the exchange; publish or inspect it before
     rotating`.
   
   If any rows exist, show their count and a per-table count of operations. The operator types
   `RESIGN`.
7. **Manifest.** Collect `(uuid, receipt_digest(packet))` for every `published=1` row whose `key_id`
   is `old_key_id`.
8. **New key.** Generate a new Ed25519 key in memory. Ask for a new identity passphrase twice (at
   least 12 characters). Seal it in the #6 form with production N.

**Journal, then idempotent steps.** Write `DIR/rotation-journal.json` exclusively (it is private):

```
{"format": "agentmesh-rotation-journal-v1", "old_key_id", "old_public": <PUBLIC_FIELDS or null>,
 "new_identity": <sealed identity.json>, "reason", "manifest", "proposal": <full signed proposal>}
```

It contains no raw key, only the sealed identity. Every step below is idempotent, and `--resume`
replays the journal from the start. A crash at any point leaves state that `--resume` completes.

1. **Trust.** Write the new trust:
   - the old entry gets `revoked: true` (added from `old_public` when it's known and absent)
   - the new self entry is `{**new_public, revoked: False}`
   - every other entry is unchanged
   - exactly one active entry per (group, node, sender)
   
   If the trust already equals this, skip.
2. **Identity.** Replace `DIR/identity.json` with `journal.new_identity` (`signed.replace`). Skip it if
   already equal. The old identity is **not** kept.
3. **DB.** Open read-write. In one `BEGIN IMMEDIATE` transaction:
   - for every `published=0` row whose `key_id` is `old_key_id`, re-sign it: remove `signature`,
     `key_id` and `sender`, then `new_security.sign(...)`
   - check the uuid, body and checksum are unchanged
   - store it with `memory_sync.canonical`
   - **inside the same transaction**, check again that no exchange file exists for any re-signed uuid
   - run `new_security.guard(c, EXCH)`
   - **commit only if every check passes**
   
   Rows already carrying the new key_id are skipped, which makes the step idempotent.
4. **Verify.** `Security(DIR, unlock=new passphrase)` loads and `check_self` passes. Every pending row
   verifies with the new key and fails with the old one.
5. **Proposal.** Write it exclusively. If it already exists with identical content, skip it.
6. **Finish.**
   - Delete `rotation-journal.json`.
   - Remove the old key's #7 OS-unlock item through an injectable backend. `current_backend()` may be
     None (Linux): skip it. A removal failure is reported **prominently**: `os_unlock_item_removed:
     false` plus a next step to run `identity-os-unlock disable`, or remove it by hand.

**Rollback.** Before the journal exists, nothing has changed. After it exists, the only direction is
forward (`--resume`). The documentation says so.

**Report:**
`{"status": "rotated", "old_key_id", "new_key_id", "new_fingerprint": <full>, "reason", "resigned": <n>, "manifest_packets": <n>, "proposal": "pairing/rotations/<new_key_id>.json", "trust_restored": bool, "os_unlock_item_removed": bool|null, "next": [...]}`.

`next` lists:
- read `new_fingerprint` to each peer out of band, then run `peer-rotate` there
- for `compromised`: run `compromise-report` everywhere, this device included
- re-pin peers if the trust was missing
- create a new backup and run a drill (backups of the old key are revoked material; delete them)
- optional `identity-os-unlock enable`
- restart the worker

### `agentmesh peer-rotate --security-dir DIR --proposal FILE`

Runs on every other device.

1. Validate the proposal.
   - Refuse if `old_key_id` is **this device's own** key, or `new.node` is this device's node:
     `a rotation proposal cannot replace this device's own key`.
   - The trust must hold `old_key_id` (active or already revoked) with the same group, node and
     sender as `new`. Refuse with `unknown old key` otherwise.
   - Refuse if `new.key_id` is already present: `already rotated` when active, `new key is revoked
     here` when revoked.
   - The group must equal this device's own group.
2. **Competing proposals.** List every other proposal in `EXCHANGE/pairing/rotations/` for the same
   `old_key_id` (pass `--exchange`, or use the proposal file's directory). If there's more than one,
   show a **warning**: more than one rotation was proposed for this key, verify with the device owner.
3. Show the old and new fingerprints (first 16 characters), the reason, `created_at` and the manifest
   size. The operator types the **full** new fingerprint, read from the rotating device's report out
   of band. A mismatch refuses with nothing changed.
4. Write `rotations.json` first (add the manifest for `old_key_id`), then the trust:
   - the old entry is revoked
   - the new entry is active
   - exactly one active entry per (group, node, sender)
   - the `approve` slot rule holds
5. **Report:** `{"status": "peer-rotated", "old_key_id", "new_key_id", "reason", "manifest_packets", "competing_proposals": <n>, "next": [...]}`.
   `next` includes:
   - `compromise-report --key <old>` for `compromised`
   - the reminder that the wizard state and profile-memory `SignedChannel` pins name the old key,
     handled per the runbook

### `agentmesh compromise-report --db DB --exchange EXCH --key OLD_KEY_ID [--security-dir DIR] [--since ISO]` (read-only)

- **Opening the DB:** read-only, without creating `-wal`/`-shm`, using the same choice as
  `install_inspect` between `mode=ro` and `immutable=1`, plus `PRAGMA query_only=ON`.
- **Scanning:** every regular file under `EXCHANGE/signed-changes/*/`. For each:
  - check the size limit
  - parse it safely
  - run `validate_envelope` against the DB group and the folder
  - check the filename equals `<uuid>.json`
  - keep only files with `key_id == OLD_KEY_ID`
- **Output fields:**
  - `committed`: packets with a local receipt whose digest matches. Each lists `uuid`, `node`,
    `file_mtime` and `changes: [{"table", "op", "key_sha256": <first 16 hex of sha256(canonical key)>}]`.
    It never includes row contents or raw keys, because keys can be source paths.
  - `in_manifest`: whether the uuid is in this device's `rotations.json` manifest for that key, when
    `--security-dir` is given. Packets that are committed **and not** in the manifest are the prime
    suspects.
  - `forged_candidates`: files in **this device's own node folder** carrying `OLD_KEY_ID` that match no
    outbox row exactly. On the compromised device itself, this is the strongest signal.
  - `receipts_without_file`: the count of local receipts whose file is no longer in the exchange. It
    can't be attributed to a key, because receipts store neither key nor node.
  - `filtered_by_since`: how many files `--since` filtered out. `--since` uses the exchange file
    mtime, which a writer can set, so it is documented as approximate.
  - `unreadable`: a count.
- Never writes, deletes or quarantines anything. A test checks that the DB, `-wal`/`-shm` and the
  exchange are byte for byte unchanged.

## Documentation

`memory-sync/KEY-PROTECTION.md` gains "Lost or compromised keys", the runbook:

1. **The decision table** above. A restore never makes a compromised key safe.
   - #8 restore refuses a key revoked in the **local** trust.
   - Restoring a revoked key into an **empty** trust works locally, but peers still refuse its new
     packets. Document this, and add a regression test.
2. **For a compromise, step 0:** on every peer, run `signed_packets revoke --fingerprint <old>`
   **immediately**, before rotating, to close the window. `peer-rotate` accepts an old key that is
   already revoked.
3. **Rotation:**
   1. stop the worker
   2. run `identity-rotate`
   3. read the new fingerprint out of band
   4. run `peer-rotate` on every peer (watch for warnings about competing proposals)
   5. run `compromise-report` everywhere
   6. update the remaining pins: re-run the wizard pairing step, which updates `security_state.peer`;
      and update the profile-memory `SignedChannel` peer fingerprint
   7. restart the workers
   8. make a new backup and run the drill
4. **`--resume`** after a crash; there is no backward rollback once the journal exists.
5. **What happens to each kind of packet:**
   - pending packets: re-signed after `RESIGN`
   - published packets: unchanged; peers accept them through the manifest
   - historical receipts: unchanged
   - forgeries already committed: found by the report, fixed by normal edits
6. **Key recovery is separate from SQLite data recovery:** never roll back the DB to fix a key.
7. **Backup destinations and custody:**
   - backup destinations: offline media, not Syncthing, git or cloud sync
   - retention: the latest verified backup per active key; delete backups of revoked keys
   - passphrase custody: a password manager or sealed paper, separate from the file
   - periodic restore drills: monthly, and after every rotation or restore
8. **Limits:**
   - forgeries committed before the rotation remain
   - a DB clone elsewhere can't be fenced
   - `--since` is approximate
   - unlinking a file is not secure erase
   - Python can't wipe memory

## Testing

Unit tests use disposable identities and DBs, and the fast KDF is test-only.

- **R1:**
  - after rotation, a receive cycle on the rotated device reports `invalid == 0` and doesn't fail
    permanently, with its old published files present
  - a local-node file that doesn't match the outbox is still `invalid`
- **R2:**
  - a peer accepts a not-yet-received legitimate old-key packet listed in the manifest after
    peer-rotate
  - it refuses an old-key packet signed after rotation that is not in the manifest (the forgery case)
  - it refuses a manifest uuid whose digest differs
  - packets pending on causality behind a manifest packet converge
- **identity-rotate:**
  - a raw old identity, a protected old identity, a lost identity with trust, and a lost identity
    without trust (with `--old-key`)
  - **refusals:**
    - a legacy DB
    - a binding mismatch
    - an unexpected key in the pending outbox
    - the worker running, the cycle lock held, the lifetime lock held
    - recent exchange activity
    - `STOPPED` or `RESIGN` not typed
    - a pending packet already in the exchange: both before the transaction, and appearing between
      the preparation and the transaction
    - #8 leftovers present, and the journal present without `--resume`
  - re-signed rows keep uuid, body and checksum, and verify only with the new key
  - published rows are byte for byte unchanged
  - the manifest equals the published old-key rows
  - **crash injection** after each journal step, then `--resume`, gives the same final state as an
    uninterrupted run
  - exactly one active key per sender
  - the OS item is removed (fake backend); a removal failure is reported, and None on Linux is handled
  - no raw key on disk: record every write and check the journal holds only a sealed identity
  - no secrets in reports or errors
- **peer-rotate:**
  - success writes the manifest and the trust
  - a wrong fingerprint changes nothing
  - an unknown old key, already rotated, a revoked new key, own key or own node, a group mismatch
  - a tampered proposal (each field, the signature, the manifest) and an oversized proposal
  - the warning about competing proposals
  - the old key already revoked (step 0) is accepted
- **End to end with two peers:**
  1. Pair A and B with signed policy.
  2. A has published packets that B hasn't received yet, plus pending packets.
  3. B revokes the old key immediately.
  4. A rotates (RESIGN), and B runs peer-rotate.
  5. Run cycles on both: everything legitimate converges, and `invalid == 0` on both.
  6. A forged old-key packet written into A's folder is refused by B, and shows up in
     `compromise-report` on A (`forged_candidates`) and on B (not committed).
  7. B's earlier receipts still verify as historical.
- **compromise-report:**
  - committed, `in_manifest`, `forged_candidates`, `receipts_without_file`, `filtered_by_since`
  - no row contents or raw keys in the output
  - the DB, WAL/SHM and exchange are unchanged
- **#8 interplay:**
  - restore refuses the rotation journal and staging
  - a revoked key restored into an empty trust is accepted locally, and its new packets are refused by
    a peer (regression)
- **Frozen smoke:**
  - `--help` for all three commands
  - without a console, `identity-rotate` and `peer-rotate` refuse and change nothing
  - `compromise-report` on a disposable DB and exchange returns an empty report
- **Packaging and CI:** add the module to `HIDDEN_MODULES` and `build_package.py`, and add the new test
  file to the native CI pytest list (macOS and Windows). Test fixtures create private directories with
  the product's private-mkdir helpers, never a bare `mkdir`, and never rely on the cwd: the #8 Windows
  lessons.

## Out of scope

- A new sender (allocation migration).
- Automatic quarantine or rollback.
- Rotating OTA release keys.
- Activating signing.
- Updating the wizard state and the profile-memory pins automatically (documented in the runbook).
