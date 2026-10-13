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

Use the read-only `conflicts` listing and local `--show-content` view in [Resolve conflicts](./use/resolve-conflicts) to inspect the affected packets. Listing does not verify signatures or update diagnostics; keep content and raw identifiers private.

| Diagnostic | What to investigate |
| --- | --- |
| `pending` | Missing predecessor or referenced evidence; let the complete retained history arrive before retrying receive. |
| `conflict` | Same-lineage divergent edits can now be resolved by an explicit, previewed decision after protocol activation. Baseline mismatch and natural-key/unique/FK repairs remain outside this procedure. |
| `invalid` | Malformed/foreign packet, checksum/signature failure or another validation rejection; preserve the original bytes. Resolution cannot waive these checks. |

Before resolution, preserve the packet files, diagnostics, runtime configuration and a [consistent SQLite backup](#preserve-a-consistent-database-backup). Stop affected workers and coordinate other writers. Follow the [activation, preview, confirmation and peer receive steps](./use/resolve-conflicts); do not restart until you have reviewed diagnostics and compared the chosen records. Resolution retains non-conflicting companion rows and marks covered held originals as superseded. It never restarts a worker automatically.

Do not delete packets, reset history or clear diagnostics to make counters look clean. Missing proof can keep a resolution pending; independently conflicting resolutions need a further explicit decision. See [resolution refusals](./use/resolve-conflicts#handle-refusals-without-rewriting-history) for stale plans, unsupported repairs and protected-key requirements.

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

Revocation alone no longer invalidates previously committed packets or stops the managed worker. Exact retained packets remain accepted against local receipts and full envelope digests. Before explicit rotation approval, new, unapplied packets from the revoked key and tampered replays remain invalid; a managed worker stops for that permanent failure. Preserve original packets, database/history/receipts and trust state, including revoked public-key records needed to verify older receipts. An approved v2 rotation manifest can authorize listed resolution evidence after matching the UUID and complete envelope digest and verifying the historical signature. This gives `verified` authorization, not an applied receipt. Outside that approval, never-committed evidence still requires current trust; exact committed receipts and superseded dispositions retain their historical-replay rules. For key recovery, follow [rotation and peer approval](./install/security#recover-a-lost-or-compromised-key) with the operator; do not delete history or diagnostics to hide a validation failure.

## Windows window closed and sync stopped

For the default bootstrap installation, reopen **START-WINDOWS.cmd** after confirming there is no other worker. With a custom local directory or device identity, repeat the [Python installer command](./install/windows#use-a-different-device-identity) with the same explicit `--local-dir` and `--node` values. For automatic sign-in startup, use the separate [Windows program installation path](./install/background-service#windows-sign-in-start). Its program bundle is different from the private bootstrap ZIP.

## I copied my database to another machine

Copying initialized local state duplicates the device identity. Stop the duplicate worker and preserve both databases; do not overwrite either during recovery. Review [device identity](./getting-started/concepts#device-identity) before proceeding. Eligible unsigned `setup-new` groups now use [origin-issued invitations](./install/new-group#adding-another-device); copying a database or creating a fresh baseline is not that procedure.

## Protected key or OS unlock refuses

First stop the affected worker, confirm its runtime selects the intended security directory, and inspect [OS-unlock status](./install/security#enable-unattended-os-unlock) as the worker’s account. Status reads the store but does not change it; exit `0` alone is not success when `available: false` or a `reason` is present. Public identity/status checks may work while signing remains locked. Some cycle reports expose only an error class; use the managed worker’s error details and the key commands’ fixed reasons below.

| Refusal or status | What to do |
| --- | --- |
| `passphrase-protected identity is locked; enable OS unlock or unlock interactively` | For unattended signed operation, explicitly enable OS unlock after protecting the identity, then start a new worker process. `wizard-resume` can unlock interactively for that run; it does not unlock a later worker. Do not remove protection or fall back to unsigned policy. |
| `protected identity could not be unlocked: wrong passphrase or damaged file` | Verify the intended identity and passphrase; wrong input and authenticated-file damage deliberately have the same error. Preserve the file and any trusted backup. Retry the interactive operation with the correct passphrase; if it still fails, request operator review. A lost identity passphrase cannot be recovered. If you have an encrypted identity backup and its separate backup passphrase, use [verified restore](./install/security#restore-only-after-stopping-the-old-device) after fencing the old installation. Without that recovery material, use [explicit key rotation and peer approval](./install/security#recover-a-lost-or-compromised-key); do not generate an unrelated key automatically. |
| `passphrase entry requires an interactive console` | Run protection, OS-unlock enable, backup/restore or the interactive wizard from a real console. Passphrases are not accepted through arguments, environment variables, files or pipes. |
| `passphrase must be at least 12 characters` / `passphrases do not match; nothing was changed` | Retry protection with a long unique passphrase entered identically twice. |
| `identity must be passphrase-protected before enabling OS unlock` | Stop workers and run `identity-protect` on the existing identity first. |
| `OS unlock is already enabled` | Inspect status. To replace the persisted unlock path, stop workers, disable it and explicitly enable it again with the passphrase. |
| `OS unlock item is not available for this account` | Confirm the worker uses the intended OS account and store. A copied identity directory does not carry the account’s wrapping key. Use the interactive passphrase path if needed; then disable/re-enable OS unlock under the intended worker account and restart. |
| `OS key store refused access` / `OS key store did not respond` | Restore the logged-in account’s store access; on macOS resolve a locked or password-out-of-sync login keychain or system dialog. Restart after correction. A timeout does not guarantee the system dialog closed. |
| `OS unlock could not open the identity: wrong key or damaged file` | Preserve the identity and inspect a trusted backup with the operator. Use the passphrase path and explicitly recreate OS unlock if appropriate; do not edit envelope fields or overwrite the identity with a new key. |
| `OS unlock is not supported on this platform` | Use the matching supported OS/account. Linux has no backend. An interactive wizard unlock is available with the passphrase, but is not an unattended worker solution. |
| `OS unlock item could not be removed` / `stray_item: true` | Disable removed the envelope but an OS item may remain, or status found an item without an envelope. Restore store access, run disable again and inspect status. Stop existing workers: they may still hold the key in memory. |
| `OS unlock is already in progress` | Wait for the other enable/disable operation to finish, then retry. Do not bypass its lock. |

The disposable identity test reproduced locked-signing and wrong-passphrase refusals. With the repository’s in-memory store helper, two worker cycles made one store read; a missing-item refusal stayed cached after the helper’s store was repaired. A fresh process is required after a real refusal. Do not expect repeated cycles or a successful status command in another process to clear that cache.

After a failed OS-item deletion, status can show this excerpt:

Output (example from code)

```json
{"protected":true,"os_unlock":"absent","backend":null,"available":false,"reason":null,"stray_item":true}
```

`stray_item: true` is not successful cleanup. Retry the [disable/status commands](./install/security#disable-os-unlock-and-check-cleanup) after fixing store access. Require no store error and `stray_item: false` for the current key ID. Status does not enumerate or clean up items for old/rotated key IDs. A leftover wrapping key plus an older identity backup containing its envelope can still unlock that identity. Keep backups private and follow the [key-protection limits](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/KEY-PROTECTION.md#threats-and-portability); never share passphrases, identity files or OS-store contents with a help request.

## Identity backup or restore refuses

Use [Back up and verify the device identity](./install/security#back-up-and-verify-the-device-identity) for the commands and [verified restore](./install/security#restore-only-after-stopping-the-old-device) for the fencing/trust sequence. Errors exit `1`; inspect the fixed `reason` as well as the exit status. Refusal is not permission to erase a current identity or trust state.

The disposable CLI run reproduced an existing-file refusal and a wrong-passphrase refusal:

Output (run on macOS)

```json
{"code":"GUARD_REFUSED","error":"ValueError","reason":"backup file already exists"}
{"code":"GUARD_REFUSED","error":"ValueError","reason":"backup could not be opened: wrong passphrase or damaged file"}
```

Each line is an excerpt from a separate failed command. The following refusal text is verified against code:

| Reason or result | What to do |
| --- | --- |
| `backup file already exists` | Keep the existing backup; use a new output filename. Creation never overwrites it. |
| `backup parent directory does not exist` | Choose an existing private parent on the intended offline medium. |
| `backup location is inside a Syncthing folder` / `backup location is inside a git work tree` | Use a location outside synced folders and repositories. These guards also apply to restore targets. |
| `backup could not be opened: wrong passphrase or damaged file` | Confirm the intended file and backup passphrase. Authentication failures deliberately do not distinguish a wrong passphrase from corruption. Preserve the file; do not edit it or guess replacement fields. |
| `invalid backup file` | The backup or decrypted payload failed validation. Preserve it and request review; do not bypass validation. |
| `backup and identity passphrases must be different` | Choose a separate backup passphrase for creation, or a different new identity passphrase for restore. Both new passphrases must be at least 12 characters and entered identically twice. |
| `backup or restore confirmation was not supplied` | Read the displayed identity and conditions, then retry from an interactive console only when you can truthfully confirm `BACKUP`, `FENCED` or the additional cloud-storage `CLOUD` acknowledgement. |
| `restore target already contains an identity` | Inspect the existing identity and configured installation. Restore cannot overwrite it; do not delete it just to retry. |
| `restore target must be outside the exchange` | Select the intended private security directory outside the exchange, Git and Syncthing. |
| `exchange does not belong to this group` | Confirm the correct accepted exchange, its `.stfolder` and genuine group status delivery. Do not fabricate a status file or change group fields. |
| `the old device looks active: recent exchange activity for this identity` | Actually stop/wipe the old installation and prevent its restart. Inspect the last 15 minutes of activity, future modification times and unreadable entries in this device identity's exchange paths. A quiet interval alone cannot prove an offline device is fenced. Do not alter timestamps or delete packets to bypass the guard. |
| `this key is revoked in the current trust; rotate instead (#9)` | Preserve revocation and arrange operator-led rotation/re-pairing. A backup cannot make a revoked or compromised key safe. Use the [explicit rotation procedure](./install/security#recover-a-lost-or-compromised-key); `identity-rotate` replaces the key and peers must separately approve it. |
| `current trust conflicts with the backup identity` | Review own public fields, any newer active key for this device identity and group membership. Retain current trust; do not use an empty directory to evade it. |
| `current trust changed during restore; retry after inspection` | Coordinate trust changes, inspect the resulting state and then retry. |
| `unfinished restore exists; inspect and remove .restore-* and trust.json.pre-restore before retrying` | Preserve and inspect the interrupted restore. Decide which trust state to retain before removing those recovery artifacts; do not blindly delete them. Request operator help if uncertain. |
| `backup readback verification failed` / `restored identity readback failed` / `identity backup or restore failed` | Do not treat the operation as successful. Preserve available evidence and inspect the destination with the operator before retrying; a generic failure does not identify a safe repair. |
| `key_id_mismatch: true` after verify/drill | The file passed its check but did not match the supplied live public identity, so no evidence was recorded for it. Select the correct file/directory; do not relabel the backup. |
| `stray_os_unlock_item: true` after restore | Explicitly disable or enable OS unlock for the restored identity to remove or replace the leftover item, then inspect OS-unlock status. Restore itself leaves OS unlock off. |

Backup status `none` means no recorded evidence for this identity. `verified` or `drilled` records a past check, not current file availability; rerun verify/drill against the stored backup when needed. A drill uses disposable peers and cannot prove acceptance by a real peer. Require a fresh live signed wizard roundtrip before normal operation after restore; remote revocations can still prevent it. The wizard can reuse an old saved probe/receipt and has no force-new-probe option. If that applies, seek operator review before restarting; do not reset state to make the check appear fresh.

## Key rotation or peer approval refuses

Follow [key recovery and rotation](./install/security#recover-a-lost-or-compromised-key) with workers and other copies stopped. A refusal exits `1`; keep the journal and evidence while inspecting the fixed `reason`. Do not start another rotation or roll back SQLite to bypass a refusal.

A wrong independently supplied fingerprint and a repeated successful approval produced these separate excerpts:

Output (run on macOS)

```json
{"code":"GUARD_REFUSED","error":"ValueError","reason":"rotation fingerprint does not match"}
{"code":"GUARD_REFUSED","error":"ValueError","reason":"already rotated"}
```

The remaining refusal text below is verified against code:

| Reason or result | Supported next step |
| --- | --- |
| `rotation requires a signed (required-policy) database; there is no key to rotate` | Confirm the intended existing signed database. Rotation does not activate signing or initialize a group. |
| `signed database binding mismatch` / `runtime does not match rotation paths` | Inspect the configured database, identity, group and runtime paths; do not edit binding fields to force agreement. |
| `old signing key cannot be determined; supply --old-key` | Obtain the full old fingerprint from trusted records and supply `--old-key` when needed. Do not guess it from an untrusted proposal. |
| `a worker or cycle is using this database` | Stop the owning managed/foreground/service execution and wait for it to release its locks. Stop clones separately; local locks cannot fence them. |
| `rotation confirmation was not supplied` | Retry only after you can truthfully type `STOPPED`, and approve `PUBLISH` for selected pending packets or `QUARANTINE` for reviewed resume collisions when prompted. No `--yes` bypass exists. |
| `the old device looks active: recent exchange activity for this identity` / `exchange does not belong to this group` | Check the accepted exchange and actual stopped execution, including clones. Keep genuine status/history; do not alter timestamps, delete packets or fabricate status. |
| `pending packet is already in the exchange; publish or inspect it before rotating` | Inspect the publication state with the operator. In a compromise incident, do not resume an unsafe signer merely to clear this guard. Do not delete the exchange copy or edit the outbox. |
| `pending packet file differs from the outbox; see compromise-report` | Inspect the differing file. For a reviewed, journal-bound collision, use [resume with quarantine](./install/security#quarantine-a-differing-file-during-resume); compare both displayed envelope digests and type `QUARANTINE`. Do not overwrite the file manually. |
| `quarantine-collision requires a journal-bound differing packet on resume` | Use repeatable `--quarantine-collision` only with `--resume` and a differing file belonging to that journal. Identical files need no quarantine. Malformed JSON or unsupported formats require separate inspection. |
| `drop-pending requires unreferenced pending packets and reason compromised` | Use repeatable `--drop-pending` only on a fresh compromised rotation. Verify the UUID is pending and no pending packet references it through `covers` or `evidence`; do not edit dependencies to bypass the guard. |
| `pending outbox is signed by an unexpected key` / `invalid pending outbox packet` | Preserve the database and packet evidence for review. Do not re-sign or repair packet bytes by hand. |
| `unfinished rotation or restore exists; inspect or run identity-rotate --resume` | Inspect whether this is rotation or restore recovery. Resume a valid rotation journal; restore artifacts or staging without a journal require review, not blind deletion. |
| `rotation in progress; run identity-rotate --resume` | Use the same paths with `--resume`, confirm stopped execution again and enter the new identity passphrase chosen before interruption. Signing stays blocked until recovery finishes. |
| `invalid rotation journal` / `invalid rotation registry` | Preserve the private files and request review. Old journals from the re-signing implementation and v1 registries are refused; there is no documented conversion. Do not reinterpret them or remove the journal to start the worker. |
| `invalid rotation proposal` / `invalid rotation manifest` | Preserve both files and verify delivery of the matching v2 proposal/manifest pair with the device owner. V1 proposals are refused; seek operator inspection rather than changing the format label or inventing a conversion. |
| `rotation conflicts with an active identity or allocation slot` | Review existing trust assignments with the operators. Do not remove revocations or create two active keys for one device identity. |
| `a rotation proposal cannot replace this device's own key` | Run peer approval on the receiving peer; use local rotation for this device's own key. |
| `unknown old key` / `rotation group does not match this device` | Confirm the intended group and peer's existing trusted old identity. A rotation proposal is not a shortcut for pairing an unknown peer. |
| `rotation fingerprint does not match` | Reconfirm the entire fingerprint directly with the rotating device owner. Never copy the approving value from the proposal. |
| `already rotated` | Inspect the already approved new identity and remaining wizard/application pins. Repeating approval is not needed. |
| `new key is revoked here` | Preserve local revocation and review with the operator; approval must not un-revoke it. |
| `local trust changed during rotation approval` | Coordinate trust changes, review the current state and then retry approval. |
| `database WAL sidecars are incomplete; inspect or checkpoint the stopped database before retrying` | Preserve the database and existing sidecars. Request inspection of the stopped database; do not delete sidecars or ignore uncheckpointed data. |
| `invalid compromise report timestamp` | Supply a UTC ISO timestamp to `--since`, or omit the filter. It filters mutable file times, not authenticated incident times. |
| `key rotation operation failed` | Keep evidence and inspect which stage completed before retrying. A generic error does not establish a safe rollback or cleanup procedure. |
| `os_unlock_item_removed: false` | Arrange cleanup of the old fingerprint's OS-store item. After rotation, `identity-os-unlock disable` targets the new key, not the old item. |
| `trust_restored: false` | Independently re-pin every peer using the explicit trust procedure before resuming incoming sync. |

A competing-proposal warning requires independent confirmation with the device owner. `compromise-report` never removes malicious records automatically; read [its evidence and privacy limits](./install/security#review-suspected-compromise-before-resuming-work) before interpreting counts.

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
