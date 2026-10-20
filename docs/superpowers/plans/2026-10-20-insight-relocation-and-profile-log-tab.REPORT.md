# Insight relocation and Profile log tab — implementation report

Date: 2026-10-20. Implementation complete in this worktree; **no commit**.
The required regression suites and new gate tests pass. Browser-based visual QA
was not runnable locally; the executable rendering fixture passes (details below).

## Implemented

- One per-item layout resolver selects new, legacy or missing locations. Peer
  ACKs, coordinator state, metrics and Syncthing exchange matching use it.
  Hermes cron/audit/session sources remain under `--home`.
- Summary and smoke metrics merge sorted archive history, legacy live and new
  live files, with later records winning by `run_id`. Repeatable metrics-log
  overrides and the standalone metrics CLI work. Archive permissions and mtime
  remain unchanged. The metrics report closes its read-only SQLite connection.
- A 0600 atomic status snapshot carries only the specified public metadata.
  Apply writes its outcome and post-apply overview; catch-up writes its outcome
  on normal, unmapped, disabled and error paths. Writers preserve the other's
  sections and suppress status I/O failures. TUI and snapshot migration targets
  share `project_targets`.
- Insight reads only the bounded status snapshot, projects allowed fields,
  validates strings/counts, caps lists, shortens device IDs, adds ages and
  redacts. The guarded, cached GET endpoint and read-only Profile log tab are
  wired into normal refresh. Both READMEs document the behavior.

## Regression diagnosis and correction

The initial retry after localhost binding was enabled reproduced the reviewer’s
finding: Insight ran 37 tests with **6 failures and 5 errors**, all in transport;
profile-log passed **132 tests in 7.93s**. The transport fixture put the legacy
exchange path in config XML without creating that directory. Under the explicit
D1 rule, both locations were missing, so the resolver selected the intended new
path. The fixture now creates its temporary legacy exchange directory. No
transport assertions were removed, weakened or skipped. A new test also proves
that an existing new exchange rejects a configuration still pointing at legacy.

Existing Insight test modules set disposable defaults for AgentMesh home and
Syncthing config. Their other test logic remains unchanged. The earlier sandbox
blocker and incomplete report are superseded by this report.

## Final command results

| Command | Actual result |
| --- | --- |
| `cd insight && python3 -m unittest` | Exit 0; **50 tests**, **16.017s**, `OK` |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=profile-log ~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests/settings profile-log/tests/migrate` | Exit 0; **139 passed in 8.97s** |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=profile-log ~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests/settings/test_status.py` | Exit 0; **7 passed in 0.63s** after strengthening exact return-value comparisons against disabled status writes |
| `node insight/profile_log_qa.mjs` | Exit 0; `PASS: Profile log navigation, fixture rendering, escaping, truncation and missing snapshot` |
| `node --check insight/qa.mjs` | Exit 0 |
| `node --check insight/security_qa.mjs` | Exit 0 |
| `git diff --check` | Exit 0; no whitespace errors |

Python 3.14 emitted resource warnings from pre-existing Insight HTTP/SQLite
fixtures; the suite completed successfully. No tests were skipped to obtain the
passing regression results.

## Gate table

All **14 gates** have passing automated evidence. G13's optional real-browser
checks remain a verification limitation, not a claimed browser pass.

| Gate | Passing test names | Result |
| --- | --- | --- |
| G1 New layout | `RelocationTests.test_g1_new_layout`; `ProfileLogTests.test_g1_g5_server_cli_layout_and_repeatable_override` | PASS: new ACK/coordinator/metrics sources and CLI wiring |
| G2 Legacy fallback | `RelocationTests.test_g2_legacy_layout`; existing peer/metrics/transport tests | PASS: all three projections report legacy |
| G3 Mixed/missing | `RelocationTests.test_g3_mixed_missing` | PASS: independent choices and unknown/unavailable missing coordinator |
| G4 Cron stays under home | `RelocationTests.test_g4_cron_home` | PASS: conflicting AgentMesh files ignored for jobs, audit and sessions |
| G5 Metrics merge | `RelocationTests.test_g5_merge_override_corruption_readonly`; `ProfileLogTests.test_g1_g5_server_cli_layout_and_repeatable_override`; `ProfileLogTests.test_g11_endpoint_cache_guards_and_metrics_override` | PASS: both metric families, archive ordering/dedup, overrides, corruption, mode/mtime preservation and CLI/server propagation |
| G6 Containment | `RelocationTests.test_g6_both_layout_containment`; existing `PeerStatusTests.test_malformed_stale_future_and_bounded_inputs` | PASS: file links and escaped status roots refused in both layouts |
| G7 Transport | `TransportTests.test_g7_resolved_exchange_and_legacy_mismatch`; all existing transport tests | PASS: matching new exchange works; legacy configuration rejected when new exists |
| G8 Writer content | `test_g8_apply_and_real_catch_up_content`; `test_g8_invalid_existing_replaced_and_unsafe_root`; `test_g8_shared_project_targets` | PASS: approved apply, real migration run with fixture broker-client responses, format/mode/sections, raw-byte exclusions, section preservation and shared mixed/skip/unmapped targets |
| G9 Writer isolation | `test_g9_dry_run_unchanged_and_rollback_snapshot`; both parameters of `test_g9_writer_failure_return_and_exit_isolation`; `test_g9_catch_up_error_disabled_unmapped_and_section_preservation`; existing `test_dry_run_no_write_calls` | PASS: absent/unchanged dry-run snapshot, rollback overview, symlink/permission failure return and exit invariance, class-only raised error, disabled/unmapped outcomes |
| G10 Reader projection | `ProfileLogTests.test_g10_projection_age_redaction_and_bounds`; `ProfileLogTests.test_g10_malformed_symlink_nonregular_oversize` | PASS: malformed/wrong-format/oversized/symlink/FIFO, type/control/length/count bounds, extra fields, nested list caps, ages, ID shortening and redaction |
| G11 Endpoint | `ProfileLogTests.test_g11_endpoint_cache_guards_and_metrics_override` | PASS: 200 projection, cached value, 400 parameters, 405 writes, 403 Host/Origin |
| G12 Never opens secrets | `RelocationTests.test_g12_adapters_never_open_secrets` | PASS: mode-000 sentinels plus open monitoring across reader and relocated adapters, including transport config read attempt |
| G13 UI | `ProfileLogTests.test_g13_render_fixture_escaped`; `node insight/profile_log_qa.mjs` | PASS: actual renderer/helpers execute against injected fixture strings; navigation, section, all tables/cards, escaping, missing/truncated states and no forms. Optional browser checks not run |
| G14 Regression | Full 50-test Insight and 139-test settings/migrate suites above | PASS |

Relocation tests are in `insight/test_relocation.py`; reader/HTTP/render tests are
in `insight/test_profile_log.py`; writer tests are in
`profile-log/tests/settings/test_status.py`. Synthetic keys and row bodies used
for byte-exclusion assertions are generated/read only inside disposable test
fixtures and are never printed.

## Limitations and interpretations

- `qa.mjs` and `security_qa.mjs` require an isolated Chromium instance. No
  `chromium`, `chromium-browser` or `google-chrome` executable was found on PATH;
  neither `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` nor
  `/Applications/Chromium.app/Contents/MacOS/Chromium` was executable. Their syntax
  checks passed, but no real-browser visual/overflow run is claimed. The new
  fixture harness executes the actual renderer and HTML helpers without a browser.
- `--metrics-log` replaces the automatic summary-run list; smoke remains its
  independent source. This preserves the existing separation of bounded and
  isolated metrics. The extra `metrics_logs` argument follows the preserved
  positional `make_server` arguments.
- Catch-up status generation does not load broker state or write overview.
  The pre-existing `--settings` migration route retains its existing broker-backed
  project-trust checks; no new broker access was added to status generation.
- The D2 read-in-place default still requires owner confirmation before deployment,
  as the plan states. Deployment and production writer configuration changes are
  outside this implementation.

No commands read or modified live `~/.agentmesh`, `~/.hermes`, `~/Library`, live
keys, recovery shares, memory, plist or Syncthing configuration. All runtime test
homes and databases were temporary. The specified cached Python executable was
used to run tests. No production service was changed, no archive was moved, and
no commit was made. The pre-existing untracked plan file was left unchanged.

## Reviewer (Claude) follow-up

- Fix: unknown catch-up outcome / skip reasons (for example a new item status) made the strict writer schema
  reject the whole document, so the status file would silently stop updating. They are now folded into
  `other` in the writer (`status.folded`) and accepted by the Insight reader. Test:
  `test_unknown_reasons_fold_into_other`.
- Mutation testing (17 mutants, each run against the relevant suite): 14 killed, 1 killed by hang (dropping
  `O_NONBLOCK`/`O_NOFOLLOW` blocks on the FIFO fixture), M12 (device id truncation) killed on a separate run.
  Two survivors are equivalent by design: removing the inner `except` in `status.write` or around
  `applied(...)` in `apply_settings` is still caught by the outer best-effort `try` in `applied`/`caught_up`.
  Killed: prefer-legacy layout, merge order, peer containment, overview passthrough, reader symlink check,
  exchange ignored, endpoint parameter check, cron read from agentmesh home, writer `valid()` bypass,
  error message instead of class name, no `other` fold, catch-up not writing status, dry-run writing status.
- Final: `cd insight && python3 -m unittest` 50 OK; profile-log settings + migrate 140 passed.
- Live finding (not changed by this PR): `~/.agentmesh/jobs/agentmesh-production.json` still has
  `summary.metrics_path` under `~/.hermes/omp-memory/`; `mac_relocate`'s path mapping does not cover it.
  Insight reads that legacy location too until it is re-pointed.
