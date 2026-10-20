# mac-relocate implementation report

Date: 2026-10-20

All six implementation tasks are complete. Synthetic evidence covers R-1–R-7
and R-9. R-8 remains owner acceptance after review and merge; no real-host
acceptance is claimed. There is no outstanding blocking question. Both reviewer
clarifications to R-3 are incorporated.

## Delivered behavior

`mac_relocate.py` provides full read-only planning, confirmed relocation and
journalled rollback. `agentmesh.py` exposes `mac-relocate --manifest M
[--dry-run] [--timeout S]` and `mac-relocate-rollback --backup B`. Both packaging
lists include the module. `MAC-RELOCATION.md` contains the manifest template,
release/offline-wheelhouse prerequisites, prompts, recovery and owner checklist.

The transaction journals all 15 prescribed stages in order: preflight, drain,
lock jobs, pause syncthing, copy db, copy state/identity, copy exchange, copy app
files, copy jobs, rebind runtime (adopt), rewrite plists/configs/rules/settings.local,
syncthing path + resume, start, verify, archive. Each records affected operations
or paths. Recovery restores dependencies before restarting the old services.
Explicit post-success rollback uses the archived runtime, retains the newer DB,
reports row changes without content and preserves newer immutable packets.

`mac_replace.py` and `test_mac_replace.py` are unchanged.

## Gate → tests

Test-file abbreviations: base = `test_mac_relocate.py`; life =
`test_mac_relocate_lifecycle.py`; safety = `test_mac_relocate_safety.py`;
cli = `test_mac_relocate_cli.py`.

| Gate | Evidence | Status |
| --- | --- | --- |
| R-1 | base: `test_copy_freeze_blocks_noncooperating_writer_and_preserves_counts`, `test_database_copy_rejects_mutation`, `test_tree_copy_preserves_identity_state_modes_and_empty_directories`, `test_tree_copy_detects_corruption`; safety: `test_wal_copy_includes_committed_uncheckpointed_rows`; life: full move plus five `test_corrupt_each_file_copy_restores_service` cases (state, identity, exchange, release, job) | Synthetic pass |
| R-2 | base: real competing SQLite writer blocked and both job locks acquired; safety: `test_lock_failure_restarts_services_unchanged`; life: drain/copy failure recovery | Synthetic pass |
| R-3 | base: 18 destination damage cases, six manifest/binding cases, disk-space/unreachable refusal and populated-root case; safety: wrong owner, symlink parent, separation, three confirmation races, archive hardlink refusal and rollback symlink refusal before chmod; life: protected sentinel preserved | Synthetic pass |
| R-4 | life: `test_full_move_rewrites_references_archives_and_preserves_identity`, boundary-aware non-cascading rewrite; safety: `test_render_matches_apply_field_for_field` executes the actual apply renderer extracted by AST; cli: full confirmed relocation | Synthetic pass |
| R-5 | life: failures before and after all 15 stages (30 cases), explicit rollback with insert/update reporting and packet preservation, worker and later-summary bootstrap retry; safety: `.new`/replace failure, unhealthy start, lock failure, archive alias refusal, inactive history remaining read-only | Synthetic pass |
| R-6 | life: `test_moved_identity_signs_packets_accepted_by_peer` uses real signing/capture/publish/receive with two synthetic nodes, unchanged key ID and peer application | Synthetic pass |
| R-7 | life: `test_end_to_end_dry_run_decline_and_eof_leave_everything_untouched` checks hashes, mtimes and modes; cli dry run; base populated-root read-only preflight | Synthetic pass |
| R-8 | Owner procedure below; deliberately not executed | Owner pending |
| R-9 | base: exact REST pause/path/resume/read-back/status calls, four non-idle states, unreachable redaction; safety: three forbidden address cases; life: exchange copy, folder-path restoration, secret-free failures and packet preservation | Synthetic pass |

`test_installed_bridge_hook_executes_real_child_code` runs the actual installed
bridge hook in a child of the prescribed interpreter. Orchestration otherwise
uses injected process responses for broker recall/catch-up, job ticks and health.
The venv test checks offline flags, pins and pip check using a fake executor.
These do not claim live launchd/broker/venv installation integration.

`test_mac_relocate_contract.py` confirms normal audit writes using the actual
Audit implementation. Its broader broker recall case is skipped because this
interpreter lacks `pyrage`; it is not counted as successful integration evidence.

## TDD and review

Task 1: 27 preflight cases failed before implementation, then passed. Task 2:
five copy/lock cases failed, then passed. Task 3: five REST/idle cases failed,
then passed. Tasks 4–5: lifecycle cases failed before orchestration, followed by
binding, rewrite, verification, archive, recovery and peer implementation.
Task 6: three CLI/package tests failed before wiring, then passed.

Additional red tests exposed immutable-packet rewriting and interrupted rollback
retry failures. Recovery now rewrites only known live descriptors and journals
restoration hashes before publication; retries accept exact archived argv and
retain the original recovery mode.

Independent read-only review by Jev found archive hardlink and rollback symlink
alias risks. Both regression tests failed before fixes. Relocation now rejects
multiply-linked files and revalidates archive trees and bounded recovery paths
before draining/chmod. Another red test found inactive-history permissions being
restored unnecessarily; explicit rollback now restores permissions only for
archives containing active runtime/service/exchange/config/status paths.
Jev's final review reported no remaining blocking finding in the synthetic scope.
Subsequent reviewer mutation testing found the six coverage gaps recorded below.
R-8 remains owner pending.

### Reviewer mutation follow-up

The reviewer reported six surviving mutants in `mac_relocate.py`. Added six
dedicated tests in `test_mac_relocate_mutations.py` (seven cases because the
fresh-preflight test checks both hashes and scope). No production bug was
exposed and **`mac_relocate.py` was not changed in this follow-up**.

| Removed protection | Dedicated test | Gates | Mutation result |
| --- | --- | --- | --- |
| `table_counts` integrity-check refusal | `test_destination_integrity_refusal_restores_old_install`: injects a bad destination integrity result, requires failure at copy DB and old runtime/jobs/folder restored | R-1, R-5 | Killed |
| `copy_tree` non-empty destination refusal | `test_copy_tree_refuses_nonempty_destination_before_copy`: direct unit call, exact refusal and unchanged hashes/modes/mtimes | R-3 | Killed |
| Final old-app reference scan | `test_final_old_app_scan_refuses_stale_rule_and_restores`: injects an app mapping that leaves old argv in a rule, requires failure at rewrite and restoration | R-4, R-5 | Killed |
| Post-adopt scope/key-ID recheck | `test_post_adopt_key_id_recheck_refuses_changed_scope_and_restores`: injects changed scope only for the newly adopted runtime, requires bind failure and restoration | R-6, R-5 | Killed |
| Post-resume `wait_idle` | `test_exchange_wait_after_resume_failure_restores_old_folder`: records set-path → resume → wait for both new and restored folders; new-folder wait fails | R-9, R-5 | Killed |
| Fresh post-confirmation hashes/scope comparison | `test_fresh_post_confirmation_prepare_mismatch_refuses_before_drain`: changes only the second prepare result, requires “relocation inputs changed,” no drain and no writes | R-3, R-7 | Killed (both cases) |

All seven cases passed against the unmodified implementation. Then a disposable
in-memory AST harness removed exactly one named guard (or the wait call inside
`exchange_start`) in each fresh interpreter and invoked only its dedicated test
via `pytest.main(['-q', '-p', 'no:cacheprovider', test_node])`. Each removal was
counted exactly once. Results: **6/6 mutants killed**, with five individual test
failures and two failures for the hashes/scope variants. No mutated source or
harness file was written. The first harness attempt was corrected after its
own transformation-count assertion caught an unintended match; the final
six-mutant run completed successfully.

The operator guide now requires unloading `org.agentmesh.profile-log.catch-up`
before relocation and regenerating/loading it afterward with
`platform_service.py generate --unit catch-up --database <new db>` and the other
required options. Its `-m agentmesh_log.cli` argv cannot be a `jobs.plists` entry.
The guide also covers reloading the saved plist after original-path recovery
and regenerating it for the archived DB after explicit rollback.

## Deviations and interpretation decisions

- Followed amended R-3: populated roots are permitted; relocation preserves
  existing entries except settings-local. Normal broker audit/presence/index/
  migration writes during verification are allowed and retained on rollback.
- The detailed implementation section's separate rollback command takes
  precedence over the earlier illustrative `--rollback` option. D4's archived
  runtime takes precedence over the earlier original-name description for a
  completed move; automatic failure recovery restores original names.
- Used `format: "agentmesh-relocation-v1"` as the strict discriminator.
- Kept adopt's existing `BIND` confirmation in addition to `RELOCATE`.
- Release source is the operator-selected local release distribution: the
  invoking module tree, or adjacent `memory-sync/` for a frozen executable.
  The manifest has no release-tag field. No tag lookup/download or worker
  upgrade occurs; the existing bound worker is copied by hash.
- Recreated venv uses exact pins, a local `wheels/` sibling, offline wheel-only
  pip with no dependency downloads and `pip check`. Pins include the dependency
  closure. Source/generated symlinks are refused, including interpreter links.
  The real-job-layout amendment below supersedes the source-interpreter rule
  and permits empty requirements for standard-library-only jobs.
- Unambiguous ingest and summary/summarize labels select verification ticks;
  one JSON rule supplies `recall_argv`. Unsupported layouts refuse before drain.
  OTA-owned installations refuse rather than bypassing the existing consumer.
- Scheduler lock precedes bounded-digest lock. A SQLite `BEGIN IMMEDIATE`
  reservation additionally excludes writers ignoring advisory locks; backup
  still reads through a separate `mode=ro` connection.
- A second SQLite backup is the row-report baseline, avoiding database contents
  or unbounded row-hash inventories in the JSON journal. Reports count changed/
  inserted rows and deletions per table; no automatic database restore/merge.
- Recovery unions immutable `changes` and `signed-changes` packets into the old
  exchange. Conflicts refuse recovery. The new DB and destination files remain
  for owner review; rollback does not clear them for a second relocation.
- Hardlink refusal is stricter than the explicit symlink rule and protects
  existing root entries from archive permission changes through aliases.
- No GATES.md, extra skill ledger, approval-cache or other working artifacts
  were created. This REPORT carries the evidence. Shared-memory recall was not
  run because it would access prohibited real installation paths.

## Commands and results

All tests use `PYTHONDONTWRITEBYTECODE=1` and the prescribed interpreter.

Previous implementation/regression command:

```sh
PYTHONDONTWRITEBYTECODE=1 ~/.cache/agentmesh-testvenv/bin/python -m pytest -q -p no:cacheprovider memory-sync/test_mac_relocate.py memory-sync/test_mac_relocate_cli.py memory-sync/test_mac_relocate_contract.py memory-sync/test_mac_relocate_lifecycle.py memory-sync/test_mac_relocate_safety.py memory-sync/test_mac_replace.py
```

Result: **118 passed, 5 skipped in 24.78s** (exit 0). This includes 103 new
passing cases and 15 unchanged mac-replace passes. Skips are four existing
compiled-binary mac-replace cases and the optional pyrage contract case.

Final relocation command after the reviewer follow-up:

```sh
PYTHONDONTWRITEBYTECODE=1 ~/.cache/agentmesh-testvenv/bin/python -m pytest -q -p no:cacheprovider memory-sync/test_mac_relocate*.py
```

Result: **110 passed, 1 skipped in 23.26s** (exit 0). The skip is the optional
`pyrage` contract case. The dedicated new file alone passed **7 cases in 3.22s**.

Full prescribed command:

```sh
PYTHONDONTWRITEBYTECODE=1 ~/.cache/agentmesh-testvenv/bin/python -m pytest -q -p no:cacheprovider memory-sync
```

Final follow-up result: **10 failed, 1673 passed, 21 skipped, 1 warning,
20 subtests passed in 333.17s** (exit 1). The previous implementation run had
1666 passes; all seven added cases pass in the full suite. All ten failures
match the baseline and are in
unchanged `test_digest_api.py`, all loopback socket bind raising
`PermissionError: [Errno 1] Operation not permitted`:

- `test_digest_routes_by_provider_and_reports_routed_model`
- `test_digest_rejects_bad_routes_and_never_echoes_worker_output[nope:x-404]`
- `test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake-400]`
- `test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake:bad name-400]`
- `test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake:boom-502]`
- `test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake:liar-502]`
- `test_token_is_required_when_configured`
- `test_client_worker_emits_scheduler_envelope`
- `test_client_envelope_satisfies_bounded_extract_command`
- `test_bench_reports_validity_stability_and_cost`

The warning is the intentional duplicate `SYNC.md` member in
`test_ota_update.py::test_authenticated_archive_rejects_unallowlisted_or_duplicate_names[SYNC.md]`.
The plan also mentions another failure present on main. No additional failure
appeared in either baseline or final run; its identity was not supplied and is
not guessed. Main was not checked out or tested.

`git diff --check` passed again after the reviewer follow-up. A separate UTF-8/
AST and trailing-whitespace check passed for all seven new Python files and both
documents (including untracked files). The allowed-file audit found exactly 12
changed/new files, all allowed by the plan, and confirmed no diff in mac-replace
implementation or tests. This follow-up changes only the dedicated test file,
the operator guide and this REPORT.

No commit, push, branch creation, network request, real launchctl/Syncthing
operation, keychain access or access to real installation paths was performed.
Synthetic relocation transports use no sockets. The unchanged broader suite
contains socket tests, which retain their sandbox failures.

## Owner steps for R-8 (after review and merge)

1. Complete review/mutation testing, then prepare the private manifest, selected
   release source and offline pinned wheelhouse. Inventory all actual jobs,
   all three rules and all history under the old memory root.
2. On the real host, run `agentmesh mac-relocate --manifest M --dry-run` and
   inspect every proposed file/reference change and new destination.
   Before the confirmed move, unload `org.agentmesh.profile-log.catch-up`,
   wait for its current tick to stop, and retain its old plist. It cannot be
   listed in `jobs.plists` because it uses `-m agentmesh_log.cli`.
3. Run `agentmesh mac-relocate --manifest M` at the owner's terminal; type
   `RELOCATE`, then `BIND`. Keep the returned private backup and journal.
4. Check completed-cycle worker health, successful ingest/summary ticks, rule
   recall with `backend: "broker"`, catch-up only `unchanged`, and bridge hook.
5. Run the usual `agentmesh-log apply` with presence if requested. Confirm no
   unrelated changes beyond the intended database-path reconciliation.
   Regenerate the unloaded catch-up service with `platform_service.py generate
   --unit catch-up --database <new db>` plus `--platform macos`, the actual
   `--home`, and a new `--output` directory. Install/load the generated plist and
   confirm its next tick succeeds. Preserve a customized interval if applicable.
6. Check the same Syncthing folder ID at `~/.agentmesh/exchange`, idle with zero
   needed files and no errors. Windows delivery now uses
   `~/.agentmesh/exchange/installers/`; device/folder IDs remain unchanged.
7. Check rewritten plists/configs/rules, old script notices, unchanged webhook
   location and read-only archives. Retain at least 14 days and until a separate
   explicit deletion request.
8. If needed, run `mac-relocate-rollback --backup B`, type `ROLLBACK`, and review
   row-change counts. Keep newer data without merging/deleting it automatically.
   The old service uses the archived runtime.

## Amendment: real job layout

Implemented items 1–4 of the 2026-10-20 amendment on
`claude/relocate-real-jobs`, following the owner's report that the first real
read-only dry run refused the live job layout. No real installation was read,
changed or exercised during this work.

### Changes and evidence

- Added script-command parsing for optional `-u`, `-I`, `-B` flags. The exact
  `<app_root>/job_notify.py` wrapper validates its option pairs, required name
  and matching status directory, private webhook path when supplied, optional
  positive integer timeout, required `--` separator, and inner interpreter/
  script. The inner script must be in `jobs.files`. Other commands accept a
  script in `jobs.files` or inside `jobs.status_dir`. Unknown interpreter flags
  and module commands remain refused.
- Interpreter resolution is a narrow exception to path symlink refusal.
  `realpath` resolves chains; the final target must be regular, executable,
  owned by the user or root, and not group/world-writable. Resolved interpreter
  targets enter the confirmation hash inventory. Old venv argv paths map to
  `jobs/.venv`; external interpreter argv paths stay unchanged. Venv creation
  uses the resolved **inner** ingest interpreter with `--copies`.
- Empty/comment-only requirements now support standard-library-only jobs:
  create the venv, skip pip install and the wheelhouse requirement, then run
  pip check. Nonempty requirements retain exact pins and offline wheel-only
  installation. The existing offline-command test now creates its synthetic
  pinned requirements file explicitly.
- Archive-only tree traversal never follows directory or file symlinks.
  Inventories and the recovery journal store `link -> target`; links are
  excluded from target hashing and permission changes. Archive copies use
  `os.symlink` with unchanged target text. Before archival chmod or rollback,
  the recorded link inventory is checked. Live app/data/state/identity/exchange/
  status/job-file trees retain symlink refusal and hardlink protections.
- Updated `MAC-RELOCATION.md` with the real wrapper and Insight layouts,
  interpreter exception, empty requirements and archive-link behavior.

The new `test_mac_relocate_real_jobs.py` fixture reproduces the specified
ingest/summary argv, including both interpreter positions, `--name`,
`--state-dir`, unchanged `--webhook-file`, separator and inner script. Its
Insight argv has `-u`, `production-status/insight-release-a1b2c3/server.py`,
`--sqlite`, and port `51877`. It creates both interpreter symlink chains and an
explicitly archived old `.venv`, with an empty requirements file and no
wheelhouse. All executable files are harmless synthetic stubs.

| Requirement / gates | Tests |
| --- | --- |
| Exact real-layout read-only planning; R-3, R-7 | `test_exact_real_layout_dry_run_is_readonly` compares bytes, link text, modes and mtimes, and requires no drain |
| Wrapper detection and full argv mapping; R-4 | `test_wrapper_flags_status_and_both_interpreters_rewritten` requires successful relocation, exact rewritten ingest/summary/Insight argv, executed verification ticks, and unchanged webhook |
| Flags before script; R-4 | `test_interpreter_flags_before_script` covers `-u` and `-I -B` |
| Root-owned real target and inner-interpreter selection | `test_resolved_root_interpreter_used_for_empty_venv` deliberately gives the wrapper a different outer interpreter, requires the root-owned inner target for `venv --copies`, and requires no pip install |
| Unsafe executable refusal; R-3 | `test_group_writable_resolved_interpreter_refused` requires refusal before drain |
| Wrapper validation; R-3 | Five cases of `test_wrapper_options_and_inner_command_validated`: missing separator, unknown option, wrong status directory, unlisted inner script, module flag |
| Archive links; R-1, R-3, R-5 | `test_archive_links_preserved_without_hashing_or_chmod_target` exercises relocation and explicit rollback, directory/dangling links, journal inventory and untouched external targets; `test_archive_copy_inventory_preserves_links_without_dereferencing` directly checks file/directory/dangling copies and forbids target hashing |
| Exception confinement; R-3 | Five `test_live_trees_still_refuse_symlinks` cases cover data, identity, exchange, status and job files |

TDD: the initial 17-case fixture run produced **7 failures / 10 passes** before
implementation. The seven positive/targeted cases exposed the old unconditional
archive symlink refusal; negative cases already refused. After implementation,
all 17 passed. An additional direct archive-copy case brings the amendment to
18 cases. The strengthened inner-interpreter test distinguishes the wrapper's
outer interpreter from the actual ingest interpreter.

Mutation verification used fresh interpreter processes and in-memory source
replacements only, asserting exactly one replacement each. **6/6 mutants were
killed**: disabled wrapper detection, disabled flag skipping, removed root-owner
acceptance, removed interpreter write-mode refusal, removed archive-link
acceptance, and replaced link creation with file copying. The flag mutant
failed both flag variants. No mutant or harness file was written. Jev's
independent read-only review found no blocking finding in the amendment diff.

### Deviations / interpretation

- Literal `/opt/homebrew`, `/usr/local` and `/Library` paths are rooted under a
  temporary synthetic `system/` directory. `H` is a temporary home; root
  ownership is simulated only for the final framework interpreter's stat
  result. This preserves the exact argv structure without reading real
  interpreters, changing system files or requiring chown.
- Existing same-filesystem archival still renames whole trees intact, which
  preserves the original symlink inode and target text without following it.
  The archive-only copy helper uses the amendment's explicit `os.symlink`
  mechanism. No unnecessary copy/delete cycle replaces the established rename
  and rollback protocol.
- “Archive-only” excludes any tree overlapping live app, exchange, status,
  production config, job files, rules or plists. The old `.venv` qualifies
  because it is archived rather than copied into the new live venv. Its links
  are never chmodded; making an archive read-only does not alter link targets.
- The wrapper supports its existing optional `--webhook-file` and `--timeout`
  options, rejects duplicate/unknown options, and requires the `--state-dir`
  value to equal the declared status directory. The selected release must
  include `job_notify.py` when a wrapper job uses it.
- Empty and comment-only requirements both mean no dependencies. Generated
  live venv trees keep strict no-symlink validation. Catch-up service handling
  remains the previously documented owner unload/regenerate/load step.

### Amendment commands and results

```sh
PYTHONDONTWRITEBYTECODE=1 ~/.cache/agentmesh-testvenv/bin/python -m pytest -q -p no:cacheprovider memory-sync/test_mac_relocate*.py
```

Result: **128 passed, 1 skipped in 24.69s** (exit 0). The skip remains the
optional `pyrage` broker contract case.

```sh
PYTHONDONTWRITEBYTECODE=1 ~/.cache/agentmesh-testvenv/bin/python -m pytest -q -p no:cacheprovider memory-sync
```

Full-suite result: **10 failed, 1691 passed, 21 skipped, 1 warning, 20 subtests
passed in 307.81s** (exit 1). The 18 amendment cases all pass. The failures are
exactly the same ten `test_digest_api.py` loopback socket-bind refusals listed
above (`PermissionError: [Errno 1] Operation not permitted`); no new failure
appeared. The warning remains the intentional duplicate `SYNC.md` ZIP member.

`git diff --check` and UTF-8/AST/trailing-whitespace checks passed. The amendment
scope audit found exactly five allowed changed/new files: `mac_relocate.py`,
`test_mac_relocate_real_jobs.py`, the one-line pinned-fixture correction in
`test_mac_relocate_safety.py`, `MAC-RELOCATION.md`, and this REPORT.
`mac_replace.py` and its tests are unchanged. No commit, branch creation,
network access, real service operation or real home-directory access occurred.

R-8 remains owner-only. After review/merge, retry the real read-only dry run
with the actual wrapper/Insight plists, inner scripts in `jobs.files`, old venv
in the archive list, selected release containing `job_notify.py`, and an empty
requirements file if all jobs use only the standard library. Then follow the
existing confirmed-move, catch-up service and host verification steps.

## Reviewer follow-up: real-host dry run (Claude, 2026-10-20)

The owner-approved read-only dry run against the real Mac still refused after the job-layout amendment. The
ingest interpreter's real target, `/Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10`, is
root-owned and admin-group writable (`rwxrwxr-x`, gid 80), which is the standard python.org installer layout.
`safe_interpreter()` now accepts a group-writable interpreter only when all of these hold: the platform is
macOS, the owner is root, the group is gid 80, and the path is under `/Library/Frameworks/Python.framework`.
World-writable interpreters, other groups, other paths and other platforms are still refused. Test:
`test_safe_interpreter_python_org_only` (7 cases). The new jobs venv is created with `--copies`, so its
interpreter is user-owned.

With that change, the real dry run plans successfully:

- 6 new destinations;
- 1.19 GB required, 10.6 GB free;
- 12,990 files: the old venv (11,075, archived with its symlinks preserved), the exchange (1,454), and the
  runtime, history and jobs;
- 22 path references rewritten.

Reviewer mutation tests killed all 5 mutants:

- python.org path restriction;
- group-writable acceptance;
- world-writable refusal;
- wrapper detection;
- archive symlinks copied as links, not followed.
