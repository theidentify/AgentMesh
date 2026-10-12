# New sync group

Create an empty local database when you are starting alone without a supplied bootstrap. Complete the [prerequisites](../getting-started/prerequisites), including Python 3.11+, first. You will have local memory that you can ingest and recall from a repository checkout; eligible unsigned groups can add two more devices through the invitation procedure below.

::: info
AgentMesh manages memory sync, not installation of Hermes or other agents. `setup-new` creates local state only; it does not copy application code, start a worker or join an existing group.
:::

## Prepare the source and storage

Obtain the [AgentMesh repository](https://github.com/theidentify/AgentMesh) and open its `memory-sync` directory in a terminal. Keep this checkout at a stable local path outside the exchange folder; it supplies the application for this route. Use that directory wherever this guide says “installed app or repository source.” There is no generated `app` directory to point service examples at. Select a source revision reviewed for your deployment and record it before creating state; this guide has not verified a recommended release or compatibility matrix. Do not assume an arbitrary latest checkout is a tested upgrade.

Use a dedicated exchange folder for each sync group, with no packets or history from another group. `setup-new` now refuses prior content, including packet directories, bootstrap files and retained Syncthing versions. Only `.stfolder`, a `.stignore` file and an empty `.stversions` directory are allowed. Do not clear an existing group’s folder to reuse it. Add a new private local folder in Syncthing and let Syncthing create `.stfolder`, as described in [prerequisites](../getting-started/prerequisites#share-the-private-exchange-folder). The new local installation directory must not exist, and its parent must already exist. The local installation and exchange folder must not be nested inside one another. Use absolute paths without parent traversal or symlink components, including in ancestor directories.

A disposable attempt to use an exchange containing bootstrap files exited `1` before creating the local directory:

Output (run on macOS)

```json
{"error":"ValueError","reason":"exchange folder is already used; each sync group needs its own exchange folder (only .stfolder, .stignore and an empty .stversions are allowed)","next_action":"Choose a new dedicated Syncthing exchange folder; retain the existing group files."}
```

Choose a new dedicated folder; do not delete existing group files to bypass this refusal.

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

Current `setup-new` groups can add a second and third device through invitations, while the group remains unsigned. **Only the origin device A can issue invitations**, including the invitation for device C; a joined device B cannot invite. Use current source code on each device, running the commands from its checkout’s `memory-sync` directory. Joining creates local state, not an application copy or a running worker.

::: warning
A group has only three device identities in total, including the origin. Issuing an invitation permanently reserves its selected identity—even if the invitation is lost, expires or cannot be written successfully. There is no supported release/reissue command. Invitations are one-time and device-bound, with a default validity of **24 hours**; `--expires-in` accepts 60–604800 seconds (up to seven days). Check the recipient clock and keep the original invitation, origin database and prepared recipient state.
:::

Signed or security-configured groups are refused; keep the group unsigned throughout outstanding joins. Groups created by `setup-new` before this feature lack the required origin provenance and cannot invite yet. A non-empty bootstrap baseline is also unsupported by this invitation flow. Updating code does not create provenance for an older database; do not add records manually or clone state to bypass these checks.

![Device B prepares a request, transfers it outside the exchange to origin A, receives A’s invitation over a trusted channel, then runs setup-join and starts and verifies its worker; copying the origin database is rejected.](/diagrams/join-flow.svg)

**1 · B: prepare-join** creates private recipient state. **Request file** travels through a trusted channel outside the exchange to **2 · A: invite-peer**, which permanently reserves the identity. **Invitation file** returns through that channel to **3 · B: setup-join**, highlighted, which consumes it and replays exchange history. **4 · Start and verify** runs the worker and checks status and recall. The rejected box, **Copy the origin database**, would duplicate its device identity rather than join safely.

### 1. Publish A’s history and prepare B

On A, [ingest the synthetic Quick start transcript](../getting-started/quick-start#prove-a-record-travels-from-a-to-b), then let its worker capture and publish it. If no worker is running, run one unsigned cycle:

```sh
python3 agentmesh.py --database "/absolute/path/to/A/data/memory.db" --exchange "/absolute/path/to/exchange" once
```

In Windows Command Prompt, use `py -3` and Windows paths on that same line. The disposable run exited `0` with no classified failure; check the [cycle output and failure rules](../use/worker#run-a-basic-unsigned-cycle-or-loop). Ingestion alone does not publish the record.

Share A’s existing exchange with B through Syncthing, accept it on B and wait for the **full history** to finish transferring. Unlike `setup-new`, joining uses the existing group’s exchange. Never delete exchange packets: replay depends on them. Missing predecessors can leave replay pending; a missing newest packet can silently omit data even when join reports success. An invitation is not a history inventory.

B’s new local root must not exist and its parent must already exist. Select an unused device identity; `--node windows` selects that node slot on any OS. Use absolute paths without symlinks. Request and invitation files must be outside both the installation and exchange, with existing parent directories and unused output filenames. The filenames below are your chosen paths, not fixed defaults.

macOS/Linux:

```sh
python3 agentmesh.py prepare-join --local-dir "/absolute/path/to/B" \
  --exchange "/absolute/path/to/exchange" --node windows \
  --output "/absolute/path/to/transfer/b-request.json"
```

Windows Command Prompt:

```bat
py -3 agentmesh.py prepare-join --local-dir "C:\path\to\B" --exchange "C:\path\to\exchange" --node windows --output "C:\path\to\transfer\b-request.json"
```

Type `REQUEST`. Exit `0` and `status: prepared` confirm creation:

Output (run on macOS)

```json
{"node":"windows","request":"/path/to/transfer/b-request.json","status":"prepared"}
```

Keep B’s entire prepared directory on B. Its `data/join-state.json` contains the private recipient secret; never transfer or copy it. `.setup-pending.json` guards this incomplete installation.

### 2. Transfer the request and invite from A

Transfer only `b-request.json` to A through a trusted channel. Neither request nor invitation contains memory rows or secrets, but they carry group/device metadata and are **unsigned**: authenticate the sender and channel. Keep transferred files owner-only (`chmod 600` on POSIX; private ACLs on Windows), as the commands require. On **origin A**:

macOS/Linux:

```sh
python3 agentmesh.py invite-peer --runtime "/absolute/path/to/A/data/runtime.json" \
  --request "/absolute/path/to/transfer/b-request.json" \
  --output "/absolute/path/to/transfer/b-invitation.json"
```

Windows Command Prompt:

```bat
py -3 agentmesh.py invite-peer --runtime "C:\path\to\A\data\runtime.json" --request "C:\path\to\transfer\b-request.json" --output "C:\path\to\transfer\b-invitation.json"
```

Type `INVITE` only after reviewing the permanent reservation. Exit `0` and `status: issued` confirm issuance; this excerpt omits group and expiry metadata:

Output (run on macOS)

```json
{"invitation":"/path/to/transfer/b-invitation.json","node":"windows","status":"issued"}
```

The full result includes `group` and `expires_at`. This is a reservation, not proof B has joined. Do not assume a silent or offline device’s identity is free.

### 3. Transfer the invitation back and join B

Transfer only `b-invitation.json` back to the **same prepared B**, outside its installation and exchange. On B:

macOS/Linux:

```sh
python3 agentmesh.py setup-join --local-dir "/absolute/path/to/B" \
  --exchange "/absolute/path/to/exchange" \
  --invitation "/absolute/path/to/transfer/b-invitation.json"
```

Windows Command Prompt:

```bat
py -3 agentmesh.py setup-join --local-dir "C:\path\to\B" --exchange "C:\path\to\exchange" --invitation "C:\path\to\transfer\b-invitation.json"
```

Type `JOIN`. With one published synthetic packet, exit `0`:

Output (run on macOS)

```json
{"database":"/path/to/B/data/memory.db","failure_kind":null,"receive":{"applied":1,"conflict":0,"invalid":0,"pending":0,"unavailable":false},"runtime":"/path/to/B/data/runtime.json","status":"joined"}
```

Require `status: joined`. Counts depend on retained history. Completion creates `data/memory.db`, `data/runtime.json` and `data/workflow.json`, removes the pending guard, and leaves ingestion and summarization disabled. Join reads the exchange without publishing or starting a worker. A pending or blocked result is not a completed installation.

### 4. Resume an incomplete replay {#resume-an-incomplete-replay}

If predecessors are still arriving, retain all prepared state and allow transport to finish. In a disposable third-device test with one predecessor delayed, join exited `2`:

Output (run on macOS)

```json
{"failure_kind":"pending-history","receive":{"applied":0,"conflict":0,"invalid":0,"pending":1,"unavailable":false},"status":"pending"}
```

On the affected device, resume the consumed invitation without passing `--invitation` again:

macOS/Linux:

```sh
python3 agentmesh.py setup-join --local-dir "/absolute/path/to/B" \
  --exchange "/absolute/path/to/exchange" --resume
```

Windows Command Prompt:

```bat
py -3 agentmesh.py setup-join --local-dir "C:\path\to\B" --exchange "C:\path\to\exchange" --resume
```

Type `RESUME`. After the missing predecessor arrived, the test exited `0`:

Output (run on macOS)

```json
{"failure_kind":null,"receive":{"applied":2,"conflict":0,"invalid":0,"pending":0,"unavailable":false},"status":"joined"}
```

Resume continues the existing reservation even after invitation expiry; it does not issue another invitation. Conflicts or invalid packets produce `status: blocked`, `failure_kind: permanent` and exit `1`; unavailable exchange leaves a pending result with `failure_kind: transient` and exit `2`. Preserve state for [join troubleshooting](../troubleshooting#invitation-join-is-refused-or-incomplete). Workers remain blocked while `.setup-pending.json` exists; never remove it to bypass replay.

### 5. Start the worker and verify delivery

After `joined`, confirm no other worker is running, then start B’s unsigned managed worker:

```sh
python3 agentmesh.py worker-start --runtime "/absolute/path/to/B/data/runtime.json" --legacy-drained
python3 agentmesh.py --database "/absolute/path/to/B/data/memory.db" status
python3 agentmesh.py --database "/absolute/path/to/B/data/memory.db" recall "amber orchard verification" --limit 1
```

In Windows Command Prompt, enter these as separate one-line commands using `py -3` and Windows paths. All three commands exited `0` in the disposable run. Status reported `received: 1`, one observation event, and zero pending/conflict/invalid counts. Recall returned:

Output (run on macOS)

```json
{"results":[{"content":"The amber orchard verification record is synthetic.","project":"GuideCheck","source_agent":"omp","source_type":"event"}]}
```

Worker startup and zero diagnostic counts do not prove complete history. Require the expected A-only record on B, then perform a fresh [A → B recall test](../getting-started/quick-start#prove-a-record-travels-from-a-to-b) to verify ongoing delivery. The disposable test used a local exchange, not real Syncthing transport. Configure [owned ingestion roots](../use/ingest#configure-automatic-ingestion) separately if B should ingest its own sessions.

For device C, repeat preparation and join with the remaining identity and fresh transfer filenames; **A issues C’s invitation**. Never copy A or B’s database or prepared installation. There is no fourth identity or automatic recycling of a lost reservation.

### Automate confirmations deliberately

Append the matching pair to the corresponding command: `--yes --confirm REQUEST` for `prepare-join`, `--yes --confirm INVITE` for `invite-peer`, `--yes --confirm JOIN` for initial `setup-join`, and `--yes --confirm RESUME` with `setup-join --resume`. `--yes` alone or a mismatched word is refused. These pairs were exercised in the third-device flow. They do not apply to `setup-new`, which still asks for `NEW`.

Declining a join confirmation returns `status: pending` and exit `2` without performing that confirmed action. Successful preparation/issuance/join exits `0`; guards and errors exit `1`. Inspect the result as well as the exit code: `pending` can also mean replay is incomplete. See the [invitation contract](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-SETUP.md#invitation-and-replay-contract) for preservation and trust details.

Next, [check sync status](../use/status) and confirm the expected record on each joined device.
