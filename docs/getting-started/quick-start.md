# Quick start

Get from a supplied private bootstrap to your first memory recall. Start with the [prerequisites](./prerequisites) and an unused [device identity](./concepts#device-identity) in the supplied group. At the end, you will have checked sync and connected your agents to local recall.

## Prepare and install

1. Complete the [Python and Syncthing checks](./prerequisites), accept the private exchange and wait for **Up to Date**.
2. Follow the installation page for [macOS](../install/macos), [Linux](../install/linux) or [Windows](../install/windows). Confirm the installer's default device identity is unused before running it.
3. Keep the foreground worker open. AgentMesh installs memory sync, not the agent applications.

If you have no bootstrap and want a separate empty group, use [New sync group](../install/new-group) instead. Running that on another device does not join this group.

## Check memory before connecting agents

1. [Check local and peer status](../use/status). Syncthing completing a transfer does not prove the records were imported.
2. [Run your first recall](../use/recall#run-a-recall) against the installed database. An empty result means the query found no supporting memory.
3. [Install recall rules for your agents](../use/recall#connect-your-agents), using the same database path.

Next, [control the worker](../use/worker) to choose one-time, foreground or managed operation.
