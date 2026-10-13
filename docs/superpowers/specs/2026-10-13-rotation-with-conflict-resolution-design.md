# Key rotation with the conflict-resolution protocol (#9 follow-up): design

Status: approved in conversation on 2026-10-13. This amends
`2026-10-13-key-rotation-recovery-design.md` (#9, merged in PR #39) for the conflict-resolution protocol
merged in PR #35. Where the two disagree, this document wins.

## Problem

#9 re-signs pending outbox packets with the new key (typed `RESIGN`). #35 makes that unsafe:

- **v3 resolution packets** (`signed_packets.RESOLUTION_FORMAT`, domain `RESOLUTION_DOMAIN`, fields
  `RESOLUTION_FIELDS`) can sit in the pending outbox.
  - `Security.sign` refuses them (`new packet emission disabled`).
  - `key_rotation.verify_old` assumes the v2 `DOMAIN` and a v2 body checksum.
  
  So rotation refuses whenever a v3 packet is pending.
- **Digests break.** #35 references packets by `(uuid, envelope_digest)`, which includes the
  signature, in `covers`/`evidence` and in `_sync_packet_dispositions`.
  `resolution_apply._index_local` stores the device's **own** outbox packets, unpublished included, in
  `_sync_packet_evidence` with their envelope digest and `key_id`. Re-signing changes that digest, which
  invalidates:
  - the stored evidence row
  - every reference to the re-signed packet

## Decision (made by the user)

**Rotation no longer re-signs anything.** It publishes the pending old-key packets **unchanged** and lists
them in the signed manifest, alongside the already-published ones. Peers accept them through R2 exactly
like earlier old-key packets.

- **The typed word** changes from `RESIGN` to `PUBLISH`.
- **No packet bytes, signatures or digests change**, so #35's references and evidence stay valid.
- **It works for v2 and v3 alike**, and in the lost-key case too, because pending packets are already
  signed.

## Changes

### `identity-rotate`

1. **Preparation.**
   - Every pending row must parse and pass `signed.validate_envelope` against the binding (v2 or v3).
   - It must carry the binding's sender, group and node, and `old_key_id`.
   - When the old public key is known, it must verify historically with the **format's** domain
     (`signed.packet_domain(p)`).
   - For v2, the body checksum is checked too; for v3, `validate_envelope` already checks the semantic
     checksum.
   - No exchange file may exist yet for any pending uuid (unchanged).
   - If there are pending rows, show their count per format and per table/op. The operator types
     `PUBLISH`.
2. **Manifest.** Every outbox row signed by `old_key_id`, **published and pending**, as
   `(uuid, receipt_digest(packet))`. `receipt_digest` is computed exactly as `verify_rotated` computes
   it.
3. **The DB step is replaced by a publish step.** In the same position in the journal, inside one
   `BEGIN IMMEDIATE` transaction:
   - For each pending old-key row, write its stored text **byte for byte** to
     `EXCHANGE/signed-changes/<node>/<uuid>.json`. Use the same no-clobber, fsync and
     immutable-collision rule as `memory_sync.publish`: a file that already exists with identical bytes
     counts as done, and different bytes are an error.
   - Then `UPDATE _sync_outbox SET published=1`.
   - Commit after every file is in place.
   - On resume it's idempotent: rows already published with identical files are skipped.
   - The packets are **not** re-signed, and `Security.guard`'s check of the unpublished backlog no longer
     sees old-key rows.
4. **Order.**
   1. journal
   2. trust
   3. identity
   4. publish pending
   5. verify
   6. proposal
   7. finish
   
   The manifest, signed in the proposal, already lists the pending uuids.
5. **The report** replaces `resigned` with `published_pending: <n>`.

### Receive and evidence (points 1 and 3)

- **Point 1 is already done** (merged in 1ae701e). Both receive paths recognize own outbox packets
  before any signature check, and verify new input through `memory_sync._verify_signed` (a revoked key
  goes to `verify_rotated`).
- **Point 3, decided:** a packet on an approved rotation manifest counts as **authorized**, including as
  `covers`/`evidence` of a resolution, and including a v3 resolution packet signed by the old key.
  - The reason: the manifest is history the operator approved by fingerprint.
  - #35's strict revocation still applies to everything **not** on a manifest.
  - Document this in SIGNED-SYNC.md (next to "Signing and revocation") and in KEY-PROTECTION.md.

### `compromise-report`

- Handle v3 files: `validate_envelope` with `RESOLUTION_FIELDS`.
- For v3, `changes` lists `kind` plus table/op and the key hash from the body. Never row contents.
- When the resolution store exists, add `superseded: true|false` per packet (a row in
  `_sync_packet_dispositions`).
- Keep the read-only guarantee.

## Testing

All tests run on DBs **with** the resolution store (`resolution_store.migrate`). Where cheap, they also run
without it.

- **Pending v2 with the store active:**
  - rotation publishes the files byte-identically and marks them published
  - `_sync_packet_evidence` digests are unchanged
  - the peer receives the packets through the manifest
  - `invalid == 0` on both devices
- **Pending v3 resolution packet signed by the old key:**
  - rotation succeeds; the packet is published unchanged and on the manifest
  - the peer accepts it
  - its `covers`/`evidence` references still resolve, and the resolution converges
  
  Build it through the real `conflict_commands`/`resolution_apply` flow used by
  `test_resolution_e2e.py`.
- **Evidence from the old key on the manifest, not yet committed at the peer:** accepted as authorized
  evidence for a resolution. Evidence from the old key **not** on the manifest is refused.
- **Crash or resume:** a crash during the publish step, then `--resume`, leaves the same final state.
  Partially written files are identical bytes or absent.
- **Refusal:** a pending file that already exists in the exchange with **different** bytes refuses, and
  nothing changes.
- **compromise-report:**
  - v3 packets are listed with `kind` and `superseded`
  - no row contents appear
  - the DB and exchange are byte for byte unchanged
- **Regression:** the existing #9 tests are updated from RESIGN to PUBLISH; the full suite runs from
  `memory-sync/` and from the repo root.

## Amendments from the security review (2026-10-13), binding

1. **H1: the manifest binds `envelope_digest`, not `receipt_digest`.**
   - **Why:** `receipt_digest` leaves the signature out. Someone holding the old key can produce another
     valid Ed25519 signature for the same unsigned content and swap that variant into the exchange.
     A peer then commits the variant's `envelope_digest`, rejects A's real file later, and every
     resolution referencing `(uuid, A's digest)` breaks for good.
   - **The manifest value** is `signed.envelope_digest(p)`, computed over A's exact outbox packet.
   - **`verify_rotated`** compares `envelope_digest(p)`.
   - **Versioning:** bump the proposal format to `agentmesh-key-rotation-v2` and the registry format to
     `agentmesh-rotations-v2`. A v1 proposal or registry entry is **refused**, never accepted alongside
     v2. It's OK to refuse, because no deployment has rotated yet.
   - **`compromise-report`'s `in_manifest`** compares `envelope_digest`.
   - **Test:** a signature variant of a manifest packet (same uuid and unsigned content, different
     signature, signed with the old key) is refused at the peer, and A's real file is still accepted
     afterwards.
2. **H2: resume during the publish step.**
   - **While a journal exists**, a pending uuid's exchange file counts as already written only when
     `read_local(file) == row text encoded`. Different bytes refuse with the specific reason
     `pending packet file differs from the outbox; see compromise-report`.
   - **Without a journal**, keep the existing check that the file is absent.
   - **Test:** crash after some files are written, before the commit, then `--resume` completes.
3. **M1: tie publishing to the signed manifest.**
   - Inside the publish transaction, check that every old-key pending row's
     `(uuid, envelope_digest)` equals its entry in the journal's staged manifest.
   - Check that the set of pending old-key rows equals the manifest's pending subset exactly.
   - Otherwise refuse, and change nothing.
4. **M2: review before a compromise.**
   - With `--reason compromised`, before `PUBLISH`, list each pending packet: uuid, format, `kind`
     (v3), table/op and `key_sha256`. Never row contents.
   - **Option `--drop-pending UUID`** (repeatable, compromised only): the named pending rows are
     neither published nor put on the manifest.
     - They stay in the outbox marked `published=0`. Record them in the journal; on finish, move them
       to a local `_sync_rotation_dropped(uuid, packet, dropped_at)` table instead of deleting them.
     - The report lists them.
     - Dropping a row that another pending packet references as evidence/covers refuses.
5. **M3: recovering from a collision.**
   - A pending uuid whose exchange file has **different** bytes during a journal is a likely
     compromise artifact.
   - `identity-rotate --resume --quarantine-collision UUID` shows both envelope digests. After the
     operator types `QUARANTINE`, it moves the exchange file to
     `EXCHANGE/pairing/rotations/quarantine/<uuid>.<digest>.json` and continues.
   - A's outbox stays authoritative.
6. **M4: compromise-report.**
   - `superseded` is true only when a `_sync_packet_dispositions` row matches this file's
     `envelope_digest`. A disposition for the same uuid with a different digest is reported as
     `variant_superseded`.
   - v3 rows are validated with `packet_versions` (the v3 validator), not the legacy projection.
   - The `forged_candidates` byte check also runs for v3.
7. **Nice to have, include:**
   - `verify_rotated` does not cache a missing registry entry across a `rotations.json` change: key the
     cache on the registry's mtime and size.
   - SIGNED-SYNC.md says that manifest acceptance gives authorization state `verified`, not `applied`.
   - Test: a resolution plan prepared before rotation is refused after it (stale trust digest).
