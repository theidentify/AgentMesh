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

Output (run on macOS)

The invalid-packet fixture returned its private packet path, `invalid` and diagnostic message, exit `0`. An empty result is `[]`; a traceback/nonzero exit means inspection failed. **Keep this output private:** paths and messages may reveal deployment or memory details. Signed-packet messages are deliberately less detailed.

| Diagnostic | What to investigate |
| --- | --- |
| `pending` | Missing parent revision or referenced record; check that predecessor packets reached this device. |
| `conflict` | Divergent ancestry, baseline mismatch or unique-key collision; retries do not merge independent edits. |
| `invalid` | Malformed/foreign packet, checksum/signature failure or another validation rejection; preserve the original bytes. |

A diagnostic is cleared when that path is successfully processed, not merely when its file disappears. Before operator-led reconciliation, preserve the packet files, diagnostics, runtime configuration and a [consistent SQLite backup](#preserve-a-consistent-database-backup) made through the backup API—not a copy of only a live main database file. Stop affected sync workers and coordinate writers for the recovery procedure. The [sync reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SYNC.md#safety-and-conflicts) provides the data model; there is no automatic conflict-resolution command.

After an approved repair, require successful packet application, reviewed/cleared diagnostics, a cycle without permanent failure and destination recall before resuming normal operation. Do not reset history or diagnostics to make counters look clean.

## Preserve a consistent database backup

First stop the relevant worker and coordinate other writers as described above. Choose a new filename in a private directory outside the exchange. This uses SQLite’s backup API, as the bootstrap installer does, to include committed database state without copying an incomplete live main file. In macOS/Linux:

```sh
python3 -c "import os,sqlite3,sys; from pathlib import Path; src=sqlite3.connect(Path(sys.argv[1]).resolve().as_uri()+'?mode=ro',uri=True); fd=os.open(sys.argv[2],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close(); print('Backup complete')" "/path/to/memory.db" "/path/to/private-backup.db"
```

In Windows Command Prompt, use `py -3` and Windows paths on the same line. Verify the destination directory’s access controls on that host.

Output (run on macOS)

```text
Backup complete
```

Exit `0` confirms the backup call completed. An existing target is refused; any traceback/nonzero exit means no confirmed backup, and a partial target may remain. Preserve it separately before trying a new filename. This backs up only SQLite: also retain transcripts, runtime/workflow configuration, signing/trust state and exchange files privately. It is evidence preservation, not a supported restore or reconciliation procedure.

## Exchange packets were deleted

Published packets are not automatically recreated. Deleting the newest packet can silently deprive a peer of data without leaving a pending dependency; deleting an intermediate packet can leave later packets pending. A Syncthing **Up to Date** indication does not detect this lost history.

Never delete packets to reclaim space. Preserve remaining exchange files, a consistent database backup with retained outbox/history/receipts, runtime configuration and existing backups from all affected devices. Recovery requires operator review of backups or the retained outbox, keeping the original packet UUID and exact bytes. Restarting or waiting alone does not repair missing publication. The code has no supported general safe reconstruction command. Report the last known successful delivery, which devices lack the record, whether later packets are pending, and sanitized counts through the [help template](#request-help-without-sharing-memory); do not hand-edit packets or create new UUIDs for old changes.

## Installer says the checksum does not match

The package check can report `package checksum mismatch; wait for Syncthing to finish`; snapshot validation can report `baseline checksum mismatch`. Wait for **Up to Date**, then retry with the unchanged matching ZIP and manifest. If the error persists, have the person supplying the package verify both files. Do not edit either to bypass validation.

## Bootstrap is too large

The shared Python installer rejects a ZIP whose members total more than **200,000,000 bytes** uncompressed with `unsafe archive size`. Separately, gunzipping the baseline past **200,000,000 bytes** produces `unsafe baseline size`. These source-derived messages apply on all three platforms, even when checksums and available disk space are otherwise satisfactory.

Keep the original artifacts and ask the group administrator to review supported packaging limits. The guide provides no safe trimming/repartitioning procedure; do not drop snapshot rows or raise limits in an installed copy as a workaround.

## Ingestion or signed publication keeps failing

Ingestion can commit valid events while reporting errors. Inspect [partial ingest results](./use/ingest#handle-partial-results); rerunning after an in-place transcript edit does not guarantee a replay. Assign one ingestion owner per source to avoid cross-device event/cursor conflicts.

Signed packets have [size and structure limits](./install/security#respect-packet-limits). A backlog already exceeding them is not fixed by reducing the next wait interval. Retain the database, outbox and original input for operator review; no automatic split/recovery command is documented.

## Signed packets are invalid after revocation

Revocation alone no longer invalidates previously committed packets or stops the managed worker. Exact retained packets remain accepted against local receipts and full envelope digests. New, unapplied packets from the revoked key and tampered replays remain invalid; a managed worker stops for that permanent failure. Preserve original packets, database/history/receipts and trust state, including revoked public-key records needed to verify older receipts. Follow [rotation and revocation](./install/security#understand-the-protection) with the operator; do not delete history or diagnostics to hide a validation failure.

## Windows window closed and sync stopped

For the default bootstrap installation, reopen **START-WINDOWS.cmd** after confirming there is no other worker. With a custom local directory or device identity, repeat the [Python installer command](./install/windows#use-a-different-device-identity) with the same explicit `--local-dir` and `--node` values. For automatic sign-in startup, use the separate [Windows program installation path](./install/background-service#windows-sign-in-start). Its program bundle is different from the private bootstrap ZIP.

## I copied my database to another machine

Copying initialized local state duplicates the device identity. Stop the duplicate worker and preserve both databases; do not overwrite either during recovery. Review [device identity](./getting-started/concepts#device-identity) before proceeding. Eligible unsigned `setup-new` groups now use [origin-issued invitations](./install/new-group#adding-another-device); copying a database or creating a fresh baseline is not that procedure.

## Invitation join is refused or incomplete

Use the [invitation procedure](./install/new-group#adding-another-device) from the same source revision on both devices. For guard failures, the CLI exits `1` and prints JSON on stderr with `reason`, `code`, `stage` and `next_action`. A replay can instead return `status: pending` (exit `2`) or `blocked` (exit `1`) on stdout. Preserve the origin database, reservations, original transfer files and the recipient’s private prepared state before investigating.

| Reason or result | Meaning and supported next step |
| --- | --- |
| `invitation expired` | The recipient clock has reached the invitation deadline before consumption. Check the clock, but do not edit expiry or backdate it to bypass the guard. Expiry does not release the permanently reserved identity; there is no supported reissue/release command. Request maintainer review. |
| `invitation already consumed` | Initial consumption already began or completed on this prepared device. If replay is incomplete, use `setup-join --resume` on that same installation. If already joined, use its existing runtime; do not join again. |
| `slot already used or reserved` | The selected device identity belongs to the origin, an observed publisher or a durable reservation. Check the origin’s assignment records, including offline peers. Prepare a new request for another genuinely unused identity only if one remains; do not delete reservation records or exchange directories. |
| `signed groups are not supported by invitations; use the security pairing workflow` | The source is signed/security-configured, the invitation is signed-policy, or the exchange contains `signed-changes`. This invitation route cannot proceed. Retain existing signing state and consult the [security operator reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SECURITY-WIZARD.md); do not downgrade policy or delete signing files. Pairing is not a replacement invitation command. |
| `non-empty baseline group; use the original bootstrap` | The group has baseline history incompatible with empty-baseline replay. Use its original compatible bootstrap through the group operator, not a newly manufactured baseline or copied initialized database. |
| `empty origin provenance unavailable; use the original bootstrap` | Run `invite-peer` on the actual origin, not a joined peer. An older `setup-new` database has no recorded origin provenance and cannot invite even after a code update. No provenance-upgrade procedure is provided; use an original bootstrap only if this deployment has one, otherwise request maintainer review. |
| `status: pending`, `failure_kind: pending-history` | A predecessor is missing. Allow full history to arrive, then resume the consumed invitation. `.setup-pending.json` must remain until successful replay. |
| `status: pending`, `failure_kind: transient` | Replay found the exchange unavailable. Restore access to the accepted folder and retained history, then resume. |
| `status: blocked`, `failure_kind: permanent` | Replay found conflicts or invalid packets. Preserve files and [inspect diagnostics](#records-are-quarantined) with the operator before retrying; restarting does not reconcile records. |
| `join is not awaiting replay` | Resume applies only to a consumed invitation awaiting replay/finalization. Do not edit `join-state.json` to force a different phase. |
| `invitation belongs to another prepared device` | Return the invitation to its matching prepared recipient. Never copy the private prepared installation or its secret to make another device match. |

In the disposable test, trying B’s invitation again after successful join exited `1` with this excerpt:

Output (run on macOS)

```json
{"error":"ValueError","reason":"invitation already consumed","code":"GUARD_REFUSED","stage":"install_join.setup_join"}
```

A repeated reservation attempt also exited `1`, with `reason: slot already used or reserved` and `stage: install_join.check_slot`. Expiry and unsupported-group messages above were checked against code; those fixtures were not executed here. For an incomplete replay, use the [tested resume command](./install/new-group#resume-an-incomplete-replay), without `--invitation`; successful resume is allowed after the consumed invitation expires and does not reserve another identity.

A missing newest packet or independent chain can escape diagnostics entirely. `joined` and zero pending counts are not proof of complete history: verify known A-only records on B and retain all exchange packets. Never bypass the guard, reset history, clone the origin or restore a prepared-state copy to reuse an invitation. Share only the sanitized failure fields in the help template below; `data/join-state.json` contains a secret and must stay private.

## Request help without sharing memory

Run [diagnose with `--json`](./use/status#diagnose-a-worker) and inspect its output locally. Share only this allowlisted summary, after checking it contains no private text:

- The action attempted and its exit code; omit command path arguments.
- `status`, `read_only`, and `program.mode`, `program.platform`, `program.python`, `program.version`.
- Each check's `stage` and `status`; for a failed check, its fixed `error.code`, `error.stage` and numeric `errno`/`winerror`/`sqlite_errorcode` if present.
- `worker.state`, `worker.cycles`, `worker.process_observation` and `worker.last_error` if available.
- Separately transcribed `failure_kind` and `warning` from worker status; the diagnostic projection does not currently include them.

Do not attach the whole status record, PID/nonce, timestamps, runtime paths, group IDs, fingerprints, transcripts, database, packets or raw diagnostic messages. For `.setup-pending.json`, report its presence and the failed action, preserving the partial directory privately. No generic partial-setup repair command is provided.

Next, review [Privacy](./privacy) before sharing a help request or screenshot.
