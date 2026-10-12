# Update or remove

Update AgentMesh's program files or stop using it while retaining your memory and configuration. Start with your [installation details](../use/worker#inspect-the-installation-first) and identify whether you use bootstrap source files or a separately installed program bundle. You will know which update path applies and what to preserve when removing the program.

::: info
These procedures concern the AgentMesh memory-sync application, not Hermes or other agents. Keep databases and WAL/SHM files, runtime and workflow configuration, identity and trust, security state, backups and the private exchange.
:::

## Update a bootstrap installation

On macOS or Linux, stop the foreground worker or its [background service](./background-service), wait for the supplied package to finish syncing, then rerun the same [macOS](./macos) or [Linux](./linux) installer with the same local directory and device identity. On Windows, stop the old foreground worker and rerun **START-WINDOWS.cmd**, as described in [Windows setup](./windows#updating). If you chose a different device identity through the Python installer, repeat that explicit selection instead of reverting to the platform default.

The bootstrap extracts application files and preserves an existing database only when its group and device identity match the supplied package. It retains existing workflow settings and recorded security paths. Do not delete a database to get past a mismatch. See the [setup reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-SETUP.md) and [Windows bootstrap reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/README-WINDOWS.md#console-progress).

A macOS standalone replacement has a different ownership and service model; use the operator's [Mac replacement reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/MAC-REPLACEMENT.md). Do not rerun bootstrap over that deployment as an upgrade shortcut.

## Update a separately installed Windows program

These commands require an existing owned program installation, beyond bootstrap alone. Use PowerShell in the new bundle extracted locally outside the exchange, with `agentmesh.exe`, adjacent `BUILD.json` and all bundled files intact. Begin with a read-only plan:

```powershell
.\agentmesh.exe windows-upgrade --runtime 'C:\path\to\runtime.json' --dry-run
```

For a basic unsigned installation, stop prior workers using [worker control](../use/worker) or their original foreground/service controls. Then run:

```powershell
.\agentmesh.exe windows-upgrade --runtime 'C:\path\to\runtime.json' --legacy-drained
```

Review the plan and its single interactive confirmation. This stopped-worker upgrade selects the new program and updates an owned login-task binding; it does not start a worker. Start the selected installed version using [worker control](../use/worker), then check its cycles.

For signed policy, omit `--legacy-drained`. If there is no managed record, `--unmanaged-drained` is a separate acknowledgment that prior source workers really exited. A dry run does not perform the later stopped-worker precheck, so a successful plan alone does not prove execution is ready. Live replacement is a separate operator procedure in the [Windows installation reference](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/WINDOWS-INSTALLATION.md#upgrade-program-rollback-uninstall).

## Roll back or remove a Windows program

Use an external extracted bundle, not an executable running inside the owned program storage. Stop the worker first. For an unsigned installation, preview whichever operation you need:

```powershell
.\agentmesh.exe windows-rollback --runtime 'C:\path\to\runtime.json' --legacy-drained --dry-run
.\agentmesh.exe windows-uninstall --runtime 'C:\path\to\runtime.json' --legacy-drained --dry-run
```

Run only the chosen command again without `--dry-run` after reviewing it. Interactive rollback requires `ROLLBACK`; removal requires `UNINSTALL`. The signed-policy drain rules above also apply.

Rollback selects a previous intact version owned by this installer, changes only program/task selection and does not restart the worker. It does not restore a database. Uninstall removes only the verified owned program files and task; it keeps private data, configuration and ownership/recovery records. Unknown files, changed hashes or uncertain ownership block removal. Preserve incomplete-operation state for operator review instead of deleting it or recreating the installation.

## Remove a macOS or Linux source installation

Stop the foreground worker and disable any configured [background service](./background-service). On macOS, move the unloaded LaunchAgent plist to private storage outside the LaunchAgents directory so it cannot load at login. On Linux, disable the user service and retain its unit privately for possible reuse.

Once no worker or service references it, remove only the application's code directory identified by your installation. Keep the sibling data directory and all private state listed above; do not delete the whole installation root. No unified macOS/Linux uninstall action is provided by this CLI. For an operator-managed macOS bundle, follow [Uninstall while keeping data](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/MAC-REPLACEMENT.md#uninstall-while-keeping-data) instead.

::: warning
These instructions were checked against source and CLI help, not executed against a native Windows or Linux installation. Verify the resulting worker and service state on the target host.
:::

Next, [check sync status](../use/status) after an update or rollback; after removal, follow [Privacy](../privacy) to protect the retained data.
