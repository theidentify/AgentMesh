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
