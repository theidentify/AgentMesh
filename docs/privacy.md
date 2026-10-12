# Privacy

Keep private memory and runtime data out of Git and public support posts. Review [Concepts](./getting-started/concepts) first to distinguish local state from private exchange artifacts. You will know what to protect and what to inspect before sharing diagnostic excerpts.

## Keep private material out of Git

Never commit or publish:

- Memories, transcripts and database snapshots.
- SQLite databases and their WAL/SHM files.
- Bootstrap ZIPs and manifests, including as release assets.
- Change packets, raw status reports and logs.
- Credentials, DSNs and device-specific deployment files.

The repository contains code, tests and documentation. A private bootstrap contains real memory; it is not a public software download.

## Separate local storage from exchange

Keep local databases, WAL/SHM files, credentials and deployment configuration outside the Syncthing exchange. Only the intended private exchange artifacts should travel between trusted peers. Record-level sync does not make live database-file synchronization safe.

## Understand integrity checks

File hashes detect changes relative to a trusted expected hash. They do not authenticate the sender or protect against replacement of both a file and its checksum manifest. Restrict access to the exchange folder. Signing and trust configuration are separate operator topics; see the [reference index](./reference/).

## Handle status and screenshots carefully

Peer status reports contain operational metadata rather than memory record bodies. They still include sync identifiers, and publication errors can expose filesystem paths. Do not treat raw status JSON as guaranteed safe to publish.

Insight can display memory excerpts. Its redaction is best effort, so keep screenshots, exported excerpts and runtime logs private too.

See the [repository privacy boundary](https://github.com/theidentify/AgentMesh/blob/main/README.md#privacy), [Windows setup boundaries](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-WINDOWS.md#important-boundaries), and [Insight privacy details](https://github.com/theidentify/AgentMesh/blob/main/insight/README.md#read-only-and-privacy-boundary).

Next, use the [privacy-safe help template](./troubleshooting#request-help-without-sharing-memory) if you need support, or [Reference](./reference/index) for deeper trust procedures.
