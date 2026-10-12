# Check sync status

Check local and peer status to distinguish file delivery from completed record imports. Start with a [configured worker](./worker) and its database and runtime paths. You will be able to identify pending changes, conflicts and worker diagnostics before investigating sync problems.

![One worker cycle: ingest new sessions, export local changes as packets, exchange through the shared folder, import peer packets, then write a status report. A row edited on two devices is quarantined rather than silently overwritten.](/diagrams/worker-cycle.svg)

Each cycle runs in arrow order. **Ingest** stores new agent sessions locally. **Export** turns local changes into change packets. The **exchange folder** carries packets from every peer. **Import** validates peer packets and applies them, or keeps them pending until their dependencies arrive. **Report** writes counts and sync health to `status/<node>.json`. The red box is what AgentMesh refuses to do: when the same row was edited on two devices, it quarantines the change instead of overwriting either one.

Run these commands from the installed `app` directory.

## Local status

```sh
python3 agentmesh.py --database /path/to/memory.db status
```

The JSON result contains memory-table counts and local sync state, including unpublished outbox, received, pending, conflict and invalid counts. Status does not scan incoming exchange files; a worker cycle must import them first.

`status` has no action-specific options. Omitting the global database argument produces `agentmesh.py: error: --database is required for this action` and exit code `2`.

## Peer status files

Each completed worker cycle writes `status/<node>.json` in the private exchange, using the database's device identity. It reports platform, counts, sync health and an update timestamp. These are last-reported observations, not proof that a peer is currently running or fully synchronized.

Reports do not include memory record bodies or credential configuration, but they include sync identifiers, and publication error text can contain filesystem paths. Keep raw status files private and inspect them before sharing excerpts. [Insight](./insight) provides a restricted projection for its dashboard.

::: warning
A zero exit code from a status query or the low-level sync command does not mean there are no conflicts. Inspect pending, conflict and invalid counts. The worker's one-cycle command does return failure for recorded conflicts or invalid packets; use both the report and exit status.
:::

## Diagnose a worker

```sh
python3 agentmesh.py diagnose --runtime /path/to/runtime.json
```

For machine-readable output:

```sh
python3 agentmesh.py diagnose --runtime /path/to/runtime.json --json
```

Diagnosis is read-only and redacted; it does not create or repair runtime state. It exits `0` only when the report is `ok`, and `1` otherwise. The existing runtime supplies the database path, so these commands do not require `--database`.

## Managed worker

```sh
python3 agentmesh.py worker-status --runtime /path/to/runtime.json
```

This inspects managed-worker lifecycle state. It does not establish whether an unrelated foreground worker is running; missing managed metadata is not proof that all workers are stopped.

To browse the same information in a dashboard, open [Insight](./insight). See [troubleshooting](../troubleshooting) and the [sync reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SYNC.md) for interpreting conflicts and recovery limits.
