# Key rotation and recovery implementation report

Worktree: `encrypted-key-storage-28470f`. Branch: `feat/key-recovery`.
The full design, including “Facts in the current code,” was read before implementation.
Tasks were executed in order, with one commit per task. No push, merge, rebase, PR, branch switch,
real-identity access or real OS-store access was performed. This is the only new bookkeeping file.

## Tasks, commits and verification

Green counts below are completed results **from each of memory-sync and repository root**, unless
explicitly qualified. Focused red runs were executed from memory-sync before implementation. Every task commit ends with the requested GPT (Codex) co-author trailer.

| Task | Commit | Evidence |
|---|---|---|
| 1: exact own-packet bytes (R1) | `0aae4a5` | Red: 2 failed / 3 passed. Green: 129 passed, including memory-sync, signed-packet and security regressions. |
| 2: proposal, streaming manifest, registry | `5544210` | Red: 25 failed / 5 passed. Green: 30 passed. |
| 3: revoked-packet manifest verification (R2) | `15d9a1d` | Red: 2 failed / 5 passed. Green: 131 passed including signed-packet/security regressions. |
| 4: preparation and held fences | `b983cc6` | Red: 20 failed. Green: 57 passed. See timing deviation below. |
| 5: sealed journal, re-sign and resume | `9e0faec` | Red: 10 failed. Green: 319 passed including backup/OS-unlock/signed-packet regressions. |
| 6: peer approval | `5788f4f` | Red: 12 failed. Green: 79 passed. |
| 7: read-only compromise report | `8d79ac4` | Red: 6 failed. Green: 85 passed. |
| 8: two-peer convergence and restore interplay | `af5eea0` | Red: 2 leftover-refusal failures; 2 existing behaviors passed. Green: 268 passed including backup/signed-packet regressions. |
| 9: CLI, packaging, smoke and CI | `84bb5b7` | Red: 2 failed. Green: 224 passed including CLI/build/backup tests; source and frozen recovery smoke passed. |
| 10: complete runbook | `a7e8cec` | 91 recovery tests passed; runbook checked against all eight spec sections and actual CLI behavior. |
| 11: final review, full suite and this report | This report's commit | Final results below. |

## Final verification

**Final results after all code and test changes:**

| Working directory | Passed | Known failures | Skipped | Warnings | Duration |
|---|---:|---:|---:|---:|---:|
| memory-sync | 1,079 | 3 | 17 | 1 | 188.22 s |
| repository root | 1,079 | 3 | 17 | 1 | 187.80 s |

All 120 recovery tests passed in both full runs. There are no additional failures.
The final frozen onedir recovery smoke passed after rebuilding the final product code.
`git diff --check` passed. Tasks 1–11 are complete, with the deviations and verification limits below.

The complete suite here means all tests under `memory-sync/`, the suite named by the plan, executed
once with that working directory and once from repository root with the `memory-sync` target.
Native keychain and native scheduled-task opt-ins are explicitly removed. Commands:

```sh
# From memory-sync/
env -u AGENTMESH_NATIVE_KEYCHAIN_TESTS -u AGENTMESH_NATIVE_TASK_FIXTURE_ROOT \
  UV_CACHE_DIR=/private/tmp/claude-501/uvcache \
  uv run --no-project --with pytest==8.4.2 --with pytest-timeout \
  --with-requirements requirements-security.txt \
  python -m pytest -q -p no:cacheprovider --timeout 120

# From repository root
env -u AGENTMESH_NATIVE_KEYCHAIN_TESTS -u AGENTMESH_NATIVE_TASK_FIXTURE_ROOT \
  UV_CACHE_DIR=/private/tmp/claude-501/uvcache \
  uv run --no-project --with pytest==8.4.2 --with pytest-timeout \
  --with-requirements memory-sync/requirements-security.txt \
  python -m pytest -q -p no:cacheprovider --timeout 120 memory-sync
```

The three permitted pre-existing failures are all in `test_sqlite_consumers.py`:

- `ConsumerTests.test_export_complete_collision_safe_manual_notes_and_source_anchors`
- `ConsumerTests.test_export_infers_transcript_tasks_even_without_structured_task_ref`
- `ConsumerTests.test_export_resolution_of_last_active_item_clears_current_memory`

They raise `symlink export root refused` for this host's `/var` temporary-directory alias.
The existing duplicate-ZIP-member test emits the one expected warning.

The first full runs produced 1,077 passed / 3 known failures / 17 skipped from memory-sync, and
1,076 passed / 4 failures / 17 skipped from root. The extra root failure was an existing
`test_key_protection` child process lacking cwd. The smallest test correction supplies explicit cwd
to its subprocesses and sends its disposable passphrase over stdin instead of argv. Its focused
root regression then passed. Both full suites passed with only the three known failures after that
correction, and were rerun again after the two incomplete-sidecar regressions and guard were added.

Final recovery coverage contains **120 tests**. Additional Task 11 checks cover:

- 60,000 manifest entries through peer approval and real receive, beyond typed JSON limits;
- every new-public-field mutation, source-manifest changes during approval, symlink refusal;
- raw legacy identity input simulated entirely in memory, and a completely absent security directory;
- resume's recent-activity, pending-path and lifetime-lock fences;
- each journal-step crash compared against uninterrupted replay from the same sealed checkpoint:
  identity/trust/exchange bytes and SQLite row/packet TEXT values match;
- cleanup interrupted after staging removal, fixed secret-free exception chains, extra-active-key refusal;
- all Python file-stream write payloads plus resulting files/reports checked for old/new raw keys and passphrase;
- production scrypt N selection, manifest directory durability, pre-journal cleanup, and refusal of incomplete WAL/SHM pairs without side effects.

Task 11 found issues with failing tests before fixes: manifests needed a parent-directory fsync,
and an ordinary pre-journal failure retained staging. Publication now fsyncs directories on POSIX;
ordinary pre-journal failure removes only this attempt's successfully created staging and its empty
new directory. After journal publication all recovery material is retained for forward replay. An
abrupt crash before journal publication can still leave staging for inspection, as documented.
Read-only opening also refuses an incomplete WAL/SHM pair: ordinary mode=ro can create the missing
sidecar, while immutable mode would ignore uncheckpointed WAL data. Both intact and cold states use
the specified install_inspect URI choice; partial state is refused with a fixed reason instead.

Frozen verification used pinned PyInstaller 6.16.0 and hooks 2026.8. The normal macOS arm64 one-file
build succeeded, but its loader cannot run in this sandbox (`semctl: Operation not permitted`). A
disposable onedir build ran the complete recovery smoke successfully: three help commands, both
non-console unchanged-state refusals, and an empty read-only report. It was rebuilt and the smoke
passed again after the final product fixes. Native Windows execution is delegated to the updated CI
list; it was not claimed locally.

## Spec-over-plan decisions and necessary corrections

1. **R1:** own-node mismatches are rejected explicitly after envelope/filename validation. Falling
   through, as the plan suggests, would allow whitespace-different JSON through `_seen` equality.
   Matches clear diagnostics without recording a signature verification attempt. R2 stays after
   committed receipts and before validation/application; manifest membership never skips historical
   signature verification. Selection is from a revoked trust entry, never an exception catch.
2. **Journal guard versus replay:** ordinary `Security.check_self` refuses the journal. A private
   internal replay subclass requires the exact sealed journal identity and unchanged live trust
   authorization; there is no public bypass flag. This resolves the spec's simultaneous requirement
   to refuse ordinary signing and to sign/verify during replay.
3. **Worker status:** existing status loads Security and cannot inspect a lost/revoked identity or an
   in-progress journal. Recovery first calls status, then on a ValueError reads the same owned worker
   metadata/liveness directly. Both specified locks are still mandatory. The usual lock infrastructure
   may create lock/control files outside security/DB/exchange snapshots.
4. **Fixed errors:** pending-path errors omit interpolated UUIDs, honoring the strict fixed-reason
   rule. Public operation boundaries discard underlying exception chains and use an explicit upstream
   allowlist. New raw keys/passphrases never appear in reports, error messages or file contents.
5. **OS cleanup:** `identity-os-unlock disable` targets the current installed key. After rotation it
   cannot remove the old item's fingerprint, so a failed deletion prominently requires manual removal
   of that old item, not a misleading instruction to disable the new key.
6. **Reporting:** the additional redacted `uncommitted` list makes the required B-side forgery evidence
   observable. `in_manifest` requires UUID and digest equality; UUID-only membership would misleadingly
   mark altered content as approved. It is null without --security-dir and false when supplied but no
   manifest entry matches. Missing-file counts are independent of the approximate mtime filter.
7. **Proposal/UI:** `competing_proposals` counts other proposal files, and any positive count warns.
   The prompt does not show/ghost the expected full fingerprint. --resume uses the journal reason;
   a fresh rotation still requires a valid reason. Manifests are rechecked against the approved hash
   when copied, including changes made while the operator is at the prompt.
8. **Process:** Task 4 was committed after both test processes started but before their final output
   was collected. Both completed successfully with 57 passed; all later commits waited for results.
   An early shell edit used unavailable `python` and made no edit; the stale report line was corrected
   in Task 2. No extra bookkeeping files were created. Shared-memory recall was omitted because its
   real private DB conflicts with the disposable-only scope.

## Safety scope and limits

No real identity, keychain or Credential Manager item was accessed by these exercises. New recovery
fixtures generate keys in memory, seal them before writing, use product private-directory helpers,
use explicit subprocess cwd and bounded parameter IDs, and inject fake OS backends or explicit None.
The raw legacy rotation case uses an in-memory read substitute, never a new plaintext identity file.

There is a pre-existing conflict between the required full regression suite and a literal “no raw
private key ever reaches disk” rule. Legacy tests intentionally call `signed.init_identity` and write
raw **disposable** identity fixtures to test migration and other old paths. They were run as required;
the rule therefore cannot honestly be claimed for the whole suite. This same limitation was recorded
in the preceding backup/restore REPORT. The touched subprocess regression previously also passed a
disposable test passphrase on argv; this is now corrected to stdin. A broader migration of legacy
fixtures remains separate from this feature. No real secrets were involved.

No implementation decision awaits user input. Native Windows execution and one-file loader execution
outside this sandbox remain verification limits. Approved manifests cannot undo already committed
forgeries; normal edits and investigation remain necessary. A remote DB clone cannot be fenced by
local locks. Python memory wiping and secure erasure are not promised. SQLite data is never rolled
back by key recovery, and the wizard/profile-memory pins are updated manually per the runbook.
