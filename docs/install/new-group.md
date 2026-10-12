# New sync group

Create an empty local database and a separate sync group when you have no supplied bootstrap. First complete the [prerequisites](../getting-started/prerequisites) and understand [device identity](../getting-started/concepts#device-identity). You will finish with local database and runtime files; application installation, ingestion and worker startup are separate steps.

::: info
AgentMesh manages memory sync, not installation of Hermes or other agents. This command creates local state only; it does not copy application code or join an existing group.
:::

## Choose empty, separate storage

Use Python 3.11 or later for this command. Unlike the bootstrap wrappers' Python 3.10 minimum, this implementation uses `hashlib.file_digest`, added in Python 3.11.

Use an accepted private Syncthing exchange containing its `.stfolder` directory. The new local directory must not exist, its parent must already exist, and neither directory may contain the other. Use absolute paths without parent traversal or symlink components.

If you received a bootstrap, use [Quick start](../getting-started/quick-start) instead. Each invocation below creates a new group, so do not repeat it on peers to join them.

## Create the group

From the repository root:

```sh
python3 memory-sync/agentmesh.py setup-new \
  --local-dir /absolute/path/to/new-installation \
  --exchange /absolute/path/to/exchange --node mac
```

The literal value after `--node` selects a device identity, regardless of the operating system. Review the prompt and type `NEW` only if you intend to create a separate empty group. No global `--database` argument is needed.

The command creates `data/memory.db`, `data/runtime.json` and `data/workflow.json` beneath your chosen local directory. Ingestion and summarization are both disabled in this new workflow. It creates no signing keys and starts no service or worker. A retained `.setup-pending.json` indicates incomplete setup requiring review; do not bypass it by copying state or rerunning into another identity.

## Inspect the result

```sh
python3 memory-sync/agentmesh.py inspect-install \
  --runtime /absolute/path/to/new-installation/data/runtime.json
```

Keep these files private and outside the exchange. To add another device to this same group, arrange a private bootstrap with the operator; creating another empty group is not a join procedure. The implementation is in [install_setup.py](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/install_setup.py).

Next, [ingest agent sessions](../use/ingest) manually to give this empty database something to recall; use the repository's `memory-sync` directory for those source commands.
