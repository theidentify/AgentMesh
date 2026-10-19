# Encrypted profile log phases 1–2 implementation report

Implemented tasks 1–7 sequentially in the requested worktree, without commits. The library,
unit/contract/integration tests, README and normative FORMAT are present. Inside the Codex sandbox the loopback
HTTP test of P2-4 could not bind 127.0.0.1; the reviewer ran it outside the sandbox and the
full suite passes (see "Reviewer verification" at the end).

## Final verification

Prepared interpreter only: `/Users/krisanathep/.cache/agentmesh-logvenv/bin/python`.

Repository root:

```text
$ ~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests
HTTP acceptance gate blocked: sandbox denies 127.0.0.1 bind
=========================== short test summary info ============================
FAILED profile-log/tests/test_storage.py::test_s3_custom_http_endpoint - Fail...
1 failed, 166 passed in 7.84s
```

Inside `profile-log/`:

```text
$ ~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider tests
HTTP acceptance gate blocked: sandbox denies 127.0.0.1 bind
=========================== short test summary info ============================
FAILED tests/test_storage.py::test_s3_custom_http_endpoint - Failed: HTTP acc...
1 failed, 166 passed in 7.31s
```

Both full commands exited 1. Measured after review fixes: 167 test cases, 166 passed, 1 failed, 0 skipped;
13 acceptance gates satisfied, 1 partially verified, 0 abandoned. The failed gate remains
required. A run in an environment permitting `127.0.0.1` bind/connect is necessary before
claiming all gates or all tests pass. No permission escalation is available in this session.

## Acceptance gate mapping

Test paths below are relative to `profile-log/tests/`. Parametrized names cover all cases
in their corresponding test; filesystem/S3 contract and end-to-end tests run on both backends.

| Gate | Evidence | Result |
| --- | --- | --- |
| **P1-1 Tamper** | `test_events.py::test_every_envelope_field_tamper` — all 11 envelope fields, including ciphertext and signature mutation | PASS |
| **P1-2 Re-sign attack** | `test_events.py::test_resign_attack_header_binding` — attacker is separately certified; verification succeeds, decryption fails | PASS |
| **P1-3 Wrong key** | `test_events.py::test_wrong_key` — sibling, other profile, older epoch; `test_e2e.py::test_taxonomy_write_only_end_to_end` — all nonmatching readers refused | PASS |
| **P1-4 Write-only** | `test_model_keys.py::test_write_key_public_only` — slots, attributes, pickle roundtrip, no private bytes, unwrap refusal; `test_events.py::test_event_roundtrip_write_only_and_paths` — write-key sealing, read-key opening and type refusal; `test_e2e.py::test_taxonomy_write_only_end_to_end` — phone cannot open | PASS |
| **P1-5 Tree** | `test_model_keys.py::test_tree_descendants_and_independent_keys`, `test_wrap_tamper` (all wrap fields), `test_unwrapped_key_id_checked` (valid HPKE with wrong claimed key ID), `test_single_branch_rotation` (nested rewrap, sibling bytes unchanged), `test_invalid_tree_operations`; stored-wrap traversal in `test_e2e.py` | PASS |
| **P1-6 Identity chain** | `test_identity.py::test_identity_chain`, `test_identity_rejections` (wrong device/account, unknown issuer, self-issued, duplicate ID/different key, bad signature, both cross-type reuse directions), `test_signature_domains`, `test_agent_certificate_strict_shape`, `test_registry_does_not_keep_callers_mutable_records`; `test_events.py::test_cert_envelope_binding` | PASS |
| **P1-7 Labels** | `test_model_keys.py::test_labels` — ordering, maximum, raising, confirmed/unconfirmed lowering; `test_events.py::test_revision_label_lowering` — same rule on revision seal | PASS |
| **P1-8 Email/metadata privacy** | `test_model_keys.py::test_account_profile_ids_and_privacy`; `test_events.py::test_envelope_metadata_and_email_privacy`; `test_e2e.py::test_taxonomy_write_only_end_to_end` — scans every stored object on both backends for email and lowercase email, local names and private metadata | PASS |
| **P1-9 Strict parsing** | `test_encoding_ulid.py::test_typed_injective_and_canonical`, `test_strict_parse`, `test_encoding_rejects`, `test_structure_limits`, `test_ulid_rejections`; `test_events.py::test_strict_before_signature` — fail-fast sentinel proves registry signature work is not reached for malformed fields/JSON/ULIDs or oversize data; `test_decrypted_payload_validation` validates post-decryption content | PASS |
| **P2-1 Shared contract** | `test_storage.py::TestStorageContract::test_roundtrip_and_missing`, `test_sorted_prefixes` — same class on filesystem and moto S3, binary roundtrip, missing object, nested/sorted/empty/no-match/partial prefixes | PASS |
| **P2-2 Key safety** | `test_storage.py::TestStorageContract::test_key_safety_before_io` — same 17 invalid-key cases on both backends, I/O sentinels; invalid prefixes also checked | PASS |
| **P2-3 Filesystem immutability** | `test_storage.py::test_filesystem_immutable_and_private`, `test_filesystem_symlinks`, `test_filesystem_dot_and_temp_files` — idempotent re-put, no overwrite, temp cleanup, POSIX permissions with Windows guard, root/subdir/file symlink refusal, hidden files ignored | PASS |
| **P2-4 S3** | `test_storage.py::test_s3_pagination_and_isolation` — four pages at page size 2, two namespaces; `test_optional_boto3_import` — fresh process blocks boto3 but filesystem still imports/works; `test_s3_custom_http_endpoint` — real loopback moto server, custom endpoint and path-style client | PASS (HTTP test verified by the reviewer outside the sandbox; see below) |
| **P2-5 End to end** | `test_e2e.py::test_taxonomy_write_only_end_to_end[filesystem]` and `[s3]`, `conftest.py::taxonomy` — work/common/client-a/client-b, personal/common, two chained devices and one agent each, write-only phone, six scope events, stored wraps, prefix listing/fetch/verification/decryption and denied readers | PASS |

Additional task-1 evidence: `test_encoding_ulid.py::test_ulid_monotonic_and_time` proves
same-millisecond monotonicity, time-order sorting and clock rollback handling. The final
combined run exposed shared ULID state in this simulated-clock test; it now isolates that
state with pytest monkeypatch and restores it after the test.

## Execution and TDD evidence

1. Scaffold/encoding/ULID: tests first failed on the missing encoding module; implementation
   produced **32 passed**.
2. Model/labels/keys: tests first failed on missing `model`; full suite then **46 passed**.
3. Identity: tests first failed on missing `identity`; full suite then **63 passed**.
4. Events: tests first failed on missing `events`; full suite then **101 passed**.
5. Storage: tests first failed on missing storage exports. Implemented shared key validation,
   filesystem publication, S3 and contract tests. First full run reached **143 passed** but
   moto's background bind failed and `start()` waited indefinitely; the run was interrupted.
   Added a loopback bind preflight so restricted hosts fail promptly. Targeted storage run
   excluding that known blocker: **43 passed, 1 deselected**. This diagnostic exclusion is
   not counted as passing HTTP evidence; both final full runs include the test.
6. End-to-end: tests first failed because `taxonomy` did not exist; adding the fixture
   produced **2 passed** on filesystem and S3.
7. README/FORMAT/REPORT: documented exact fields, encodings, HPKE suite, domains, verification
   order, caller responsibilities, storage visibility and limitations; reconciled all 14 gates
   against the final full runs.

## Scope and deviations

- Changes are only `profile-log/**` and this REPORT. `git status --short` shows only those
  two untracked paths; no tracked files were modified. `git diff --check` passes.
- No commits, pushes, branches, installs, external network requests, real credentials,
  identities, keychains, exchanges or user data were used. Tests use disposable `tmp_path`
  roots, generated keys and dummy AWS credentials. The only socket attempt was the plan's
  required loopback HTTP test, rejected by the sandbox before listening.
- No GATES.md or other working ledger was created. The user-mandated REPORT served as the
  acceptance ledger. Shared-memory recall was not performed because the plan explicitly
  forbids reading `~/.hermes`.
- **Required deviation / unresolved verification:** an all-green full run is impossible in
  this sandbox because of the loopback restriction. The implementation and test remain in
  place for the reviewer to run with loopback permission. No acceptance requirement was
  weakened to conceal this.
- Tests ran on macOS/Python 3.11, not Windows. POSIX-only calls have platform/capability
  guards and permission assertions are Windows-aware, but native Windows execution is not
  claimed.

## Open questions and boundaries

No new product decision is needed for phases 1–2. The sole required handoff is the real
HTTP test in a loopback-enabled environment. Later-phase broker policy, detectors, grants,
revocation, enrollment, keyring persistence, recovery, migration, conflict resolution and
snapshots remain out of scope as specified.

The library enforces revision lowering when `previous_label` is supplied, as planned;
callers must resolve the scope default before sealing. It cannot look up an encrypted
previous event from a ULID alone. S3 uses plain PUT and relies on unique names/later Worker
policy for immutability. Root/ancestor filesystem ownership remains a trusted local boundary.


## Review fixes

F1–F4 are implemented. The current full-run results above supersede the initial implementation
counts in the execution history. No commits, installations, external network requests or
GATES.md were added; changes remain confined to `profile-log/**` and this REPORT.
The remaining HTTP failure is the sandbox restriction explicitly accepted for this review
turn; the reviewer runs that test outside the sandbox.

| Finding | Change and proving tests (relative to `profile-log/tests/`) | TDD evidence |
| --- | --- | --- |
| **F1 — authenticated wraps** | `wrap_key` now requires a parent read key. Required `auth` is hex HMAC-SHA256 over the specified domain plus typed record without auth, using the specified HKDF-SHA256 derivation from the parent's raw private key and decoded key ID. `unwrap_key` uses `hmac.compare_digest` before decrypting. ScopeTree creation/rotation and wrap path schema use the authenticated format. `test_model_keys.py::test_write_only_wrap_forgery_rejected`, `test_wrap_auth_contract_and_before_decryption`, `test_wrap_tamper[auth]`, `test_unwrapped_key_id_checked`, `test_single_branch_rotation`, and both end-to-end backend cases prove the behavior. | First run: **2 failed** — a public-key-only forged wrap was accepted (`DID NOT RAISE`), and legitimate wraps lacked `auth`. After implementation: **19 passed** across model/key and end-to-end tests. The wrong-child-key-ID fixture now has valid authentication so it still reaches and proves the decrypted-key check. |
| **F2 — account binding** | `verify_event` explicitly compares envelope account to registry account. `test_identity.py::test_device_account_binding_with_valid_root_signature` uses this account's valid root signature with another account ID; `test_agent_account_binding_with_valid_device_signature` uses a registered device's valid signature with another account ID. `test_events.py::test_event_account_binding_with_valid_signatures[False]` tests the corresponding event, and `[True]` isolates the explicit envelope guard from certificate verification. | Before the event fix: **1 failed, 1 passed**, with the isolated envelope case incorrectly accepted. Each existing registry account guard was temporarily removed independently: its new test failed with `DID NOT RAISE`; both guards were restored in a `finally` block. These are regression/mutation checks for existing behavior, not claims that those guards were initially missing. After the fix/restoration: **59 passed** across identity/event tests. |
| **F3 — robust listing** | Both backends skip only `InvalidKey` failures for stored names; filesystem safety checks occur before that skip. `test_storage.py::test_listing_skips_invalid_stored_names` covers spaces, non-ASCII names and invalid directory components on both backends. `test_invalid_name_does_not_hide_unsafe_file` covers a symlink and FIFO with invalid names; existing symlink tests remain active. | First run: **6 failed, 2 passed**, with each stray-name case raising `InvalidKey` and aborting listing. After the fix: **8 passed**. |
| **F4 — prefix-scoped walk** | Filesystem listing starts at the directory named by complete slash-terminated prefix components, checks each ancestor, filters partial final components, and avoids any walk for missing/non-directory paths. `test_storage.py::test_filesystem_prefix_limits_walk` records both `os.walk` starts and `os.scandir` visits for full/partial/missing/file prefixes. `test_filesystem_prefix_checks_each_ancestor` proves a symlink ancestor is rejected even when a deeper requested component is missing. | First run: **4 failed, 1 passed**, because every query started at the root, including missing prefixes. After the fix: **56 passed, 1 deselected** for storage excluding the known HTTP blocker. Both subsequent full runs include that HTTP test. |

FORMAT.md specifies authenticated wrap fields, key derivation, verification order and
parent-key-holder provenance. It explicitly leaves current-epoch selection and rotation
authorization to the Phase 4 keyring, and notes that authentication does not prevent replay
of older legitimate wraps. README documents the changed wrap API. The format also documents
explicit event account binding, skipped invalid stored names and prefix traversal semantics.

Final review verification: **166 passed, 1 expected sandbox-blocked failure, 0 skipped**
from both required directories. All four review findings are resolved; live HTTP verification
remains the reviewer's external-environment check.

## Reviewer verification (Claude, outside the sandbox, 2026-10-19)

- Full suite from the repo root and from `profile-log/`: **167 passed, 0 failed, 0 skipped**
  (macOS, Python 3.11.16, cryptography 50.0.2, boto3 1.43.114, moto 5.2.4). This includes
  `test_s3_custom_http_endpoint`, so **P2-4 is PASS**.
- Mutation testing of the security checks (each mutation applied alone, suite run with `-x`):
  removed or broken event signature check, HPKE `info` header binding (seal and open), cert ↔
  envelope binding, envelope/registry account check, agent-cert signature, agent/device signature
  domain separation, device-cert signature, device-cert and agent-cert account checks, device-id
  rebinding, child key-id check after unwrap, wrap `info` binding, wrap `auth` check, wrap `auth`
  coverage of the ciphertext, label lowering and label resolution, filesystem no-clobber, `..`
  key rejection, symlink refusal, lowercase ULID rejection, email in the public record, label in
  the envelope header, and raising on stray listed names. **All were killed.**
- One mutation survives by design: removing the explicit self-issued device-cert check. A
  self-issued cert for a new device is already refused as "unknown issuer", and one for an
  existing device with a different key as "already registered", so the check is defence in depth.
- Not verified: Windows execution, real Cloudflare R2 (tested only against moto in-process and a
  loopback moto HTTP server), and interoperability with any non-Python implementation.

## Opt-in live Cloudflare R2 check

Added the live harness without running it against R2. No real credentials were read,
installed or printed, and no external network request was made. Changes remain under
`profile-log/**` and this REPORT; no commits, installs or GATES.md were created.
The results in this section supersede the earlier full-suite counts.

- `S3Storage.list(prefix, *, page_size=None)` forwards `PaginationConfig={'PageSize': n}`
  only when a page size is supplied. The default call remains unchanged.
  `tests/test_storage.py::test_s3_page_size` first failed with an unexpected-keyword
  TypeError, then passed against moto: five keys returned sorted over exactly three
  ListObjectsV2 requests with MaxKeys=2. Existing pagination/isolation coverage also passes.
- `tests/test_r2_live.py` has a module-wide skip marker unless `AGENTMESH_R2_LIVE=1`.
  Its module-scoped fixture constructs boto3 explicitly from the four dedicated
  `AGENTMESH_R2_*` settings, requires an HTTPS endpoint, and passes the client to S3Storage.
  It uses no AWS credential fallback. Configuration/client errors are sanitized and SDK
  logging is suppressed during the live fixture. Missing settings when enabled fail setup.
- `test_contract`, `test_identical_reput`, and `test_encrypted_events` cover random bytes,
  missing objects, sorted nested/paged listings, identical re-put and three encrypted
  work/client-a events using the existing taxonomy fixture. One event uses the phone's
  write-only key; sibling and write-only opening fail. Raw fetched objects are checked
  for the body, tags, task, label value and account email.
- Each run gets `agentmesh-live-tests/<ULID>/`. The registered module finalizer lists all
  run keys using the raw client, validates every key against that exact prefix, deletes
  batches of at most 1,000, verifies the prefix is empty and emits one counts/time-only
  summary. It executes on assertion failures too. Process termination or lost access can
  prevent cleanup; this limitation and manual abandoned-prefix cleanup are documented.
  No delete method was added to the library Storage interface.
- README contains private-bucket and bucket-scoped Object Read & Write token instructions,
  all environment variable names, silent `read -rs` credential entry, the exact requested
  live command with `-s`, and the rule that CI leaves live mode disabled.

Offline safety evidence (`tests/test_r2_safety.py`, helpers in `tests/r2_support.py`):

| Requirement | Tests |
| --- | --- |
| Required configuration, explicit dedicated credentials, HTTPS | `test_missing_env` (four cases), `test_explicit_credentials`, `test_endpoint_and_error_redaction` |
| Enabled mode fails rather than skips with missing settings | `test_enabled_missing_env_fails` runs a subprocess with the flag enabled and all four required settings removed; all three live tests error before client construction, with no network possible |
| Prefix restrictions and protection of unrelated objects | `test_cleanup_prefix_guard` (five cases), `test_cleanup_refuses_foreign_result`, `test_cleanup_batches_and_isolation` (1,001 run objects, batches 1,000 + 1, sibling object preserved) |
| Sanitized cleanup failures | `test_cleanup_error_redaction` first failed on an injected SDK RuntimeError containing dummy credential text; implementation now exposes only a fixed error with suppressed context |
| Full harness, finalizer and summary | `test_live_harness_offline` calls the three live test functions against moto, verifies cleanup, and checks exactly one summary line: 11 successful puts, 5 gets, 5 lists, 3 verified events and 10 deleted objects |

The safety tests were written before their helper module existed and failed collection for
that missing module; all 16 now pass. Actual R2 endpoint/authentication behavior remains
explicitly unverified here and must be checked by the operator.

Latest default full run, with `AGENTMESH_R2_LIVE` unset, from the repository root:

```text
$ env -u AGENTMESH_R2_LIVE ~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests
FAILED profile-log/tests/test_storage.py::test_s3_custom_http_endpoint - Fail...
1 failed, 184 passed, 5 skipped in 9.28s
```

Inside `profile-log/`, the equivalent full command ended:

```text
1 failed, 184 passed, 5 skipped in 8.95s
```

The five skips are the three opt-in live cases and two Windows-only junction cases.
The sole failure remains the previously documented sandbox denial of 127.0.0.1 bind,
not an R2 check. Excluding only that known local HTTP blocker:

```text
$ env -u AGENTMESH_R2_LIVE ~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests -k 'not custom_http_endpoint'
184 passed, 5 skipped, 1 deselected in 9.01s
```

The live suite was not enabled with usable R2 configuration. No live success is claimed.
