# Update or remove

Update the AgentMesh code on a device, or take a device out of your account and remove AgentMesh from it. The commands
assume the layout from the [macOS](./macos), [Windows](./windows) or [Linux](./linux) page.

## Update the code

Updates replace the copy under `~/.agentmesh/app`. State, keys, settings and the replica stay where they are.

1. Take a fresh copy from the commit you want to run, into a new folder, and swap it in:

   ```sh
   git archive --format=tar HEAD profile-log | tar -x -C ~/.agentmesh/app.new
   mv ~/.agentmesh/app ~/.agentmesh/app.old && mv ~/.agentmesh/app.new ~/.agentmesh/app
   ```

   Create `~/.agentmesh/app.new` first. On Windows, use the `git archive --format=zip` and `Expand-Archive` commands
   from the Windows page.
2. Install the pinned requirements again, in case they changed:

   ```sh
   ~/.agentmesh/venv/bin/pip install -r ~/.agentmesh/app/profile-log/requirements.txt \
     -r ~/.agentmesh/app/profile-log/requirements-s3.txt
   ```

3. Restart the broker. Jobs pick up the new code at their next run.

   ```sh
   agentmesh-log control restart broker
   agentmesh-log control status
   ```

4. Once the broker syncs (`sync-status.json` shows `ok`) and the next jobs are `ok`, delete `~/.agentmesh/app.old`.

To roll back, swap `app.old` back in and restart the broker the same way.

Other parts update separately:

- **Menu bar app:** quit it, then run `control-app/bundle.sh` again.
- **Insight:** `insight/deploy_insight.py` deploys a release and reloads its unit.
- **The supervisor** itself runs from the same venv. Restart it to load new supervisor code; this restarts every
  service it runs. With the menu bar app owning it, untick and tick **Run supervisor at login**; otherwise run
  `launchctl kickstart -k "gui/$(id -u)/org.agentmesh.control"`.

## Upgrade a device set up before 2026-11

If a device's transcript jobs still run from the `memory-sync` directory, follow the
[migration runbook](./migrate-memory-sync). The SQLite archives that the retirement left behind can be listed with
`agentmesh-log control sqlite-archive list` and deleted for good with `control sqlite-archive delete --archive NAME`,
which asks for typed presence (and Touch ID on a Mac).

## Remove a device

Removing a device from the account is done **from another device** that holds the keys:

```sh
agentmesh-log device list --home ~/.agentmesh/profile-log
agentmesh-log device remove DEVICE_ID --home ~/.agentmesh/profile-log REMOTE
```

`REMOTE` is the four remote flags from the [Quick start](../getting-started/quick-start#conventions). Removal revokes
the device's agents and rotates every scope key it could read. Other devices learn of it at their next sync. The
removed device keeps what it already read, but cannot read anything written later.

## Uninstall from a device

1. Stop everything. On a Mac or Windows, give every unit back and stop the supervisor:

   ```sh
   agentmesh-log control panic-revert
   ```

   Then unload each AgentMesh job: on a Mac, `launchctl bootout "gui/$(id -u)/LABEL"` for
   `org.agentmesh.control`, `org.agentmesh.profile-log.broker` and the jobs, and delete their plists from
   `~/Library/LaunchAgents`; on Windows, delete the tasks under `\AgentMesh\` in Task Scheduler. On Linux,
   `systemctl --user disable --now agentmesh-broker.service`.
2. [Remove the menu bar app](../use/control-app#remove-the-app) on a Mac.
3. [Remove agent recall rules](#remove-agent-recall-rules).
4. Delete `~/.agentmesh` (Windows: `%USERPROFILE%\.agentmesh`). This deletes the device keys, the replica and the
   agent key files. Memory stays in the profile log for your other devices.
5. Delete the credential store items: on a Mac, the `agentmesh.profile-log` items and your bucket credential service
   (`security delete-generic-password -s org.agentmesh.profile-log.r2 -a access-key-id`, and again for
   `secret-access-key`); on Windows, the `agentmesh.profile-log/…` and bucket items in Credential Manager.

Remove the device from the account as well, from another device, as described above. Deleting the folder alone does
not revoke anything.

## Remove agent recall rules

Delete the rule and its command manifest for each agent you connected:

| Agent | Files |
| --- | --- |
| Claude Code | `~/.claude/rules/agentmesh-shared-memory.md` and `agentmesh-shared-memory.command.json` beside it |
| Codex and OMP | `~/.agents/skills/agentmesh-shared-memory/` (the whole folder) |
| Hermes | `<profile>/skills/agentmesh-shared-memory/` |

Then revoke the agent's grant: remove it from `agents` in `settings.json` and run `agentmesh-log apply`. Removal needs
no confirmation.
