# Privacy

Keep private memory and runtime data out of Git and public support posts. Review [Concepts](./getting-started/concepts) first to distinguish local state from private exchange artifacts. You will know what to protect and what to inspect before sharing diagnostic excerpts.

## Keep private material out of Git

Never commit or publish:

- Memories, transcripts and database snapshots.
- SQLite databases and their WAL/SHM files.
- Bootstrap ZIPs and manifests, including as release assets; installed `app/baseline.jsonl.gz`, `app/bootstrap-manifest.json` and `data/baseline.jsonl`.
- Change packets, raw status reports and logs.
- Credentials, DSNs and device-specific deployment files, including private `data/join-state.json` recipient secrets.
- Signing identities, trust files and encrypted identity backups, even when their keys are passphrase-protected.

The repository contains code, tests and documentation. A private bootstrap contains real memory; it is not a public software download. Its extracted `app` directory also contains private snapshot material. Protect the entire installation root and never share `app` as a code-only bundle.

## Separate local storage from exchange

Keep local databases, WAL/SHM files, credentials and deployment configuration outside the Syncthing exchange. Only the intended private exchange artifacts should travel between trusted peers. Record-level sync does not make live database-file synchronization safe.

Invitation request and invitation transfer files contain no memory rows or secrets, but carry group/device metadata and are unsigned. Transfer them only through an authenticated trusted channel outside the exchange; never transfer the prepared installation itself.

## Understand integrity checks

File hashes detect changes relative to a trusted expected hash. They do not authenticate the sender or protect against replacement of both a file and its checksum manifest. Restrict access to the exchange folder. Signing and trust configuration are separate operator topics; see the [reference index](./reference/).

## Protect keys and their backups

[Passphrase protection](./install/security#protect-the-device-key-at-rest) encrypts the device signing key at rest in `identity.json`. It helps protect copied identity directories, backups and disk images, and detects tampering with protected key/public fields. It does not encrypt the memory database, packets or transcripts. It does not protect an unlocked process from code running as the same user, nor prevent guessing a weak/reused passphrase. Use a long unique passphrase. A lost passphrase cannot be recovered; an existing encrypted identity backup and its separate passphrase can restore the identity under a new identity passphrase.

OS unlock adds a wrapping key in the account’s OS store. Any process of that user can read it, making the device key effectively always unlockable by that user. This is not hardware-isolated signing. macOS currently uses an interim file-based login-keychain backend with `/usr/bin/security`’s default item ACL; Windows uses a DPAPI-protected Credential Manager item. Neither changes the private-file or trusted-peer boundaries.

Use the [encrypted identity backup and drill procedure](./install/security#back-up-and-verify-the-device-identity) for portable recovery. Keep the backup offline, outside Git and Syncthing, and keep its long, unique passphrase separately from both the file and the identity passphrase. The encrypted payload contains the device key and captured trust; it contains neither the identity protection envelope nor OS unlock. Its public manifest exposes the key ID, public key, sender, group, device identity and creation time to anyone holding the file. Cloud copies permit offline guessing and expose this metadata; location checks are best effort, even when they require `CLOUD`.

Older identity-directory and full-system backups have different risks. An identity-directory copy alone does not carry the OS wrapping key, but OS backups may: Time Machine/Migration Assistant can copy the login keychain, and Windows profile/password or domain DPAPI recovery material can recover credentials. A backup containing both unlock paths is only as strong as the **weaker of the passphrase and OS login password**.

Earlier backups, snapshots, journaled or copy-on-write storage and SSD blocks can retain the old plaintext key after protection. Protect raw-key backups separately; migration cannot erase all old copies. If exposure matters, use the operator’s coordinated key rotation/pairing procedure. Use the verified restore command rather than editing identity fields. Restore requires the old device to be fenced, always writes a protected identity and leaves OS unlock off. It does not restore SQLite memory or make a compromised/revoked key safe. There is still no in-place passphrase-change or unprotect command.

Disable failures can leave a wrapping key in the OS store. That key plus an older identity-file backup containing its envelope can still unlock the old identity. Resolve deletion failures and check `stray_item`; status checks only the current key ID and does not clean up old or rotated IDs. See the [key-protection threat model and portability limits](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/KEY-PROTECTION.md#threats-and-portability).

Backup verification and drill evidence records past checks, not current file availability; same-user code can forge it. Decrypted backup keys and passphrases remain in process memory, which Python cannot reliably wipe. Encryption does not protect against malicious code running as that user.

## Keep compromise reports private

[Compromise review](./install/security#review-suspected-compromise-before-resuming-work) shows packet identifiers, device identity, file modification times, table names, upsert/delete operations and a 16-hex SHA-256 prefix for each row key. It omits row contents and raw row keys, but the remaining metadata is sensitive and key hashes are not anonymization. Do not publish raw reports or rotation proposals/manifests; they expose identity relationships and packet history. Share only deliberately redacted counts and fixed refusal reasons.

The report compares local evidence; it does not independently authenticate signatures or prove that a listed change is malicious. File times are mutable, and receipt totals cannot all be attributed to the selected key. The read-only report does not undo committed changes or erase evidence.

Rotation journals contain the sealed new identity and public recovery metadata. Keep them private and intact until recovery finishes. Create and drill a backup of the new active key; retire backups of revoked keys under your custody policy. Neither unlinking a file nor Python process cleanup guarantees that old secret material has been securely erased.

## Handle status and screenshots carefully

Peer status reports contain operational metadata rather than memory record bodies. They still include sync identifiers, and publication errors can expose filesystem paths. Do not treat raw status JSON as guaranteed safe to publish.

Insight can display memory excerpts. Its redaction is best effort, so keep screenshots, exported excerpts and runtime logs private too.

See the [repository privacy boundary](https://github.com/theidentify/AgentMesh/blob/main/README.md#privacy), [Windows setup boundaries](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-WINDOWS.md#important-boundaries), and [Insight privacy details](https://github.com/theidentify/AgentMesh/blob/main/insight/README.md#read-only-and-privacy-boundary).

Next, use the [privacy-safe help template](./troubleshooting#request-help-without-sharing-memory) if you need support, or [Reference](./reference/index) for deeper trust procedures.
