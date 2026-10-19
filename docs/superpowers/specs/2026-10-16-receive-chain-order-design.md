# Receive chain order: dependency-driven retries (0.3.2): design

Status: approved in conversation on 2026-10-16. Builds on main f649310 (0.3.1).

## Problem (measured)

After 0.3.1, receive no longer rescans the database per packet. But `receive` still visits packet
files in **filename order** (random UUIDs), and retries every pending packet on each pass while any
pass makes progress. A chain of packets on the same key (a peer updates the same row every minute)
therefore needs about one pass per link. Every pending packet is re-attempted, and each attempt opens
a connection, runs a transaction, writes `_sync_diagnostics` and commits.

- **Disposable benchmark** (single-key chain, 20k-row DB): 50 → 677 attempts, 100 → 2,549, 200 → 10,379
  (about N²/4); 200 links take 26 CPU s and 54 s wall.
- **Production evidence:** the Windows node's 855-packet backlog drained at a steady 24.5 packets/min
  on 0.3.1, about 35 minutes for one cycle.

## Change

Make retries **dependency-driven** in `memory_sync.receive`, on the legacy path and the indexed path
alike. Keep every per-packet check and transaction exactly as it is: authorization, signature,
`_validate`, `_seen`, the targeted capture probe, `_apply`/`receive_indexed`, the classification,
the #47 file marks and the 0.3.1 interrupt checks.

1. **First pass:** visit files in a stable order that prefers creation order: file `mtime_ns`, then
   name. For every packet that ends `pending`, remember the parent revisions it is waiting for, taken
   from its parsed body (`parent`, or `parents` for v2/v3).
   - Store this as an in-memory map `waiting[revision] -> {path}`.
   - A pending packet whose missing parents can't be determined goes to a fallback list.
2. **After every successful apply**, look up the revisions that packet introduced (each change's
   `revision`) in `waiting`, and attempt those dependants **immediately**, depth-first, under the same
   per-packet rules. That way a chain resolves in one pass.
3. **Then run the existing retry loop**, but only over packets that are still pending and the fallback
   list. Keep the "stop when a pass makes no progress" rule as the safety net, so correctness never
   depends on the map being complete.
4. **Diagnostics:** don't write the per-attempt `_sync_diagnostics` row for a packet that is going to
   be retried immediately because of a dependency. Write the final classification once per packet
   per receive. The final pending, conflict and invalid outcomes and messages are identical to today.
5. **Interrupt:** the 0.3.1 check runs before every attempt, including dependency-driven ones.

## Tests

The tests use disposable data.

- **Linear attempts:** a single-key chain of 200 in shuffled filename order applies in one receive
  with at most 2N `_apply` attempts. Run it legacy and indexed.
- **Multiple interleaved chains** and a chain whose head arrives last: all of them apply.
- **Unchanged outcomes:**
  - a packet whose parent never arrives stays pending, with the same diagnostic
  - conflicts and invalid packets are classified as before
- **Every existing receive, #47 and 0.3.1 test stays green.**
- **Benchmark:** extend `memory-sync/support/benchmark_receive_backlog.py`, or add a chain mode, with
  before and after numbers recorded in the REPORT.
