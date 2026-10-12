# Troubleshooting

## Nothing syncs between devices

Direct exchange needs overlapping online time, a working Syncthing connection and workers on both devices. Bring both devices online, keep Syncthing running, and wait for the exchange to show **Up to Date**. Then [check local and peer status](./use/status). File transfer completion alone does not prove that a worker imported the records.

## Two workers are running

A foreground worker may still be open after you enabled a service. Stop the extra foreground worker with Ctrl-C, or stop the service using the [background-service instructions](./install/background-service). Keep one worker for the installation. Managed-worker status does not account for unrelated source workers.

## Records are quarantined

Independent edits to the same row or natural unique key can conflict. Inspect the counts in the exchange's `status/<node>.json` and [local status](./use/status). Pending packets may be waiting for dependencies; conflicts require review, not an assumption that another retry will merge them.

Do not edit packets or discard state to force a merge. There is no automatic conflict-resolution command. Keep backups before operator-led reconciliation and follow [Safety and conflicts](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SYNC.md#safety-and-conflicts).

## Installer says the checksum does not match

The bootstrap may still be syncing or may be corrupted. The package check can report `package checksum mismatch; wait for Syncthing to finish`; snapshot validation can report `baseline checksum mismatch`.

Wait for **Up to Date**, then retry the installer. Never edit the ZIP or its manifest to bypass validation. If the error persists, have the trusted package provider verify the archive and matching manifest before retrying.

## Windows window closed and sync stopped

The initial Windows launcher runs a foreground worker. Reopen **START-WINDOWS.cmd** after confirming that another worker is not already running. For startup after login, follow [Windows sign-in start](./install/background-service#windows-sign-in-start); it requires separate installed program state.

## I copied my database to another machine

Copying an initialized database also copies its node identity and local sync state. Running that identity on two active devices is unsupported. Stop the duplicate worker and keep the original state intact for review. Use the supplied bootstrap and a distinct supported identity for the other device; do not overwrite either database to repair this casually.

Review [node identities](./getting-started/concepts#node) and the [bootstrap reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SYNC.md#bootstrap-and-run) before reinitializing anything.
