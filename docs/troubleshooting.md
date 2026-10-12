# Troubleshooting

Use these checks to identify sync and installation problems while preserving evidence. Start with [local status and worker diagnostics](./use/status), using a second terminal if a foreground worker is running. You will know which failures can retry and which need review before restarting.

## Nothing syncs between devices

Direct exchange needs overlapping online time, connected Syncthing peers and workers on both devices. Confirm the private exchange folder is **Up to Date**, then inspect worker cycle results. A transient failure reports `failure_kind: transient` and `warning: exchange unavailable`; a recurring managed worker retries after the folder becomes accessible again. Check the accepted folder, mount and permissions instead of creating a replacement `.stfolder`.

A managed worker stops on permanent failure. Use [worker status](./use/worker#use-managed-execution) and diagnose before restarting. File transfer completion and `outbox: 0` alone do not prove records were imported. Finish with the [A → B recall test](./getting-started/quick-start#prove-a-record-travels-from-a-to-b).

## Two workers are running, or a stop timed out

Stop extra foreground execution with Ctrl-C in its original terminal, or use the relevant [service controls](./install/background-service). `worker-stop` controls only managed execution. A missing managed record or an idle lock cannot prove an unrelated worker exited.

Startup timeout requests a stop but does not wait for completion; stop timeout also leaves execution uncertain. Inspect the existing instance, wait for its cycle to finish and confirm stopped execution before starting, updating or removing code. Do not kill a guessed PID. If you cannot identify the original launcher, preserve the state and use the help template below.

## Records are quarantined

A conflict in one row rejects the whole packet, including nonconflicting rows in it. Dependent packets can remain pending. Unrelated packets may still apply during that cycle; the managed worker then stops for the permanent failure.

The counters identify the problem class, not the affected records. To inspect local details without writing the database, run from a terminal with Python:

```sh
python3 -c "import sqlite3,sys; from pathlib import Path; c=sqlite3.connect(Path(sys.argv[1]).resolve().as_uri()+'?mode=ro',uri=True); print(c.execute('SELECT path, kind, message FROM _sync_diagnostics ORDER BY path').fetchall()); c.close()" "/path/to/memory.db"
```

This query was run against a disposable invalid-packet fixture and returned its private packet path, `invalid` and diagnostic message, exit `0`. An empty result is `[]`; a traceback/nonzero exit means inspection failed. **Keep this output private:** paths and messages may reveal deployment or memory details. Signed-packet messages are deliberately less detailed.

| Diagnostic | What to investigate |
| --- | --- |
| `pending` | Missing parent revision or referenced record; check that predecessor packets reached this device. |
| `conflict` | Divergent ancestry, baseline mismatch or unique-key collision; retries do not merge independent edits. |
| `invalid` | Malformed/foreign packet, checksum/signature failure or another validation rejection; preserve the original bytes. |

A diagnostic is cleared when that path is successfully processed, not merely when its file disappears. Before operator-led reconciliation, preserve the packet files, diagnostics, runtime configuration and a consistent SQLite backup made through the SQLite backup API—not a copy of only a live main database file. Stop affected sync workers and coordinate writers for the recovery procedure. The [sync reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SYNC.md#safety-and-conflicts) provides the data model; there is no automatic conflict-resolution command.

After an approved repair, require successful packet application, reviewed/cleared diagnostics, a cycle without permanent failure and destination recall before resuming normal operation. Do not reset history or diagnostics to make counters look clean.

## Exchange packets were deleted

Published packets are not automatically recreated. Deleting the newest packet can silently deprive a peer of data without leaving a pending dependency; deleting an intermediate packet can leave later packets pending. A Syncthing **Up to Date** indication does not detect this lost history.

Never delete packets to reclaim space. Preserve remaining exchange files and retained outbox/history. Recovery requires operator review of backups or the retained outbox, keeping the original packet UUID and exact bytes. Restarting or waiting alone does not repair missing publication. No general safe reconstruction command is supplied here; do not hand-edit packets or create new UUIDs for old changes.

## Installer says the checksum does not match

The package check can report `package checksum mismatch; wait for Syncthing to finish`; snapshot validation can report `baseline checksum mismatch`. Wait for **Up to Date**, then retry with the unchanged matching ZIP and manifest. If the error persists, have the person supplying the package verify both files. Do not edit either to bypass validation.

## Bootstrap is too large

The shared Python installer rejects a ZIP whose members total more than **200,000,000 bytes** uncompressed with `unsafe archive size`. Separately, gunzipping the baseline past **200,000,000 bytes** produces `unsafe baseline size`. These source-derived messages apply on all three platforms, even when checksums and available disk space are otherwise satisfactory.

Keep the original artifacts and ask the group administrator to review supported packaging limits. The guide provides no safe trimming/repartitioning procedure; do not drop snapshot rows or raise limits in an installed copy as a workaround.

## Ingestion or signed publication keeps failing

Ingestion can commit valid events while reporting errors. Inspect [partial ingest results](./use/ingest#handle-partial-results); rerunning after an in-place transcript edit does not guarantee a replay. Assign one ingestion owner per source to avoid cross-device event/cursor conflicts.

Signed packets have [size and structure limits](./install/security#respect-packet-limits). A backlog already exceeding them is not fixed by reducing the next wait interval. Retain the database, outbox and original input for operator review; no automatic split/recovery command is documented.

## Windows window closed and sync stopped

If you installed with **START-WINDOWS.cmd**, reopen that file after confirming there is no other worker. For automatic sign-in startup, use the separate [Windows program installation path](./install/background-service#windows-sign-in-start). Its program bundle is different from the private bootstrap ZIP.

## I copied my database to another machine

Copying initialized local state duplicates the device identity. Stop the duplicate worker and preserve both databases; do not overwrite either during recovery. Review [device identity](./getting-started/concepts#device-identity) before proceeding. A `setup-new` group cannot currently add devices by copying a database or creating a fresh baseline.

## Request help without sharing memory

Run [diagnose with `--json`](./use/status#diagnose-a-worker) and inspect its output locally. Share only this allowlisted summary, after checking it contains no private text:

- The action attempted and its exit code; omit command path arguments.
- `status`, `read_only`, and `program.mode`, `program.platform`, `program.python`, `program.version`.
- Each check's `stage` and `status`; for a failed check, its fixed `error.code`, `error.stage` and numeric `errno`/`winerror`/`sqlite_errorcode` if present.
- `worker.state`, `worker.cycles`, `worker.process_observation` and `worker.last_error` if available.
- Separately transcribed `failure_kind` and `warning` from worker status; the diagnostic projection does not currently include them.

Do not attach the whole status record, PID/nonce, timestamps, runtime paths, group IDs, fingerprints, transcripts, database, packets or raw diagnostic messages. For `.setup-pending.json`, report its presence and the failed action, preserving the partial directory privately. No generic partial-setup repair command is provided.

Next, review [Privacy](./privacy) before sharing a help request or screenshot.
