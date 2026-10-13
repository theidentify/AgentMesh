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
Before revoking a peer key, follow the operator’s [rotation and revocation procedure](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md#rotation-and-revocation). Revocation blocks new, unapplied packets from that key. Previously committed packets remain accepted when they match the recorded receipt and complete envelope digest, including the signature; retaining those exact packets does not make them invalid or stop the managed worker. New packets from a revoked key and tampered replays remain invalid and can stop managed execution. Preserve packets, receipts/history, database backups and trust state; never delete history to hide a failure. Older receipts need the retained pinned public key to verify their first replay and record the full envelope digest, so keep revoked public-key records.
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

1. If no signing identity exists, create it through the coordinated `wizard-resume` procedure above first. Do not generate a replacement for an existing identity. Stop/drain all workers before changing protection or unlock policy.
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

## Check trust in every direction

Trust is local to each receiver; approval does not pass through another device. In a three-device group, verify all six directions: A accepts B and C, B accepts A and C, and C accepts A and B. Confirm each sender’s key independently and verify actual receipt in every direction. A↔B and A↔C do not establish B↔C trust.

The wizard records one selected peer pair. Its `pairing: approved` and round-trip result describe that recorded pair, not all trust relationships or delivery across the group. Use the operator’s [explicit pairing procedure](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SIGNED-SYNC.md#explicit-pairing-cli) for each receiver/sender relationship.

## Respect packet limits

Two representations must each fit **8 MiB (8 × 1024 × 1024 bytes)**: the internal typed binary encoding used for signing and the UTF-8 JSON bytes written to the packet file. File size only measures the latter; a smaller file does not prove the internal encoding fits. Structure is limited to 64 levels of nesting and 100,000 encoded values, counting dictionary keys as values. The validator checks these limits; users need not count them manually. Its errors include `encoded packet too large`, `packet too large` and `packet structure limit exceeded`, though worker reports can expose only the error class.

A **diff** is the set of changed database records captured together, not a Git diff. The current capture step does not automatically split a large diff. See [signed_packets.py](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/signed_packets.py) for the exact validation.

A **backlog** is local record changes or captured outgoing packets still waiting to be published. An oversized accumulated backlog will still fail after reducing `--interval`; that only changes the wait before another attempt. Preserve a consistent database backup (including outbox/history), original transcripts, packets, runtime/workflow and private signing/trust state. Report the action, exit code and error class through the [help template](../troubleshooting#request-help-without-sharing-memory), keeping raw material private. No supported automatic backlog-splitting recovery command exists in the code; do not edit or split signed bytes by hand.

Next, [control the worker](../use/worker) using the runtime and signing policy already configured for this installation.
