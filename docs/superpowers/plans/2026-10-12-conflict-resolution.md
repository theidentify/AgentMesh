# Conflict resolution implementation plan

2026-10-12; approved design: `../specs/2026-10-12-conflict-resolution-design.md`.
Checkpoint policy: implement and review each gate separately. This document records gates 1–4; the current work is **gate 4 only**. No git writes, no network. No emission until explicit gate-4 group activation. Retain unsigned-v1 and signed-v2 readers permanently, including after activation.

## Scope and invariants

Approved: ancestor-aware full-state resolution; complete immutable evidence retention; same-lineage update/delete only; no natural-key/unique/FK repair planner; strict revocation for never-committed evidence; operator-managed activation and explicit worker resume.

Required invariants:

1. A resolution covers whole source packets on both sides, transitively including predecessor packets and prior resolutions. Every covered key has exactly one fresh final revision, even uncontested companions. Coverage cannot be a UUID-only assertion.
2. Each key has a verified origin (baseline content hash or key-specific absence). Parents form a same-key acyclic DAG with immutable globally unique revision definitions. Different baseline origins are refused.
3. Parents are precisely the maximal covered revisions for that key. Omitted branches, redundant ancestors, fabricated edges, cycles, missing companions, and revision reuse are refused. Unknown evidence is pending, not permission to invent ancestry.
4. All current heads must be covered ancestors, identical immutable revisions, or proved descendants. An incomparable head blocks the whole operation; no partial transition escapes. Equal/descendant heads are preserved. Identical rows with different sibling revisions still conflict.
5. Rows, graph, receipts/dispositions, and local outbox commit atomically. Held packets get superseded dispositions, never counterfeit applied receipts. Replay must preserve later edits and exact envelope identity.
6. Agreement is conditional on a terminal user decision dominating all concurrent branches, eventual evidence delivery, and compatible trust. Independent resolutions remain siblings until another resolution covers both.
7. Never-committed evidence from revoked keys is refused. Committed receipts/dispositions authorize only exact historical replay. Public diagnostics contain identifiers and fixed reasons only.
8. Bound graph/packet work and refuse oversized atomic closure; never silently split or truncate it.

## Current merged join recheck

Inspected HEAD `15fcdfc`, not the historical `ac6b814` copy. `memory-sync/install_join.py:119–157` still requires the exact unsigned-v1 envelope and checks scalar `parent` for baseline ancestry. `:382–416` creates the empty DB, calls `receive`, blocks on conflict/invalid, and retains pending on missing history. `:417–434` checks for signed namespace before publishing runtime and removing the pending guard last. `:109–115`, `:186–189`, `:249–259` retain empty-origin unsigned-only invitation scope. Gate 4 must update these actual merged paths; no signed invitation join is added.

## Test commands and baseline policy

Use this interpreter for **every pytest invocation** (shell variable `TEST_PY` below is shorthand only):

```sh
TEST_PY=/Users/krisanathep/.cache/agentmesh-testvenv/bin/python
"$TEST_PY" -m pytest -q memory-sync
```

Run the full suite before new tests and after each gate. Record actual failures, not just counts. User-supplied baseline: 750 passed, 3 known `test_sqlite_consumers.py` failures, 14 skipped. Acceptance permits only the reproduced baseline failures; no new failure may be hidden by deselection. Tests before code must fail for the intended missing contract or behavior. After fixes, run focused tests, then full suite. Do not install dependencies.

## Gate 1 — formal contract and independent model (this change)

### Files and boundaries

Create:

- `memory-sync/resolution_protocol.py`: pure, bounded semantic validation/coverage/transition functions. No filesystem, SQLite, signing, transport, migration, or activation behavior.
- `memory-sync/test_resolution_protocol.py`: named negative/positive contract tests and old-runtime isolation assertions.
- `memory-sync/test_resolution_model.py`: independent set-based oracle and exhaustive/seeded ordering tests against the contract functions.

Create this plan. Existing production files and existing tests remain unchanged. No database schema in this gate. No live caller imports the new module.

### Data structures and functions

Frozen `Key(table, values)`, `Ref(uuid, digest)`, `Change(key, revision, parents, origin, row_json)`, `Packet(ref, kind, changes, covers, evidence, group, node)`, `Limits(packets, revisions, keys, parents, ancestry)`, `Resolution(packet, ancestors, changes, origins)`, and `Transition(key, revision, row_json, action)` values. `row_json` is canonical immutable JSON or None (tombstone). Origins are a trusted caller-supplied map `Key -> baseline revision | None`; gate 2 must authenticate baseline witnesses before populating it. Packet adapters must authenticate and bind complete envelope digests before calling these functions. Pure validation is **not signature authorization**.

Public signatures:

```python
payload_digest(payload: dict) -> str
packet_from_payload(payload: dict, *, envelope_digest: str) -> Packet
validate_resolution(packet: Packet, evidence: Mapping[str, Packet],
                    origins: Mapping[Key, str | None], *, limits: Limits = Limits()) -> Resolution
plan_resolution(resolution: Resolution, heads: Mapping[Key, str | None]) -> tuple[Transition, ...]
```

Errors: `InvalidResolution` for malformed/conflicting immutable definitions or incomplete declared coverage; `MissingEvidence` for unavailable referenced packets, roots, or current heads; `ResolutionConflict` for known incomparable current heads. Fixed messages only, no row contents. Frozen records are untrusted until validated; direct construction must not bypass invariants.

Proposed wire contract, inert until later gates: unsigned `omp-memory-changes-v2`; signed `agentmesh-signed-changes-v3`. Semantic payload has exactly `group,node,uuid,kind,body,covers,evidence`. Body changes have exactly `table,key,row,revision,parents,origin`. References have exactly `uuid,envelope_digest`. Lists are sorted (body by table/canonical key, refs by UUID, parents lexically), unique and bounded. Complete row schema/SQL validation belongs to the adapter/apply layer; gate 1 enforces key binding, row object/tombstone, supported key/table types, typed encoding, origins, and graph invariants.

Checksum = SHA-256 of a new semantic-domain prefix plus existing typed encoding of the **entire semantic payload**. New envelope `format` must be exact and is pinned by full-envelope identity; signed signature covers format and every payload/checksum/identity field. Evidence is a manifest of required source packets; covers must be a subset with exact digests. Extra authenticated evidence may prove a receiver descendant but cannot authorize omission of absorbed parents/companions. Normal edits have one parent, except absence-root creation with no parents; resolutions have the maximal covered frontier (one parent is valid for companions).

Default conservative closure bounds for the initial contract: 256 evidence packets, 4,096 revisions, 1,024 keys, 64 parents per revision, and 262,144 total ancestor-set memberships, plus existing typed encoding limits. The additional membership budget prevents a legal long chain from consuming quadratic memory. Per-payload limits are hard caps; callers may use stricter graph limits. These are refusal thresholds, not automatically negotiated group settings; revisiting them requires tests and review. Wire byte/signature limits remain gate 2.

### Tests first

`test_resolution_protocol.py`:

- `test_payload_digest_binds_every_semantic_field`: any semantic field change changes checksum; map presentation order does not.
- `test_payload_rejects_malformed_contract`: exact fields, UUIDs/digests, duplicate/unsorted lists, wrong types, key mismatch, invalid origin, scalar parent, unknown table, empty changes.
- `test_valid_resolution_covers_companions_from_both_packets`: full union accepted, including tombstones and single-parent companion revisions.
- `test_incomplete_coverage_is_refused`: missing companion, omitted parent packet, incomplete transitive resolution coverage, redundant/missing frontier.
- `test_missing_or_changed_evidence`: missing source is pending; mismatched pinned digest or reused identities is invalid.
- `test_graph_refuses_cycles_cross_key_parents_and_origin_mismatch`: no invented roots or cross-key ancestry; baseline mismatch refused.
- `test_limits_fail_closed`: every bound is exercised with a positive control.
- `test_transition_is_atomic_and_preserves_descendants`: baseline, absence, either side, identical head, descendant and incomparable/unknown head behavior; no mutation on failure.
- `test_existing_runtime_remains_unchanged`: current formats remain old, new formats rejected by old validators, no existing production caller imports the isolated contract.

`test_resolution_model.py`:

- `test_three_peer_all_delivery_orders`: independent oracle enumerates P/Q/R deliveries with duplicates, withholding evidence; compare atomic rows/heads and eventual companion preservation for A/B/C.
- `test_independent_resolutions_need_terminal_choice`: different and identical row choices still conflict until T covers both, including their extra companions.
- `test_offline_edit_and_later_edit`: unseen offline edit blocks; later causal edit is preserved; late original packets cannot restore old state in the model.
- `test_seeded_graphs_match_independent_oracle`: generate varied branching packet histories and terminal resolutions, randomize peer starting frontiers/delivery, compare against an independent set-union ancestry oracle. Include deletion and duplicate delivery, record seed in assertions. Does not use production traversal to compute expected ancestors.

Commands: `"$TEST_PY" -m pytest -q memory-sync/test_resolution_protocol.py memory-sync/test_resolution_model.py` (red, then green), followed by the full command above. Review production diff and import boundaries. Acceptance: all focused cases pass; full suite has only reproduced baseline failures; no live behavior/schema/format changes; explicit oracle counterexamples demonstrate why naive parent membership and omitted companions fail. Stop for review. Model checks establish protocol invariants, not SQL, cryptographic, crash or worker correctness.

## Gate 2 — migration and read compatibility (implemented; execution evidence below)

### Exact files

Create `memory-sync/resolution_store.py`, `memory-sync/packet_versions.py`, `memory-sync/test_resolution_store.py`, `memory-sync/test_packet_versions.py`. Modify `memory-sync/memory_sync.py`, `memory-sync/signed_packets.py`, `memory-sync/test_memory_sync.py`, `memory-sync/test_signed_packets.py`, `memory-sync/build_package.py`, `memory-sync/standalone_build.py`, `memory-sync/test_package.py`, `memory-sync/test_standalone_build.py`. The source-package FILES allowlist and standalone hidden imports must include the newly reachable modules at this gate. Do not change capture's emitted format. Preserve historical constants or introduce explicit legacy aliases rather than repurposing them.

### Schema and functions

Idempotent explicit migration, independent of `initialize`'s early return:

- `_sync_protocol(id=1, schema_version, emission_enabled DEFAULT 0, activation_manifest_digest NULL)`.
- `_sync_revision_nodes(revision PRIMARY KEY, table_name, key, origin, row_digest, row_json, packet_uuid, evidence_state)`; keep legacy history/shadow intact for old readers before activation.
- `_sync_revision_parents(revision,parent, PRIMARY KEY(revision,parent))`; same-key DAG checked by contract, not just foreign keys.
- `_sync_packet_evidence(uuid PRIMARY KEY, envelope_digest, envelope_json, format, sender, key_id, authorization_state)`; immutable complete identity, private local data.
- `_sync_packet_dispositions(packet_uuid,envelope_digest,resolution_uuid, disposition, PRIMARY KEY(packet_uuid,resolution_uuid))`; never fabricate an applied receipt.
- `_sync_origins(table_name,key,revision,row_json, PRIMARY KEY(table_name,key))`; baseline content verified using original hash function, or explicit absence. Missing witness remains unresolved.

Signatures: `migrate(c) -> None`; `load_origins(c, keys) -> dict`; `retain_evidence(c, envelope, authorization) -> Ref`; `load_evidence(c, refs) -> dict`; `decode_packet(c, envelope, *, security, folder, filename) -> Packet`; `validate_wire(envelope, *, group, folder) -> None`; `receipt_identity(envelope) -> str`. Migrate old receipts/history incrementally without guessing lost ancestry. Historical signature compatibility stays exact. Retained but never-committed evidence must be reauthorized under current trust, never treated as a historical receipt.

Version dispatch permanently accepts old unsigned-v1 and signed-v2. New unsigned-v2/signed-v3 parsing validates payload plus exact format/fields/limits. V3 changes signature domain, covers all semantic fields, and pins signature bytes. Gate-2 new-format receive may retain authenticated evidence but remains pending with `resolution_apply_not_enabled`; no row application and no resolution emission. Old normal receive remains functional. Explicit dispatch replaces existing non-legacy-means-signed assumptions.

### Tests first / commands / exit criteria

Write `test_migration_is_idempotent_and_atomic`, `test_missing_baseline_witness_is_not_invented`, `test_reconstruct_edges_from_retained_packets`, `test_changed_envelope_cannot_replace_evidence`, `test_legacy_fixtures_remain_readable_forever`, `test_new_packet_read_does_not_emit_or_apply`, `test_old_reader_rejects_new_formats_without_receipt`, `test_v3_tampering_every_field`, `test_revoked_uncommitted_evidence_refused`, `test_historical_receipt_and_disposition_exact_replay`, `test_downgrade_and_limits_fail_closed`. Assert rollback at each migration boundary, exact old signature compatibility, distinct evidence/receipt states, and no new emitter reachable.

Run `"$TEST_PY" -m pytest -q memory-sync/test_resolution_store.py memory-sync/test_packet_versions.py memory-sync/test_memory_sync.py memory-sync/test_signed_packets.py memory-sync/test_package.py memory-sync/test_standalone_build.py`, then full suite. Acceptance: schema restart-safe; all four formats have fixtures; old traffic behavior preserved; strict revocation verified with real crypto; migration/receipt audit passes; emission still disabled. Review before gate 3.

## Gate 3 — atomic same-lineage application and convergence (implemented; execution evidence below)

### Exact files

Create `memory-sync/resolution_apply.py`, `memory-sync/test_resolution_apply.py`, `memory-sync/test_resolution_e2e.py`. Modify `memory-sync/resolution_store.py`, `memory-sync/memory_sync.py`, `memory-sync/signed_packets.py`, `memory-sync/test_signed_packets.py`, `memory-sync/build_package.py`, `memory-sync/standalone_build.py`, `memory-sync/test_package.py`, `memory-sync/test_standalone_build.py` for transactional dispatch, dispositions and packaging of the newly reachable apply module. No user emission command yet.

### Functions and transaction boundary

`prepare_resolution(c, source_refs, choices, *, security) -> ResolutionPlan`; `apply_resolution(c, packet, *, security) -> ApplyResult`; `commit_resolution(c, plan, *, security, operation_id) -> Packet`; `receive_resolution(c, envelope, *, security) -> ApplyResult`. Require activation for authoring; tests set up an explicit test activation record, never introduce a production bypass flag.

`ResolutionPlan` binds immutable source refs, complete key set, expected actual/shadow state, origins, schema version and trust digest. `ApplyResult` separates applied/superseded/no-op. Add `_sync_resolution_operations(operation_id PRIMARY KEY,plan_digest,packet_uuid)` for idempotent CLI retries. Every local commit writes rows, shadow, DAG, dispositions, outbox and operation record together. Receiver writes receipt/envelope pin atomically. Import flags, FK checks, off-packet cascade checks remain mandatory. Natural-key/unique/FK/cascade repair requirements return a fixed unsupported-repair error and roll back; do not auto-expand to repair plans.

Receive indexes/retains evidence before attempting resolution so order does not force intermediate conflicting applications. Retry affected pending/conflict paths after resolution progress; use exact dispositions to avoid recreating stale diagnostics. Preserve newer proved descendants. Existing status gains separate superseded counts without redefining received as applied+superseded.

### Tests first / commands / exit criteria

Write `test_two_peers_companions_exactly_once`, `test_three_peers_resolution_before_sources`, `test_independent_resolutions_then_terminal_choice`, `test_offline_new_branch_is_not_lost`, `test_later_edit_precedes_resolution_delivery`, `test_transaction_faults_leave_no_partial_dispositions`, `test_publish_retry_keeps_uuid`, `test_capture_before_resolve_invalidates_stale_plan`, `test_relational_repairs_refused_atomically`, `test_signed_three_peer_revocation_boundaries`, `test_late_covered_packet_does_not_restore_diagnostic`, `test_randomized_sql_matches_model`. Use real SQLite and crypto, compare rows/heads with gate-1 oracle, inject crashes around durable boundaries and publication. Verify replay does not capture echoes or rescan full rows per retained packet.

Run `"$TEST_PY" -m pytest -q memory-sync/test_resolution_apply.py memory-sync/test_resolution_e2e.py memory-sync/test_signed_packets.py`, gate-1 tests, then full suite. Acceptance: all supported same-lineage histories converge conditionally; no companion loss; all failure boundaries atomic; signed identity and revocation preserved; no CLI/network emission available and authoring fails without activation. Review before gate 4.

## Gate 4 — CLI, join, workers and activation (implemented locally; native CI pending)

### Exact files

Create `memory-sync/conflict_commands.py`, `memory-sync/protocol_activation.py`, `memory-sync/test_conflict_commands.py`, `memory-sync/test_protocol_activation.py`. Modify `memory-sync/agentmesh.py`, `memory-sync/worker_lifecycle.py`, `memory-sync/install_join.py`, `memory-sync/install_inspect.py`, `memory-sync/windows_install.py`, `memory-sync/mac_replace.py`, `memory-sync/resolution_apply.py`, `memory-sync/memory_sync.py`, `memory-sync/test_install_join.py`, `memory-sync/test_worker_lifecycle.py`, `memory-sync/test_agentmesh.py`, `memory-sync/test_windows_install.py`, `memory-sync/test_mac_replace.py`, `memory-sync/SYNC.md`, `memory-sync/SIGNED-SYNC.md`. Also modify `memory-sync/build_package.py`, `memory-sync/standalone_build.py`, `memory-sync/test_package.py`, and `memory-sync/test_standalone_build.py` to package the new command/activation modules. `agentmesh_launcher.py` delegates to the CLI and needs no independent DB reader or fence.

### Data, signatures and behavior

`list_conflicts(db, exchange, *, show_content=False) -> ConflictReport` is read-only, including verification telemetry. `preview_resolution(runtime, selection, choices) -> ResolutionPlan`; `resolve(runtime, plan_digest, *, confirm_word, operation_id) -> ResolveReport`; `receive_only(runtime) -> ReceiveReport`. Interactive listings may show local content; JSON/public reports redact by default. Typed `RESOLVE`, exact plan digest for noninteractive mode, revalidation after prompt, cooperative stop and DB locks. No automatic worker resume; instruct explicit existing `worker-start` only when healthy. No ingestion/summary in receive-only recovery.

`activation_plan(runtime, peer_inventory) -> ActivationPlan`; `activate(runtime, plan_digest, *, confirm_word) -> None`; `require_capability(runtime, database) -> None`. Persist complete operator-confirmed inventory (including offline peers) and minimum protocol/software capability with manifest digest in `_sync_protocol`; all gates 1–4 supported before activation. Normal capture may emit the new single-parent format **only after activation**; unsigned-v1/signed-v2 readers never disappear. Draining immutable old outboxes does not resign packets. Fail safely for unsupported runtime/DB combinations and block supported installer rollback to older binaries after activation.

Old unmanaged binaries cannot retroactively learn a new DB fence. The activation preview must require operator confirmation that old workers/binaries are drained; launcher/installer fences protect managed entry points, not malicious or bypassed direct execution. Do not claim an absolute downgrade guarantee.

Join `observed_slots` must dispatch all supported unsigned formats and walk origin evidence, so array parents cannot evade non-empty-baseline exclusion. Keep signed invitation refusal, slot reservations and replay-only install. A replay ends only after all delivered required evidence/coverage is settled; retain pending guard otherwise. Transport completeness remains outside this feature.

### Tests first / commands / exit criteria

Write `test_conflicts_is_read_only_and_redacts_public_output`, `test_mine_theirs_explicit_row_and_delete_preview`, `test_wrong_word_eof_and_stale_plan_write_nothing`, `test_resolve_commit_publish_retry_uses_same_operation`, `test_stopped_worker_receive_only_then_explicit_restart`, `test_activation_requires_all_peer_inventory_and_typed_word`, `test_emission_disabled_before_activation`, `test_old_readers_remain_after_activation`, `test_managed_downgrade_refused_after_activation`; extend join tests with `test_join_replays_resolution_before_conflicting_sources`, `test_join_new_parent_arrays_cannot_hide_baseline`, `test_join_missing_evidence_retains_guard`, `test_join_signed_invitation_still_refused`. Test runtime/trust/path changes during confirmation and unrelated worker failure retention.

Run `"$TEST_PY" -m pytest -q memory-sync/test_conflict_commands.py memory-sync/test_protocol_activation.py memory-sync/test_install_join.py memory-sync/test_worker_lifecycle.py memory-sync/test_agentmesh.py memory-sync/test_windows_install.py memory-sync/test_mac_replace.py`, gates 1–3 focused suites, full suite, and `"$TEST_PY" -m pytest -q memory-sync/test_package.py memory-sync/test_standalone_build.py`. Gate-4 native CI (not run in gate 1) must provision private disposable `RUNNER_TEMP` and use its already-installed native test/build interpreter; run these exact script entry points:

```sh
"$TEST_PY" memory-sync/standalone_build.py --dist "$RUNNER_TEMP/agentmesh-dist" --work "$RUNNER_TEMP/agentmesh-build"
AGENTMESH_DIST="$RUNNER_TEMP/agentmesh-dist" "$TEST_PY" memory-sync/smoke_worker_lifetime.py
# Windows runner only; AGENTMESH_NATIVE_TASK_FIXTURE_ROOT must be a new disposable directory.
"$TEST_PY" memory-sync/smoke_windows_install.py
```

Use equivalent environment assignment syntax in the native Windows shell. No dependency downloads are part of these commands. Acceptance: typed CLI works locally; no private content in public state; explicit worker restart; join replays resolved histories safely; operator activation is the sole emission switch; no regressions; cross-platform installation/rollback review complete. Only now permit operational activation, separately per group.

## Refinements from the design

- Merged join citations replace historical commit citations; inspected behavior is unchanged in the relevant paths.
- Gate 1 uses trusted normalized origins/evidence as an explicit boundary. It does not pretend pure DAG validation authenticates signatures or database roots. Gate 2 supplies that boundary.
- Initial scope refuses *all* relational repairs, as approved; the design's optional multi-row repair path is not part of gates 1–4.
- Conservative closure bounds are now concrete; no on-the-fly splitting. Schema and payload validation are separated so gate 1 cannot accidentally become a new live wire reader. An explicit ancestor-membership budget bounds quadratic graph memory. Missing current baseline state is invalid, never retryable pending.
- Worker resume is explicitly separate, without the design's optional auto-resume flag. Activation fencing is limited honestly to managed entry points plus operator draining.

## Gate-1 execution evidence

Completed gate 1 only. All pytest runs used the exact supplied interpreter. Existing production files/tests and the approved design document have no diff. Four untracked deliverables: this plan and the three gate-1 Python files.

| Run | Actual result |
|---|---|
| Full suite before new files | 750 passed, 3 failed, 14 skipped; 20 subtests passed; 120.01s |
| First focused red run, tests present/module absent | 86 setup errors: `ModuleNotFoundError: resolution_protocol`; 1.27s |
| First focused green run | 86 passed; 10.61s |
| Additional boundary-test red run | 1 failed, 93 passed; missing current baseline incorrectly classified pending; 12.84s |
| Final focused run | 94 passed; 11.00s |
| Full suite after implementation | 844 passed, 3 failed, 14 skipped; 20 subtests passed; 129.00s |

The same three baseline failures before/after are all `ConsumerTests` in `memory-sync/test_sqlite_consumers.py`:

- `test_export_complete_collision_safe_manual_notes_and_source_anchors`
- `test_export_infers_transcript_tasks_even_without_structured_task_ref`
- `test_export_resolution_of_last_active_item_clears_current_memory`

Each reports `ValueError: symlink export root refused`; they were not modified or deselected. Both full runs also reported the existing duplicate ZIP-entry warning. No new failures.

Focused command: `"$TEST_PY" -m pytest -q memory-sync/test_resolution_protocol.py memory-sync/test_resolution_model.py`. Full command: `"$TEST_PY" -m pytest -q memory-sync`. Logs are local under `$TMPDIR/conflict-g1-{baseline,red,green1,red2,focused-final,full-after}.log`; no private database or network was used by the new tests.

The model tests include 18 two/three-peer initial-state and delivery-order combinations, 720 six-packet permutations for each of two independent-resolution variants (different and identical row choices), 24 later-edit permutations, and 40 reproducible randomized DAG seeds with eight frontier comparisons each. They verify resolution decisions against a test-owned set-union fixed-point oracle; production uses a bounded topological traversal. The test driver models ordinary packet delivery/dispositions independently; it is not a claim about unimplemented SQL/worker behavior.

Risks/limits discovered: absent state cannot substitute for a required baseline; proof expansion needs an explicit quadratic-memory budget; complete-envelope bindings and origin witnesses must be authenticated by gate 2; read-only listing, strict revocation integration, SQL atomicity, crash recovery and managed activation fences remain unimplemented. Limits deliberately refuse some large legitimate histories rather than split atomic resolutions. Native Windows smoke checks are a later gate and were not run here.

Gate-1 acceptance is met under the documented known-failure baseline. Gates 2–4 are **unstarted**, and emission is still unavailable.


## Gate-2 execution evidence (2026-10-12)

Gate 2 implements migration and versioned **read compatibility only**. Gate 3 has not started. No activation command, resolution application, row mutation from a new packet, or new-format signing/emission API was added. `capture` still emits unsigned-v1 or signed-v2. `Security.sign` and `publish` refuse new-format emission; the existing minimal legacy signing call (without prefilled format/checksum) is preserved.

### Files and implementation

New files:

- `memory-sync/resolution_store.py`: savepoint-based explicit migration; six new tables and reverse-parent index; verified baseline witnesses; bounded affected-component reconstruction; immutable envelope/revision identity; exact evidence loading.
- `memory-sync/packet_versions.py`: permanent legacy readers, unsigned-v2/signed-v3 validation, versioned receipt/envelope identities, current-trust authorization or exact committed replay, row-schema checks, and normalized evidence.
- `memory-sync/test_resolution_store.py` and `memory-sync/test_packet_versions.py`: migration boundary faults, graph/identity cases, all four packet formats, real Ed25519 tampering and revocation/replay, downgrade/limits, and disabled emission.

Modified planned files: `memory_sync.py`, `signed_packets.py`, `build_package.py`, `standalone_build.py`, `test_memory_sync.py`, `test_signed_packets.py`, `test_package.py`, and `test_standalone_build.py` under `memory-sync/`, plus this plan.

Two narrow gate-transition changes outside the original gate-2 file list: `test_resolution_protocol.py` replaces gate 1's temporary prohibition of every production contract import with a prohibition of resolution-application imports/module existence; all legacy-format assertions remain. `resolution_protocol.py` has **docstring changes only**, acknowledging its gate-2 reader callers. No gate-1 contract logic changed. Keeping the previous import prohibition would make the planned gate-2 readers impossible.

### Refined boundaries and schema behavior

`migrate(c)` creates `_sync_protocol`, `_sync_revision_nodes`, `_sync_revision_parents`, `_sync_packet_evidence`, `_sync_packet_dispositions`, `_sync_origins`, and `_sync_revision_parent_lookup` atomically. A savepoint commits independently only when no outer transaction exists; it never commits a caller's transaction. Fault tests cover every table creation, index creation, origin seeding and final protocol-version insertion. Repeat migration is a no-op; unsupported future schema versions are refused. `emission_enabled` defaults to zero.

Migration preserves baseline payloads only when a current baseline shadow still supplies the matching content hash. It never infers old baseline values from later rows. Packet reconstruction is incremental through the explicit authenticated `decode_packet`/`retain_evidence` adapter, including retained local outbox packets; schema migration itself has no exchange/trust argument and does not silently authorize historical packets. Unknown parent edges remain `unresolved`, and legacy normalization raises `MissingEvidence` after retaining their bytes rather than borrowing the current key's origin. Callers that want to retain this pending evidence must catch that exception within their transaction.

The new receive branch lazily migrates after validation and authorization, then commits evidence and the fixed pending diagnostic `resolution_apply_not_enabled` together. Raising the existing `Pending` exception there would roll back the evidence, so this branch records pending directly. It does not alter application rows, shadow, legacy history, outbox, or applied receipts. Existing legacy receive/apply paths remain intact; they do not require a bulk migration before exchanging legacy traffic.

`load_evidence(c, refs)` returns pinned **raw envelopes**, not currently authorized Packet objects. It has no security argument, so every consumer must pass those envelopes through `decode_packet` again. The transient `Authorization` record binds a decoder authorization result to its complete envelope digest; persisted `authorization_state` is not used as a trust shortcut. A revoked key whose packet was merely retained remains refused, including after a successful earlier verification. Exact applied receipts and committed superseded dispositions retain the historical exception, including complete signature-byte pinning. No API writes a superseded disposition in this gate; tests seed that future gate-3 commit state solely to exercise its reader.

Signed-v3 uses `AgentMesh/change-packet/Ed25519/v3\0` as its separate signature/receipt domain. The old v2 domain and receipt digest are unchanged. New unsigned complete identity uses `AgentMesh/change-packet/unsigned/v2\0`; historical unsigned receipts still use the original body checksum. New checksums cover the entire gate-1 semantic payload. Tests change each v3 field, use a wrong signing domain, and recompute a checksum after changing coverage to prove that an unchanged signature still fails. Fixture packets are signed with real test keys directly; no production v3 emitter is exposed.

Exact new-format historical replays remain read-only in gate 2. Telemetry uses a presence hint only after the decoder validates the exact committed identity, so cached replays do not count as new signature checks. Legacy receipts without complete envelope pins still perform and record historical verification. The legacy runtime behavior remains covered by the unchanged regression tests and new capture-format tests.

### TDD and verification

All pytest commands used the supplied interpreter:

```text
/private/tmp/claude-501/-Users-krisanathep-Workspaces-AgentMesh--claude-worktrees-gitbook-agent-mesh-usage-1839e8/726c4aa1-e480-4547-b773-90d2bb8f75d5/scratchpad/testvenv/bin/python
```

| Run | Actual result |
|---|---|
| Full suite before gate-2 edits | 844 passed, 3 known failures, 14 skipped; 20 subtests passed; 115.40s |
| Listed focused tests written before implementation | 4 failed, 110 passed, 39 setup errors; 12.65s |
| First focused implementation run | 153 passed; 16.37s |
| Additional ancestry/isolation red run | 2 failed, 93 passed; 5.99s |
| Expanded focused run including gate 1 | 256 passed; 23.12s |
| Intermediate full regression | 896 passed, same 3 failures, 14 skipped; 144.06s |
| Minimal-signing/coverage audit red run | 1 failed, 1 passed, 28 deselected; 0.56s |
| Cached-replay telemetry red run | 2 failed, 30 deselected; 4.70s |
| Final expanded focused run | 260 passed; 26.64s |
| Final full regression | 900 passed, same 3 failures, 14 skipped; 20 subtests passed; 132.85s |

The first red run failed for the intended missing `resolution_store` / `packet_versions` modules and missing package/hidden-import entries; existing focused tests passed. Later red runs exposed an unproved legacy-origin inference, gate 1's now-obsolete import prohibition, an overly narrow guard on the historical signing API, and incorrect cached-replay verification counting. Each was corrected and retested.

Focused command (the plan's listed files plus gate-1 regressions):

```sh
"$TEST_PY" -m pytest -q memory-sync/test_resolution_store.py memory-sync/test_packet_versions.py memory-sync/test_memory_sync.py memory-sync/test_signed_packets.py memory-sync/test_package.py memory-sync/test_standalone_build.py memory-sync/test_resolution_protocol.py memory-sync/test_resolution_model.py
```

Full command: `"$TEST_PY" -m pytest -q memory-sync`. Logs are local under `$TMPDIR/conflict-g2-{baseline,red,green1,red2,red3,red4,focused-final,full-after,full-final}.log`. Real cryptography was available and used. No dependencies were installed; no network or git writes were performed.

### Remaining risks and gate-3 handoff

- Missing original baseline payloads remain a hard proof gap; retained later rows cannot repair that gap automatically.
- Affected evidence components are bounded at 4,096 revisions. Very large legitimate histories may be refused rather than indexed without bounds; no automatic splitting or truncation is performed.
- `decode_packet` writes local evidence metadata; the future read-only conflicts listing must not call this mutating adapter. Loading retained bytes or origin witnesses does not reauthorize their source packets. Gate 3 must re-decode every dependency under current trust before using the gate-1 proof validator or applying rows.
- Gate-2 row validation checks schema/key/type shape, not a prospective SQL transaction's FK/unique/cascade effects. Gate 3 must preserve full rollback and refuse relational repairs.
- New packets deliberately remain pending, including fixtures representing future commits; application, supersession writes, diagnostic drain/recovery, CLI choices, join activation changes, and worker restart remain later gates.
- Native-platform skips and the known consumer-export failures are not fixed or waived beyond the previously approved baseline.

Gate-2 acceptance is met under the approved baseline: 56 additional tests pass; all three failure identities are unchanged. The final focused run contains the 166 gate-2/legacy/package tests and all 94 gate-1 tests. The unchanged failures are `ConsumerTests.test_export_complete_collision_safe_manual_notes_and_source_anchors`, `ConsumerTests.test_export_infers_transcript_tasks_even_without_structured_task_ref`, and `ConsumerTests.test_export_resolution_of_last_active_item_clears_current_memory` in `test_sqlite_consumers.py`; all still report `ValueError: symlink export root refused`. Both full runs have the same duplicate ZIP-entry warning. Gate 3 remains **unstarted**.


## Gate-3 execution evidence (2026-10-12)

Gate 3 implements atomic same-lineage application and conditional convergence. **Gate 4 is not started.** Production capture still emits unsigned-v1/signed-v2; their readers remain. No CLI authoring/activation command, worker lifecycle change, join change, or network operation was added. No git writes were performed.

### Files and gate-transition deviations

Created `memory-sync/resolution_apply.py`, `memory-sync/test_resolution_apply.py`, and `memory-sync/test_resolution_e2e.py`.

Modified all planned gate-3 files: `memory-sync/resolution_store.py`, `memory-sync/memory_sync.py`, `memory-sync/signed_packets.py`, `memory-sync/test_signed_packets.py`, `memory-sync/build_package.py`, `memory-sync/standalone_build.py`, `memory-sync/test_package.py`, and `memory-sync/test_standalone_build.py`, plus this plan.

Narrow additions to the planned file list:

- `test_packet_versions.py`: replaces gate 2's temporary “all new packets stay pending” assertions with ordinary-change application and missing-resolution-proof pending assertions. Its uncommitted revocation fixture now has missing resolution evidence, so it remains genuinely uncommitted after gate 3.
- `test_resolution_store.py`: extends migration fault coverage to the two new tables and checks the current schema version.
- `test_resolution_protocol.py`: replaces gate 2's temporary prohibition of the apply module/import with a prohibition on CLI authoring/activation wiring; legacy wire assertions remain.
- `packet_versions.py` and `resolution_protocol.py`: docstring updates only. Their executable adapters and pure gate-1 contract are unchanged.

Schema advances from 1 to 2. Alongside the planned `_sync_resolution_operations(operation_id PRIMARY KEY, plan_digest, packet_uuid)`, it adds `_sync_packet_paths(path PRIMARY KEY, packet_uuid, envelope_digest)`. This extra identity binding is necessary because the legacy diagnostics table stores only a filesystem path: filename suffix matching cannot safely identify which diagnostic a resolution may clear. Both additions and the version update are atomic. Existing schema-1 evidence, origins, receipts and dispositions are preserved. Legacy-only capture/receive/status never create resolution tables; migration remains triggered by new-format receipt or explicit test activation.

### Application and authorization boundaries

`prepare_resolution(c, source_refs, choices, *, security)` produces a frozen `ResolutionPlan` containing canonical payload bytes, complete transitive packet/key coverage, expected actual/shadow/config/activation state digest, schema version and a trust digest binding both pinned trust and the local signing identity. Choices must supply a full row or tombstone for every covered key; there is no default winner. The gate-1 validator checks closure and exact maximal frontiers.

`commit_resolution(c, plan, *, security, operation_id)` revalidates the plan, then commits the new immutable outbox envelope, application rows, shadows, materialized history, evidence DAG, exact superseded dispositions, receipt/envelope pin and operation record in one savepoint transaction. Savepoints never commit an existing caller transaction. Reusing an operation with the same plan returns the original packet/UUID; a different plan is refused. Dirty actual rows or stale shadow/config/trust fail closed. FK, unique and off-packet cascade/trigger effects roll back with the fixed `unsupported relational repair` error. Importing flags roll back too.

`receive_resolution` retains authenticated evidence before the nested application transaction. `apply_resolution` reauthorizes the complete bounded proof, checks origins and current heads, and returns `ApplyResult(status, packet_uuid)` with `applied`, `superseded` or `noop`. It preserves proved descendants and rejects incomparable heads without applying companion prefixes. Retained evidence is never an authorization cache. Exact committed receipt/disposition replay is the only revocation exception; complete-envelope changes remain invalid. Trust is checked again before completing a row transaction to catch changes during application. This narrows the existing filesystem-trust/SQLite race; it is not a claim of a cross-filesystem atomic trust transaction.

The receive adapter retains legacy evidence only once the resolution schema exists. The first migration retries earlier exchange files so already-applied or held legacy packets can supply proof. Subsequent progress retries held pending/conflict paths. It clears diagnostics through exact path/envelope bindings in the same commit as disposition/receipt writes; unrelated and differently bound diagnostics survive. A later legacy edit descending from a superseded branch is classified as conflict, without adding fake applied history or receipts for the original held packet. Status exposes a separate distinct-packet `superseded` count and preserves the meaning of `received`.

### Emission remains fenced

The only authoring capability in gate 3 is a database record inserted by test fixtures: schema 2, `emission_enabled=1`, and `activation_manifest_digest='gate3-test-only'`. Production contains a read-only predicate for this record; no production entry point creates it. There is no environment bypass or force flag. The private v3 signer, signed backlog guard and publish guard all require this record. Public `Security.sign` still refuses new formats, and capture's implementation and emitted formats are unchanged. Gate 4 must replace this temporary test sentinel with the approved operator inventory/capability activation protocol before exposing authoring.

### TDD and verification

All pytest runs used the supplied interpreter:

```text
/private/tmp/claude-501/-Users-krisanathep-Workspaces-AgentMesh--claude-worktrees-gitbook-agent-mesh-usage-1839e8/726c4aa1-e480-4547-b773-90d2bb8f75d5/scratchpad/testvenv/bin/python
```

| Run | Actual result |
|---|---|
| Full baseline before gate-3 edits | 900 passed, 3 known failures, 14 skipped; 20 subtests passed; 134.82s |
| Listed new apply/e2e tests before implementation | 51 setup errors, all `ModuleNotFoundError: resolution_apply`; 0.68s |
| First application run | 48 passed, 3 failed; 14.85s |
| Expanded run after initial fixes | 283 passed; 36.29s |
| Later-edit branch classification red test | 1 failed; known superseded predecessor incorrectly left pending; 0.51s |
| Expanded focused/package run | 310 passed; 46.25s |
| Mid-transaction revocation red test | 1 failed; trust change during row writes was not rechecked; 1.45s |
| Intermediate full regression | 982 passed, same 3 failures, 14 skipped; 20 subtests passed; 171.76s |
| Final focused verification | 312 passed; 67.09s |
| Final full regression | 983 passed, same 3 failures, 14 skipped; 20 subtests passed; 189.53s |

All twelve named gate-3 scenarios were written before implementation. Initial failures were missing-module failures, not import/environment failures in existing modules. The first implementation failures exposed a late evidence-status update and two fixture mistakes (a trigger deleted an on-packet row subsequently restored by the valid vector, and a simulated schema-1 database retained a schema-2 table). Those fixtures were corrected to target an off-packet row and a real schema-1 shape. Follow-up red tests independently exposed the superseded-branch classification and mid-transaction trust race, then passed after their fixes.

Focused command:

```sh
"$TEST_PY" -m pytest -q memory-sync/test_resolution_apply.py memory-sync/test_resolution_e2e.py memory-sync/test_signed_packets.py memory-sync/test_packet_versions.py memory-sync/test_resolution_store.py memory-sync/test_resolution_protocol.py memory-sync/test_resolution_model.py memory-sync/test_package.py memory-sync/test_standalone_build.py
```

Full command: `"$TEST_PY" -m pytest -q memory-sync`. Local logs: `$TMPDIR/conflict-g3-{baseline,red,first,focus1,focus2,branch-red,focused,trust-red,full,focused-final,focused-verified,full-final}.log`.

The final suites include 40 reproducible randomized SQLite-versus-independent-model seeds. Histories vary keys, packet companions, predecessor chains, forks, tombstones, choices, ordering and duplicates. Expected final heads and rows come from the gate-1 test-owned least-fixed-point oracle, not SQL application code. Tests also cover two independent resolutions followed by a terminal choice, a third peer missing both branches, offline divergent edits, later descendants, exact matching diagnostic cleanup, legacy replay with no capture echoes, and one initial row scan with no per-retained-packet replay scans.

Fault coverage uses real SQLite: seven injected local transaction boundaries, five receiver boundaries, and eight subprocess hard exits (including after successful commit). Reopening the database verifies rollback/durability and idempotent operation retry. A separate subprocess exits after publishing the immutable resolution file but before marking the outbox published; retry preserves identical bytes and UUID. These tests exercise process crashes, not hardware power-loss certification. Real Ed25519 keys/signatures cover three peers, revocation of retained uncommitted dependencies, exact committed replay after revocation, signature-byte tampering, stale trust plans and revocation during row application.

The unchanged baseline failures are the same three `ConsumerTests` in `memory-sync/test_sqlite_consumers.py`: `test_export_complete_collision_safe_manual_notes_and_source_anchors`, `test_export_infers_transcript_tasks_even_without_structured_task_ref`, and `test_export_resolution_of_last_active_item_clears_current_memory`. They report `ValueError: symlink export root refused`; none was changed or deselected. The existing duplicate ZIP-entry warning and native-platform skips remain.

### Risks and gate-4 handoff

- Original baseline payloads lost before lazy migration cannot be invented from later rows. Missing witnesses remain pending/refused; a different baseline is invalid. This is deliberately not a cross-lineage repair mechanism.
- Proof and closure limits still reject large legitimate histories rather than truncate or split atomic resolutions. Evidence, outbox and dispositions are retained; no garbage collection or large-history performance claim is added.
- Exact replay has a receipt/disposition fast path. Preparing/applying a new resolution still scans application rows for stale edits and off-packet side effects, and reauthorizes referenced immutable evidence; this is required safety work, not a background merge policy.
- Test activation is not production rollout approval. CLI choices/confirmation, peer inventory, managed downgrade fences, join replay integration and stopped-worker recovery remain gate 4. Workers are not automatically restarted.
- Trust files and SQLite have no shared transaction coordinator. Final digest checks detect tested mid-transaction changes but do not make a filesystem revocation concurrent with the final SQLite commit globally atomic.
- No native Windows build/run or physical power-loss test was performed. Existing approved baseline failures remain environmental limitations, not new passing checks.

Gate-3 acceptance is met under the approved baseline: 83 additional tests pass, all three failure identities are unchanged, and emission is still unavailable through production activation/authoring entry points. Gate 4 remains **unstarted**.


## Gate-4 execution evidence (2026-10-12)

Gate 4 implements the local CLI, real operator activation, worker recovery,
unsigned join compatibility, capability fences and packaging. No network or git
writes were performed. All activations and worker exercises were on disposable
test databases; no operational group was activated or deployed.

### Files

Created `memory-sync/conflict_commands.py`, `memory-sync/protocol_activation.py`,
`memory-sync/test_conflict_commands.py`, and
`memory-sync/test_protocol_activation.py`.

Modified every planned gate-4 file: `agentmesh.py`, `worker_lifecycle.py`,
`install_join.py`, `install_inspect.py`, `windows_install.py`, `mac_replace.py`,
`resolution_apply.py`, `memory_sync.py`, `test_install_join.py`,
`test_worker_lifecycle.py`, `test_agentmesh.py`, `test_windows_install.py`,
`test_mac_replace.py`, `SYNC.md`, `SIGNED-SYNC.md`, `build_package.py`,
`standalone_build.py`, `test_package.py`, and `test_standalone_build.py`, all under
`memory-sync/`, plus this plan.

Additional files required by the reviewed gate transition:

- `resolution_store.py`: schema 3 persists the real activation manifest and
  minimum capability; removes the test-sentinel predicate entirely. Schema-1/2
  upgrades and new schema creation stay transactional and lazy.
- `signed_packets.py`: the v3 signer and signed backlog guard now check the real
  activation record, with no test-only branch or alternate environment flag.
- `test_resolution_apply.py` and `test_signed_packets.py`: earlier-gate fixtures
  now activate through the public operator API. The descendant test now receives
  the legitimately new-format post-activation capture through the new reader;
  it no longer incorrectly feeds it to the legacy-only validator.
- `cli_errors.py`: fixed, redacted protocol-command reasons and guidance. Without
  this addition the existing CLI error projector returned only an exception class
  for the new commands, making refusal and stale-plan recovery opaque.

### Implementation decisions and review-note evidence

**Real activation, no production test bypass.** `activation_plan(runtime,
peer_inventory)` writes an owner-only local proposal. `activate(runtime,
plan_digest, confirm_word='ACTIVATE')` requires its exact digest, confirms the
complete inventory and legacy-worker drain acknowledgment, rechecks observed and
reserved slots, runtime/path/trust bindings and authoritative DB scope, and commits
the real inventory/minimum-capability/digest record. The inventory includes each
node's declared software, capability and offline status. Capability `1` denotes
this complete resolution implementation. No production module contains the old
sentinel or its predicate, and no production test activation writer exists.

The activation record adds `activation_manifest` and `minimum_capability` to
`_sync_protocol`, advancing schema 2 to 3. Its integrity and scope are checked
before emission. Unactivated capture still emits unsigned-v1/signed-v2; activated
capture emits ordinary unsigned-v2/signed-v3 with one parent (or absence root).
Previously committed outboxes retain their exact bytes. Public legacy signing
still rejects a new-format payload; the database-bound v3 signer checks activation.
The former gate-3 fixtures use public activation, not direct flag/sentinel writes.

**Strict read-only listing.** `list_conflicts` never calls capture, migration,
packet application, security guard, or verification telemetry writes. A three-attempt
stable copy of the DB/WAL pair is opened in a private temporary directory; only
that disposable copy may create a WAL index. The source database, WAL, SHM,
exchange, diagnostics and counters remain byte-identical. This deliberately
avoids treating SQLite `mode=ro` as a zero-filesystem-write guarantee. Default and
JSON reports redact rows/natural keys; explicit local content display shows both
sides, keys, revisions and parents. Listings are labeled unverified previews;
authorization is checked at execution. Continuous writes that prevent a stable
snapshot cause a retryable inspection refusal rather than a guessed snapshot.

**Concrete private plans and confirmations.** `preview_resolution` indexes proof
only on a disposable copy, includes selected source packets, local heads and
transitive companions, and supports `mine`, `theirs`, explicit `row` overrides or
`delete`. Non-conflicting companions retain their maximal values. It rejects
uncaptured edits and rehearses the complete SQL transaction on the copy, refusing
unsupported relational repair before confirmation. A private plan under
`runtime.json`'s sibling `.agentmesh-plans/` contains full chosen rows and immutable
refs; its digest binds both the core `ResolutionPlan` and runtime/path/trust state.
This local artifact is the concrete mechanism for the plan's cross-process
`resolve(runtime, plan_digest, ...)` API; it is never exchanged or put in status.
Reports are JSON records, consistent with the existing CLI.

Execution checks typed `RESOLVE`, the exact plan digest and a stable operation ID.
Wrong confirmation/EOF applies nothing. An already-stale plan is refused before
worker control files are created or a worker is stopped; the locked transaction
then revalidates again. Repeated operation/plan pairs recover the same committed
packet and retry publication. Source/evidence envelope changes fail revalidation.

**Worker integration.** Recovery cooperatively stops only owned running execution,
then holds the lifetime lock and the existing DB-directory cycle lock. Receive-only
captures local edits and receives without ingestion, summarization or publication.
Resolution commits, publishes and receives while holding those locks. Neither
operation restarts a worker or overwrites existing failed-worker metadata. Reports
request diagnostic review while anything remains pending/conflicted/invalid or
unavailable, publication retry if needed, and otherwise an explicit `worker-start`.
Unrelated worker errors still require operator review; an empty sync diagnostic
count is not proof that ingestion or summarization is healthy.

**Legacy interoperation and race fixes discovered in this gate.** Explicit real
unsigned and Ed25519 tests exchange both directions between a legacy-only DB and
an unactivated DB that has the resolution store; their captures remain legacy and
the legacy-only DB remains unmigrated. The indexed receiver now also captures an
intervening local edit under the incoming packet's write lock before applying an
unseen packet. A new red test showed that gate 3's indexed legacy path could
otherwise overwrite a local edit committed after receive's initial capture. Exact
committed/own replays retain the no-per-packet-row-scan fast path. Another red test
showed new-format capture's own outbox replay being misclassified as a conflict;
committed local ordinary captures now return no-op, while local resolutions still
run their atomic commit path.

Exchange indexing binds paths to exact envelope digests, including diagnostics
created before activation. A crash immediately after resolution commit but before
publication now leaves covered diagnostics cleared atomically and unrelated
failures intact; a follow-up receive is not required to finish that cleanup.

**Join and managed capability fences.** `observed_slots` validates both unsigned
formats, checks every delivered source packet's scalar/array parents and origin,
and therefore inspects ancestors even when their packets were superseded. It
refuses non-empty baseline ancestry. Missing ancestor/evidence packets stay pending
through receive; the pending-install guard is removed only after replay settles.
Signed namespace/invitation refusal, durable slot reservations and replay-only
installation remain intact. Tests replay resolved histories from empty, with and
without required sources, and verify guard retention.

The new read-only `protocol-capability` entry point allows Windows program
selection/rollback and Mac replacement/restore to check the actual chosen
executable before it can run against an activated database. Legacy source workers
are rejected without executing their worker script. Capability checks repeat
under installer revalidation; worker run/start and installation inspection refuse
unsupported DB capability. Installer rehearsal tests prove refusal before program,
service or task changes. This uses an executable capability response rather than
changing the existing strict `BUILD.json` format, preserving old package metadata
compatibility. It is still an operator-trusted executable check, not remote
attestation or a retroactive fence in unmanaged old binaries.

### TDD and local verification

All pytest commands used the supplied interpreter:

```text
/private/tmp/claude-501/-Users-krisanathep-Workspaces-AgentMesh--claude-worktrees-gitbook-agent-mesh-usage-1839e8/726c4aa1-e480-4547-b773-90d2bb8f75d5/scratchpad/testvenv/bin/python
```

| Run | Actual result |
|---|---|
| Full baseline before gate-4 edits | 983 passed, same 3 failures, 14 skipped; 20 subtests passed; 179.06s |
| Listed command/activation/join tests before implementation | 4 failed, 45 passed, 16 setup errors; 12.05s |
| First command/activation/join implementation run | 65 passed; 14.70s |
| Managed-program capability red tests | 2 failed; 4.09s |
| Expanded focused suites | 502 passed, 4 skipped; 77.06s |
| Stale-plan pre-lock refusal red test | 1 failed; 1.25s |
| Expanded interoperation/WAL suites | 506 passed, 4 skipped; 84.25s |
| Fixed actionable CLI errors red test | 1 failed; 0.05s |
| Indexed legacy interleaving red test | 1 failed; 2.53s |
| Activated own-packet replay red test | 1 failed; 0.27s |
| Command/crypto/installer boundary run | 266 passed, 4 skipped; 57.16s |
| Pre-activation diagnostic atomic-cleanup red test | 1 failed; 0.46s |
| Preview uncaptured-edit/SQL-rehearsal red test | 1 failed; 1.84s |
| Intermediate full regression | 1020 passed, same 3 failures, 14 skipped; 20 subtests passed; 171.35s |
| Expanded focused run | 515 passed, 4 skipped; 81.03s |
| Pending-recovery guidance red test | 1 failed; 0.43s |
| Final focused verification | 516 passed, 4 skipped; 88.21s |
| Final full verification | 1022 passed, same 3 failures, 14 skipped; 20 subtests passed; 181.67s |

The initial failures were the absent `protocol_activation`/`conflict_commands`
modules and the old join reader rejecting new-format history before the intended
baseline check. The existing signed-invitation refusal already passed. All later
red tests failed for their named behavioral assertions, then passed after the
corresponding implementation change. One recovery-guidance fixture initially
claimed the receiver's own node; it was corrected to a remote node and rerun red
before changing the guidance code.

Final focused command (gate-4 files, all earlier protocol/application regressions,
and packaging):

```sh
"$TEST_PY" -m pytest -q memory-sync/test_conflict_commands.py memory-sync/test_protocol_activation.py memory-sync/test_install_join.py memory-sync/test_worker_lifecycle.py memory-sync/test_agentmesh.py memory-sync/test_windows_install.py memory-sync/test_mac_replace.py memory-sync/test_resolution_apply.py memory-sync/test_resolution_e2e.py memory-sync/test_signed_packets.py memory-sync/test_packet_versions.py memory-sync/test_resolution_store.py memory-sync/test_resolution_protocol.py memory-sync/test_resolution_model.py memory-sync/test_package.py memory-sync/test_standalone_build.py
```

Full command: `"$TEST_PY" -m pytest -q memory-sync`. Also ran the real source CLI
`agentmesh.py protocol-capability` (capability 1) and `agentmesh.py --help` locally.
Packaging tests validate source ZIP membership and standalone hidden imports;
no dependencies were downloaded. Local evidence logs are under
`$TMPDIR/conflict-g4-{baseline,red,first,fence-red,focused1,stale-red,focused2,errors-red,interleaving-red,self-red,boundaries,diagnostics-red,preview-red,guidance-red,focused-final,focused-verified,full1,full-final,cli-help}.log`.

All three baseline failures remain in `test_sqlite_consumers.py::ConsumerTests`:
`test_export_complete_collision_safe_manual_notes_and_source_anchors`,
`test_export_infers_transcript_tasks_even_without_structured_task_ref`, and
`test_export_resolution_of_last_active_item_clears_current_memory`. Each reports
`ValueError: symlink export root refused`. No failure was deselected or changed.
The duplicate ZIP-entry warning and existing native/platform skips remain.

### Native CI still required

Per the current task, these native entry points were **not run here**. Provision
private disposable `RUNNER_TEMP`, use an already-installed native interpreter and
build dependencies, and run without downloads:

```sh
"$TEST_PY" memory-sync/standalone_build.py --dist "$RUNNER_TEMP/agentmesh-dist" --work "$RUNNER_TEMP/agentmesh-build"
AGENTMESH_DIST="$RUNNER_TEMP/agentmesh-dist" "$TEST_PY" memory-sync/smoke_worker_lifetime.py
# Windows only; set AGENTMESH_NATIVE_TASK_FIXTURE_ROOT to a NEW disposable directory.
"$TEST_PY" memory-sync/smoke_windows_install.py
```

Use native Windows environment-assignment syntax on that runner. Frozen CLI
capability/command loading, real worker lifetime, Windows task/ACL behavior and
platform installation/rollback smoke evidence remain unverified locally. No claim
is made about actual host upgrade, login/autostart, signing/notarization or release
readiness from the mocked OS-boundary installer tests.

### Risks and completion boundary

- Private preview artifacts contain memory rows and must remain outside exchange;
  retain the selected artifact for durable operation retry. No artifact/evidence
  garbage collector was added.
- Inventory completeness and drained unmanaged workers are operator assertions.
  Observed/reserved slots are checked, but undiscovered offline devices and old
  binaries bypassing managed entry points cannot be fenced retroactively.
- Missing original baseline witnesses and oversized closure/ancestry proofs still
  fail closed. Relational repairs, ID remapping and automatic LWW remain unsupported.
- Signed authorization is rechecked, but filesystem trust and SQLite do not form
  one distributed atomic transaction. Packet signatures do not encrypt content.
- Stable read-only snapshots may require retry during sustained writes. Resolution
  preview rehearses SQL privately; the live transaction must still revalidate
  rows, heads, scope, trust and constraints after confirmation.
- The three environment-specific export failures and native CI listed above remain
  explicit verification limits. No operational activation or deployment was done.

Gate-4 implementation and local acceptance are complete under the approved baseline: 39 additional tests pass, and the three failure identities are unchanged. Native CI acceptance remains pending the explicit commands above. Work stops here for review; no operational activation or deployment was performed.

## Gate-4 merged-WIP resume audit (2026-10-13)

This section supersedes the earlier local test totals for the merged tree only
when new executions are recorded below. PR #26 and PR #33 introduced protected
keys, `Security.sign_bytes`, OS unlock and frozen-build hooks after the earlier
Gate-4 run. Earlier results are historical evidence, not validation of this merge.

Initial audit:

- `_sign_resolution` now calls `self.sign_bytes(...)` with the v3 domain. This is
  the correct merge resolution: `require_unlocked` supplies the fixed protected-key
  refusal rather than dereferencing a missing key. Public legacy signing and v3
  activation guards remain separate.
- `cli_errors.py` retains both protocol reasons and `KEY_REASONS`, including OS
  refusal reasons; `resolve` is included in the protected error-reporting actions.
- No `gate3-test-only` or `test_activation` remains in production Python modules.
  Real inventory-backed activation is the sole supported emission switch.
- `memory_sync.receive` routes every packet through `receive_indexed` when the
  resolution store exists, and preserves the old path for legacy-only databases.
  The signed/unsigned preactivation interoperability regression remains present.
- Listing uses a disposable DB/WAL snapshot, performs structural checks only,
  and does not capture, migrate, authorize signatures or write telemetry. The
  runtime binding loads public identity data without requesting OS unlock.
- `install_join.observed_slots` recognizes both unsigned formats and refuses
  baseline parents/origins. Signed invitations remain refused and incomplete
  replay retains `.setup-pending.json`.
- Source `FILES` and frozen hidden imports contain `conflict_commands`,
  `protocol_activation`, `key_protection` and `os_unlock`. The merged OS smoke
  runtime-hook support is retained; ordinary builds do not inject a test hook.
- Integration gap to test before changing implementation: authoring/recovery
  creates `Security` without opting into OS unlock. A properly enrolled protected
  identity therefore still appears locked to the new commands.

The exact requested interpreter was attempted for the full baseline on this
resume, but its path no longer exists (`exit 127`). A replacement path has been
requested. No baseline count, red/green result or completion is inferred from
that failed launch. New tests have been written before any production changes.


### Resume implementation and TDD evidence (2026-10-13)

The maintainer supplied `/Users/krisanathep/.cache/agentmesh-testvenv/bin/python`;
the top-level `TEST_PY` reference now uses it. Every test invocation in this resume
uses that interpreter (pytest 9.1.1, cryptography 50.0.2). Historical interpreter
paths and results above are intentionally preserved as historical evidence.

Changes relative to the merged WIP:

- `conflict_commands.py`: signed preview/preflight uses
  `Security(..., os_unlock=True).require_unlocked()`, exactly the worker's #33
  path. No prompt, passphrase argument, alternative key store or new unlock
  mechanism was introduced.
- `worker_lifecycle.py`: common recovery for resolve, receive-only and activation
  checks unlock before worker stop/control-file creation. The initial scope check
  is non-authoritative/read-only; scope is revalidated authoritatively before
  stopping and again under the locks. Current self-trust is checked under lock.
  This ordering also avoids creating a missing SQLite WAL index on locked refusal.
- `test_conflict_commands.py`: adds six real-SQLite/real-crypto cases (one test is
  parameterized over receive-only/activation): direct locked signing and atomic
  authoring refusal with an unlocked positive control; CLI preview's exact locked
  reason and no writes; OS-enrolled resolution with failed-unlock confirmed retry;
  locked signed CLI listing with no OS access; and locked/unlocked recovery and
  activation. OS stores are disposable fake backends from #33, not host credentials.
- `test_package.py`, `test_standalone_build.py`: explicit joint assertions for
  Gate-4 modules plus key_protection/os_unlock; a new test inspects both the ordinary
  hidden-import arguments and Windows spec. Existing merged packaging already
  satisfied these tests. Normal builds retain no injected OS test hook.
- `test_key_protection.py`: existing merged restart test now sets its child Python
  process's cwd to the module directory. The repository-root pytest command
  otherwise failed with ModuleNotFoundError for signed_packets. No production
  import behavior or failure policy changed.
- `SYNC.md`, `SIGNED-SYNC.md`: protected recovery, standard locked/OS reasons,
  read-only inspection, explicit OS enrollment, fresh-process retries after cached
  failures and preview invalidation after identity changes. This plan records the
  audit, commands and handoff.

The merged `_sign_resolution`/sign_bytes change and combined cli_errors reasons
needed no further production edit. Original signed-v2/unsigned-v1 packet handling,
activation predicates, join guards and wire formats are unchanged in this resume.
The plan deviations are #26/#33 integration, the initial recovery read-order
fix demonstrated by byte snapshots, and the two merged test-fixture corrections
described here (subprocess cwd and frozen-hook cleanup).

| Run | Actual result |
|---|---|
| Existing merged suite before production edits | 1118 passed, 4 failed, 17 skipped, 5 deselected; 20 subtests passed; 186.26s |
| New protected-key/packaging tests plus their existing files, before implementation | 37 passed, 3 failed; 6.14s |
| First fix plus protected-key suite | 60 passed, 2 failed; 15.04s |
| Unlock/read-order and subprocess corrections | 62 passed; 10.30s |

The baseline excluded only the five new test functions already present at
collection; the two additional parametrized recovery cases were written after
baseline collection. No existing test was deselected. The fourth baseline failure
was `test_key_protection.py::test_protected_identity_stays_locked_across_restarts_and_the_restarted_worker_refuses`,
whose child could not import signed_packets from the repository-root cwd. The
three approved sqlite_consumer symlink failures were also present.

The initial three red failures established that enrolled resolution preview did
not attempt OS unlock, and locked receive-only/activation incorrectly succeeded
when no new signature was needed. Direct signing/commit refusal and packaging
were already correct after the merge and passed without artificial breakage.
The next two failures showed new DB `-wal`/`-shm` files on locked refusal; the
read-order fix made the complete filesystem snapshots equal. The baseline child
import failure also passed after the explicit cwd fix.

Focused command extends the earlier 16-file Gate-4 regression command with
`memory-sync/test_key_protection.py memory-sync/test_os_unlock.py`. Final full
command is `"$TEST_PY" -m pytest -q memory-sync`, with no exclusions. Logs for this
resume are under `$TMPDIR/conflict-g4-resume/{baseline,red,green,green2,focused,full}.log`.

### Additional native CI handoff after the merge

The three native entry points listed above remain unrun: standalone_build.py,
smoke_worker_lifetime.py and Windows smoke_windows_install.py. Also run the merged
#33 credential/frozen smoke on supported Mac/Windows CI with its explicit opt-in
and private disposable RUNNER_TEMP:

```sh
AGENTMESH_NATIVE_KEYCHAIN_TESTS=1 "$TEST_PY" memory-sync/smoke_os_unlock.py
```

Use Windows environment syntax on Windows. This script builds a separate test
binary with a disposable-store hook; never distribute that binary as a release.
Normal builds have no such hook. Native frozen recovery commands with an enrolled
protected key still require OS-level validation; local tests use real Ed25519 and
AES/KDF operations with fake OS storage. No native credentials or live runtime
were accessed here, and no group was operationally activated.


The expanded focused ordering also exposed a second merged test-fixture defect:
`test_frozen_smoke_hook_binds_only_the_disposable_store` restored its backend with
`monkeypatch.setattr` in `finally`, which registered the *temporary* backend as the
teardown value and reinstated it after the test. Consequently
`test_os_unlock.py::test_current_backend_by_platform` saw a Windows fixture on
Linux. The first expanded run was 619 passed, 1 failed, 4 skipped (101.33s).
The hook test now restores the original function directly in `finally`; this is
an additional test-isolation deviation, with no production hook changes. The
ordered standalone-build + OS-unlock regression is recorded in `hook-order.log`.


Final expanded focused run: **620 passed, 4 skipped**, 105.86s, recorded in
`focused-final.log`. The standalone-build/OS-unlock ordering check was **88 passed**,
12.39s (`hook-order.log`). The real source CLI returned protocol capability 1;
`--help` exposes conflicts, resolve, receive-only, activate-protocol,
protocol-capability and identity-os-unlock (`cli-help.log`).

Resume risks remain bounded by the original design: operator inventory/drain
assertions cannot fence unmanaged old binaries; missing/oversized ancestry is
refused; private previews contain row content; filesystem trust and SQLite are
not one atomic store. The existing #33 helper caches unlock success/refusal per
process, so fixing an OS-store problem requires a new command process, and an
already-unlocked process retains its in-memory key until exit. These commands do
not add an interactive passphrase fallback. Read-only listing remains available
without unlock. Native OS/frozen behavior is not established by the local fake
backend tests.


### Final merged-tree acceptance

The exact full command `"$TEST_PY" -m pytest -q memory-sync` completed with
**1126 passed, 3 failed, 17 skipped; 20 subtests passed**, 188.36s. The only failures
are the same three `test_sqlite_consumers.py::ConsumerTests` symlink-export cases
listed above, each reporting `ValueError: symlink export root refused`. No tests
were deselected in this final full run. The existing duplicate ZIP warning remains.
Compared with the initial merged baseline, seven new cases pass and the existing
protected-key child-import failure is fixed; the frozen-hook isolation failure
exposed by focused ordering is also fixed. Final focused, full and scope evidence
are in `focused-final.log`, `full.log` and the local acceptance audit.

Nine files differ from the merged WIP: this plan, SYNC.md, SIGNED-SYNC.md,
conflict_commands.py, worker_lifecycle.py, test_conflict_commands.py,
test_key_protection.py, test_package.py and test_standalone_build.py. Only the two
recovery modules changed production behavior. Packet schemas, legacy readers,
activation records, signed wire/sign_bytes integration, join logic and packaging
module manifests remain unchanged from the audited merged WIP. Source capability
and help commands ran; `git diff --check` is clean; no staged changes or new
untracked repository files were introduced.

Gate 4 is complete for local review under the approved three-failure baseline.
The four local acceptance checks cover scope/invariants, red/green and regression
evidence, unlock/doc review, and explicit native handoff. Native CI remains pending
the commands above; this is not a claim of native-release acceptance. No git writes,
network, live-key operations, deployment or operational group activation occurred.
Work stops after Gate 4 for maintainer review.
