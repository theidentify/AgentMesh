# Conflict resolution implementation plan

2026-10-12; approved design: `../specs/2026-10-12-conflict-resolution-design.md`.
Checkpoint policy: implement and review each gate separately. This change implements **gate 1 only**. No git writes, no network. No emission until explicit gate-4 group activation. Retain unsigned-v1 and signed-v2 readers permanently, including after activation.

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
TEST_PY=/private/tmp/claude-501/-Users-krisanathep-Workspaces-AgentMesh--claude-worktrees-gitbook-agent-mesh-usage-1839e8/726c4aa1-e480-4547-b773-90d2bb8f75d5/scratchpad/testvenv/bin/python
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

## Gate 3 — atomic same-lineage application and convergence (NOT started)

### Exact files

Create `memory-sync/resolution_apply.py`, `memory-sync/test_resolution_apply.py`, `memory-sync/test_resolution_e2e.py`. Modify `memory-sync/resolution_store.py`, `memory-sync/memory_sync.py`, `memory-sync/signed_packets.py`, `memory-sync/test_signed_packets.py`, `memory-sync/build_package.py`, `memory-sync/standalone_build.py`, `memory-sync/test_package.py`, `memory-sync/test_standalone_build.py` for transactional dispatch, dispositions and packaging of the newly reachable apply module. No user emission command yet.

### Functions and transaction boundary

`prepare_resolution(c, source_refs, choices, *, security) -> ResolutionPlan`; `apply_resolution(c, packet, *, security) -> ApplyResult`; `commit_resolution(c, plan, *, security, operation_id) -> Packet`; `receive_resolution(c, envelope, *, security) -> ApplyResult`. Require activation for authoring; tests set up an explicit test activation record, never introduce a production bypass flag.

`ResolutionPlan` binds immutable source refs, complete key set, expected actual/shadow state, origins, schema version and trust digest. `ApplyResult` separates applied/superseded/no-op. Add `_sync_resolution_operations(operation_id PRIMARY KEY,plan_digest,packet_uuid)` for idempotent CLI retries. Every local commit writes rows, shadow, DAG, dispositions, outbox and operation record together. Receiver writes receipt/envelope pin atomically. Import flags, FK checks, off-packet cascade checks remain mandatory. Natural-key/unique/FK/cascade repair requirements return a fixed unsupported-repair error and roll back; do not auto-expand to repair plans.

Receive indexes/retains evidence before attempting resolution so order does not force intermediate conflicting applications. Retry affected pending/conflict paths after resolution progress; use exact dispositions to avoid recreating stale diagnostics. Preserve newer proved descendants. Existing status gains separate superseded counts without redefining received as applied+superseded.

### Tests first / commands / exit criteria

Write `test_two_peers_companions_exactly_once`, `test_three_peers_resolution_before_sources`, `test_independent_resolutions_then_terminal_choice`, `test_offline_new_branch_is_not_lost`, `test_later_edit_precedes_resolution_delivery`, `test_transaction_faults_leave_no_partial_dispositions`, `test_publish_retry_keeps_uuid`, `test_capture_before_resolve_invalidates_stale_plan`, `test_relational_repairs_refused_atomically`, `test_signed_three_peer_revocation_boundaries`, `test_late_covered_packet_does_not_restore_diagnostic`, `test_randomized_sql_matches_model`. Use real SQLite and crypto, compare rows/heads with gate-1 oracle, inject crashes around durable boundaries and publication. Verify replay does not capture echoes or rescan full rows per retained packet.

Run `"$TEST_PY" -m pytest -q memory-sync/test_resolution_apply.py memory-sync/test_resolution_e2e.py memory-sync/test_signed_packets.py`, gate-1 tests, then full suite. Acceptance: all supported same-lineage histories converge conditionally; no companion loss; all failure boundaries atomic; signed identity and revocation preserved; no CLI/network emission available and authoring fails without activation. Review before gate 4.

## Gate 4 — CLI, join, workers and activation (NOT started)

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
