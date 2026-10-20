# Encrypted profile log Phase 4 REPORT

Date: 2026-10-20. Implements the eight Phase 4 tasks and the owner's per-profile
age-identity amendment. All **80 Phase 4 test cases pass**, covering P4-1 through
P4-10. Whole-change acceptance remains **not fully verified**: three existing tests
fail explicitly because this sandbox denies socket binding, and native Mac/Windows
key-store smoke plus three-OS execution remain owner steps. No commit was made.

## Work completed

Tasks 1–7 were retained from the preceding implementation, then updated for the
amendment. Task 8 is completed with CLI integration, end-to-end coverage, README
“Devices and recovery”, FORMAT “Phase 4 records”, and this report.

| Task | Implementation |
| --- | --- |
| 1. Age and signed registry | Real pyrage X25519 identities; strict signed device, profile-recipient, revocation and recovery records; historical/live certificate checks. |
| 2. Keyring | Signed age grants, issuer/ancestor authority, recipient binding, decrypted key-ID check, epochs and deterministic concurrent winner. All profile grants use independent per-profile identities. |
| 3. Presence | Console TTY confirmation, protected class windows (default 15 minutes, explicit 1–15), every-trigger log, first-access/cross-profile/secret broker hooks. |
| 4. Pairing | Storage commit/reveal SAS and QR, expiry/single-use pins, eight committed profile slots, signed assignments, recipient publication and scoped bootstrap. |
| 5. Lifecycle | Device removal with affected-descendant rotation; archive read-only behavior; profile age/epoch/index destruction on delete and peer sync; deletion rotates recovery. |
| 6. Recovery | SLIP-39 2-of-3; one-shot nonmutating test; per-profile master-encrypted recovery identities; historical root recovery; signed alerts; all-scope/master rotation and new shares. |
| 7. OS store | Ported Mac/Windows backends, injected fake tests, sealed state, read-back-before-unlink migration, root-free first initialization. |
| 8. CLI/docs/tests | Administration commands, later-profile recipient/regrant flow, recovery/delete share inputs and output, CLI alerts, two-device integration, normative records and owner instructions. |

Scope remained `profile-log/**` and this report. No network, dependency installation,
real identity/database access or native key-store operation was used for this amendment
pass. No shared-memory recall or `~/.hermes` access was repeated. Only the prepared
`~/.cache/agentmesh-logvenv/bin/python` interpreter was used. Text I/O specifies UTF-8;
the OS subprocess wrapper specifies UTF-8 with text mode. No branch, commit or push
was created. Native tests use disposable state and fake backends.

## Final verification

Both commands used `PYTHONDONTWRITEBYTECODE=1 AGENTMESH_R2_LIVE=` and the prepared
interpreter. The final tests include the completed source changes; subsequent edits
were this report only.

| Directory | Arguments after interpreter | Result |
| --- | --- | --- |
| Repository root | `-m pytest -q --tb=short -p no:cacheprovider profile-log/tests` | **327 passed, 5 skipped, 3 failed**, 36.04 s |
| `profile-log/` | `-m pytest -q --tb=short -p no:cacheprovider tests` | **327 passed, 5 skipped, 3 failed**, 36.03 s |

The 250 pre-existing active tests retain their baseline outcome: 247 pass and three
fail on the same bind restriction recorded in the Phase 3 REPORT. The 80 added Phase 4
cases pass (55 original plus 25 reviewer-follow-up cases). The five skips are the existing opt-in live R2 and Windows-only junction
cases; no socket test was skipped or xfailed.

The explicit failures, relative to `profile-log/`, are:

- `tests/broker/test_isolation.py::test_p3_1_processes`: `P3-1 blocked: sandbox denies socket binding`.
- `tests/broker/test_transport.py::test_real_transport`: `IPC acceptance blocked: sandbox denies socket binding`.
- `tests/test_storage.py::test_s3_custom_http_endpoint`: `HTTP acceptance gate blocked: sandbox denies 127.0.0.1 bind`.

`git diff --check` passes. No Phase 1–3 event/wrap/frame format was changed; Phase 4
adds records and a private broker-state v2. Existing policy fixtures explicitly open
test presence windows. Existing CLI administration tests inject fake OS storage and
console input in-process, so automated testing never calls a real native keychain;
the real transport process tests remain intact and fail explicitly in this sandbox.

## Gate-to-test mapping

Paths below are relative to `profile-log/tests/phase4/`. “Pass” means executed on
this macOS host with fake OS storage, not an unperformed native/cross-OS test.

| Gate | Dedicated tests | Evidence |
| --- | --- | --- |
| P4-1 Forged grants | `test_keyring.py::test_p4_1_forged_grants`; `test_profile_recipients.py::test_p4_1_profile_binding`, `test_p4_1_recovery_binding` | Pass. Unknown/revoked/nonholding issuers, altered signed fields and wrong decrypted key ID fail. Another profile's age key and the generic device age key fail. Missing/tampered profile binding fails. A daily-device signature cannot replace a master-signed profile recovery binding. |
| P4-2 Pairing MITM | `test_pairing.py::test_p4_2_commit_reveal_mitm`, `test_p4_2_order_expiry_and_replay`, `test_p4_2_slot_binding` | Pass. Substituted commitments yield different SAS; “no” refuses approval. All 10,000 freshly self-signed alternative reveals fail commitment verification. Premature reveal, replay, expiry and request reuse fail. Slot tampering and duplicate recipient slots fail. |
| P4-3 Scoped grants | `test_pairing.py::test_p4_3_scoped_bootstrap`; `test_profile_recipients.py::test_p4_3_later_profile` | Pass. Client-a bootstrap cannot open client-b, work root/common or personal. Later personal regrant fails before B publishes its profile identity, then succeeds with a distinct identity and independently checked metadata/grants. Unassigned pairing identities are discarded. |
| P4-4 Revocation | `test_records.py::test_p4_4_registry_revocation`; `test_lifecycle.py::test_p4_4_remove_rotates_descendants`; `test_end_to_end.py::test_p4_4_two_device_end_to_end` | Pass. Self/unknown/revoked revokers fail; post-revocation certificates fail. A/B broker write, sync and recall through filesystem storage; after removal, handshake verification rejects B on both synced states and every stolen scope key fails future events. Remaining devices retain historical readability. |
| P4-5 Root gone | `test_oskeystore.py::test_p4_5_root_gone` | Pass. Captured initial/migration account root bytes are absent in raw/hex/base64url from every home file and decrypted store record. Initialization and migration drop root authority; a surviving device still certifies another device. |
| P4-6 Recovery | `test_recovery.py::test_p4_6_recovery_all_lost[ab/ac/bc]`, `test_p4_6_scopes_added_after_init`, `test_p4_6_cli_alert`, `test_p4_6_forged_alert` | Pass. Every pair of shares restores all profile/scope history after loss; one fails. Every current epoch changes; alert reaches a survivor and CLI sync. Old shares fail the new current record. A second total loss recovers history using replacement shares. Recovery test hashes the full temporary tree excluding its log, which grows exactly one line with `window_used=false`; no window or other file is changed. Forged alerts are ignored. |
| P4-7 Presence | `test_cli_phase4.py::test_p4_7_cli` (12 actions × TTY/non-TTY); `test_presence.py::test_p4_7_console_windows`, `test_p4_7_broker_hooks`, `test_p4_7_secret_results` | Pass. All listed sensitive actions plus recipient/regrant refuse failed/non-TTY approval without changing sealed state. First access, cross-profile and secret reads require appropriate windows. Expired windows and a forged external window file fail; triggers are logged. |
| P4-8 Amended profile lifecycle | `test_lifecycle.py::test_p4_8_archive_delete`, **`test_p4_8_retained_age`**, `test_p4_8_bad_delete_shares`; `test_cli_phase4.py::test_p4_8_delete_cli` | Pass. Archive still recalls and refuses writes. After deletion/sync on A/B, every retained generic/profile age identity fails every stored deleted-profile age payload; every retained epoch key fails every deleted event. Deleted key material is absent from both homes and decrypted state in raw/hex/base64url and age secret-string forms. Surviving events remain readable on A/B and a fresh recovery home. New master shares cannot restore the deleted profile. Two old shares explicitly decrypt its old profile recovery object, then the old root grant and event. Insufficient shares change nothing except one presence-log line. CLI prints the old-share warning and returns current replacement shares. |
| P4-9 Epochs | `test_keyring.py::test_p4_9_concurrent_epochs`; `test_end_to_end.py::test_p4_9_pending_epoch` | Pass. Two independent readers converge on the deterministic concurrent winner, including ties; losing epochs stay readable. Broker refuses stale preparation and an already prepared write superseded before signing. |
| P4-10 OS store | `test_oskeystore.py::test_p4_10_encryption_tamper`, `test_p4_10_migration_failure`; supporting `test_p4_5_root_gone` | Pass with fake backend. Sealed files contain no tested plaintext key material, wrong K/tampered files fail closed, read-back failure preserves the plaintext migration source, successful migration removes it only after matching read-back. |

The integration test exercises broker sessions and handshake verification directly.
It does not substitute for the blocked native socket tests or claim that native IPC
ran successfully.

## TDD evidence for the amendment and Task 8 completion

The owner-requested `test_p4_8_retained_age` name was retained and rewritten around
per-profile capabilities and the explicit old-shares exception. Observed red states
before implementing the respective behavior were:

- Profile recipient test: missing `profile_recipient` API.
- Adapted retained-age regression: missing `control['profile_ages']`.
- Later-profile CLI flow: parser rejected the missing `pair recipient` action. After
  adding it, the presence action whitelist still refused it; implementation completed
  both before the test passed.
- Delete CLI: parser rejected missing `--share-file` support; implementation then wired
  shares into deletion and returned replacement shares.
- CLI recovery alert: `KeyError: 'alerts'`; verified alerts were then added to Phase 4
  sync output.

Intermediate focused runs reached 46 and then 50 passing Phase 4 tests. Those original full
runs included all 55; the reviewer follow-up below increases this to 80. Additional adversarial regression coverage checks recipient
signatures/slots, forged alerts, insufficient deletion shares, age secret-string
removal and the one-shot presence-log outcome. No test was weakened to permit the
original generic-age crypto-shredding flaw.

## Deviations and concrete choices

1. **Per-profile identity amendment is normative.** Generic device age keys remain in
   device records for the existing device binding but never encrypt profile grants.
   Independent random profile identities encrypt both scope keys and metadata. Recovery
   uses independent random profile identities encrypted to the derived master.
2. **Deletion takes two current shares.** Rotating the master recovery record requires
   the previous recovery signing key, and neither it nor the account root may remain
   on a daily device. Delete therefore accepts the same share-file/hidden input flow
   as recovery, uses one-shot approval so its warning is always shown, and emits new
   shares. This follows the amendment without retaining recovery signing authority.
3. **Explicit old-share exception.** Two old shares still decrypt the deleted profile's
   immutable old profile-recovery object and grants. README, the delete prompt and the
   regression test all state/assert it. Destroy those shares or manually remove the
   old objects; no automatic storage deletion is performed.
4. **Additive recovery-history envelopes.** The plan did not specify how a fresh home
   recovers private scope names/topology or preserves old root epochs through repeated
   master rotations. Signed, per-profile-encrypted history supplies both, and is fully
   specified in FORMAT. It contains root keys only; descendants use existing wraps.
   Profile recovery objects use a path versioned by master recovery ID.
5. **Pairing certificate publication.** An approver cannot create the joiner's own
   signature. It publishes the certificate at a transcript path; after result validation
   the joiner publishes its own device and profile-recipient records before opening
   grants. The first-join fingerprint binds account, device ID and signing public key.
6. **Later-profile preparation is `pair recipient PROFILE_ID`.** It persists the fresh
   private identity locally before publishing the immutable signed public record.
   Regrant fails/waits operationally until the record exists; the owner can retry it.
   Missing recipients are never substituted with generic or another profile's keys.
7. **Existing profile metadata is immutable.** New-profile metadata is imported on
   sync; evolving an already paired profile's topology is not automatically refreshed
   by this phase. Grants/regrant cover known selected scopes and descendants. Recovery
   history does record later scopes (tested). This limitation is documented in README.
8. **Historical validation and deletion monotonicity.** Live revoked agents are refused,
   while historical events validate at their timestamps. A seen deletion cannot be
   undone by replaying an active record. All index snapshots are conservatively removed
   on profile deletion; surviving profiles rebuild their indexes from encrypted data.
9. **No physical erasure claim.** Destruction removes persisted/current capabilities;
   Python cannot guarantee zeroing all historical RAM allocations, and old backups or
   stolen copies remain outside that guarantee. The amended gate deliberately tests
   remaining state plus the old paper-share exception.
10. **Environment evidence only.** Native keychain/Credential Manager and Windows/Linux
    execution were not attempted. The three socket failures are retained, and the
    original five environmental/opt-in skips are unchanged. No live R2 request ran.

## Manual owner steps

1. Run both documented suite commands outside the socket-restricted sandbox. In
   particular rerun the three explicit failures above. Run the suite on macOS, Linux
   and Windows using the prepared dependencies; verify Windows UTF-8 behavior and
   named-pipe paths. No three-OS CI result is claimed here.
2. On Mac and Windows, run `profile-log/tests/smoke_os_keystore.py` with the platform's
   prepared interpreter. It asks for `smoke`, creates one disposable native item,
   verifies a sealed round-trip and deletes that item in `finally`. This is the first
   point where a real OS store should be touched for acceptance.
3. Initialize a disposable A/B setup using the README commands. Compare trusted
   approver fingerprints and SAS, grant only the desired scopes, write/sync/recall,
   then remove B and verify future-event exclusion through native broker transport.
4. Print the three initial recovery shares and store them separately. Keep a trusted
   copy of the public account record in the recovery kit. `--out` must target a new
   directory outside both broker home and replica; otherwise shares are printed once
   in the command's JSON result. Do not capture that output in ordinary logs.
5. Test two shares with one-shot `recovery test`. For actual recovery or deletion,
   safely store the replacement shares before destroying the superseded paper set.
   Understand that two old shares still decrypt deleted-profile history unless those
   shares are destroyed or the old stored recovery objects/grants are manually removed.
   Allow every remaining device to sync the deletion; handle offline copies/backups
   separately. Never use real memory databases for these acceptance rehearsals.

## Reviewer follow-up: mutation coverage and presence duration

The reviewer reported an outside-sandbox baseline of **305 passed, 5 skipped**, and
13/23 security mutants killed before this follow-up. The supplied actionable list
names nine mutant IDs; R2-adjacent explicitly requires no action. Every named mutant
now has a dedicated regression test that was observed failing with that edit applied
and passing against the real implementation. The additional parent-time mutation
below independently verifies the requested backdating boundary. This is evidence for
those ten specified mutation scenarios, not a claim to have rerun all 23 or an
unidentified tenth survivor.

### Mutation table

All tests in this table are in `profile-log/tests/phase4/test_mutations.py`.
Each row was run in a separate process with exactly that source edit loaded into the
module in memory. No production file was modified by mutation testing. All selected
parameter cases fail under their mutant; failures are assertion/expected-error
failures, not import, collection or syntax errors. The same 14 security regression
cases pass with unmodified production security code.

| Mutant / gate | Dedicated test | Observed mutant failure and real-code behavior |
| --- | --- | --- |
| K2 / P4-1: remove grant issuer authorization | `test_k2_no_scope` | Mutant accepts a grant signed by a valid non-revoked device with no scope grant. Real code raises `unauthorized key issuer`. The attacker generated the key; receiver binding, signature, age ciphertext and key ID are otherwise valid. |
| K6 / P4-1: remove recovery-root restriction | `test_k6_recovery_root` | Mutant validates a non-root recovery grant. Real code raises `recovery requires profile root`; a root grant from the same authorized issuer validates. This tests validation directly so a missing recovery recipient cannot mask the restriction. |
| K7 / P4-9: remove epoch issuer authorization | `test_k7_epoch_authority` | Mutant selects the unauthorized device's signed successor as current. Real code retains the authorized genesis epoch and rejects the rogue epoch with `unauthorized epoch issuer`. |
| K8 / P4-1, P4-3: drop ancestor/scope filtering | `test_k8_sibling_scope` | Mutant lets a device holding only client-b issue a client-a grant. Real code rejects it. The test positively verifies client-b authority and provides valid receiver binding and ciphertext. |
| R3 / P4-4: remove revocation signature verification | `test_r3_forged_revocation` | Mutant loads a forged revocation naming a real, non-revoked signer. Real registry loading ignores it, the subject's agent remains valid, and direct insertion rejects its invalid signature. |
| R4 / P4-4: omit issuer-chain walk | `test_r4_issuer_chain[device/agent/grant]` | All three cases fail under the mutant: descendant public-key lookup, its agent and its grant are accepted. Real code rejects each with `revoked device`. The descendant certificate and an otherwise sufficient scope grant are cached before the ancestor revocation arrives; the revocation timestamp predates certificate issuance. |
| P2 / P4-2: omit Na and approver_hash from SAS | `test_p2_nonce_sas` | Mutant gives the same SAS for all approvals. Real SAS changes when only the legitimate approver's nonce/signature changes, with commit and reveal held constant. Eight nonce values avoid reliance on a single possible six-digit collision. |
| P5 / P4-2: remove begin reuse guard | `test_p5_reused_begin[persisted/approval/reveal]` | All three cases fail under the mutant. Persisted pins survive a relay deleting approval; a second legitimate approver respects an existing approval; a retained reveal alone blocks reuse. Real code raises exactly `pairing_reused` before changing storage. The approval case explicitly rejects a generic filesystem `ObjectExists` error as insufficient, eliminating the earlier masking effect. |
| P6 / P4-2: omit approval commit-hash comparison | `test_p6_commit_replay` | Mutant accepts an approval for another commit whose request ID was patched and legitimately re-signed. Real `check_reveal` raises exactly `pairing_mismatch`. Request-ID and signature checks cannot mask the commit-hash check. |
| Additional parent-time boundary / P4-9 | `test_backdated_epoch` | Removing the successor timestamp constraint makes a backdated successor displace the legitimate rotation and fails the test. Real code retains the post-revocation rotation for an older branch, a backdated successor and a post-revocation-dated forgery. |

Reproduce the mutation checks with the prepared interpreter, from repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 AGENTMESH_R2_LIVE= ~/.cache/agentmesh-logvenv/bin/python profile-log/tests/phase4/run_review_mutations.py
```

The runner is an offline test utility, not a collected pytest test. It uses isolated
child processes, explicit UTF-8 text capture, and in-memory source replacement. It
exits zero only if every selected case fails for each mutant; expected red pytest
output is printed as evidence. Final execution: **10 mutation scenarios killed,
14 dedicated parameter cases failed as expected, runner exit 0**. No security
production-code change was needed for these findings.

### Timestamp residual and tested boundary

`issued_at` and `created_at` are self-declared by signers. They are not trusted
proof of when a record was made. A revoked device that retained an old key can
backdate a signed grant and redistribute that key. This is a residual capability,
not a promise that revocation can retract already disclosed keys. It is now stated
in README as well as here.

`test_backdated_epoch` first demonstrates this residual explicitly: after revocation,
a grant of the old root key with an earlier `issued_at` still opens. It then proves
that a legitimate rotation dated after `revoked_at` remains current despite three
attempts: a backdated branch off the old epoch, a backdated successor of the new
epoch, and a successor dated after revocation. The first loses current-epoch selection;
the second predates its parent and is excluded; the third fails revoked-signer
validation. The removed device does not gain replacement keys from the retained old
key. Historical backdating remains possible; trusted timestamping is not added.

### Presence duration: failing tests, fix and verification

The reviewer-selected behavior is implemented: `PresenceManager.open(minutes=N)`
accepts exact integers 1–15 and stores expiry at `clock() + N * 60,000`. The default
and implicit action-confirmation windows remain 15 minutes. Invalid durations are
rejected before logging or writing state. This is the only production-code change
in this reviewer follow-up; no additional security bug was uncovered.

New tests in `test_presence.py`:

- `test_window_minutes[1/7/15]`: exact stored expiry, active just before expiry and
  inactive exactly at expiry.
- `test_bad_minutes`: zero, negative, over-limit, boolean, fractional, integer-valued
  float and string inputs fail without modifying state or appending a trigger.
- `test_cli_minutes`: the real parser/execute flow honors `--minutes 3` and stores
  exactly the requested duration.

TDD observations: the 1- and 7-minute tests and the CLI 3-minute test failed on the
original `minutes != 15` guard before implementation. After the fix, the focused
mutation-regression/presence suite passes **28 cases**. Both final full-suite runs
listed above pass **327**, skip the same **5**, and fail only the same **3** sandbox
bind tests. All **80 Phase 4 cases pass**. README and FORMAT now describe configurable
1–15 minute explicit windows. No network, real native store, shared-memory recall,
commit or dependency installation was used. The reviewer should rerun the updated
suite outside the sandbox; the reported earlier 305-pass result predates these 25
new cases.

## Reviewer verification (Claude, 2026-10-20)

Run on macOS outside the Codex sandbox with the prepared interpreter (pyrage 1.4.0, shamir-mnemonic 0.3.0,
segno 1.6.6): **330 passed, 5 skipped**, including the socket tests that failed inside the sandbox. Three-OS CI
runs on the PR.

Owner decisions during implementation:

- `recovery test` may append only its presence log line.
- Per-profile age identities enable crypto-shredding (amendment). The old-shares exception is documented and
  asserted.

Independent mutation testing of the Phase 4 security checks. Each mutant was applied alone, and the full suite
was run against it.

- Round 1: 14 of 23 mutants killed. The 9 survivors were K2, K6, K7, K8, R3, R4, P2, P5 and P6.
- Round 2: Codex added 25 regression cases. **All 23 are killed**: keyring K1–K8, revocation/registry R1–R4,
  pairing P1–P6 and presence S1–S5.
- The Phase 3 isolation mutants (19) were rerun on the Phase 4 code and are all still killed.
