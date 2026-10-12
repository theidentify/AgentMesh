# New sync group

Create an empty local database when you are starting alone without a supplied bootstrap. Complete the [prerequisites](../getting-started/prerequisites), including Python 3.11+, first. You will have local memory that you can ingest and recall from a repository checkout; this route cannot add another device yet.

::: info
AgentMesh manages memory sync, not installation of Hermes or other agents. `setup-new` creates local state only; it does not copy application code, start a worker or join an existing group.
:::

## Prepare the source and storage

Obtain the [AgentMesh repository](https://github.com/theidentify/AgentMesh) and open its `memory-sync` directory in a terminal. Keep this checkout at a stable local path outside the exchange folder; it supplies the application for this route. Use that directory wherever this guide says “installed app or repository source.” There is no generated `app` directory to point service examples at. Select a source revision reviewed for your deployment and record it before creating state; this guide has not verified a recommended release or compatibility matrix. Do not assume an arbitrary latest checkout is a tested upgrade.

Use a dedicated exchange folder for each sync group, with no packets or history from another group. The current setup checks do not reject that mix; a successful setup is not proof that reuse is safe. Do not clear an existing group’s folder to reuse it. Add a new private local folder in Syncthing and let Syncthing create `.stfolder`, as described in [prerequisites](../getting-started/prerequisites#share-the-private-exchange-folder). The new local installation directory must not exist, and its parent must already exist. The local installation and exchange folder must not be nested inside one another. Use absolute paths without parent traversal or symlink components, including in ancestor directories.

## Create local memory

From `memory-sync`:

macOS/Linux:

```sh
python3 agentmesh.py setup-new \
  --local-dir "/absolute/path/to/new-installation" \
  --exchange "/absolute/path/to/exchange" --node mac
```

Windows Command Prompt:

```bat
py -3 agentmesh.py setup-new --local-dir "C:\absolute\path\to\new-installation" --exchange "C:\absolute\path\to\exchange" --node mac
```

The prompt asks you to type `NEW`. After confirmation, exit `0`:

Output (run on macOS)

```json
{"format":"agentmesh-setup-new-v1","group":"<UUID>","node":"mac","runtime":"/path/to/new-installation/data/runtime.json","status":"created"}
```

Success is `status: created`. Declining leaves `status: pending` and exits `2`; a validation or setup error exits `1`. The `--node` value selects the node slot independently of OS; the command creates a new group UUID. Running it again on another device creates a different group.

The new directory contains `data/memory.db`, `data/runtime.json` and `data/workflow.json`, with ingestion and summarization disabled. There are no signing keys or running services. If `.setup-pending.json` remains, preserve the entire partial directory and [request help](../troubleshooting#request-help-without-sharing-memory); do not delete its marker to bypass validation. There is no supported generic partial-setup repair command. Report the failed action, exit code and presence of the marker without sharing its contents or paths.

## Inspect and use the database

macOS/Linux:

```sh
python3 agentmesh.py inspect-install --runtime "/path/to/new-installation/data/runtime.json"
```

Windows Command Prompt:

```bat
py -3 agentmesh.py inspect-install --runtime "C:\path\to\new-installation\data\runtime.json"
```

Exit `0`:

Output (run on macOS)

```json
{"database":"/path/to/new-installation/data/memory.db","exchange":"/path/to/exchange","format":"agentmesh-install-inspect-v1","group":"<UUID>","identity":"absent","node":"mac","policy":"legacy","status":"ready"}
```

`ready` means the existing local installation passed inspection, not that a worker is running. `identity: absent` means no signing-key directory exists; the database already has its device identity. A missing runtime can return `not_found` with exit `0`; an invalid installation exits `1`.

Use this database path for [manual ingestion](../use/ingest) and [recall](../use/recall), running from the checkout's `memory-sync` directory. Manual ingestion must be repeated when new sessions arrive. To automate supported local sources later, use the [workflow example](../use/ingest#configure-automatic-ingestion) and [worker control](../use/worker); no summaries is normal while summarization is disabled.

## Adding another device

**A group created with `setup-new` cannot add another device yet.** The invitation-based join feature is being implemented separately. Do not copy the initialized database, create another group with the same label, or manufacture a new baseline to work around this limitation.

The existing [package builder](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/build_package.py) can build a private peer bootstrap from an already available `omp-sqlite-snapshot-v1` snapshot and an explicitly chosen group UUID. This is a separate operator path, not an export/join procedure for your new SQLite group. The repository has no supported SQLite-to-bootstrap snapshot exporter preserving the required baseline history. If you already have a compatible snapshot, whoever administers that group—possibly you—must follow the [private bootstrap reference](https://github.com/theidentify/AgentMesh/blob/main/README.md#private-bootstrap-construction), retain the common baseline and assign distinct device identities.

Next, [ingest a supported local session](../use/ingest) to give your new database something to recall.
