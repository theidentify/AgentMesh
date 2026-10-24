# W1 implementation report

Implemented G1, G2 and G3 without network access or commits. Native Windows verification remains outstanding. The required full profile-log test command is not green in this sandbox; failures are listed below.

## Files changed

| File | Change |
|---|---|
| `profile-log/agentmesh_log/storage/r2creds.py` | Explicit reference/OS dispatch, injectable Windows Credential Manager read/write/delete backend, safe errors, owner store/check helper. Reuses `WindowsCredential._api()` and its `use_last_error=True` ctypes declarations. |
| `profile-log/agentmesh_log/cli.py` | Dispatches remote credentials through `load_credentials`; adds `r2-credentials store/check --ref`; adds broker log redirection and safe output when stdout is absent. Keys remain explicit boto arguments. |
| `profile-log/agentmesh_log/connector_cli.py` | Updates the credential comment; connector storage already delegates to `cli.remote_storage`, so it automatically uses the same dispatch. |
| `memory-sync/platform_service.py` | Platform-specific credential references; Windows broker principal/action binding, restart count 999, and log argv. |
| `memory-sync/install_agent_rules.py` | Direct broker mode with absolute key/Python paths and exclusion of database/app-dir arguments. |
| `profile-log/tests/test_r2_wincred.py` | 32 credential test cases with fake native API; no real credential access. |
| `profile-log/tests/test_broker_logging.py` | 5 logging/pythonw test cases. |
| `profile-log/tests/smoke_r2_wincred.py` | Manual Windows owner smoke: requires typing `smoke`, uses a random service and disposable fake pair, deletes both targets in `finally`. Not collected by pytest. |
| `profile-log/tests/test_r2_credentials.py` | Existing integration mocks use the new dispatch boundary; Keychain unit tests explicitly fake macOS. |
| `profile-log/tests/test_verify_remote.py` | Existing credential mock receives the full reference at the dispatch boundary. |
| `profile-log/tests/broker/test_sync_status.py` | Existing credential retry mock uses the new dispatch boundary. |
| `memory-sync/test_platform_service.py` | 3 Windows renderer, credential-platform and unchanged POSIX fixture tests. |
| `memory-sync/test_install_agent_rules.py` | 4 broker-mode/parser/quoting and database-output regression tests. |
| `memory-sync/test_profile_log_services.py` | Updates the Windows broker snapshot expectation in code while retaining the existing fixture bytes. |
| `docs/superpowers/plans/2026-10-24-windows-profile-log-rollout.md` | Updates G1/G2/G3 rows and W1 scope/status to the implemented design. |
| `docs/superpowers/plans/2026-10-24-windows-profile-log-w1-report.md` | This report. |

Local, untracked `.unlazy/w1/GATES.md` records three passing acceptance groups (3 met, 0 unmet, 0 abandoned); it is workflow bookkeeping, not a proposed product file. `memory-sync/tests/fixtures/profile_log_units.json` is unchanged, verified by git diff and byte comparisons.

## Added tests mapped to requirements 1–7

There are 17 new test functions, expanding to 44 pytest cases. Existing tests retained their assertions except for the new credential mock boundary and the intentionally changed Windows broker XML snapshot.

| Requirement | New test function | Cases / assertion |
|---|---|---|
| 1 | `test_os_refusal` | 4: wincred/macOS, keychain/Windows, and both/Linux refuse without calling the native API. |
| 1, 2 | `test_dispatch_and_reference_validation` | 1: successful Windows/macOS dispatch, exact Windows targets, freeing both buffers, both reference grammars and malformed references. |
| 2, 3 | `test_native_failures` | 16: both targets tested for missing, wrong length, uppercase, non-hex, NUL, non-ASCII, native error and secret-bearing exception. Checks exact error, empty context/cause, no values in str/repr, and buffer release. |
| 3, 4 | `test_store_bad_format` | 4: bad length, uppercase, non-hex and NUL; backend and owner helper reject before any write. |
| 3, 4 | `test_store_verify` | 4: success, altered load-back, failed write and secret-bearing exception. Checks targets, generic type, persistence 2, verification and sanitized output/errors. |
| 4 | `test_helper_refuses_non_windows` | 2: macOS/Linux refusal before prompting or API access. |
| 4 | `test_helper_cli_output` | 1: actual CLI store/check success and missing/refused results; exact JSON contains no credential values. |
| 5 | `test_profile_windows_broker_and_catchup` | 1: XML principal IDs match Actions Context, InteractiveToken, LeastPrivilege, count 999, exact quoted log argv; catch-up retains count 3; no process execution or log-directory creation. |
| 5 | `test_profile_credentials_platform_and_macos_logs` | 1: all six platform/reference combinations; Linux preserves existing keychain rendering; macOS uses plist log paths without log argv. |
| 5 | `test_profile_posix_fixtures_unchanged` | 1: exact existing macOS/Linux broker and catch-up fixture bytes. |
| 6 | `test_log_dir_append_utf8_before_broker` | 1: files opened before BrokerHome; append preservation, UTF-8, backslashreplace, line buffering, actual stdout/stderr and CLI JSON output. |
| 6 | `test_invalid_log_dir` | 3: relative, missing and regular-file paths produce invalid_request before broker startup. |
| 6 | `test_pythonw_none_streams` | 1: fake broker runs/closes and CLI returns successfully with both streams None. |
| 7 | `test_broker_exact_argv_and_examples` | 1: exact manifest argv, POSIX round-trip, PowerShell quoting, instructions examples and real profile-log argparse parsing of query/limit/project. |
| 7 | `test_broker_invalid_inputs` | 1: relative key, missing Python, database combination and app-dir combination all rejected before writes. |
| 7 | `test_broker_cli_modes` | 1: actual installer CLI rejects conflicting options and installs exact broker argv into a synthetic home. |
| 7 | `test_database_instructions_bytes_unchanged` | 1: SHA-256 snapshot of unchanged database-mode instructions; existing tests also verify exact database manifest/frontmatter and repeat-install bytes. |

## Test commands and results

Every pytest invocation used `~/.cache/agentmesh-logvenv/bin/python -m pytest` with `-p no:cacheprovider`. No packages were installed. `ls memory-sync/test_platform_service*.py` found only `memory-sync/test_platform_service.py`.

For the table, `P` means `~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider`.

| Command | Results |
|---|---|
| `P profile-log/tests` | Final: **967 passed, 10 failed, 11 skipped**, 2 warnings. Initial: 966 passed, 11 failed, 11 skipped; the additional failure was the new-test explicit-ID/length convention, now fixed. |
| `P memory-sync/test_install_agent_rules.py memory-sync/test_platform_service*.py` | Final: **27 passed, 0 failed**. Initial: 26 passed before adding the database instruction snapshot. |
| `P memory-sync/test_profile_log_services.py` | **36 passed, 0 failed**, both runs. |
| `P profile-log/tests/test_r2_wincred.py profile-log/tests/test_broker_logging.py` | **37 passed, 0 failed**. |
| `P profile-log/tests/test_r2_wincred.py` | Acceptance ledger: **32 passed, 0 failed**. |
| `P profile-log/tests/test_broker_logging.py` | Acceptance ledger: **5 passed, 0 failed**. |
| `P memory-sync/test_install_agent_rules.py memory-sync/test_platform_service.py` | Acceptance ledger: **27 passed, 0 failed**. |
| `P -x profile-log/tests` | Diagnostic early stop: 38 passed, 1 failed (sandbox socket binding). |
| `P profile-log/tests/test_r2_wincred.py profile-log/tests/test_broker_logging.py profile-log/tests/test_collection.py profile-log/tests/test_r2_credentials.py` | Intermediate: 65 passed, 1 failed (test node ID exceeded 80 characters). Names shortened; final full run passes the collection check. |
| `P profile-log/tests/control/test_migrate.py::test_panic_revert profile-log/tests/control/test_native.py::test_reconcile_same_process_only` | Untouched `HEAD` archive in `/tmp/agentmesh-w1-baseline`: 0 passed, **2 failed**, reproducing both process-control failures without W1 changes. |
| `P --collect-only profile-log/tests/test_r2_wincred.py profile-log/tests/test_broker_logging.py` | 37 cases collected, no tests executed; used to inspect node IDs. |

`git diff --check` passed. The local gate linter passed without warnings and its runner recorded all three groups passing.

Final full-suite failures:

- Socket binding denied by this sandbox: `broker/test_isolation.py::test_p3_1_processes`, `broker/test_sync_status.py::test_connections_offline`, `broker/test_transport.py::test_real_transport`, `control/test_server.py::test_supervisor_round_trip`, `control/test_server.py::test_ticker_and_close_hooks`, `migrate/test_processes.py::test_p5_4_concurrent_processes`, and `test_storage.py::test_s3_custom_http_endpoint`. The two warnings are the supervisor threads encountering the same socket denial.
- Process-control failures also reproduced on untouched HEAD: `control/test_migrate.py::test_panic_revert`, `control/test_native.py::test_reconcile_same_process_only`. Their process-identity lookup uses `/bin/ps`, which this sandbox refuses.
- Known baseline failure, ignored as instructed: `test_status_actions.py::test_p6_s12_close_cross_verifier`, due to missing gateway Node dependencies.

## Recall JSON comparison

Compared `cli.execute`, `broker/index.py::recall_compat`, `memory-sync/agentmesh.py` and `memory-sync/recall_memory.py` (`_broker_only` and `_broker_row`). Both CLIs print the returned JSON object directly.

Both successful results share `query`, `query_plan`, `candidate_counts`, `results` and `evidence`. Both provide result content, source type, score and source references. They are not structurally identical for an agent:

- Direct `recall --compat` has **no top-level `backend`**. The database wrapper adds `backend: "broker"` on its broker path. The W4 check that expects this field cannot be applied unchanged to the direct command.
- Direct compat retains broker row fields such as the broker scope, label, source record, author device and evidence event IDs. The wrapper normalizes rows into its durable/summary shape, converts scope using source metadata, and adds fields such as `status`, `memory_key`, `supersedes_id`, and `metadata.profile_log` where applicable.
- Direct compat always returns `evidence: []`. The database wrapper can expand evidence using local SQLite rows for locally sourced records. Windows direct recall has no such database enrichment.
- A pure SQLite recall path can return integer row IDs and event candidates; broker compat uses broker IDs and reports zero event candidates.

These differences are material to consumers expecting a backend tag, normalized row fields or expanded evidence. Recall output was not changed in this task.

## Outstanding verification

The native Windows smoke, actual Credential Manager/Task Scheduler/pythonw execution, PowerShell execution and Windows CI were not run on this macOS host. Native behavior is covered here with injected fakes, XML assertions and shell-string assertions. The owner must run the manual smoke and the reviewer must run the Windows CI/mutation checks. No tasks were registered or started and no real credentials were read or written.

The full suite requires an environment permitting its local socket and process-control checks. No sandbox escalation was available. Shared-memory recall was unavailable because its configured Python executable did not exist; implementation used the supplied plan and repository sources.
