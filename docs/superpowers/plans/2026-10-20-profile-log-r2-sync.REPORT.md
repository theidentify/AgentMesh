# R2 sync I-1–I-3 implementation report

Implemented in this worktree without committing, installing dependencies, contacting a remote service,
reading a real Keychain, running `security`/`launchctl`, or accessing the live AgentMesh/Library trees.
All new I/O tests use temporary directories, fake runners, moto, or in-memory channels. Socket tests
remain enabled. This report is the explicitly requested exception to the source-file scope.

## Integration boundary

PR #61's `status.py` is absent here. `Broker(..., on_sync=None)` and `serve(..., on_sync=...)` expose
content-free sync status, validated by `sync_status_valid` in `broker/service.py`. CLI `broker run`
publishes it to `<home>/sync-status.json` using the existing `atomic_write` helper (0600 on POSIX).
**I-4 must merge this into `profile-log-status.json` → `last_sync` after #61.** No Insight changes,
I-5 verification tool, deployment, or live gates G-4–G-10 were attempted.

The status fields are exactly `at, outcome, uploaded, downloaded, invalid, errors, outbox,
local_objects, remote_objects, interval_s, backend`. Outcomes are `ok`, `credentials_missing`,
`credentials_refused`, `remote_error`, and `error`. `backend` is `s3` for the S3 factory; existing
filesystem syncing is also supported with `filesystem`. Transfer/object counts include the control
prefixes, not just events/wraps. Failed remote operations retain the last successful remote snapshot;
counts on such attempts are conservative. `at` is the attempt completion time. The hook is throttled
on outcome/count changes or 600 elapsed seconds; a failed hook is retried on the next attempt.
Only outcome changes produce a fixed, content-free stderr line.

The CLI rebuilds the S3 client and rereads credentials on every background attempt. Missing or refused
credentials never prevent local startup with valid remote flags. No ambient AWS credential chain is
used by the CLI: the factory passes both keys explicitly, with region `auto`. The low-level storage
API retains its existing injectable client/factory interface. SDK/HTTP debug logging is suppressed
because those libraries can log Authorization headers. S3 errors now use fixed messages and have
neither a retained cause nor a retained exception context.

## Requirement-to-test map

Paths below are relative to the worktree. Test names refer to the final source.

| Plan bullet | Proof |
|---|---|
| I-1.1 Keychain tuple, shape validation, timeout, minimal environment, safe failure/context | `profile-log/tests/test_r2_credentials.py::test_keychain_contract`, `test_keychain_safe_failures` (7 cases), `test_second_item` (3 cases): exact two commands/accounts, explicit temporary keychain, only `PATH`, bounded timeout, malformed/uppercase/missing/refused/timeout/OS/unexpected errors, including second-item failure. `clean()` checks both exception context and cause are absent and repr excludes sentinels. |
| I-1.2 Required reference on run/sync; HTTPS; explicit boto factory | `test_remote_config_rejected` (6 cases), `test_cli_requires_credentials` (2 cases, initialized home and no client/serve calls), `test_moto_explicit_credentials`, `test_cli_missing_one_shot`. Moto verifies the signer's actual keys and absent ambient session token with bogus AWS env and a temporary `.aws/credentials`. |
| I-1.3 Missing/refused classification; no secret text in errors/logs/repr | Above tests plus `test_s3_safe_errors` (4 operations), `test_s3_error_response` (3 cases) and `profile-log/tests/broker/test_sync_status.py::test_cli_retry_status`. Sentinels are tested against exceptions, context/cause, repr, captured DEBUG logs, CLI output and persisted status. |
| I-2.1 Immediate attempt, interval retry, exception survival | `test_immediate_retry_and_recovery`: background thread emits remote_error → error → ok, starts before the first interval, and continues at the configured interval. `test_cli_retry_status`: missing → refused → recovered on three fresh credential reads without restarting, with local remember/recall on each attempt. |
| I-2.2 Strict content-free status, atomic file, count/change/600-second throttle | `test_throttle_schema_hook` rejects extra/missing fields, invalid outcome/backend/timestamp/count/interval and retries a raising hook; checks changed counts, unchanged counts, 599s and 600s. `test_cli_retry_status` checks real JSON publication, POSIX 0600 and no leftover temporary files. `test_control_counts`, `test_download_and_invalid_counts`, `test_failed_list_keeps_counts`, `test_completion_timestamp` verify control/event/wrap totals, downloads/invalid objects, retained counts after a failed listing, and completion time. |
| I-2.3 Content-free stderr per outcome change | `test_immediate_retry_and_recovery` verifies three lines for three outcomes and no sentinel; `test_throttle_schema_hook` verifies only one ok line despite count changes and periodic writes. |
| I-2.4 Failing remote, recovery, strict schema, broker connections | Above tests and `test_memory_connection` exercise authenticated broker connection, remember and recall through in-memory channels while the sync thread repeatedly fails. `test_connections_offline` exercises the real OS transport and is blocked by sandbox socket binding; it is not skipped. |
| I-3.1 Renderer remote/log options and exact validation/defaults | `memory-sync/test_profile_log_services.py::test_r2_plist`, `test_r2_invalid` (14 cases), `test_r2_logs_validation` (3 cases); existing `test_snapshots` (6 platform/unit cases) and `test_sync_unchanged` (2 cases). Prefix validation mirrors `validate_key(prefix=True)` without making the standalone memory-sync renderer depend on profile-log imports. |
| I-3.2 Generate flags, fresh output only, never register/start, no raw keys | `test_r2_generate` verifies flags, no subprocess execution, no log-directory creation, and exclusive output; `test_r2_generate_missing_credentials`, `test_r2_parser_does_not_echo_secrets`; existing `test_exclusive` verifies all platform/unit outputs and permissions. |
| I-3.3 Plist round-trip, secret-free argv, invalid input, live-layout parity | `test_r2_plist` uses plistlib and compares the complete dictionary against the existing broker output plus the requested flags and `broker.out.log`/`broker.err.log`; it rejects standalone secret-shaped argv values and checks no environment block. `test_r2_invalid` covers bucket, HTTP, userinfo, controls, prefix, credentials and interval. Live-layout parity is against the layout supplied in the plan, not a read of the prohibited live plist. |

## Acceptance gates

| Gate | Result |
|---|---|
| G-1 Full suites and three-OS CI | **Not fully verified.** Both requested full commands were run, with live R2 disabled. Exact results are below. Socket tests require a run outside this sandbox; three-OS CI was not run. No socket test was skipped. |
| G-2 No credential leakage | Offline sentinel tests pass; fake Keychain and moto cover the credential path, sanitized exceptions/context/repr/logging/status, and renderer/parser argv/plist behavior. Diff inspection confirms explicit CLI credentials and no new default credential fallback, secret environment block, or remote deletion. |
| G-3 Defaults unchanged | Existing six profile-unit snapshots and two sync snapshots pass. `test_local_run_unchanged` verifies no remote, no interval, and no status file in the ordinary local run path. Fixture bytes match HEAD exactly; SHA-256: `294e24b37cfc6896d101bb18a54bf6c0ddff158ce16aac1c991ab18eb354fde3`. |

## TDD evidence

Tests for the credential, loop/status, and renderer behaviors were written before the initial
implementation. Initial commands and summaries:

```text
~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests/test_r2_credentials.py profile-log/tests/broker/test_sync_status.py
24 failed, 3 passed in 1.54s

~/.cache/agentmesh-testvenv/bin/python -m pytest -q -p no:cacheprovider memory-sync/test_profile_log_services.py
17 failed, 15 passed in 0.49s
```

Review added failing regressions before their fixes: parser echo (`1 failed, 35 passed in 0.31s`),
malformed S3 error response/context (`2 failed, 26 passed in 1.17s`), and stale counts/completion
clock (`2 failed, 8 deselected in 0.34s`; then `2 passed, 8 deselected in 0.27s`). Additional
in-memory and real socket acceptance coverage was added during implementation. An initial full
run found missing/overlong test IDs; those were corrected to satisfy the existing collection gate.

The renderer's focused final verification:

```text
~/.cache/agentmesh-testvenv/bin/python -m pytest -q -p no:cacheprovider memory-sync/test_profile_log_services.py
....................................                                     [100%]
36 passed in 3.93s
```

`git diff --check` exited 0 with no output. A read-only `git show HEAD:memory-sync/tests/fixtures/profile_log_units.json`
comparison produced:

```text
fixture byte-identical: True
sha256: 294e24b37cfc6896d101bb18a54bf6c0ddff158ce16aac1c991ab18eb354fde3
```

## Required full verification (exact captured output)

Both commands ran from the worktree root. `AGENTMESH_R2_LIVE` was unset for profile-log.
Both exited 1 because this sandbox prohibits socket binding. The five profile-log failures
include the new real-transport acceptance test; all ten memory-sync failures are the existing
HTTP digest API tests at `ThreadingHTTPServer` bind. **Run these commands outside the sandbox
and in three-OS CI to complete G-1.** No socket tests were skipped or changed to hide failures.
The known three `test_sqlite_consumers.py` symlink failures did not reproduce in this run.
The memory-sync warning is the existing duplicate-name ZIP fixture test.

```sh
unset AGENTMESH_R2_LIVE
~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests
```

```text
............................F.....................................F....F [ 14%]
......................F................................................. [ 28%]
........................................................................ [ 42%]
........................................................................ [ 57%]
........................................................................ [ 71%]
..................................................................sss... [ 85%]
.......................................................F..............ss [ 99%]
.                                                                        [100%]
=================================== FAILURES ===================================
_____________________________ test_p3_1_processes ______________________________
[Errno 1] Operation not permitted

During handling of the above exception, another exception occurred:
P3-1 blocked: sandbox denies socket binding
___________________________ test_connections_offline ___________________________
[Errno 1] Operation not permitted

During handling of the above exception, another exception occurred:
IPC acceptance blocked: sandbox denies socket binding
_____________________________ test_real_transport ______________________________
[Errno 1] Operation not permitted

During handling of the above exception, another exception occurred:
IPC acceptance blocked: sandbox denies socket binding
________________________ test_p5_4_concurrent_processes ________________________
[Errno 1] Operation not permitted

During handling of the above exception, another exception occurred:
P5-4 blocked: sandbox denies socket binding
_________________________ test_s3_custom_http_endpoint _________________________
[Errno 1] Operation not permitted

During handling of the above exception, another exception occurred:
HTTP acceptance gate blocked: sandbox denies 127.0.0.1 bind
=========================== short test summary info ============================
FAILED profile-log/tests/broker/test_isolation.py::test_p3_1_processes - Fail...
FAILED profile-log/tests/broker/test_sync_status.py::test_connections_offline
FAILED profile-log/tests/broker/test_transport.py::test_real_transport - Fail...
FAILED profile-log/tests/migrate/test_processes.py::test_p5_4_concurrent_processes
FAILED profile-log/tests/test_storage.py::test_s3_custom_http_endpoint - Fail...
5 failed, 495 passed, 5 skipped in 48.71s
```

```sh
~/.cache/agentmesh-testvenv/bin/python -m pytest -q memory-sync
```

```text
........................................s............................... [  4%]
...............................................FFFFFFFFF.F.............. [  8%]
........................................................................ [ 12%]
........................................................................ [ 16%]
........................................................................ [ 20%]
........................................................................ [ 24%]
........................................................................ [ 28%]
........................................................................ [ 32%]
................................................................s....... [ 36%]
........................................................................ [ 41%]
............................s.......sss................................. [ 45%]
......ssssssssssss...................................................... [ 49%]
..................................................................... [ 53%]
........................................................................ [ 57%]
........................................................................ [ 61%]
........................................................................ [ 65%]
........................................................................ [ 69%]
........................................................................ [ 73%]
........................................................................ [ 77%]
........................................................................ [ 81%]
............................................................................................................................... [ 89%]
........................................................................ [ 93%]
........................................................................ [ 97%]
..............................................                           [100%]
=================================== FAILURES ===================================
___________ test_digest_routes_by_provider_and_reports_routed_model ____________

service = <function service.<locals>.start at 0x10b7e9640>

    def test_digest_routes_by_provider_and_reports_routed_model(service):
        from digest_api_client import digest
>       url = service()
              ^^^^^^^^^

memory-sync/test_digest_api.py:53: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
memory-sync/test_digest_api.py:36: in start
    server = ThreadingHTTPServer(('127.0.0.1', 0), digest_api.make_handler(providers, token=token, timeout=30))
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:457: in __init__
    self.server_bind()
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/server.py:148: in server_bind
    socketserver.TCPServer.server_bind(self)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <http.server.ThreadingHTTPServer object at 0x10b237e00>

    def server_bind(self):
        """Called by constructor to bind the socket.
    
        May be overridden.
    
        """
        if self.allow_reuse_address and hasattr(socket, "SO_REUSEADDR"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Since Linux 6.12.9, SO_REUSEPORT is not allowed
        # on other address families than AF_INET/AF_INET6.
        if (
            self.allow_reuse_port and hasattr(socket, "SO_REUSEPORT")
            and self.address_family in (socket.AF_INET, socket.AF_INET6)
        ):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
>       self.socket.bind(self.server_address)
E       PermissionError: [Errno 1] Operation not permitted

/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:478: PermissionError
__ test_digest_rejects_bad_routes_and_never_echoes_worker_output[nope:x-404] ___

service = <function service.<locals>.start at 0x10b9d3ab0>, model = 'nope:x'
message = '404'

    @pytest.mark.parametrize('model, message', [('nope:x', '404'), ('fake', '400'), ('fake:bad name', '400'),
                                                ('fake:boom', '502'), ('fake:liar', '502')])
    def test_digest_rejects_bad_routes_and_never_echoes_worker_output(service, model, message):
        from digest_api_client import digest
>       url = service()
              ^^^^^^^^^

memory-sync/test_digest_api.py:64: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
memory-sync/test_digest_api.py:36: in start
    server = ThreadingHTTPServer(('127.0.0.1', 0), digest_api.make_handler(providers, token=token, timeout=30))
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:457: in __init__
    self.server_bind()
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/server.py:148: in server_bind
    socketserver.TCPServer.server_bind(self)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <http.server.ThreadingHTTPServer object at 0x10a6fc7d0>

    def server_bind(self):
        """Called by constructor to bind the socket.
    
        May be overridden.
    
        """
        if self.allow_reuse_address and hasattr(socket, "SO_REUSEADDR"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Since Linux 6.12.9, SO_REUSEPORT is not allowed
        # on other address families than AF_INET/AF_INET6.
        if (
            self.allow_reuse_port and hasattr(socket, "SO_REUSEPORT")
            and self.address_family in (socket.AF_INET, socket.AF_INET6)
        ):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
>       self.socket.bind(self.server_address)
E       PermissionError: [Errno 1] Operation not permitted

/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:478: PermissionError
___ test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake-400] ____

service = <function service.<locals>.start at 0x10b985dd0>, model = 'fake'
message = '400'

    @pytest.mark.parametrize('model, message', [('nope:x', '404'), ('fake', '400'), ('fake:bad name', '400'),
                                                ('fake:boom', '502'), ('fake:liar', '502')])
    def test_digest_rejects_bad_routes_and_never_echoes_worker_output(service, model, message):
        from digest_api_client import digest
>       url = service()
              ^^^^^^^^^

memory-sync/test_digest_api.py:64: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
memory-sync/test_digest_api.py:36: in start
    server = ThreadingHTTPServer(('127.0.0.1', 0), digest_api.make_handler(providers, token=token, timeout=30))
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:457: in __init__
    self.server_bind()
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/server.py:148: in server_bind
    socketserver.TCPServer.server_bind(self)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <http.server.ThreadingHTTPServer object at 0x10a6fc190>

    def server_bind(self):
        """Called by constructor to bind the socket.
    
        May be overridden.
    
        """
        if self.allow_reuse_address and hasattr(socket, "SO_REUSEADDR"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Since Linux 6.12.9, SO_REUSEPORT is not allowed
        # on other address families than AF_INET/AF_INET6.
        if (
            self.allow_reuse_port and hasattr(socket, "SO_REUSEPORT")
            and self.address_family in (socket.AF_INET, socket.AF_INET6)
        ):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
>       self.socket.bind(self.server_address)
E       PermissionError: [Errno 1] Operation not permitted

/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:478: PermissionError
_ test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake:bad name-400] _

service = <function service.<locals>.start at 0x10b9849e0>
model = 'fake:bad name', message = '400'

    @pytest.mark.parametrize('model, message', [('nope:x', '404'), ('fake', '400'), ('fake:bad name', '400'),
                                                ('fake:boom', '502'), ('fake:liar', '502')])
    def test_digest_rejects_bad_routes_and_never_echoes_worker_output(service, model, message):
        from digest_api_client import digest
>       url = service()
              ^^^^^^^^^

memory-sync/test_digest_api.py:64: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
memory-sync/test_digest_api.py:36: in start
    server = ThreadingHTTPServer(('127.0.0.1', 0), digest_api.make_handler(providers, token=token, timeout=30))
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:457: in __init__
    self.server_bind()
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/server.py:148: in server_bind
    socketserver.TCPServer.server_bind(self)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <http.server.ThreadingHTTPServer object at 0x10b7e3a80>

    def server_bind(self):
        """Called by constructor to bind the socket.
    
        May be overridden.
    
        """
        if self.allow_reuse_address and hasattr(socket, "SO_REUSEADDR"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Since Linux 6.12.9, SO_REUSEPORT is not allowed
        # on other address families than AF_INET/AF_INET6.
        if (
            self.allow_reuse_port and hasattr(socket, "SO_REUSEPORT")
            and self.address_family in (socket.AF_INET, socket.AF_INET6)
        ):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
>       self.socket.bind(self.server_address)
E       PermissionError: [Errno 1] Operation not permitted

/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:478: PermissionError
_ test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake:boom-502] _

service = <function service.<locals>.start at 0x10b984bf0>, model = 'fake:boom'
message = '502'

    @pytest.mark.parametrize('model, message', [('nope:x', '404'), ('fake', '400'), ('fake:bad name', '400'),
                                                ('fake:boom', '502'), ('fake:liar', '502')])
    def test_digest_rejects_bad_routes_and_never_echoes_worker_output(service, model, message):
        from digest_api_client import digest
>       url = service()
              ^^^^^^^^^

memory-sync/test_digest_api.py:64: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
memory-sync/test_digest_api.py:36: in start
    server = ThreadingHTTPServer(('127.0.0.1', 0), digest_api.make_handler(providers, token=token, timeout=30))
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:457: in __init__
    self.server_bind()
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/server.py:148: in server_bind
    socketserver.TCPServer.server_bind(self)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <http.server.ThreadingHTTPServer object at 0x10b7e3360>

    def server_bind(self):
        """Called by constructor to bind the socket.
    
        May be overridden.
    
        """
        if self.allow_reuse_address and hasattr(socket, "SO_REUSEADDR"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Since Linux 6.12.9, SO_REUSEPORT is not allowed
        # on other address families than AF_INET/AF_INET6.
        if (
            self.allow_reuse_port and hasattr(socket, "SO_REUSEPORT")
            and self.address_family in (socket.AF_INET, socket.AF_INET6)
        ):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
>       self.socket.bind(self.server_address)
E       PermissionError: [Errno 1] Operation not permitted

/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:478: PermissionError
_ test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake:liar-502] _

service = <function service.<locals>.start at 0x10b985e80>, model = 'fake:liar'
message = '502'

    @pytest.mark.parametrize('model, message', [('nope:x', '404'), ('fake', '400'), ('fake:bad name', '400'),
                                                ('fake:boom', '502'), ('fake:liar', '502')])
    def test_digest_rejects_bad_routes_and_never_echoes_worker_output(service, model, message):
        from digest_api_client import digest
>       url = service()
              ^^^^^^^^^

memory-sync/test_digest_api.py:64: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
memory-sync/test_digest_api.py:36: in start
    server = ThreadingHTTPServer(('127.0.0.1', 0), digest_api.make_handler(providers, token=token, timeout=30))
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:457: in __init__
    self.server_bind()
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/server.py:148: in server_bind
    socketserver.TCPServer.server_bind(self)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <http.server.ThreadingHTTPServer object at 0x10b62b2f0>

    def server_bind(self):
        """Called by constructor to bind the socket.
    
        May be overridden.
    
        """
        if self.allow_reuse_address and hasattr(socket, "SO_REUSEADDR"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Since Linux 6.12.9, SO_REUSEPORT is not allowed
        # on other address families than AF_INET/AF_INET6.
        if (
            self.allow_reuse_port and hasattr(socket, "SO_REUSEPORT")
            and self.address_family in (socket.AF_INET, socket.AF_INET6)
        ):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
>       self.socket.bind(self.server_address)
E       PermissionError: [Errno 1] Operation not permitted

/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:478: PermissionError
____________________ test_token_is_required_when_configured ____________________

service = <function service.<locals>.start at 0x10b987b60>

    def test_token_is_required_when_configured(service):
        from digest_api_client import digest, post
>       url = service(token='t0k')
              ^^^^^^^^^^^^^^^^^^^^

memory-sync/test_digest_api.py:72: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
memory-sync/test_digest_api.py:36: in start
    server = ThreadingHTTPServer(('127.0.0.1', 0), digest_api.make_handler(providers, token=token, timeout=30))
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:457: in __init__
    self.server_bind()
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/server.py:148: in server_bind
    socketserver.TCPServer.server_bind(self)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <http.server.ThreadingHTTPServer object at 0x10babe8b0>

    def server_bind(self):
        """Called by constructor to bind the socket.
    
        May be overridden.
    
        """
        if self.allow_reuse_address and hasattr(socket, "SO_REUSEADDR"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Since Linux 6.12.9, SO_REUSEPORT is not allowed
        # on other address families than AF_INET/AF_INET6.
        if (
            self.allow_reuse_port and hasattr(socket, "SO_REUSEPORT")
            and self.address_family in (socket.AF_INET, socket.AF_INET6)
        ):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
>       self.socket.bind(self.server_address)
E       PermissionError: [Errno 1] Operation not permitted

/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:478: PermissionError
_________________ test_client_worker_emits_scheduler_envelope __________________

service = <function service.<locals>.start at 0x10b985220>
capsys = <_pytest.capture.CaptureFixture object at 0x10b89f260>
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x10b927230>

    def test_client_worker_emits_scheduler_envelope(service, capsys, monkeypatch):
        import digest_api_client
>       url = service()
              ^^^^^^^^^

memory-sync/test_digest_api.py:80: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
memory-sync/test_digest_api.py:36: in start
    server = ThreadingHTTPServer(('127.0.0.1', 0), digest_api.make_handler(providers, token=token, timeout=30))
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:457: in __init__
    self.server_bind()
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/server.py:148: in server_bind
    socketserver.TCPServer.server_bind(self)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <http.server.ThreadingHTTPServer object at 0x10babead0>

    def server_bind(self):
        """Called by constructor to bind the socket.
    
        May be overridden.
    
        """
        if self.allow_reuse_address and hasattr(socket, "SO_REUSEADDR"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Since Linux 6.12.9, SO_REUSEPORT is not allowed
        # on other address families than AF_INET/AF_INET6.
        if (
            self.allow_reuse_port and hasattr(socket, "SO_REUSEPORT")
            and self.address_family in (socket.AF_INET, socket.AF_INET6)
        ):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
>       self.socket.bind(self.server_address)
E       PermissionError: [Errno 1] Operation not permitted

/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:478: PermissionError
____________ test_client_envelope_satisfies_bounded_extract_command ____________

service = <function service.<locals>.start at 0x10b984040>

    def test_client_envelope_satisfies_bounded_extract_command(service):
        from bounded_digest import extract_command
>       url = service()
              ^^^^^^^^^

memory-sync/test_digest_api.py:89: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
memory-sync/test_digest_api.py:36: in start
    server = ThreadingHTTPServer(('127.0.0.1', 0), digest_api.make_handler(providers, token=token, timeout=30))
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:457: in __init__
    self.server_bind()
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/server.py:148: in server_bind
    socketserver.TCPServer.server_bind(self)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <http.server.ThreadingHTTPServer object at 0x10b856850>

    def server_bind(self):
        """Called by constructor to bind the socket.
    
        May be overridden.
    
        """
        if self.allow_reuse_address and hasattr(socket, "SO_REUSEADDR"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Since Linux 6.12.9, SO_REUSEPORT is not allowed
        # on other address families than AF_INET/AF_INET6.
        if (
            self.allow_reuse_port and hasattr(socket, "SO_REUSEPORT")
            and self.address_family in (socket.AF_INET, socket.AF_INET6)
        ):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
>       self.socket.bind(self.server_address)
E       PermissionError: [Errno 1] Operation not permitted

/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:478: PermissionError
________________ test_bench_reports_validity_stability_and_cost ________________

service = <function service.<locals>.start at 0x10b985e80>

    def test_bench_reports_validity_stability_and_cost(service):
        import digest_bench
        from digest_api_client import digest
>       url = service()
              ^^^^^^^^^

memory-sync/test_digest_api.py:106: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
memory-sync/test_digest_api.py:36: in start
    server = ThreadingHTTPServer(('127.0.0.1', 0), digest_api.make_handler(providers, token=token, timeout=30))
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:457: in __init__
    self.server_bind()
/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/server.py:148: in server_bind
    socketserver.TCPServer.server_bind(self)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <http.server.ThreadingHTTPServer object at 0x10b855b50>

    def server_bind(self):
        """Called by constructor to bind the socket.
    
        May be overridden.
    
        """
        if self.allow_reuse_address and hasattr(socket, "SO_REUSEADDR"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Since Linux 6.12.9, SO_REUSEPORT is not allowed
        # on other address families than AF_INET/AF_INET6.
        if (
            self.allow_reuse_port and hasattr(socket, "SO_REUSEPORT")
            and self.address_family in (socket.AF_INET, socket.AF_INET6)
        ):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
>       self.socket.bind(self.server_address)
E       PermissionError: [Errno 1] Operation not permitted

/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/socketserver.py:478: PermissionError
=============================== warnings summary ===============================
memory-sync/test_ota_update.py::test_authenticated_archive_rejects_unallowlisted_or_duplicate_names[SYNC.md]
  /opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/lib/python3.14/zipfile/__init__.py:1755: UserWarning: Duplicate name: 'SYNC.md'
    return self._open_to_write(zinfo, force_zip64=force_zip64)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED memory-sync/test_digest_api.py::test_digest_routes_by_provider_and_reports_routed_model
FAILED memory-sync/test_digest_api.py::test_digest_rejects_bad_routes_and_never_echoes_worker_output[nope:x-404]
FAILED memory-sync/test_digest_api.py::test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake-400]
FAILED memory-sync/test_digest_api.py::test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake:bad name-400]
FAILED memory-sync/test_digest_api.py::test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake:boom-502]
FAILED memory-sync/test_digest_api.py::test_digest_rejects_bad_routes_and_never_echoes_worker_output[fake:liar-502]
FAILED memory-sync/test_digest_api.py::test_token_is_required_when_configured
FAILED memory-sync/test_digest_api.py::test_client_worker_emits_scheduler_envelope
FAILED memory-sync/test_digest_api.py::test_client_envelope_satisfies_bounded_extract_command
FAILED memory-sync/test_digest_api.py::test_bench_reports_validity_stability_and_cost
10 failed, 1726 passed, 21 skipped, 1 warning, 20 subtests passed in 334.61s (0:05:34)
```

## Final focused recheck

After making the mode assertion POSIX-only for Windows portability (no production change),
the new tests and the existing collection-ID gate were rerun, still including real socket acceptance:

```sh
unset AGENTMESH_R2_LIVE
~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests/test_collection.py profile-log/tests/test_r2_credentials.py profile-log/tests/broker/test_sync_status.py
```

```text
..................................F....                                  [100%]
=================================== FAILURES ===================================
___________________________ test_connections_offline ___________________________
[Errno 1] Operation not permitted

During handling of the above exception, another exception occurred:
IPC acceptance blocked: sandbox denies socket binding
=========================== short test summary info ============================
FAILED profile-log/tests/broker/test_sync_status.py::test_connections_offline
1 failed, 38 passed in 4.28s
```

## Changed files

- `profile-log/agentmesh_log/storage/r2creds.py`: explicit, bounded, sanitized Keychain reads.
- `profile-log/agentmesh_log/storage/s3.py`: sanitized errors without retained context and SDK log suppression.
- `profile-log/agentmesh_log/cli.py`: credential references, retry factory, atomic sync-status writer.
- `profile-log/agentmesh_log/broker/service.py`: immediate resilient loop, counts, schema, throttled hook/logging.
- `profile-log/tests/test_r2_credentials.py`, `profile-log/tests/broker/test_sync_status.py`: offline credential, safety, status and serving regressions.
- `profile-log/README.md`: storage credentials and broker sync/status behavior.
- `memory-sync/platform_service.py`, `memory-sync/test_profile_log_services.py`: validated remote/log rendering, generation and default-parity tests.
- This report: requirement/gate mapping and exact verification output. The existing fixture was not modified; no commit was made.

## Review (Claude, 2026-10-21)

Fixes applied after review:
- `remote_storage()` gives boto3 a bounded `Config(connect_timeout=5, read_timeout=20, retries standard/3)`.
  `sync_once` holds the broker lock during network I/O, so with boto defaults a stalled R2 could block recall
  for minutes. The moto test asserts the config.
- `load_keychain_credentials` passes `HOME` and `LANG=C` to `/usr/bin/security`, as `oskeystore.MacKeychainFile` does.

Verification outside the sandbox (socket tests included), from the repository root:
- `env -u AGENTMESH_R2_LIVE ~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests`
  → **500 passed, 5 skipped** (3 opt-in live R2 cases, 2 Windows-only).
- `~/.cache/agentmesh-testvenv/bin/python -m pytest -q -p no:cacheprovider memory-sync` → 1735 passed, 21 skipped,
  1 failed: `test_digest_api.py::test_client_envelope_satisfies_bounded_extract_command`, which fails the same way on
  `origin/main` (12c6c91) and is unrelated to this change.

Former lock limit resolved (2026-10-21 follow-up): remote LIST/GET now run in an unlocked fetch phase,
and PUT runs in an unlocked flush phase. Only local apply and failed-upload reconciliation hold the broker
lock. Authenticated broker-connection tests complete recall while LIST or PUT is blocked on an Event;
ownership assertions cover every remote method, including control sync. Local verification/indexing still
serialize requests, and prefetched objects plus queued upload bytes consume memory for the attempt.

## PR #62 follow-up: staged network I/O (2026-10-21)

`broker/service.py` now snapshots all six prefixes once and fetches objects absent from the filesystem
replica without the request lock. Failed GETs become staged `StorageError`s. Under the request lock,
`StagedRemote` supplies the snapshot and prefetched bytes and queues PUTs while the existing refresh,
`sync_control`, indexing and `Replica.sync_once(on_event=self.ingest)` execute unchanged. Uploads then
flush through `SyncRemote` outside the request lock; failed keys are removed from both remote-key sets
under a short lock, preserving retryable outbox entries and counting only successful uploads. A partial
control apply also flushes its queued immutable uploads before propagating the apply failure.

A non-reentrant `_sync_lock` serializes the complete fetch/apply/flush flow for both direct one-shot
sync and the background loop. A separate attempt lock serializes status publication; transfer counters
belong to each attempt instead of a shared `_sync_remote` field. Existing status schema, throttling,
outcomes, control download counts and alerts are preserved. Failed LISTs abort before apply and retain
the previous remote snapshot, including the existing one-shot error-count return contract. Unlike the
original conservative PUT-failure snapshot described above, a completed apply now retains successful
uploads and removes just failed uploads, so outbox reports the remaining work accurately.

All regression tests are in `profile-log/tests/broker/test_sync_staging.py`:

- `test_recall_during_network` (LIST/PUT): real authenticated `BrokerClient`/`Broker.connection` over
  in-memory channels; recall completes before the blocked remote is released, then sync reports `ok`.
- `test_network_verified`: all remote method ownership assertions, six real LISTs,
  verified event ingestion visible to recall, and invalid-object counting.
- `test_failed_flush_retries` (replica/control): failed PUT reports `remote_error`, remains in outbox,
  and succeeds on the next attempt with exact upload and outbox counts.
- `test_sync_serialized` (loop/one-shot): concurrent sync entry points cannot overlap remote activity.
- `test_failed_get_is_staged` (event/control): failed prefetch is counted and never fetched under lock.
- `test_control_download_unlocked`: control bytes are downloaded and counted with no locked remote I/O.
- `test_list_abort`: a late LIST failure performs no GET/PUT and preserves prior remote counts.

TDD: the six initial cases were written before production changes and produced **5 failed, 1 passed**
(3.16s): both blocked-recall cases, remote lock ownership, and both failed-upload cases reproduced the
old behavior. Additional control/GET coverage passed (**9 passed**, 1.50s) before adding the direct
one-shot concurrency and late-LIST cases. The first full run caught the one-shot LIST return regression
and a test-name length violation; both were fixed without changing existing tests.

No network services, installations, new dependencies or commits were used. Changes are confined to
`profile-log/**` and this report. `keyring.py`, `broker/replica.py`, the CLI and existing tests are unchanged.

Final required verification (live R2 disabled, socket tests enabled):

```sh
env -u AGENTMESH_R2_LIVE ~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests
```

```text
FAILED profile-log/tests/broker/test_isolation.py::test_p3_1_processes
FAILED profile-log/tests/broker/test_sync_status.py::test_connections_offline
FAILED profile-log/tests/broker/test_transport.py::test_real_transport
FAILED profile-log/tests/migrate/test_processes.py::test_p5_4_concurrent_processes
FAILED profile-log/tests/test_storage.py::test_s3_custom_http_endpoint
5 failed, 506 passed, 5 skipped in 43.37s
```

All five failures are `PermissionError: [Errno 1] Operation not permitted` from sandbox socket binding
(four broker IPC cases and the HTTP endpoint test). None were skipped or changed. The five existing
skips are the three opt-in live R2 cases and two Windows-only cases. All eleven new staged-sync cases
pass, as does the existing portable test-ID check. `git diff --check` passes.

### Review of the lock change (Claude, 2026-10-21)
- Outside the sandbox: `profile-log/tests` → **511 passed, 5 skipped**, three consecutive runs.
- Mutation: moving `StagedRemote(...)` (remote LIST/GET) back under `self.lock` makes `test_sync_staging.py` fail.

### I-5 — read-only remote verification

Implemented `python -m agentmesh_log.verify_remote`: inventory/hash comparison, remote wire parsing and
signed event/path checks, case-sensitive UTF-8 marker scanning (probes of 6+ chars) (including local profile/scope/agent names),
read-only SQLite sampling, and a disposable restore with local epochs or unwrapped child keys. Output
is one content-free JSON line with exit codes 0/1/2. No broker construction or lock; filesystem roots
must already exist. `cli.remote_storage` accepts an optional filesystem factory so the verifier can
reuse remote configuration and Keychain-only S3 credentials without creating directories. README and
the D-5 command are updated. A running broker can change objects between observations; this is not an
atomic snapshot, and a concurrent change can require another verification run.

TDD: the new test file first failed collection because the module did not exist. The targeted command
passed **19 tests** after implementation (3.93s), covering filesystem and moto S3, missing/tampered
objects, automatic markers, plaintext/credential/key redaction, read-only DB sampling, unchanged
home/remote/DB mtimes and SHA-256, malformed arguments, missing roots, wrap-based restore and cleanup:

```sh
env -u AGENTMESH_R2_LIVE ~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests/test_verify_remote.py
env -u AGENTMESH_R2_LIVE ~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests
```

The first full run caught missing explicit parametrization IDs; those were fixed. Final full result:
**525 passed, 5 skipped, 5 failed in 46.44s**. All failures are expected sandbox socket-bind denials:
`test_p3_1_processes`, `test_connections_offline`, `test_real_transport`,
`test_p5_4_concurrent_processes`, and `test_s3_custom_http_endpoint`. All 19 verifier cases pass.
`git diff --check` passes. No live network, installations, new dependencies, commits or GATES.md.

### Review of I-5 (Claude, 2026-10-21)
- Changed marker matching to case-sensitive, probes >= 6 chars. ASCII case-folding plus 4-char probes (e.g. `work`)
  would hit random base64 ciphertext by chance (~0.7 expected hits over 459 objects). New tests
  `test_markers_not_folded[case|profile-case|short]`.
- Outside the sandbox with a basetemp outside `tempfile.gettempdir()`: `profile-log/tests` → **533 passed, 5 skipped**.
