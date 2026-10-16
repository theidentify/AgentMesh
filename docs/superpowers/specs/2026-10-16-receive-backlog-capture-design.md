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
