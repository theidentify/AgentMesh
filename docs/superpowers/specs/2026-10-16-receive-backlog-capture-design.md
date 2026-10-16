# Receive backlog: targeted capture, interruptible cycles, stop-failure recovery (0.3.1): design

Status: approved in conversation on 2026-10-16. Builds on main b5ce417 (#47, file marks and `capture_first`).

## Incident

On 2026-10-16 the Windows node (DESKTOP-DOJ622I, rc.12) had used one CPU core continuously since every logon
from 10/12 onwards. It never completed a cycle, and `status/windows.json` was last written on 10/12.

- **Where the time goes:** three py-spy dumps show
  `run_once → cycle → receive (memory_sync.py:377) → _capture → rows()/canonical()`.
  `receive` calls `_capture` (a full scan of every synced table, diffed against `_sync_shadow`) **once for
  every unseen incoming packet**. With a backlog (809 Mac files, 16 pending) and a 199 MB DB, the first
  cycle takes hours or days. Pending packets re-scan on every pass that makes progress.
- **Why it can't stop:** the worker checks `stop.json` only between cycles. So `worker-stop` and the
  0.3.0 upgrade failed (`WORKER_STOP_TIMEOUT`).
- **Why the installer is stuck:** the failed upgrade left `installed.json` in `status=recovery_required`
  with `pending={operation: replace, phase: stop}`. Every installer command refuses a status other than
  `ready`, and `windows-recover` supports only `upgrade/start`. So no supported command can clear it,
  although nothing had changed: the old version is still active and the task is untouched.
- **Is it fixed on main?** No. #47 skips files that were already received, but keeps the per-packet
  `_capture` before `_apply`. The indexed path (`resolution_apply.receive_indexed`) has the same
  per-packet `sync._capture`.

## Changes

### 1. Targeted capture

`memory_sync._capture(c, *, security=None, keys=None)`:

- **`keys=None`:** exactly today's behaviour (full scan). `capture()` and every existing caller stay
  unchanged.
- **`keys` a set of `(table, canonical_key)`:** read **only** those rows from their tables and **only**
  those `_sync_shadow` entries, then diff them exactly as the full path does.
  - Same revision, history and outbox writes, packet format, signing, v2 emission and
    `_index_local` behaviour.
  - Rows that no longer exist count as deletions, as in the full path.
  - Unknown tables refuse with `ValueError`.
- **Row lookup by key:** match on the table's key columns (`KEYS[t]`). Build the canonical key from the
  fetched row exactly as `rows()` does, and assert it equals the requested canonical key. Factor out
  the row normalization (dict, `metadata` JSON, canonical key) so `rows()` and the targeted path share
  one function.

**Why this keeps the guarantee.** The per-packet capture exists so that a local edit committed after
receive's first capture is recorded (its shadow revision bumped) before an incoming change can
overwrite **that row**. Only rows the packet changes can be overwritten, so capturing exactly those
rows preserves the guarantee. Local edits to other rows stay uncaptured until the next full
`capture()`, at the start of the next cycle, and are published then. That is the same as an edit made
just after the cycle's capture today.

### 2. Callers in receive

- **Legacy path** (`memory_sync.receive`):
  `_capture(c, security=security, keys=_packet_keys(p))`.
- **Indexed path** (`resolution_apply.receive_indexed`):
  `sync._capture(c, security=security, keys=_packet_keys(envelope))`.
- **`_packet_keys(packet)`:**
  - Returns the set of `(table, canonical(key))` for every entry in the packet body that has `table`
    and `key`.
  - For resolution packets, the body is the full resolved state of every key it writes; include
    exactly those.
  - If the body shape isn't recognized, it returns `None` and the caller falls back to a full capture.
    That is slower but correct, and must never under-capture.
  - Read the v2/v3 decoders (`packet_versions`, `resolution_protocol`) to cover every body form.

### 3. Interruptible cycles

- **Plumbing:** `sync_worker.run_once(..., interrupt=None)` → `memory_sync.cycle(..., interrupt=None)` →
  `receive(..., interrupt=None)`.
- **receive:** checks `interrupt()` before each packet file on every pass. When it returns true, receive
  stops and returns its report with `interrupted: True`, processing nothing more.
  - Each packet is its own transaction, so stopping between packets is safe.
  - Packets left unprocessed are simply retried on the next cycle.
- **run_once:** also checks `interrupt()` between its major phases (after workflow/ingest, before and
  after `cycle`).
- **worker_lifecycle.run:** passes `interrupt=lambda: stop_requested(directory, nonce)`.
  - An interrupted cycle is recorded like a completed cycle for `process.json`, with an
    `interrupted: true` note in the cycle report, not an error.
  - The existing post-loop stop handling then exits normally, so the `stop_reason` and `stop.json`
    consumption behave as today.
- **Test:** a `worker-stop` during a long receive is acknowledged within one packet.

### 4. Installer: a failed stop leaves `ready`; recover the stuck shape

- **The failure itself:** `windows_install.run`, `replace`. When `managed.stop` raises (timeout or any
  error) during `pending.phase == 'stop'`:
  - **when to roll back:** nothing has been switched yet (the active version is still `pending.old`,
    and the task binding is unchanged)
  - **what to write:** `status='ready'`, `pending=None`, keeping the staged new version in `programs`
    (exactly as staging left it)
  - **what to raise:** the original fixed error (`WORKER_STOP_TIMEOUT`)
  
  `mark_failed` must not override this outcome.
- **`windows-recover`** also accepts the shape `pending.operation=='replace' and pending.phase=='stop'`
  when all of these hold:
  - `active == pending.old`
  - `pending.new` is in `programs`
  - the live task binding equals `state['task']`
  
  After typed `RECOVER`, it sets `status='ready'` and `pending=None`, and changes nothing else. It
  doesn't start or stop any worker and doesn't touch the task. Other shapes keep refusing.
- **Today's Windows host:** its task was disabled by hand with `schtasks /DISABLE`. The operator
  re-enables it (`/ENABLE`, same binding) immediately before `windows-recover` and the upgrade, without
  signing out in between. Document this in WINDOWS-INSTALLATION.md under recovery.

## Tests

The tests use disposable DBs.

- **Reproduction** (must fail on main first):
  - a DB with several thousand rows; an exchange with a backlog of at least 50 unseen packets from
    another node, plus a pending chain
  - `receive` must complete with at most 2 full `rows()` calls
  - assert `_capture` was called with `keys`, and record the key sets
  - also run it with the resolution store migrated (indexed path)
- **Correctness:**
  - A local uncaptured edit to a row a packet changes is captured first, giving the same
    conflict/pending outcome as full capture.
  - A local uncaptured edit to a **different** row is not captured during receive, then is captured and
    published by the next `capture()`.
  - A deletion and a new row through targeted capture.
  - An unknown body shape falls back to full capture.
  - Signed and v2-emission captures through the targeted path produce valid packets.
- **Interrupt:**
  - `receive` stops before the next packet when `interrupt()` turns true; the report has
    `interrupted`; the remaining packets apply on the next run.
  - A worker-lifecycle test: a stop request during a long receive is honored, and the worker exits with
    a normal stop.
- **Installer:**
  - A replace whose stop times out leaves `status='ready'`, `pending=None`, the old version active and
    the task unchanged, and still raises `WORKER_STOP_TIMEOUT`.
  - `windows-recover` on a hand-crafted `replace/stop` recovery_required state returns it to `ready`.
  - Other shapes still refuse.
- **Keep #47's tests green:** `test_cycle_scans_unchanged_tables_once` and
  `test_replays_do_not_rescan_full_memory_per_retained_file`.
- **Performance evidence:** a scripted benchmark on a disposable DB of comparable size (generated, not
  real data), with before and after `receive` CPU time for a 200-packet backlog, recorded in the report.

## Amendment 1 (2026-10-16, binding): application verification by changed-row tracking

Codex's Task 1 stopped with a finding that holds. Application also does a full-database scan per
packet:
- `memory_sync._apply` compares `rows(c)` against the whole shadow plus the packet, after writing.
- `resolution_apply._write` compares the whole DB before writing (`uncaptured local edits`) and after.

Those whole-DB comparisons have two consequences:
- **Cost:** they keep the per-packet cost proportional to the DB.
- **Correctness conflict:** they **reject any unrelated uncaptured local edit**, which conflicts with
  targeted capture.

Replace them, in both `_apply` and `_write`, with **exact changed-row tracking**.

1. **Tracking.** Once per connection, create TEMP triggers
   `AFTER INSERT/UPDATE/DELETE ON <t>` for every synced table in `TABLES`. Each trigger inserts
   `(table, canonical key)` into a TEMP table `_sync_apply_touched`, using `NEW`/`OLD` key columns per
   `KEYS[t]`.
   - **Canonical key in SQL:** build it so it equals `canonical([...])`. If that isn't reliable in SQL,
     record the key columns as `json_array(...)` and canonicalize in Python.
   - **Scope:** the triggers fire for FK `ON DELETE CASCADE` / `SET NULL` actions and for other triggers'
     writes (SQLite runs FK actions as triggers). Confirm this with a test using the real schema's
     cascades, and with a test trigger.
   - **REPLACE:** where REPLACE conflict resolution can delete rows, make sure `recursive_triggers` is
     enabled for the apply, or prove that REPLACE is never used on synced tables.
   - **Lifecycle:** clear `_sync_apply_touched` right before a packet's writes, and read it right after.
2. **Check after the writes:**
   - (a) Every touched `(table, key)` must be in the packet's own key set. Anything else raises
     `Conflict('off-packet cascade or trigger changed a concurrent row')`, the same reason as today.
   - (b) For every packet key, the live row read **by key** must equal the packet's row (or be absent
     for a deletion).
   - (c) `PRAGMA foreign_key_check` stays as it is, with the same pending/conflict classification.
3. **`_write`'s pre-check** becomes key-scoped: for the keys the resolution or change will write, the
   live row must equal the shadow row. That holds right after the targeted capture. Unrelated
   uncaptured edits elsewhere no longer reject the packet; they are captured and published by the
   next cycle's capture. `_write`'s post-check uses the same tracking (a)–(c) as `_apply`.
4. **Equivalence.** Anything the old whole-DB comparison rejected because of the packet's own effects
   is still rejected:
   - off-packet changes are caught by tracking
   - wrong on-packet values are caught by the keyed comparison
   - FK problems are caught by `foreign_key_check`
   
   What the old check also rejected, unrelated pre-existing uncaptured edits, is intentionally no
   longer a reason to refuse. Document this in SYNC.md.
5. **Tests**, in addition to the earlier list:
   - a real-schema cascade (deleting a parent whose children aren't in the packet) is detected as off-
     packet
   - a test-only trigger that writes another synced row is detected
   - an UPSERT path fires the tracking
   - an unrelated uncaptured local edit no longer blocks an incoming packet, and is published by the
     next `capture()`
   - the new scan bound: a backlog receive does at most 2 full `rows()` calls in total, counting
     capture **and** verification
   - all existing `_apply` / `_write` conflict tests stay green

## Amendment 2 (2026-10-16, binding): review findings on Amendment 1

**1. Allowed touches are what is written, and expected rows come from shadow for everything else.**
- **Which keys may change.** In `_write`, the touched set must be a subset of the keys of `install`
  transitions only. `preserve` keys (where the local head is newer) must not be touched: their
  expected live row is the current shadow row. In `_apply`, allowed touches are the packet's keys, as
  every legacy change is written.
- **What each written key must hold.** For every allowed key, the live row read by key must equal the
  install/packet row, or be absent for a deletion.
- **Preserved keys.** For every preserved key, the live row must equal its shadow row.
- **Test:** a resolution that deletes an `install` parent while a `preserve` child exists must
  conflict, not delete the newer child.

**2. Never split a local transaction (rationale in §1 corrected).**
- Targeted capture is a cheap probe. For the packet's keys, compare live rows against shadow.
- **No difference** (the normal case): capture nothing and continue.
- **Any difference:** an uncaptured local edit exists on a key the packet touches. Discard the targeted
  diff and run one full `_capture` for that packet, exactly as today. A committed local transaction is
  then never split across outgoing packets, at a cost only when a real local edit collides with an
  incoming packet.
- **Test:** a local uncaptured delete with a cascade, colliding with an incoming update, produces one
  outgoing packet holding both the deletion and the cascade.

**3. `_index_local` cost.**
- `apply_resolution` calls it for each non-replay packet, and it decodes every `_sync_outbox` row.
  Skip outbox rows already retained in `_sync_packet_evidence` with a matching envelope digest, so
  each own packet is decoded once overall.
- Add a decode-count bound to the reproduction and benchmark: decodes per receive must not grow
  with backlog × outbox.

**4. Targeted foreign-key checks** replace the per-packet whole-DB `PRAGMA foreign_key_check`.
- **Rows the packet writes:** for every row the packet inserts or updates, look up each of its FK
  parents by key.
- **Parents the packet deletes:** for every parent key it deletes, look up child rows referencing it,
  using the schema's FK list.
  - Add an index for `memory_items.supersedes_id` if no index exists. Use a schema migration that is
    idempotent and safe on existing DBs.
  - Otherwise accept a scan restricted to deletions.
- **Classification:** violations found this way are classified exactly as today (pending vs
  conflict).
- **Unrelated violations:** existing violations elsewhere must no longer block packets.
- **Tests:** an unrelated pre-existing FK violation doesn't block; a packet-caused violation still
  pends or conflicts as before.

**5. Exact value comparison** compares normalized dicts (as `rows()` builds them, with `metadata`
parsed), never canonical strings. That keeps REAL-column `1.0` equal to `1` as the old code did.

**6. Trigger hygiene.**
- **Naming:** TEMP triggers are `... ON main.<t>`, and their names are unique to this feature.
- **UPDATE:** log both OLD and NEW keys.
- **Keys:** canonicalize logged keys in Python (`canonical(json.loads(json_array(...)))`).
- **recursive_triggers:** set it ON for the apply and restore the previous value afterwards.

**7. `_packet_keys`** on an unrecognized body fails closed: the packet is `invalid`, not a full-capture
fallback. This is effectively unreachable after decode/`_validate`, and a fallback would let a
crafted file force full scans every cycle.

**8. Installer stop failure.**
- **Withdrawing the stop request.** On a `replace` stop failure, withdraw `stop.json` only if it still
  equals `{'nonce': N}`, so the old worker isn't told to stop later.
- **Error report.** Re-read worker status and include `worker_state` in the fixed error report.
- **windows-recover for `replace/stop`:**
  - reports `managed.status` (running or not) along with the reset to `ready`
  - must not go through `finish_interrupted_start`; give it its own branch
- **Interrupted cycles** increment `cycles`; this is acceptable, since it only happens on stop.

**9. Operator procedure for today's Windows host** (WINDOWS-INSTALLATION.md, recovery section).
- **First, the old worker must not be running.** The stuck rc.12 worker can't be interrupted, because
  it predates this fix. Keep the task disabled, then sign out and back in, or restart, and confirm with
  `worker-status` that no worker is running.
- **Then, in one session without signing out:**
  1. Re-enable the task (`schtasks /Change /ENABLE`).
  2. Run `windows-recover` (`replace/stop` → ready).
  3. Upgrade **without** `--replace`, so `precheck_stopped` verifies nothing is running.
  4. The upgrade starts the fixed worker.

## Amendment 3 (2026-10-16, binding): keep the global FK check; operator procedure corrected

**1. Amendment 2 item 4 (targeted FK checks) is withdrawn.** Both `_apply` and `_write` keep the
existing global `PRAGMA foreign_key_check`, with its classification unchanged.
- **Why:** it catches packet-induced violations in unsynced tables created by triggers. The existing
  regression `test_relational_repairs_refused_atomically[foreign-key]` stays as it is.
- **Cost:** measured on a 340 MB production-sized DB (read-only, immutable), it takes 0.16 s cold and
  1–2 ms warm. It is not the backlog bottleneck; the full `rows()` canonicalization is.
- **Behaviour on violations:** an unrelated pre-existing violation still blocks packets, exactly as
  today. That is not a regression, and it is documented.
- **What stays from Amendments 1 and 2:**
  - changed-row tracking (touched ⊆ allowed keys)
  - keyed value checks for installed and preserved keys
  - the targeted capture probe with a full-capture fallback
  - `_index_local` decode-once
  - normalized-dict comparison and trigger hygiene
  - `_packet_keys` failing closed
  - the installer changes
- **The bound:** the reproduction's scan bound counts `rows()` calls only. The benchmark records the
  time taken by `foreign_key_check`.

**2. Operator procedure.** Amendment 2 item 9 step 4 was wrong. An upgrade without `--replace` does
not start the worker. The authoritative procedure is the one now in WINDOWS-INSTALLATION.md (commit
99af5aa): recover, then upgrade without `--replace`, then an explicit `worker-start` of the selected
installed version (or the next logon).
