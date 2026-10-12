# Privacy

Keep private memory and runtime data out of Git and public support posts. Review [Concepts](./getting-started/concepts) first to distinguish local state from private exchange artifacts. You will know what to protect and what to inspect before sharing diagnostic excerpts.

## Keep private material out of Git

Never commit or publish:

- Memories, transcripts and database snapshots.
- SQLite databases and their WAL/SHM files.
- Bootstrap ZIPs and manifests, including as release assets; installed `app/baseline.jsonl.gz`, `app/bootstrap-manifest.json` and `data/baseline.jsonl`.
- Change packets, raw status reports and logs.
- Credentials, DSNs and device-specific deployment files, including private `data/join-state.json` recipient secrets.

The repository contains code, tests and documentation. A private bootstrap contains real memory; it is not a public software download. Its extracted `app` directory also contains private snapshot material. Protect the entire installation root and never share `app` as a code-only bundle.

## Separate local storage from exchange

Keep local databases, WAL/SHM files, credentials and deployment configuration outside the Syncthing exchange. Only the intended private exchange artifacts should travel between trusted peers. Record-level sync does not make live database-file synchronization safe.

Invitation request and invitation transfer files contain no memory rows or secrets, but carry group/device metadata and are unsigned. Transfer them only through an authenticated trusted channel outside the exchange; never transfer the prepared installation itself.

## Understand integrity checks

File hashes detect changes relative to a trusted expected hash. They do not authenticate the sender or protect against replacement of both a file and its checksum manifest. Restrict access to the exchange folder. Signing and trust configuration are separate operator topics; see the [reference index](./reference/).

## Protect keys and their backups

[Passphrase protection](./install/security#protect-the-device-key-at-rest) encrypts the device signing key at rest in `identity.json`. It helps protect copied identity directories, backups and disk images, and detects tampering with protected key/public fields. It does not encrypt the memory database, packets or transcripts. It does not protect an unlocked process from code running as the same user, nor prevent guessing a weak/reused passphrase. Use a long unique passphrase; there is no recovery for a lost one.

OS unlock adds a wrapping key in the account’s OS store. Any process of that user can read it, making the device key effectively always unlockable by that user. This is not hardware-isolated signing. macOS currently uses an interim file-based login-keychain backend with `/usr/bin/security`’s default item ACL; Windows uses a DPAPI-protected Credential Manager item. Neither changes the private-file or trusted-peer boundaries.

Keep the protected `identity.json` and its passphrase available through your private backup procedure. An identity-directory copy alone does not carry the OS wrapping key, but OS backups may: Time Machine/Migration Assistant can copy the login keychain, and Windows profile/password or domain DPAPI recovery material can recover credentials. A backup containing both unlock paths is only as strong as the **weaker of the passphrase and OS login password**.

Earlier backups, snapshots, journaled or copy-on-write storage and SSD blocks can retain the old plaintext key after protection. Protect raw-key backups separately; migration cannot erase all old copies. If exposure matters, use the operator’s coordinated key rotation/pairing procedure. No passphrase-change, unprotect or backup/restore command is supplied by this feature; do not invent a restore by editing identity fields.

Disable failures can leave a wrapping key in the OS store. That key plus an older identity-file backup containing its envelope can still unlock the old identity. Resolve deletion failures and check `stray_item`; status checks only the current key ID and does not clean up old or rotated IDs. See the [key-protection threat model and portability limits](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/KEY-PROTECTION.md#threats-and-portability).

## Handle status and screenshots carefully

Peer status reports contain operational metadata rather than memory record bodies. They still include sync identifiers, and publication errors can expose filesystem paths. Do not treat raw status JSON as guaranteed safe to publish.

Insight can display memory excerpts. Its redaction is best effort, so keep screenshots, exported excerpts and runtime logs private too.

See the [repository privacy boundary](https://github.com/theidentify/AgentMesh/blob/main/README.md#privacy), [Windows setup boundaries](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-WINDOWS.md#important-boundaries), and [Insight privacy details](https://github.com/theidentify/AgentMesh/blob/main/insight/README.md#read-only-and-privacy-boundary).

Next, use the [privacy-safe help template](./troubleshooting#request-help-without-sharing-memory) if you need support, or [Reference](./reference/index) for deeper trust procedures.
