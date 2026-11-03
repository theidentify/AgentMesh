# SQLite retirement R4: report

Phase R4 of `../specs/2026-11-02-sqlite-retirement-design.md`: the owner-run cutover ceremony, its rollback and the
archive. Nothing runs by itself: the Mac changes only when the owner runs `control sqlite-retire --apply`.

## The ceremony

`agentmesh-log control sqlite-retire` is a dry run: it lists every problem that would block the cutover, what would be
seeded and what would be archived, and changes nothing. With `--apply` (typed `migrate` plus Touch ID or the Mac
password through the supervisor's helper), in this order:

1. Maintenance on, then wait for both job locks (ingest, summary), so no run is in flight.
2. Final import as the importer (`migrate run` with the catch-up cache) and `migrate verify`. A missing or wrongly
   retired row aborts before anything changes.
3. Seed `jobs/transcript-cursors.json` and `jobs/summary-state.json` from the database (`memory-sync/sqlite_cutover.py`).
4. Save the production and bridge configs in the archive, then switch: production `backend: broker` (summarizer key,
   mapping, CLI from the bridge config), bridge `dual_write` and `authority` off. Recall settings are untouched.
5. Fold the WAL, then move the database, WAL/SHM, locks, catch-up cache, digest state and the Markdown export into
   `backups/archive/sqlite-<node>-<stamp>/`: a rename on the same disk, files 0400, folders 0700, README and manifest.
6. Maintenance off, then one ingest scan and one forced summary through the broker (skip with `--no-first-run`).

A failure anywhere in steps 2 to 5 puts back every moved file, both configs and the seeded state, and removes the
archive folder. `sqlite-rollback --archive <name>` (same presence) restores the database, the export and both
configs and sets the summarizer state aside in the archive. `sqlite-archive list` shows archives and whether the
30-day rollback window is open; `sqlite-archive delete --archive <name>` (typed `delete` plus Touch ID) refuses
inside the window unless `--before-30-days`.

## Seeding without a drain

Events the SQLite summary has not reached yet are not stored anywhere after the cutover. Instead of running the old
summary until it drains, each transcript is seeded one of two ways:

- **Nothing waiting:** the broker cursor starts where the ingest cursor stopped.
- **Events waiting:** the broker cursor starts at the top of the file. Every event whose exact key the ingest stored,
  and which is not waiting, is marked done. The broker then summarizes the waiting events and anything written after
  the ingest cursor.

The live Mac Air (read-only plan, 2026-11-02) has 23 transcripts, 158 waiting events, 2 transcripts read again,
3 open quarantine rows, no ingestion floor. 38 events in the active Claude transcript key differently when the file
is parsed whole than when the ingest read it piece by piece. Those are summarized again rather than marked done: a
repeat is an unchanged upsert, a skip would lose a memory. A waiting event in a transcript that is gone or replaced
blocks the cutover (none today).

## R2 gaps closed here

- **Confirming a quarantined deletion:** `agentmesh-log summary delete --agent-key K --scope S --memory-key KEY`
  (typed `delete`). Quarantine records now carry the target scope.
- **Run metrics:** each broker summary tick that runs appends a row to the existing summary metrics log, failed ticks
  included, so Insight's metrics view continues.

Still open: the memory tree build after a summary tick (run `memory build` as today), and per-device rollback of
memories the summarizer wrote after the cutover (rollback summarizes those transcripts again into SQLite).

## Cutover on the Mac Air (2026-11-02)

Applied as archive `sqlite-mac-20261102-135815` (23 transcripts, 180 waiting events, 39 rekeyed). The first ingest
passed; the first broker summary failed with `summarizer related failed: truncated`: the live mapping sends every
project to `work/common`, which holds more than the 500 keyed heads the R2 `heads` list allowed. The cursor did not
move, so nothing was lost. Fixed by paging the list: `heads` without `memory_keys` returns 500 at a time with `next`,
and the summarizer reads every page (`summarize.Heads`, at most `PAGES` pages).

The next run reached the write and failed with `summarizer write failed: unavailable`: the summarizer passed the scope
name as the client's `expected_scope`, but the unsigned event names the scope by id, so the client refused to sign
(`invalid scope binding`) and nothing was written. The binding is dropped (the broker binds the named scope itself), and
`test_write_through_broker_client` now drives the real `BrokerClient`, which the in-process test client had bypassed.

## Owner runbook (Mac Air)

1. Merge and deploy `main` (the runtime needs `broker_digest.py` and `sqlite_cutover.py`; the dry run says so if not).
2. Add two agents to `~/.agentmesh/settings.json` and apply (first access asks for presence):
   - `summarizer`: read and write `work/common`, `personal/common`; `allow_summarize: true`.
   - `insight` (R3, optional): read `work`, `personal`; write nothing.
3. `agentmesh-log control sqlite-retire`: the dry run must show no problems.
4. `agentmesh-log control sqlite-retire --apply` in a terminal.
5. Remove the `importer` agent from settings and apply (its past imports keep verifying).
6. After 30 days: `agentmesh-log control sqlite-archive delete --archive <name>`.

Windows keeps SQLite until R5.

## Tests

- `profile-log/tests/control/test_retire.py`: dry run changes nothing; full retire, rollback and delete on a real
  staging database and an in-process broker (modes, configs, seeded state, maintenance, the imported memory); refused
  presence changes nothing; a verify gap and a failure after the switch both restore everything; preflight problems;
  CLI dry run and archive listing.
- `memory-sync/test_sqlite_cutover.py`: waiting events carry over exactly (and appended ones follow), floor, schedule
  and quarantine carry over, a replaced transcript with waiting events refuses, a rekeyed waiting event is summarized
  again, WAL checkpoint.
- `test_summarize.py` (confirmed deletion), `test_broker_digest.py::test_tick_metrics`.
