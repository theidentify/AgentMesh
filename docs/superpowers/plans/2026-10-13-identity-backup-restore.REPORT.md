# Identity backup and restore implementation report

Worktree: `encrypted-key-storage-28470f`; branch: `feat/identity-backup`.
The approved design was read in full before implementation. Tasks were executed in order, with
failing tests before each implementation task and a separate commit for every task. No push,
merge, rebase, PR, branch switch, or work on another worktree was performed.

## Commits and verification

| Task | Commit | Verification at that task |
|---|---|---|
| 1: authenticated format, pure trust validation | `f899759` | 91 passed: 24 backup tests plus signed-packet regression tests |
| 2: locations and no-clobber publication | `fe60aa3` | 36 backup tests passed |
| 3: evidence and read-only status | `bede988` | 38 backup tests passed |
| 4: create and verify | `adf421f` | 48 backup tests passed |
| 5: sealed disposable signed drill | `3bdf05b` | 51 backup tests passed |
| 6: fenced restore and trust reconciliation | `ce3fd8b` | 91 backup tests passed |
| 7: CLI, fixed errors, packaging, smoke coverage | `3c39370` | 109 passed across backup, package and standalone-build tests |
| 8: operator documentation | `9f1e748` | Reviewed against the spec; `git diff --check` passed |
| 9: review fixes, final suite, this report | This report's commit: `test: verify identity backup and record report (#8)` | Final result below |

Every task commit ends with `Co-Authored-By: GPT (Codex) <noreply@openai.com>`.
No bookkeeping file other than this report was created.

Task 9 added regression coverage for unsafe core exception context, concurrent recovery-copy
preservation, permission-denial handling, unsupported permission filesystems, Windows volume
capability calls (mocked), production KDF defaults for create and restore, unreadable exchange
entries, and explicit OS-unlock enable after restore using an in-memory fake. Identity publication
is fsynced on POSIX before recovery artifacts are removed.

The first full run, before the final integration/capability additions, produced **950 passed,
3 failed, 17 skipped**, with one expected duplicate-ZIP-member warning, in 123.18 seconds.

**Final full-suite result: 956 passed, 3 known failures, 17 skipped, 1 warning in 124.36 seconds.**
All 109 tests in `test_identity_backup.py` passed. The warning is the existing intentional
duplicate-ZIP-member test. There are no additional failures. `git diff --check` passed after the
final implementation changes.

Command, from `memory-sync/`:

```sh
env -u AGENTMESH_NATIVE_KEYCHAIN_TESTS -u AGENTMESH_NATIVE_TASK_FIXTURE_ROOT \
  UV_CACHE_DIR=/private/tmp/claude-501/uvcache \
  uv run --no-project --with pytest==8.4.2 --with pytest-timeout \
  --with-requirements requirements-security.txt \
  python -m pytest -q -p no:cacheprovider --timeout 120 --tb=short
```

The known failures are all `ValueError: symlink export root refused` in
`test_sqlite_consumers.py::ConsumerTests`:

- `test_export_complete_collision_safe_manual_notes_and_source_anchors`
- `test_export_infers_transcript_tasks_even_without_structured_task_ref`
- `test_export_resolution_of_last_active_item_clears_current_memory`

They were not modified or suppressed. Native keychain/Credential Manager tests were explicitly
not enabled. Native Windows/frozen smoke execution remains CI work; this session added the frozen
smoke assertions and ran the source packaging/build tests, not a frozen binary.

## Secret-handling audit

`git grep -n "private_key" memory-sync/identity_backup.py` has exactly three hits:

- Line 95 constructs the plaintext payload in memory and immediately encrypts it.
- Line 119 reads the decrypted key in memory for derivation/identity validation.
- Line 128 validates the decrypted payload's exact field set.

None is a plaintext filesystem write. The backup writer receives ciphertext JSON. Drill and restore
write only sealed key-bearing identity documents; trust and evidence contain public data. New test
identities are sealed from creation, and new passphrases are generated in memory. Write guards cover
the restored key and reject raw identity payloads. Failed drill and restore paths verify cleanup.
The fresh-process test passes its randomly generated phrase over an anonymous stdin pipe to an
injected unlock callback, never through a file, argv or environment variable. No secret material is
included in this report.

All new restore tests inject fake OS backends. Create does not consult OS unlock; a regression test
fails if backend selection is attempted. No real identity, keychain or Credential Manager store was
read or modified by the new feature tests or implementation exercises.

**Existing-suite limitation:** the requested unmodified full suite includes legacy tests that call
`signed.init_identity` and write raw disposable identity fixtures (for example,
`test_key_protection.py` and `test_signed_packets.py`). Those tests were run, so the literal rule that
no raw private key ever reaches disk cannot be claimed for the entire regression suite. No real
identity was involved. The new feature and its new fixtures do not write raw identities. This
conflict should be resolved with a dedicated legacy-fixture migration before imposing the same
no-plaintext-at-any-time rule on the whole existing suite.

## Plan corrections and decisions

1. The drill plan suggested `signed.init_identity` for the disposable peer. It writes a raw key,
   conflicting with the spec's sealed-at-all-times drill. Both drill identities are generated in
   memory and written directly in `protection` form instead. Legacy raw export is exercised using
   an in-memory `read_local` substitute, without a raw identity file in the new tests.
2. `signed.typed` is a binary authentication encoding with no decoder. The encrypted payload uses
   `signed.wire`/`signed.parse`, which enforce typed-compatible JSON; associated data uses exactly
   the specified domain plus `signed.typed(manifest)`.
3. `check` reports malformed structures as `invalid backup file`; `open_backup` maps damaged
   authenticated fields to the uniform opening error, including a broken public fingerprint.
   Both paths reject unbounded KDF parameters before deriving anything.
4. Restore defers target creation until after console prompts, validation, fencing and backend
   inspection, preserving an absent target on non-console refusal. An existing private target can
   hold current trust. Recovery preserves exact old trust bytes through a private hard-link snapshot;
   identity adoption is an exclusive hard link from sealed staging. These local security operations
   use the same hard-link filesystem assumption as existing `signed.write_local`.
5. Filesystem permission support is distinguished from permission denial. Windows queries the
   volume's `FILE_PERSISTENT_ACLS` flag; FAT/exFAT can return `unsupported-by-filesystem`, while
   unexpected capability/ACL failures refuse. POSIX unsupported chmod is reported; access denial
   fails and cleans up. Backup publication still has the required exclusive-create fallback.
6. Task 9 review fixes were test-first and kept in Task 9's final verification commit rather than
   rewriting earlier task commits. Core errors now discard lower exception context; cleanup retains
   recovery artifacts owned by another attempt.
7. Native frozen/OS-store execution was not attempted in this worktree. Native opt-ins were removed
   from the test environment explicitly. No keychain or Credential Manager authorization is implied
   by the fake-backend tests.

## Open questions and remaining limits

- No implementation decision is waiting on user input.
- Native Windows ACL/FAT behavior and frozen CLI smoke need their native CI runs; mocked capability
  tests do not substitute for those runs.
- The three pre-existing tmp-symlink failures remain, as allowed by the plan.
- The legacy raw-fixture/full-suite conflict described above remains outside the new feature.
- FENCED remains an operator control, not proof that an offline old device is stopped. Restore sees
  only local revocations, so a live signed peer roundtrip is still required. No worker, exchange,
  database, or real OS unlock configuration was changed by the implementation exercises.
