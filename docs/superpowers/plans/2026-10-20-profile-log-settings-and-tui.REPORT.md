# Profile-log settings and terminal UI REPORT

Date: 2026-10-20. Implements all six tasks and the reviewer-approved audit amendment.
S-1 through S-14 have passing dedicated local coverage. Whole-change acceptance
still requires the reviewer to rerun sandbox-denied socket tests and execute the
three-OS checks. No commit, push or branch was created.

## Work completed

| Task | Implementation |
| --- | --- |
| 1. Settings | AGENTMESH_HOME paths, owner/mode/type/symlink checks, strict schemas and duplicate rejection, key-material guard, per-key merging and settings show/validate. |
| 2. Shared administration | admin.py contains scope creation/keyring/history publication and agent grant/file creation used by both the existing CLI and apply. Existing CLI behavior tests pass. |
| 3. Apply | Complete named diffs, fresh presence for widening, prompt-free narrowing, unsupported reporting, named exclusive agent files, revocation/history preservation, bridge/mapping rendering, rollback on ordinary write failures and outcome auditing. |
| 4. Projects/migration | One-shot trust pins both files by canonical path and hash; permitted existing-scope overlays; changed/untrusted/invalid routes warn and are ignored; all five migration commands accept settings or mapping. |
| 5. Terminal UI | Pure screen model, numbered menu, guarded curses with fake-screen tests, grid/scope/agent edits, ledger-based mapping preview, existing pairing flow with exact selected scopes, and settings-save followed by apply. Windows-only curses dependency pinned. |
| 6. Documentation | README Settings and admin UI describes schema, commands, permissions, trust, defaults, limitations and terminal controls. This REPORT replaces the earlier blocker report. |

Only the allowed profile-log tree, this REPORT and the one allowed memory-sync test
are changed. No real AgentMesh/Hermes directories, real databases, keychains,
identities, network services or cloud storage were accessed. Tests use synthetic
SQLite data and temporary homes. Most fixtures use FileKeyStore; the sealed-byte
regression uses an injected fake backend, never a native credential store. All
profile-log tests now receive a temporary AGENTMESH_HOME through an autouse fixture.
No dependency was installed. No socket failure was skipped or xfailed.

## Reviewer amendment

The prior audit question is resolved. `--dry-run` performs zero writes, including
no audit, presence log or lock-file writes. Every non-dry-run apply appends one
apply audit record when the root/log destination is safe and writable, including
approved, declined and no-op outcomes. A declined presence check also writes the
existing presence audit, but leaves protected state and agent keys unchanged.
An unsafe root or audit destination is refused rather than followed or repaired;
no implementation can append safely to a refused destination.

## Gate-to-test mapping

`T` means `profile-log/tests/settings/`; `M` means `profile-log/tests/migrate/`.
All test identifiers below refer to the final files.

| Gate | Dedicated tests | Evidence |
| --- | --- | --- |
| S-1 | T/test_settings.py::test_s1_refusal, test_file_owner_and_type, test_key_material_names, test_parent_symlink; T/test_apply.py::test_s1_no_mutation | Separate symlink, mode, root, unknown, duplicate, local-only and key-marker cases. Fixed invalid_settings errors; state unchanged and no named key directory created. Parent symlinks and key markers in names are also refused. |
| S-2 | T/test_apply.py::test_s2_widening, test_s2_declined, test_s2_narrow_mixed_rename, test_s2_sealed_bytes | Eleven widening cases include scopes, agents, read/write, all grant flags, bridge enable and all three bridge widening flags. An open agent window cannot authorize; no-TTY/decline preserves state; narrowing prompts zero times and mixed changes once. Sealed ciphertext remains byte-identical on decline. |
| S-3 | T/test_apply.py::test_s3_s5_s14_lifecycle, test_s2_sealed_bytes | Second apply prints exactly no changes, has an empty diff and no additional prompt, and preserves both file-store and sealed-store bytes. |
| S-4 | T/test_apply.py::test_s4_broker_authority_and_s5_revoke | A running broker denies an added recall scope after only editing settings; the same request succeeds only after approved apply. |
| S-5 | T/test_apply.py::test_s3_s5_s14_lifecycle, test_s2_narrow_mixed_rename, test_s5_exclusive_file, test_s4_broker_authority_and_s5_revoke; M/test_peer_authority.py::test_local_revoked | Named key mode/path, exact sets/flag, protected name map, remove/create rename diff, exclusive-file refusal, next-request revocation and persistent import-agent history. Existing historical-import restart regression passes. |
| S-6 | T/test_apply.py::test_s6_scope, test_s6_phase4_scope, test_s6_unsupported, test_s6_allow_unsupported; T/test_admin.py::test_shared_admin | New scope and label, Phase 4 keyring objects and recovery history; scope/profile deletion, label change and unknown profile refuse without state changes. Explicit allow-unsupported applies only the supported remainder. |
| S-7 | T/test_projects.py::test_s7_cli_parity, test_migrate_parser, test_root_mapping | Identical plan output and initial-run/no-op summaries through settings and equivalent mapping, using copied disposable broker state and real in-process signing/import. All five parsers accept settings and reject both switches. Rendered root-scope targets reload successfully. |
| S-8 | T/test_apply.py::test_s8_bridge; memory-sync/test_profile_log_settings_bridge.py::test_s8_settings_bridge | Exact bridge field set, named key/mapping paths, POSIX 0600, local authority/database/argv, and enabled:false rendering. The unchanged memory-sync load_config accepts the generated file. |
| S-9 | T/test_projects.py::test_s9_trust_changes, test_s9_project_grants_invalid, test_s9_layer_pairs, test_project_invalid_layer, test_empty_project_layer, test_trust_race | Untrusted/changed/disallowed routes ignored, TTY trust required, invalid grant-bearing files ignored, all six precedence pairs tested. Invalid local files do not suppress valid shared routing. A file changed during confirmation is not trusted. |
| S-10 | T/test_tui.py::test_s10_save_only_via_apply, test_s10_exact_apply_diff, test_fake_curses_grid_save | Key-store save spy forbids direct UI persistence; edits leave state bytes unchanged; Save writes the user layer and delegates. One grid toggle yields exactly one expected widening diff. |
| S-11 | T/test_tui.py::test_s11_db_markers, test_s11_fake_curses_content_free | A synthetic SQLite content marker is absent from menu/fake-curses screens and every model attribute; project names/counts remain visible. |
| S-12 | T/test_tui.py::test_model_grid_mapping_pair, test_s12_pair_delegation, test_s12_real_pair | Empty selection rejected. Real commit/reveal pairing reaches Approver.grant with exactly the ticked opaque scope IDs and prompts for a six-digit code comparison. |
| S-13 | T/test_tui.py::test_s13_fallback, test_curses_import_failure, test_menu_scripted_save, test_admin_cli | Actual injected curses import failure, non-TTY and forced menu paths. Scripted menu creates a scope/agent, toggles read/write, saves and reaches the apply presence prompt. |
| S-14 | T/test_apply.py::test_s14_decline_noop, test_s14_dry_run_no_writes, test_dry_run_no_write_calls, test_s14_unsafe_audit | Exactly three records for decline/approval/no-op; no key path, private-key material or content fields. POSIX mode checked. Dry-run preserves the complete file snapshot and rejects any write-capable os.open call. Symlink audit target remains untouched. |

Additional tests cover list replacement/local merging, prompt-free initial bridge
disable, transaction rollback on injected store failure, global-route preview
semantics, retired-row previews and older unmanaged agents in the overview.
There are 87 new profile-log tests and one new memory-sync test. All 14 feature
gates have passing local evidence; no feature gate is abandoned. Whole-change
socket and cross-OS verification remains outstanding as specified by the plan.

## Final verification

Every command used `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider` with
`~/.cache/agentmesh-logvenv/bin/python` (LOGPY below). Full-suite runs disabled
live R2 and native credential opt-ins with `AGENTMESH_R2_LIVE=` and
`AGENTMESH_NATIVE_KEYCHAIN_TESTS=`. The memory suite ran in a Python
TemporaryDirectory context with AGENTMESH_HOME set to that directory; it was
removed when the subprocess exited. The new bridge test sets its own temporary
home. No test reached a real default home.

| Directory | Final command arguments after LOGPY | Result |
| --- | --- | --- |
| Root | `-m pytest -q -p no:cacheprovider --tb=short profile-log/tests/settings memory-sync/test_profile_log_settings_bridge.py` | **88 passed**, 2.55 s |
| Root | `-m pytest -q -p no:cacheprovider --tb=short profile-log/tests` | **453 passed, 5 skipped, 4 failed**, 38.46 s |
| profile-log/ | `-m pytest -q -p no:cacheprovider --tb=short tests` | **453 passed, 5 skipped, 4 failed**, 38.46 s |
| Root | `-m pytest -q -p no:cacheprovider --tb=short memory-sync` | **1575 passed, 17 skipped, 10 failed, 1 warning**, 328.22 s |

The full memory-sync run preceded the final settings/TUI edge-case refinements;
the final focused run above reverified its new bridge integration. No memory-sync
implementation or pre-existing memory-sync test changed.

The four profile-log failures, in both invocation directories, are explicit
sandbox bind denials:

- tests/broker/test_isolation.py::test_p3_1_processes
- tests/broker/test_transport.py::test_real_transport
- tests/migrate/test_processes.py::test_p5_4_concurrent_processes
- tests/test_storage.py::test_s3_custom_http_endpoint

The ten memory-sync failures are unchanged test_digest_api.py cases, all failing
at ThreadingHTTPServer binding to 127.0.0.1:

- test_digest_routes_by_provider_and_reports_routed_model
- test_digest_rejects_bad_routes_and_never_echoes_worker_output: nope:x-404,
  fake-400, fake:bad name-400, fake:boom-502, fake:liar-502
- test_token_is_required_when_configured
- test_client_worker_emits_scheduler_envelope
- test_client_envelope_satisfies_bounded_extract_command
- test_bench_reports_validity_stability_and_cost

The pre-existing OTA warning is duplicate ZIP entry SYNC.md. Profile-log's five
skips are the existing opt-in live R2 and platform-only cases. Socket failures
remain failures. The initial full run also caught the repository's 80-character
test-node-ID limit; new test names were shortened, and that check now passes.
No acceptance test or socket test was weakened.

Final git diff --check and explicit UTF-8/trailing-whitespace checks pass.
The final inventory contains 21 changed/new files, all in the allowlist. No
GATES.md, cache file or other working artifact was added.

## TDD and implementation choices

Tests were written before each implementation task: missing settings/admin/apply
modules failed collection; project trust/precedence/parser behavior failed before
implementation; the screen model and frontend tests preceded their modules, and
the admin CLI test failed before command wiring. Further failing regressions
caught root-scope mapping reload, key markers in names, global mapping previews,
parent symlinks, unnecessary initial-disable presence, unmanaged-agent overview
omission and missing empty-project-file warnings. Each was corrected and rerun.

- The audit ambiguity is resolved exactly as the reviewer amended the plan.
- Relative and fully qualified scope paths are accepted inside a profile's scopes
  map. Existing topology is seeded by the new-scope UI so Save does not propose
  accidental deletions.
- Mapping accepts profile-root targets as required by the settings target grammar;
  equivalent legacy --mapping inputs also accept them to preserve parity and
  rendered-bridge compatibility. Existing valid mapping behavior is unchanged.
- Missing grant lists/flags default to empty/false; default_write is null. Default
  recall-profile selection for such grants is deterministic and documented.
  Legacy broker grant retains its existing default-write selection rules.
- Trust hashes length-frame both byte strings to disambiguate file boundaries.
  Invalid project layers are warned about and ignored individually. Remembered
  trusted folders are applied in canonical-path order; duplicate project names
  therefore have a deterministic final route.
- Apply persists the last applied bridge/mapping alongside managed names in sealed
  control state. Ordinary write failures restore files and local keyring objects;
  cross-file power-loss atomicity is not claimed. A process crash can require
  inspecting orphaned files before retrying exclusive agent creation.
- Followed this plan's explicit apply operations: mapping edits render routing;
  actual row movement remains an explicit migrate run/catch-up operation. No
  automatic migration, service installation or remote sync was introduced.
- The TUI delegates pairing to the existing CLI flow. It uses the local replica
  unless --filesystem supplies shared filesystem storage. No second pairing
  implementation or direct edit-screen state writer was added.
- Safe-file checks deliberately fail closed when an audit destination is unsafe.
  Windows owner-only ACL inspection remains out of scope as the plan specifies.
- This REPORT is the completion record; no GATES.md or other working files were
  created. Shared-memory recall was not invoked because real database access is
  prohibited. No sub-agents were used.

## Reviewer verification (Claude, 2026-10-20)

**Fixes made in review.**

- **Mapping changes are widening.** Changing the migration mapping, including through
  a trusted project route, can move rows from `personal` into a `work` scope that
  other agents read. It now needs one-shot presence. Tests:
  `test_s2_widening[mapping]`, plus the updated `test_root_mapping`.
- **Bridge changes are widening, except switching flags off.** The old rule only
  looked at `recall`, `dual_write` and `authority` turning on, so a new `cli_argv`,
  database, profile, recall/import agent or timeout applied silently. A new
  `cli_argv` redirects which program memory-sync runs with the agent key path.
  Now only switching `enabled`, `recall`, `dual_write` or `authority` off narrows.
  Tests: `test_s2_widening[cli_argv]`, `test_s2_widening[timeout]`,
  `test_s2_bridge_flags_off_narrow`. The implicit "never applied" bridge baseline
  now includes the default timeout, so a first disabled bridge stays a no-prompt
  no-op.
- **Project files accept git checkout modes.** The 0700/0600 rule refused every
  repository file, because git writes 0755/0644. Project files must now be regular,
  non-symlink, owned by the user and not group- or world-writable. Trust still pins
  their exact bytes. Test: `test_s9_git_checkout_modes` (0644 accepted after trust,
  0666 ignored).
- README "Settings and admin UI" updated to match.

**Mutation testing.** 20 mutants on the settings, apply, project and TUI security
properties; 19 killed in the first pass. U15, which disables the checkout-mode check
in `safe()`, survives because `read_raw` repeats the same check on the open file.
With both checks removed, `test_s9_git_checkout_modes` fails, so the property is
covered. Killed mutants:

- an open presence window auto-approving an apply;
- `agent_widen` not counted as widening;
- `mapping_change` not counted as widening;
- the old bridge rule;
- no mode check on settings files;
- no key-material guard;
- local-only keys accepted in `settings.json`;
- no content-hash pin for trusted projects;
- no `allowed_scopes` check;
- project files accepting grant/bridge keys;
- `--dry-run` writing state;
- the TUI saving without `apply`;
- no audit line;
- no presence call at all;
- overwriting an existing agent key file;
- no trust-race recheck;
- pairing with no scopes ticked;
- applying unsupported changes;
- skipping widening classification.

**Reviewer test runs (macOS, outside the sandbox).**

| Run | Result |
| --- | --- |
| profile-log, from the repository root | 462 passed, 5 skipped, socket tests included |
| profile-log, from `profile-log/` | 462 passed, 5 skipped |
| `memory-sync/test_profile_log_settings_bridge.py` + `test_profile_log_bridge.py` | 30 passed |
| `git diff --check` | clean |

## Owner steps

1. Review the diff, then rerun the four profile-log socket tests and the ten
   memory-sync HTTP cases outside the sandbox. Run the full suites on macOS,
   Linux and Windows, including fake-curses/menu tests and Windows dependency
   installation. Native key-store smoke is a separate owner/reviewer action.
2. Create/protect ~/.agentmesh as POSIX 0700 (owner-only ACL on Windows). Write
   the first settings.json from the already drafted
   ~/.agentmesh/migration-mapping.json: move global, null_project, projects and
   prefixes under migration, omit the mapping format field, and add the settings
   $schema. Declare profiles/scopes, named recall/importer grants and allowed
   project routes. Keep database, authority and cli_argv only in
   settings.local.json. Both files are 0600 and per-device.
3. Follow the Phase 5 Mac rollout with **~/.agentmesh/profile-log** as the broker
   home. After merge, initialize it with the OS key store and intended profiles,
   preserve the recovery kit, then run settings validate/show and apply --dry-run.
   Review the entire diff and approve apply in the terminal. Keep bridge disabled
   during preparation. Do not move SQLite, identities or ~/.hermes in this phase.
4. Run migrate plan --settings against the owner-selected source and review all
   project destinations. Prefer common plus project filters unless access differs.
   Trust project folders explicitly and keep allowed_scopes narrow. Rerun the
   owner-approved parity workflow after selecting the real mapping; this agent
   performed only synthetic parity and never opened the real mapping or DB.
5. Open first_access presence for initial agent requests. Start the broker with
   the new home and without automatic remote synchronization; run migration and
   inspect its content-free report. Enable the Mac bridge with authority=true
   through reviewed settings/apply only after those checks. Keep exactly one
   migration authority.
6. Generate, inspect and manually load the existing broker/catch-up units using
   the new home and named key paths. Catch-up repairs missed hooks and incorporates
   rows arriving through the unchanged SQLite packet-sync path.
7. After Windows native key-store smoke and pairing, configure Windows recall
   with authority=false and dual_write=false. Review/register only desired units.
   The Phase 5 paired-device provenance trust limitation still applies.
8. Roll back by disabling the bridge through settings/apply and disabling the
   generated units. Before any broker sync, discard the dedicated home only when
   intentionally removing migrated data. After sync, follow profile deletion,
   recovery and old-share/offline-backup procedures. Bridge disable alone does not
   retract ciphertext or distributed keys.

Reviewer commits. No rollout, deployment or real-data verification was performed.
