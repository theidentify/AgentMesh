# Signed sync

Signed sync authenticates change packets against explicitly trusted peers, helping reject forged or unauthorized changes. Begin with an [existing installation](../getting-started/quick-start) or [new empty group](./new-group), and keep its runtime path available. This page helps you inspect security readiness and resume setup; coordinated activation remains an operator procedure.

::: info
These commands configure the AgentMesh memory-sync application, not Hermes or other agents. They do not install Python, Syncthing or security dependencies, and do not start a worker.
:::

For initial local testing, the default is unsigned (`legacy`) sync; you can defer this opt-in setup. It relies on a trusted private exchange folder and does not authenticate packets. If your group requires signed sync, coordinate activation before relying on it.

## Understand the protection

Ed25519 signatures authenticate packet metadata against an approved peer key, including the group, persistent sender identifier and device identity. The sender identifier distinguishes a signing installation; it is separate from the device identity used to allocate record IDs. Signing does not encrypt memory or restrict an approved writer to particular records. Status JSON remains unsigned telemetry, and a signature check alone does not prove records were applied.

Keep private keys and trust files outside Git and the exchange. Peer proposals are not automatically trusted: verify the full fingerprint and scope through an independent authenticated channel. See [Signed sync](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md) for the trust boundary and dependency setup.

::: warning
Before revoking a peer key, follow the operator’s [rotation and revocation procedure](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md#rotation-and-revocation). Before any explicit rotation approval, revocation blocks new, unapplied packets from that key. For lost or compromised keys, follow the [recovery decision and peer approval procedure](#recover-a-lost-or-compromised-key). Previously committed packets remain accepted when they match the recorded receipt and complete envelope digest, including the signature; retaining those exact packets does not make them invalid or stop the managed worker. New packets from a revoked key and tampered replays remain invalid and can stop managed execution. Preserve packets, receipts/history, database backups and trust state; never delete history to hide a failure. Older receipts need the retained pinned public key to verify their first replay and record the full envelope digest, so keep revoked public-key records.
:::

## Inspect readiness

From the installed `app` directory (or the repository's `memory-sync` directory):

```sh
python3 agentmesh.py wizard-status --runtime "/path/to/runtime.json"
```

For an unsigned empty test group (exit `0`):

Output (run on macOS)

```json
{"policy":"legacy","pairing":"unknown","wizard_step":"prerequisites","next_action":"check_prerequisites","prerequisites":{"crypto_ready":false,"exchange_ready":true,"python_ready":true},"roundtrip":"pending"}
```

Here `crypto_ready: false` identifies a missing dependency in the test interpreter. Status exit `0` means inspection succeeded, not that setup is ready. Installation/path errors exit `1`.

This is read-only. It reports policy, pairing state, wizard step, next action and prerequisite readiness. A successful status command does not mean signing is active. No global `--database` argument is needed: the existing runtime identifies the database.

## Resume deliberately

```sh
python3 agentmesh.py wizard-resume --runtime "/path/to/runtime.json"
```

Output (example from code)

Numbered prerequisite, identity, pairing and probe steps with explicit confirmations. Pending setup exits `2`; blocked setup exits `1`. This command is interactive and can write private state after confirmation. It resumes identity creation, peer approval and an isolated signed probe using the existing group and device identity. Missing prerequisites leave setup pending; missing or mismatched persistent identity requires recovery rather than generating replacement keys.

::: warning
The interactive wizard can reach activation prompts. Before approving them, follow the operator's [coordinated upgrade boundary](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SECURITY-WIZARD.md#upgrade-activation-separate-coordinated): all peers must be coordinated, unsigned history drained and backups preserved. A probe receipt does not prove production memory convergence or all peers' readiness.
:::

`wizard-resume` exits `0` when signing policy is `required`, wizard state is `active`, pairing is `approved` and prerequisites are ready; incomplete setup exits `2`, and a blocked operation exits `1`. Managed workers now resolve explicit security paths or the same defaults as the wizard; a standard bootstrap needs no manual runtime patch. The wizard does not rewrite OS service definitions. Keep the same identity and follow the [security wizard reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SECURITY-WIZARD.md) for activation and recovery.

## Protect the device key at rest

Use passphrase protection when copies of the signing identity directory or its backups must not expose a usable device key. This encrypts the existing Ed25519 private key in `identity.json`; it does not encrypt memory, change device identity, replace pairing or enable signed policy. Public identity fields, peer trust and the database binding remain unchanged. It requires the security dependencies from [KEY-PROTECTION.md](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/KEY-PROTECTION.md).

Use this order before relying on a protected key in a worker:

1. If no signing identity exists, create it through the coordinated `wizard-resume` procedure above first. Do not generate an unrelated replacement during setup; use [explicit rotation](#recover-a-lost-or-compromised-key) when key replacement is needed. Stop/drain all workers before changing protection or unlock policy.
2. Protect the existing identity using the command below. Use an [encrypted identity backup](#back-up-and-verify-the-device-identity) for portable recovery. Keep older security-directory copies private because they may contain the raw key.
3. For unattended signed workers on macOS or Windows, explicitly enable OS unlock under the **same account that runs the worker**, then inspect its status.
4. Resume any unfinished pairing/activation with `wizard-resume`. It tries OS unlock first and, if necessary, asks for the passphrase at most once per run. That interactive unlock is confined to the wizard process; it does not unlock a subsequently started worker.
5. After signing is configured and OS unlock is available, start a fresh managed worker and check its cycles. An already signed installation keeps its existing policy and identity; protection alone does not require new pairing.

From the installed `app` or checkout’s `memory-sync` directory, in an interactive console:

```sh
python3 agentmesh.py identity-protect --security-dir "/absolute/path/to/identity"
```

Windows Command Prompt:

```bat
py -3 agentmesh.py identity-protect --security-dir "C:\path\to\identity"
```

Enter a long, unique passphrase of at least **12 characters**, then repeat it; neither entry is echoed. There is no passphrase argument, environment-variable, file or piped-input option. A disposable interactive run exited `0` with this excerpt (the full output also includes `key_id`):

Output (run on macOS)

```json
{"status":"protected"}
```

Require `status: protected`; refusal/error exits `1`. A missing console, short or mismatched passphrase, or an already protected key is refused. The file is checked before atomic replacement and checked again afterwards. The protected key starts locked: signing refuses until an interactive unlock or explicitly enabled OS unlock succeeds. There is no in-place passphrase-change or unprotect command. A forgotten identity passphrase cannot be recovered, but an existing encrypted identity backup can [restore the same identity under a new passphrase](#restore-only-after-stopping-the-old-device) using its separate backup passphrase. See [locked-key troubleshooting](../troubleshooting#protected-key-or-os-unlock-refuses).

## Enable unattended OS unlock

OS unlock keeps the passphrase-protected copy and adds a second unlock path using a random wrapping key in the worker account’s OS store. It is opt-in, for device identity keys only; release signing keys are excluded. A copied identity directory alone remains locked on another account or machine. Same-user processes can read the wrapping key, so this trades protection from that user’s processes for unattended operation; review [Privacy](../privacy#protect-keys-and-their-backups).

After protecting the identity, while workers are stopped:

```sh
python3 agentmesh.py identity-os-unlock enable --security-dir "/absolute/path/to/identity"
```

Windows Command Prompt:

```bat
py -3 agentmesh.py identity-os-unlock enable --security-dir "C:\path\to\identity"
```

Enable asks for the existing passphrase once in an interactive console. It stores and verifies a fresh wrapping key before committing the OS-unlock envelope. Native OS operations below are examples derived from code; documentation verification used only the repository’s in-memory test backend, never the login keychain.

Output (example from code)

```json
{"backend":"macos-keychain-file","status":"enabled"}
```

Successful enable exits `0`; errors exit `1`. Windows reports `backend: windows-credential`. Inspect availability before starting the worker:

```sh
python3 agentmesh.py identity-os-unlock status --security-dir "/absolute/path/to/identity"
```

On Windows use the same one-line command with `py -3` and a Windows path.

Output (example from code)

```json
{"protected":true,"os_unlock":"enabled","backend":"macos-keychain-file","available":true,"reason":null,"stray_item":false}
```

These excerpts omit `key_id`. Status is read-only but **reads the OS store** to verify decryption or detect a leftover item. Exit `0` means inspection completed, even when `available` is `false`; inspect `reason` and `stray_item`. Availability does not prove the later worker’s account/session can unlock or that synchronization succeeded.

Once `wizard-resume` has completed coordinated signed activation, start the configured worker in a new process:

```sh
python3 agentmesh.py worker-start --runtime "/absolute/path/to/runtime.json"
```

Use `py -3` and a Windows path in Command Prompt. For signed policy, omit `--legacy-drained`. Require successful startup and healthy [worker status and cycles](../use/worker#use-managed-execution), not only a successful OS-unlock status query. The worker reads the OS store at most once per identity per process and caches success or refusal. **Restart after fixing a refusal**; a repaired store does not make the same refused process retry. Neither workers nor one-shot/foreground sync commands prompt for a passphrase or fall back to plaintext/unsigned signing. Legacy policy does not use the signing identity; it is not an unlock workaround.

On macOS, the interim `macos-keychain-file` backend uses `/usr/bin/security` with the account’s explicit login keychain from the account database, not `HOME`, the default keychain or its search list. It keeps the tool’s default item ACL. It is a file-based login-keychain backend, not Secure Enclave protection; the planned signed data-protection-keychain replacement is not implemented. Use it in the logged-in user’s LaunchAgent session. A locked or password-out-of-sync keychain may show a system dialog; the store operation is capped at 15 seconds, but terminating the tool does not guarantee the dialog closes.

Windows uses that user’s Credential Manager generic credential with local-machine persistence and DPAPI protection, through native APIs, for an interactive-token logon task. Linux has no OS-unlock backend: an interactive wizard unlock does not provide unattended unlock to a later worker. Keep the existing protection intact and plan supported operation with the operator.

## Disable OS unlock and check cleanup

Stop workers first. Disable needs no passphrase and leaves passphrase protection in place:

```sh
python3 agentmesh.py identity-os-unlock disable --security-dir "/absolute/path/to/identity"
python3 agentmesh.py identity-os-unlock status --security-dir "/absolute/path/to/identity"
```

On Windows enter the commands separately with `py -3` and Windows paths. Successful disable exits `0` with this excerpt:

Output (example from code)

```json
{"status":"disabled"}
```

Disable removes the envelope from `identity.json` first, then deletes the account’s stored wrapping key. If deletion fails it exits `1`; restore store access and run disable again. Status should then show `os_unlock: absent`, `available: false`, `stray_item: false` and no store error. `protected: true` remains true. A process already holding the key in memory stays unlocked until it exits, so stop/restart workers when changing this policy. A newly started signed worker will refuse the protected key without a usable unlock path. See [cleanup and refusal checks](../troubleshooting#protected-key-or-os-unlock-refuses).

## Back up and verify the device identity

Create an encrypted backup after establishing the signing identity, before reinstalling or retiring its device, and again after restoring or changing trust. This preserves the device key and a historical trust snapshot, **not SQLite memory, runtime configuration or exchange packets**. Keep those through their separate [backup procedure](../troubleshooting#preserve-a-consistent-database-backup).

Use offline media outside Git and Syncthing. Keep a long, unique backup passphrase separately from the file and from the identity passphrase, with a recovery plan for that passphrase. The command rejects detected Git/Syncthing locations and existing output files; the parent directory must already exist. Location checks cannot detect every custom sync root. Detected cloud storage requires the additional word **CLOUD**, but offline storage is preferable: encryption still exposes public metadata and allows offline guessing.

From an interactive console in the installed `app` or checkout's `memory-sync` directory:

```sh
python3 agentmesh.py identity-backup create --security-dir "/path/to/identity" \
  --output "/path/to/offline-media/identity-backup.json"
```

On Windows, use `py -3`, Windows paths and one line without the trailing backslash. For a protected identity, enter its current identity passphrase; creation does **not** use OS unlock. A raw identity can also be backed up. Review the displayed public identity and type **BACKUP**, then enter a new backup passphrase twice. It must have at least **12 characters** and differ from the identity passphrase when one was used. No passphrase arguments, environment variables, files, pipes or `--yes` bypass are supported.

Output (run on macOS)

```json
{"permissions":"owner-only","status":"created"}
```

This excerpt omits `key_id`, `file_sha256` and `verified_at`. Exit `0` with `status: created` means the newly written ciphertext was read back and verified before evidence was recorded. Refusals exit `1`. `permissions: unsupported-by-filesystem` means the medium cannot enforce private permissions; encryption does not make its public metadata private. See [backup storage privacy](../privacy#protect-keys-and-their-backups).

Authenticate the stored file again, then exercise recovery without adopting the identity:

```sh
python3 agentmesh.py identity-backup verify --file "/path/to/offline-media/identity-backup.json" \
  --security-dir "/path/to/identity"
python3 agentmesh.py identity-backup drill --file "/path/to/offline-media/identity-backup.json" \
  --security-dir "/path/to/identity"
```

Each asks for `Backup passphrase:`. The respective result excerpts were:

Output (run on macOS)

```json
{"status":"verified"}
{"status":"drill-passed"}
```

Both exited `0`. Verify authenticates the complete file. Drill additionally creates two temporary protected identities, signs and verifies a probe and signed acknowledgement, then removes both directories. It does not write to the exchange, adopt an identity or require **FENCED**. A failure exits `1`; an offline drill does not prove a real peer still trusts this key.

`--security-dir` is optional for verify/drill. When supplied, evidence is recorded only if the full public identity matches the backup. `key_id_mismatch: true` means the backup check succeeded but it did not match that identity; do not count it as that device's backup evidence. Neither action needs to unlock the existing identity. Both warn if an older backup uses a lower-than-production scrypt work factor.

```sh
python3 agentmesh.py identity-backup status --security-dir "/path/to/identity"
```

Output (run on macOS)

```json
{"state":"drilled"}
```

The full report includes `key_id` and `backups`, with encrypted-file hashes, `created_at`, `verified_at`, `drilled_at` and their ages in seconds. State is `none`, `verified` or `drilled`. This read-only command reports past evidence from private `backup-status.json`; it does not prove the file still exists or can still be read. Re-run verify or drill against the stored file when you need current evidence.

## Restore only after stopping the old device

Restore preserves the same device identity; it is **not a way to add a device**. Stop or wipe the old installation and prevent it from running again before restoring. A disconnected old device is not necessarily stopped. Restore itself never starts, stops or signals workers, changes databases, writes the exchange or enables signed policy.

Use the group's accepted exchange folder, containing `.stfolder` and at least one status file for the backup's group. Restore checks file modification times for this device identity's `status/<node>.json`, `changes/<node>/` and `signed-changes/<node>/`; `<node>` is the node slot. Any activity within 15 minutes, future-dated file or unreadable entry refuses restore. The check covers the entire device identity's namespace, not only this key's packets, and is repeated immediately before adoption. Do not delete packets or alter timestamps to bypass it. A quiet exchange is only a heuristic; your **FENCED** confirmation is the actual assurance that the old device cannot run.

Choose a private target outside Git, Syncthing and the exchange, with **no `identity.json`**. Restore can create the target directory if its parent exists. Retain any current `trust.json` in the intended target so its revocations and newer identities remain authoritative; do not choose a blank target to evade a refusal.

```sh
python3 agentmesh.py identity-restore --file "/path/to/offline-media/identity-backup.json" \
  --security-dir "/path/to/restored-identity" --exchange "/path/to/exchange"
```

Enter the backup passphrase, review the authenticated identity, trust capture time and exchange activity, then type **FENCED** only after actually fencing the old installation. For each new active peer offered from the backup, independently confirm and type its **full 64-character fingerprint** to pin it. Enter or a wrong fingerprint skips the peer; an occupied identity is skipped too. Re-pair skipped peers through the wizard. Existing peer entries remain, and revocation in either trust copy wins; backup-only revoked peers are imported as revoked without a prompt.

Finally enter a new identity passphrase twice, at least 12 characters and different from the backup passphrase. The old identity passphrase is unnecessary. The restored identity is always passphrase-protected, even if the source identity was raw; neither the old protection envelope nor its OS-unlock envelope is exported in the backup.

Output (run on macOS; disposable OS store)

```json
{"status":"restored","stray_os_unlock_item":false}
```

The disposable test exited `0`, preserved the public identity and left the restored key locked without an OS-unlock envelope. Only the stray-item check used the repository's in-memory OS-store helper; no login keychain was accessed. Full output includes `key_id`, `pinned`, `skipped`, `kept_revoked`, `imported_revoked` and `next`. Success includes reading back the restored identity with the new passphrase. Old target backup evidence is removed; its backup status starts at `none` until a new check is recorded.

Before normal operation:

1. Review the trust results and ensure the installation's configured security path selects this restored identity. Restore does not rewrite runtime or wizard state; preserve their original bindings during reinstall rather than creating a second identity.
2. If `stray_os_unlock_item: true`, explicitly use [OS-unlock disable or enable](#disable-os-unlock-and-check-cleanup) to remove or replace that item. A successful restore does not enable OS unlock.
3. Complete a **fresh live signed roundtrip with a real peer**, using the operator's [wizard probe procedure](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SECURITY-WIZARD.md#signed-roundtrip-evidence). An earlier wizard success or offline drill is insufficient. Restore knows only local revocations; a remote peer may still refuse this key.
4. For an unattended signed worker, [enable OS unlock](#enable-unattended-os-unlock) with the new identity passphrase under its actual account. Then explicitly [start the worker](../use/worker#use-managed-execution), review its cycles, and create a new encrypted backup with a fresh output filename.

::: warning A resumed probe may be old
The current wizard can reuse its saved probe and an existing signed receipt; it has no force-new-probe option. A successful resume is therefore not, by itself, evidence of fresh peer contact after restore. If retained wizard state would reuse an old probe, obtain an operator-reviewed fresh-probe procedure before restarting. Do not delete or edit wizard state to manufacture a successful check.
:::

Restore refuses an existing identity, unfinished restore artifacts, incompatible current trust or a locally revoked own key. It does not make a compromised key safe, recover a missing backup or restore SQLite data. Keep evidence and follow [restore refusal guidance](../troubleshooting#identity-backup-or-restore-refuses); do not overwrite identities, remove revocations or manufacture an exchange status file to make a restore pass.

## Recover a lost or compromised key

Choose recovery according to whether the old key is still trustworthy. Rotation replaces the signing key while preserving the sender, group and device identity; it does not add a device or restore memory. The database must already use required signed policy. Never roll back SQLite to repair a key.

| Situation | Procedure |
| --- | --- |
| Lost/damaged key, usable trustworthy backup, no suspicion of compromise | [Restore the same identity](#restore-only-after-stopping-the-old-device) after fencing the old installation. |
| Lost/unavailable key without a usable backup | Rotate with `--reason lost`. |
| Suspected compromise | Immediately revoke on **every peer**, then rotate with `--reason compromised` and review evidence everywhere. |
| Planned replacement of a usable key | Rotate with `--reason planned`. |

A backup cannot make a compromised or revoked key safe. Restoring into empty local trust does not authorize that key on remote peers. Do not reactivate a compromised key to clear a backlog.

### Revoke a compromised key first

Get the full old fingerprint from trusted records. On every peer, run from the installed `app` or checkout's `memory-sync` directory:

```sh
printf 'Old fingerprint from trusted records: '
read -r OLD_KEY
python3 signed_packets.py --security-dir "/path/to/peer-identity" revoke --fingerprint "$OLD_KEY"
```

These are POSIX shell examples. On Windows use `py -3`, Windows paths and your shell's input/variable syntax; enter multiline commands on one line without backslashes. Revocation returned exit `0` in the disposable test. The response contains trust metadata, so keep it private. `peer-rotate` can approve replacement of an already revoked old key.

### Stop execution and rotate locally

Stop the rotating device's managed worker through [worker control](../use/worker), stop foreground/service execution through its own controls, and stop any database clones elsewhere. Local locks cannot fence a clone on another machine. Keep those copies stopped throughout recovery.

```sh
python3 agentmesh.py identity-rotate --security-dir "/path/to/identity" \
  --db "/path/to/memory.db" --exchange "/path/to/exchange" \
  --runtime "/path/to/runtime.json" --reason compromised
```

Use `lost` or `planned` instead when appropriate. `--runtime` is optional, but include the installation's runtime when available so rotation checks managed execution and holds its lifetime lock as well as the database cycle lock. It must name the same database and exchange. These actions use `--db`, not the global `--database` argument.

If the entire security directory is lost, add `--old-key "$OLD_KEY"`, using the old full fingerprint from trusted records. With an identity file present, its public key ID takes precedence; otherwise the explicit old key or a single matching active trust entry identifies the key. The old private key need not be available or unlockable. Do not delete an identity or trust store to force a different selection.

Type **STOPPED** only after all copies have stopped. The exchange must have `.stfolder` and genuine status for this group. Rotation refuses recent activity within 15 minutes, future-dated files or unreadable entries in this device identity's exchange paths, using the [same activity check as restore](#restore-only-after-stopping-the-old-device). Do not change timestamps or delete packets to bypass it. If a supposedly unpublished pending packet already exists in the exchange, stop and inspect the refusal.

For a compromised key, first [review each pending packet and decide whether to exclude any](#review-or-exclude-compromised-pending-packets) before approving publication. When pending packets remain selected for publication, review `pending_packets`, `packets_by_format` and `operations_by_table`, then type **PUBLISH** to authorize publication. Enter a new identity passphrase twice, at least 12 characters, at the interactive console. There is no passphrase or `--yes` automation option. A refusal is not authorization to edit outbox records manually.

Output (run on macOS; disposable OS store)

```json
{"dropped_pending":[],"manifest_packets":2,"os_unlock_item_removed":true,"published_pending":1,"reason":"compromised","status":"rotated","trust_restored":true}
```

This exit-`0` excerpt came from a test with one previously published and one pending ordinary change packet; it omits fingerprints and paths. The full report includes `old_key_id`, `new_key_id`, the complete `new_fingerprint`, `proposal` and `next`. It publishes a proposal and adjacent `.manifest` under `pairing/rotations/` in the exchange. Use the actual proposal path from the report. The new identity is protected, OS unlock remains off, and no worker is started. Selected pending v2/v3 packets, including resolution packets, are published byte for byte with their original old-key signatures and included in the manifest. Rotation does not re-sign them or change their evidence references. The test used the repository's fake OS backend for old-item removal, not the login keychain.

`trust_restored: false` means prior local trust was unavailable. Re-pin every peer through the operator's [explicit trust procedure](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md#explicit-pairing-cli), independently confirming fingerprints and scope. Do not infer peer authorization from proposal files.

### Review or exclude compromised pending packets

With `--reason compromised`, `pending_review` lists every pending packet before the publication confirmation: `uuid`, `format`, `changes` with `table`, `op` and `key_sha256`, plus `kind` for v3 packets. Operation values are `upsert` or `delete`; v3 kind is `change` or `resolution`. The key hash is a 16-hex prefix, not the raw row key. Row contents are not displayed. Review this local metadata before typing **PUBLISH**.

If you decide to exclude a packet after viewing the list, cancel at **PUBLISH** before a journal is created, then repeat the fresh compromised rotation with its actual UUID:

```sh
printf 'Reviewed pending packet UUID to archive: '
read -r DROP_PACKET
python3 agentmesh.py identity-rotate --security-dir "/path/to/identity" \
  --db "/path/to/memory.db" --exchange "/path/to/exchange" \
  --runtime "/path/to/runtime.json" --reason compromised --drop-pending "$DROP_PACKET"
```

Repeat `--drop-pending` for additional reviewed UUIDs. It is accepted only on a fresh `--reason compromised` rotation, never with `--resume`. A selected packet must be pending; the command refuses if another pending packet references it through `covers` or `evidence`. It does not silently drop dependants.

Excluded packets are not published or listed in the manifest. At successful completion their exact stored bytes move from the outbox to the local `_sync_rotation_dropped` archive, and `dropped_pending` lists their UUIDs. This preserves evidence and does not undo local memory changes. `published_pending` counts the remaining pending packets published by this rotation; if none remain, there is no **PUBLISH** prompt. Do not edit the archive or outbox to force a different outcome.

### Approve the new key on every peer

Read the **full new fingerprint from the rotating device's terminal report** to each peer operator by phone or in person. Never obtain the approving fingerprint from the exchange or proposal. Once the proposal and its matching adjacent manifest arrive, run on every other device:

```sh
python3 agentmesh.py peer-rotate --security-dir "/path/to/peer-identity" \
  --proposal "/path/to/rotation-proposal.json" --exchange "/path/to/exchange"
```

Replace the proposal placeholder with the actual file. Review the displayed fingerprint prefixes, reason, creation time and manifest count. At the prompt, type the complete new fingerprint received independently. A mismatch refuses without approving it. If there is a warning about competing proposals, contact the device owner before choosing; the warning does not identify which proposal is legitimate.

Output (run on macOS)

```json
{"competing_proposals":0,"manifest_packets":2,"reason":"compromised","status":"peer-rotated"}
```

Exit `0` means this peer revoked the old entry and pinned the new key. It does not update other peers or all application pins. Re-run wizard pairing to update its saved peer identity; if you use the separate profile-memory prototype, update its `SignedChannel` peer fingerprint too. Rotation does not rewrite either pin. Check [trust in every direction](#check-trust-in-every-direction) before resuming normal work. A running peer worker does not need a restart merely to notice `peer-rotate`: changed registry metadata invalidates its cached manifest authorization, including a cached missing entry. A worker already stopped or failed still needs explicit startup after review; the rotating device remains stopped until you start it.

Proposals use `agentmesh-key-rotation-v2` and local registries use `agentmesh-rotations-v2`. V1 proposals and registries are refused. If you hold v1 material, preserve it and request operator inspection; there is no documented conversion command, and changing its format label cannot make it a valid v2 proposal.

After approval, a manifest-listed packet can authorize resolution evidence even if it was never committed on this peer before revocation. Its UUID and complete envelope digest, including the signature, must match, and historical signature verification must pass. This gives authorization state `verified`, not `applied`; it creates no receipt and does not reactivate the old key. See [Resolve conflicts](../use/resolve-conflicts#handle-refusals-without-rewriting-history).

### Review suspected compromise before resuming work

Run this on **every device, including the rotating device**, with the old fingerprint from trusted records:

```sh
python3 agentmesh.py compromise-report --db "/path/to/memory.db" \
  --exchange "/path/to/exchange" --key "$OLD_KEY" --security-dir "/path/to/identity"
```

The command is read-only; it does not unlock the identity. It scans old-key-labelled exchange files and compares local receipt, outbox and approved-manifest evidence. It does not independently verify packet signatures, so it is an investigation report, not proof that the old key signed every listed file or that its contents were legitimate.

On the receiving test device, two old-key packets appeared in `committed` with `in_manifest: true`. This excerpt omits those records' identifiers, file time and change metadata:

Output (run on macOS)

```json
{"filtered_by_since":0,"forged_candidates":[],"legacy_receipts":0,"receipts_without_file":0,"uncommitted":[],"unreadable":0}
```

Exit `0` means the report completed, not that compromise is absent. Interpret the full report:

- `committed` matches local receipts; `uncommitted` lists other observed files for the selected key. A sender's own publication need not have a local receive receipt.
- `in_manifest` compares the UUID and complete envelope digest (including the signature) with the locally approved manifest. Without `--security-dir` it is `null`; with no registered manifest it is `false`. Investigate committed entries outside the manifest; inclusion is not a verdict on the legitimacy of the original data.
- V3 packet entries and their change summaries include `kind: change` or `kind: resolution`. When the resolution store exists, `superseded` means a committed disposition matches this exact envelope; `variant_superseded` means a disposition exists for a different envelope with the same UUID. A changed file cannot inherit the original's superseded status.
- `forged_candidates` flags files claiming this device identity that differ from its outbox bytes. Investigate them rather than treating the label as a verdict.
- `receipts_without_file` counts full-envelope receipt records without an observed packet file; `legacy_receipts` counts receipts without a full-envelope record. Both are database-wide counts, not attribution to the selected key. `unreadable` counts files that could not be inspected.
- Optional `--since` takes a UTC ISO timestamp and filters **file modification times**; `filtered_by_since` counts excluded old-key files. A writer can change these times, so this is not a trusted incident-time boundary.

The report never removes, quarantines or undoes records. Published packet files and historical receipts remain unchanged by rotation; selected pending packets are published byte-identically, retaining their UUID, body, checksum and old-key signature. Records already committed remain committed, including malicious changes. Review and repair data separately through supported data procedures. Do not delete exchange history or roll back the database to make a key usable.

### Finish or resume recovery

If `os_unlock_item_removed: false`, arrange removal of the **old fingerprint's** OS-store item with the operator. After rotation, `identity-os-unlock disable` targets the **new installed key**, so it cannot clean up the old item. `null` means no supported OS backend was selected. Do not infer old-item cleanup from successful rotation alone.

After peer approvals and review, create and drill a [new encrypted backup](#back-up-and-verify-the-device-identity). Retire/delete backups of the revoked key under your custody policy; deletion is not guaranteed secure erasure. Enable [OS unlock for the new key](#enable-unattended-os-unlock) if unattended workers need it, then explicitly [start the worker](../use/worker#use-managed-execution) and inspect its cycles.

If interrupted after `rotation-journal.json` was written, recovery is forward only:

```sh
python3 agentmesh.py identity-rotate --security-dir "/path/to/identity" \
  --db "/path/to/memory.db" --exchange "/path/to/exchange" \
  --runtime "/path/to/runtime.json" --resume
```

Resume repeats **STOPPED**, locks and exchange checks, then asks for `New identity passphrase:`—the passphrase chosen for the interrupted rotation. It completes publication of the journaled original bytes; `published_pending` retains the original count even on a retry. Identical files already published by this journal are accepted. Do not start a second rotation or choose another new key. Ordinary signing/publishing refuses while the journal exists. Never delete the journal or restore older trust/identity/SQLite files to bypass it. If only staging remains without a journal, request inspection; `--resume` requires a valid journal. Old re-signing journals are refused as `invalid rotation journal`; preserve them for inspection rather than deleting or reinterpreting them. See [rotation refusals](../troubleshooting#key-rotation-or-peer-approval-refuses).

### Quarantine a differing file during resume

If a journal-bound exchange file differs from the authoritative outbox, resume refuses with `pending packet file differs from the outbox; see compromise-report`. Inspect the evidence first. For a reviewed collision, repeat the resume with the affected packet UUID:

```sh
printf 'Journal-bound colliding packet UUID: '
read -r COLLISION_PACKET
python3 agentmesh.py identity-rotate --security-dir "/path/to/identity" \
  --db "/path/to/memory.db" --exchange "/path/to/exchange" \
  --runtime "/path/to/runtime.json" --resume --quarantine-collision "$COLLISION_PACKET"
```

Repeat `--quarantine-collision` for additional collisions. It is accepted only with `--resume`, for differing files bound to that journal's pending packets. After **STOPPED**, the command displays `uuid`, `exchange_envelope_digest` and `outbox_envelope_digest`; review both digests and type **QUARANTINE** for each selected collision. Cancelling leaves the conflicting file in place.

The confirmed bytes are preserved under `pairing/rotations/quarantine/` in the exchange, in a filename formed from the packet UUID and exchange envelope digest. The conflicting file leaves the publication path, and resume continues with the original outbox bytes. Quarantine does not authorize the moved packet. Supported envelope JSON can be quarantined even if it claims a different UUID or scope; malformed JSON or unsupported formats require separate operator inspection. Unrelated recent exchange activity still refuses recovery.

## Check trust in every direction

Trust is local to each receiver; approval does not pass through another device. In a three-device group, verify all six directions: A accepts B and C, B accepts A and C, and C accepts A and B. Confirm each sender’s key independently and verify actual receipt in every direction. A↔B and A↔C do not establish B↔C trust.

The wizard records one selected peer pair. Its `pairing: approved` and round-trip result describe that recorded pair, not all trust relationships or delivery across the group. Use the operator’s [explicit pairing procedure](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md#explicit-pairing-cli) for each receiver/sender relationship.

## Respect packet limits

Two representations must each fit **8 MiB (8 × 1024 × 1024 bytes)**: the internal typed binary encoding used for signing and the UTF-8 JSON bytes written to the packet file. File size only measures the latter; a smaller file does not prove the internal encoding fits. Structure is limited to 64 levels of nesting and 100,000 encoded values, counting dictionary keys as values. The validator checks these limits; users need not count them manually. Its errors include `encoded packet too large`, `packet too large` and `packet structure limit exceeded`, though worker reports can expose only the error class.

A **diff** is the set of changed database records captured together, not a Git diff. The current capture step does not automatically split a large diff. See [signed_packets.py](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/signed_packets.py) for the exact validation.

A **backlog** is local record changes or captured outgoing packets still waiting to be published. An oversized accumulated backlog will still fail after reducing `--interval`; that only changes the wait before another attempt. Preserve a consistent database backup (including outbox/history), original transcripts, packets, runtime/workflow and private signing/trust state. Report the action, exit code and error class through the [help template](../troubleshooting#request-help-without-sharing-memory), keeping raw material private. No supported automatic backlog-splitting recovery command exists in the code; do not edit or split signed bytes by hand.

Next, [control the worker](../use/worker) using the runtime and signing policy already configured for this installation.
