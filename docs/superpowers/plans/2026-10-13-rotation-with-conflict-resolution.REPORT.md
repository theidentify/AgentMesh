# Rotation with conflict resolution: implementation report

Branch: `fix/rotation-resolution-packets`.
Worktree: `/Users/krisanathep/Workspaces/AgentMesh/.claude/worktrees/encrypted-key-storage-28470f`.

The new design, the full #9 design and plan Ground rules, and #35's “Signing and revocation
decision” were read before implementation. The new design takes precedence. Tasks were executed
in order. No branch switch, push, PR, merge, rebase, deployment or real credential-store operation
was performed. This is the only new report/bookkeeping file; no GATES.md was created.

## Task commits and test-first evidence

All commits end with `Co-Authored-By: GPT (Codex) <noreply@openai.com>`.
Counts are per working directory, not the sum of duplicate runs.

| Task | Commit | Verification |
|---|---|---|
| 1. Failing regression tests | `3058810` | From both directories: 6 expected failures, 3 passes. Failures demonstrate the old confirmation word, v3 preparation refusal, mutation during replay and missing publish checkpoint. Manifest-authorized evidence, strict refusal of unlisted evidence and existing-file refusal already passed. |
| 2. PUBLISH implementation | `e7a53c2` | 435 passed from each directory: rotation, memory sync, signed packets, security regressions, identity backup and resolution E2E. Includes the source recovery smoke. Additional interrupted-temporary-file tests failed before their fix. |
| 3. v3 compromise report | `2f4b70a` | 8 new tests failed before implementation, then passed. All 158 recovery tests passed from each directory. |
| 4. Documentation | `bd7b56b` | The documentation contract check first failed on the old wording, then passed. 31 relevant publication/report tests passed from each directory. |
| 5. Final verification and report | This report's commit | Final suite results below. Includes a test-first correction to skip updates of already-published rows, and two additional publication fault-injection test updates. |

## Implemented behavior

- Pending signed v2 and v3 packets, including resolutions, are published from their stored UTF-8
  text without re-signing, reserializing or changing signatures, digests or evidence references.
  Tests compare exchange bytes and retained `_sync_packet_evidence` rows, including noncanonical
  stored whitespace and lost-key cases.
- Preparation validates the envelope, binding, old key and format-specific checksum; known old
  public keys verify with `signed.packet_domain`. Pending packets count toward the manifest together
  with published old-key packets. The operator sees counts per format and table/operation, types
  `PUBLISH`, and receives `published_pending` in the report.
- Publication uses one `BEGIN IMMEDIATE` transaction. All files are in place before the flags commit.
  Tests cover failures before/after a file, after the commit, between files in a batch, each journal
  step, abandoned temporary writes, collisions, changed outbox state and immediate resume.
  Completed rows are skipped on replay; the post-commit retry regression also compares DB bytes.
- Real activation and resolution prepare/commit helpers build signed conflict histories. Peers
  converge with old-key v3 resolutions and previously uncommitted old-key evidence on an approved
  manifest. The negative control leaves the resolution listed but removes its source evidence from
  the manifest: application refuses and no disposition is committed.
- Receive, signature-verification and evidence-authorization code is unchanged. No synthetic receipts
  or blanket revocation exception were introduced. Manifest membership still requires the receipt
  digest and historical signature verification. Everything unapproved retains #35's strict revocation
  and its existing exact committed-receipt/disposition rules.
- `compromise-report` accepts v3 envelopes using their semantic validation and existing row-schema
  validator. V3 packet and change summaries expose `kind`, table/op and hashed keys only. With the
  resolution store present, `superseded` requires matching complete-envelope dispositions, so a
  changed signature cannot inherit that status. Tests cover v2/v3, with/without the store, malformed
  input, superseded v3 resolutions, ordinary v3 changes, redaction and unchanged source files.
- KEY-PROTECTION.md and SIGNED-SYNC.md describe unchanged publication, approved manifests as authorized
  resolution evidence, strict revocation outside them, resume behavior and reporting.

## Necessary corrections and deviations

1. **Shared immutable writer.** Factoring publication required editing `memory_sync.py`, beyond the
   plan's suggested file list. The previous existence-check/`os.replace` sequence could overwrite a
   competing immutable file. `write_packet` now atomically links a flushed temporary file into place;
   an existing target must contain identical bytes. There is no overwrite fallback. Both ordinary
   publication and rotation use it. Four old failure injections across `test_memory_sync.py`,
   `test_signed_packets.py` and `test_resolution_apply.py` were retargeted from replace to link.
   The two subprocesses in the latter now have explicit cwd.
2. **Resume versus the old inactivity fence.** Requiring every pending path to be absent and refusing
   all recent writes makes immediate recovery from rotation's own publication impossible. The
   `identity_backup.exchange_activity` extension exempts only byte hashes pinned to the journal's
   original pending rows. An abandoned temporary file is eligible only at that journal's exact name
   and only for an exact stored-text prefix. Cleanup happens in the shared writer under the locks.
   Other recent activity, changed files, unexpected keys and changed outbox text still refuse.
   Fresh preparation still refuses any pre-existing pending exchange path, including identical bytes.
3. **Journal metadata.** A v2 journal records original pending UUID/text hashes and `published_pending`
   so that replay stays byte-bound and reports a stable original count. Existing proposal and manifest
   formats are unchanged. Old v1 re-sign journals are refused, not migrated or replayed: safely
   recovering arbitrary already-re-signed evidence cannot be inferred from the new metadata. This
   compatibility limit is documented; do not delete an unfinished old journal to bypass it.
4. **Read-only live WAL.** The new byte-snapshot test demonstrated that `mode=ro` can change source
   SHM read marks. `compromise-report` now uses the existing `protocol_activation.snapshot` reader
   for intact live-WAL pairs; only the disposable private copy is opened by SQLite. Cold DBs retain
   immutable read-only opening, and incomplete WAL/SHM pairs still refuse. This is the smallest
   correction preserving the required DB/WAL/SHM/exchange byte-for-byte guarantee.
5. **Report identity matching.** `superseded` checks complete-envelope digests, not UUID presence
   alone. `kind` is included both on the v3 packet summary and each change summary. The report is
   an inventory, not signature authentication; changing a signature produces an uncommitted,
   non-superseded candidate rather than borrowing the original packet's disposition.
6. **Final replay review.** A trigger-backed regression failed because replay redundantly updated
   published rows to the same flag. Replay now skips those updates while rechecking the immutable
   file. Its regression passes and the DB bytes remain unchanged after a post-commit crash/resume.
7. **Smoke.** `smoke_standalone.py` had no old confirmation word or report-field reference to update.
   Its existing source recovery smoke ran in the recovery and full suites. No new frozen build was
   required by this follow-up plan or performed.

## Final verification

Final full-suite results after all implementation and test corrections:

| Working directory | Passed | Known failures | Skipped | Warnings | Duration |
|---|---:|---:|---:|---:|---|
| memory-sync | 1,406 | 3 | 17 | 1 | 256.39 s |
| repository root | 1,406 | 3 | 17 | 1 | 242.99 s |

The suite means all tests under `memory-sync/`, run once there and once from the repository root,
as specified by the #9 plan and its existing report. Native OS-store/task opt-ins are removed:

```sh
# From memory-sync/
env -u AGENTMESH_NATIVE_KEYCHAIN_TESTS -u AGENTMESH_NATIVE_TASK_FIXTURE_ROOT \
  UV_CACHE_DIR=/private/tmp/claude-501/uvcache \
  uv run --no-project --with pytest==8.4.2 --with pytest-timeout \
  --with-requirements requirements-security.txt \
  python -m pytest -q -p no:cacheprovider --timeout 120 --tb=short

# From repository root
env -u AGENTMESH_NATIVE_KEYCHAIN_TESTS -u AGENTMESH_NATIVE_TASK_FIXTURE_ROOT \
  UV_CACHE_DIR=/private/tmp/claude-501/uvcache \
  uv run --no-project --with pytest==8.4.2 --with pytest-timeout \
  --with-requirements memory-sync/requirements-security.txt \
  python -m pytest -q -p no:cacheprovider --timeout 120 --tb=short memory-sync
```

The three permitted failures are `test_sqlite_consumers.py::ConsumerTests`:

- `test_export_complete_collision_safe_manual_notes_and_source_anchors`
- `test_export_infers_transcript_tasks_even_without_structured_task_ref`
- `test_export_resolution_of_last_active_item_clears_current_memory`

They report `symlink export root refused` with this host's temporary-directory alias. The expected
warning comes from the duplicate-ZIP-member fixture. The first full run, before the final test-hook
corrections, had 1,403 passed / 5 failed / 17 skipped: these three plus the two stale resolution
publication fault injections. The latter two passed their focused regression after correction.

`git diff --check` passed. `git grep -n "RESIGN" -- memory-sync` returned no matches.
The documentation contract checks passed, and no GATES.md exists. All 159 recovery tests passed
in each final full run. No additional failures remain. Tasks 1–5 are complete, with the corrections
and verification limits recorded here.

## Safety scope and limits

New rotation/report fixtures use disposable DBs and identities, generated secrets held in memory,
sealed identities on disk, product private-directory helpers, fake OS backends and bounded test IDs.
The directory-fsync fault injection is explicitly POSIX-only. No new secret is passed through argv,
environment, logs or reports. Error boundaries retain fixed reasons and discard lower exception
contexts. No real identity, keychain or Credential Manager item was used for implementation or tests.

As already recorded by #9, the mandated legacy full suite intentionally writes raw **disposable**
identity fixtures via `signed.init_identity`. Thus the literal no-raw-key-on-disk rule cannot be
claimed for the pre-existing suite as a whole; those fixtures were not broadened or migrated here.
All new recovery scenarios keep identities sealed. Read-only project-memory recall was used;
no private recall records were copied into this repository.

Native Windows execution was not performed locally. No CI, remote operation or frozen binary rebuild
was triggered. Atomic hard-link publication requires filesystem support and fails closed if unavailable;
it was exercised on this macOS host, not on a native Windows filesystem. Existing manifests cannot undo
already committed forgeries, and local locks cannot fence a remote DB clone. No implementation decision
requires user input; unfinished old-format journals require separate operator review as documented.

## Round 2

Implemented the binding security-review amendments (items 1–7) on
`fix/rotation-resolution-packets`, on top of round 1. This section supersedes round 1's
receipt-digest manifest description and its statement that proposal/registry formats remain unchanged.
The full follow-up spec, round-1 report, original recovery spec/ground rules, and signing/revocation
decision were read. Read-only project-memory recall was also performed.

### Amendment commits and test-first evidence

Every commit ends with `Co-Authored-By: GPT (Codex) <noreply@openai.com>`.
Focused green runs below were executed from both `memory-sync/` and the repository root.

| Item | Commit | Evidence |
|---|---|---|
| 1 — H1 | `15b9b64` | Two new tests failed before the fix, then both passed in each directory. A full rotation-file run also passed (161 tests). Manifests and receive authorization bind `signed.envelope_digest`; proposals/registries use v2 and refuse v1. The v1 proposal test includes a valid signature under its old domain. |
| 2 — H2 | `789d719` | The differing-file test failed on the old activity-fence reason. Six focused resume/batch cases then passed in each directory. Resume compares exact stored UTF-8 bytes, accepts identical files, and reports the specified fixed reason for different files. Fresh rotation retains the absent-file requirement. |
| 3 — M1 | `3d00c42` | A test deleting the later of two pending rows during replay first demonstrated an earlier file being written. After the fix, all rows and the exact pending set are validated under `BEGIN IMMEDIATE` against the staged signed manifest before any file write; three focused transaction/resume cases passed in each directory. |
| 4 — M2 | `61ad266` | The new archive/review success test failed before implementation. Seven drop tests passed in each directory: archival, review redaction, reason/UUID/reference refusals, and resume before/after the archive transaction. |
| 5 — M3 | `794d496` | The new quarantine tests failed before the option existed. Approval/cancellation tests then passed as part of 11 drop/resume/quarantine cases in each directory. Both actual envelope digests are shown, and only typed `QUARANTINE` permits the move. |
| 6 — M4 | `72d275d` | The new v3 variant test first failed on missing `variant_superseded`. Fifteen reporting tests then passed in each directory, covering versioned validation, complete-envelope dispositions, manifest membership, v3 forged candidates, redaction and unchanged source bytes. |
| 7 — included improvements | `c6a57e4` | A cached missing entry first remained refused after registry creation; it now refreshes on registry mtime/size changes. The pre-rotation resolution-plan test passes and explicitly proves unchanged DB state plus a changed trust digest. CLI option forwarding and fixed-reason tests are included; 21 focused cases passed in each directory. |

The signature-variant tests use a small **test-only raw Ed25519 implementation** with a chosen
nonce (`r = 42)) and the fixture's old private key held in memory. They construct a different,
mathematically valid signature over identical unsigned content. The pinned cryptography verifier
accepts it before revocation. After rotation authorization, the peer rejects that variant and
subsequently accepts A's original packet. No signature-verification monkeypatch or simulation was
needed. The reporting variant test uses the same real-signature technique for a v3 resolution.

### Resulting recovery behavior

- The manifest covers signature bytes as well as unsigned content. Historical receipt digests remain
  unchanged solely for their existing committed-receipt purpose; `compromise-report.in_manifest`
  uses the complete envelope digest.
- All publication candidates, their journal-pinned stored-text hashes, historical signatures,
  manifest envelope digests and existing files are checked before writing the first file. Replay
  skips already-committed published flags and preserves byte-identical packet text.
- Compromised-key preparation prints each pending UUID, format, v3 kind and table/operation with
  `key_sha256`, without raw keys or row bodies. Repeatable `--drop-pending UUID` is accepted only
  for fresh compromised rotation. References from any other pending packet's covers/evidence
  refuse the drop. The private journal pins dropped packet text hashes; dropped rows remain
  unpublished until finish, then move transactionally to
  `_sync_rotation_dropped(uuid, packet, dropped_at)`. Replay checks archived bytes and preserves
  existing archive timestamps. The report lists `dropped_pending`.
- With drops, the normal pending-backlog guard runs after the finish-time archive transaction,
  because intentionally retained old-key pending rows must remain in the outbox until then.
  Binding and complete publication-set checks still run inside the earlier publish transaction.
  Ordinary signing remains fenced by the journal throughout.
- Repeatable `--resume --quarantine-collision UUID` is restricted to journal-bound packets.
  It shows the exchange and authoritative outbox envelope digests, confirms all requested moves
  before moving any file, and preserves a flushed snapshot of the confirmed bytes using no-clobber publication
  followed by source unlink in `pairing/rotations/quarantine/<uuid>.<digest>.json`. Source bytes are rechecked
  around confirmation and movement. Cancelling leaves DB, trust, outbox and exchange unchanged.
- A disposition matching this file sets `superseded`; a disposition for another envelope of the
  same UUID sets `variant_superseded`. V3 reporting uses `packet_versions.validate_wire` and
  its row validator, and runs the same own-outbox byte comparison as v2.
- KEY-PROTECTION.md and SIGNED-SYNC.md describe v2-only manifests, both CLI options, archive and
  quarantine recovery, and the authorization state `verified` rather than `applied`.

### Final review correction

Commit `0d1c688` contains the final review correction. A new negative test demonstrated that
compromise review could print an unrecognized table label
before the existing aggregate validation rejected it. Review now checks the fixed table allowlist
before emitting metadata. The test failed first and then passed, together with the drop/report
regressions, in both directories. A prior race test's expected error was updated to the exact H2
reason; its initial broader run had 175 passes and that one obsolete expectation failure.
CLI help now describes option scope and repeatability. Archive timestamps record the archive step.
Nine focused review/drop/CLI cases passed in each directory after these final changes.
Commit `8649c2d` adds a further test-first quarantine correction permits a colliding envelope that claims a different
UUID/group from its journal-bound path. The two new approval/cancellation cases failed before the
fix; six quarantine/option-scope cases then passed in each directory. Quarantining hostile bytes
does not grant them trust. This avoids making recovery depend on the attacker's scope claims.

Commit `eddd226` adds a race regression that demonstrated that hard-linking the hostile source itself allowed an in-place
writer to change the quarantined evidence. Quarantine now copies the confirmed bytes into a private,
flushed temporary file, publishes that snapshot without clobbering a destination, rechecks the source
and only then unlinks it. A changed source is retained and refused while the confirmed snapshot stays
intact. The regression failed before this fix; all five quarantine cases then passed in each
directory.

All three new refusal reasons are included in `cli_errors.KEY_REASONS` through its existing
union with `key_rotation.REASONS`; a CLI regression verifies that allowlist and the rendered reasons.

### Full-suite verification

Final full suites ran against committed code at `eddd226`:

| Working directory | Passed | Known failures | Skipped | Warnings | Duration |
|---|---:|---:|---:|---:|---|
| memory-sync | 1,430 | 3 | 17 | 1 | 291.90 s |
| repository root | 1,430 | 3 | 17 | 1 | 292.36 s |

All 183 rotation/recovery tests passed in each full run, including 24 additional cases since round 1.
The only failures remain the same three `test_sqlite_consumers.py::ConsumerTests` cases documented
above: complete collision-safe export, transcript-task inference, and clearing the last active item.
All report `symlink export root refused` on this host's temporary-directory alias. The one warning
is the existing duplicate-ZIP-member fixture. There are no new failures.

`git diff --check` passed. No GATES.md exists. The seven amendments and the test-first final-review
corrections are complete. The final commit only records these results; it does not change tested code.

The full-suite commands are the same as round 1's recorded commands above, with native keychain/task
opt-ins removed and pytest cache writing disabled. The suite covers all `memory-sync/` tests,
invoked from each required working directory. An initial full run before the final label-redaction
test was collected had 1,426 passed, the three known consumer failures, 17 skipped and one warning
in 272.77 seconds. Intermediate complete runs after the label correction had 1,427 passes in
each directory (324.04 seconds from memory-sync and 331.47 seconds from root), again only the
three known failures, 17 skips and one warning. Final verification was repeated after the
quarantine scope and source-mutation corrections.

### Safety and compatibility limits

Only disposable test data, sealed fixture identities and fake OS backends were used for new work.
No real identity, OS key store, deployment, push, merge, rebase, branch switch or remote operation
was performed. No GATES.md or other repository bookkeeping file was created. As documented in
round 1, the pre-existing full suite still includes raw disposable identity fixtures; these were not
expanded by this work.

Native Windows execution was not available. New fixtures use the existing private-directory and
sealed-identity helpers and do not rely on the invocation directory. Hard-link publication and
quarantine require filesystem support and fail closed when unavailable.

V1 proposals and registries are refused, as required. Round-1 journals with v1 proposals or without
the new drop metadata are also refused rather than reinterpreted. Unparseable collision files or unsupported formats
cannot supply a supported envelope digest and require separate inspection. The quarantine option
accepts parsed v2/v3 envelopes even if they claim a different UUID or scope; its authority comes
from the journal-bound path and explicit confirmation, never the hostile contents. Local worker locks still
cannot fence a remote clone. Already-committed forgeries remain a review/recovery concern.
