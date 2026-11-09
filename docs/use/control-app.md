# Menu bar app

Watch AgentMesh's background jobs from the macOS menu bar, answer the questions they raise, and check the keys and devices this profile holds. Start with a Mac where the AgentMesh supervisor (`org.agentmesh.control`) is installed and a repository checkout to build from. You will have AgentMesh Control in the menu bar, know what each icon state means, and know which warnings need you.

::: info
The app is a plain client of the supervisor. It has no authority of its own: every action is an ordinary request the supervisor checks and audits, exactly like the `agentmesh-log control` command. Nothing in it reads memory content or opens a key file.
:::

## Install the app

Use a Swift 6 toolchain on macOS 14 or later; the Command Line Tools are enough. From the repository root:

```sh
control-app/bundle.sh
```

This builds and installs `~/Applications/AgentMesh Control.app` and the Touch ID helper the supervisor uses (`~/.agentmesh/bin/agentmesh-presence`). Quit a running copy of the app first; the script refuses to replace it while it runs.

Open the app from `~/Applications`, then tick **Launch at login** in its menu. macOS asks once for permission to show notifications. The menu shows the app's version and build.

## Read the icon

The icon is three nodes and a hub, with one corner badge for the most important state:

| State | Icon |
| --- | --- |
| Supervisor unreachable | Faded mark, empty ring |
| A job failed, is down or is in conflict | Red dot |
| A job is failing or stuck, or a key or device warning needs you | Orange dot |
| A new project needs a scope | Blue dot with a question mark |
| A service is moving between launchd and the supervisor | Crossing arrows |
| Maintenance mode | Pause bars |
| A job is running | Green dot, and the hub turns into a spinning ring |
| All healthy | Green dot |

The app checks every 10 seconds, every 5 seconds with the menu open and every 2 seconds while a job runs, so short runs such as catch-up still show.

## Control jobs and services

Each row shows a job or service: its health, whether launchd or the supervisor runs it, its process or last exit, and when it last ran. Start, restart, stop, reset and maintenance mode act at once, within the supervisor's rate limits.

Moving a service between launchd and the supervisor is a protected change. The supervisor raises its own Touch ID or Mac password sheet; the app cannot answer it, and neither can any other client. Approve a sheet only if you started that move. The supervisor allows at most three such prompts every ten minutes, denials included.

## Run everything as one background item

Each AgentMesh plist in `~/Library/LaunchAgents` shows as its own entry under **System Settings > General > Login Items & Extensions > Allow in the Background**. The supervisor can run all of them itself, so macOS lists one entry, `org.agentmesh.control`, shown under AgentMesh Control when the app is installed. Read the plan first; it changes nothing:

```sh
agentmesh-log control consolidate
```

It lists each unit with a plist the supervisor can reproduce: services (KeepAlive), timer jobs (StartInterval or StartCalendarInterval) and run-once jobs (RunAtLoad only). A plist it cannot reproduce exactly, such as a KeepAlive dictionary, is listed as `skip` and stays with launchd. Then apply it in your own terminal:

```sh
agentmesh-log control consolidate --apply
```

It asks you to type `migrate`. For each unit it waits for a running job to finish, disables and boots out the launchd job, and moves its plist to `~/.agentmesh/control/units/`. The supervisor starts it within a second. Logs stay at the paths the plist names, and job status stays in `~/.agentmesh/status/`, so Insight and this app show the same health as before. The supervisor also writes `~/.agentmesh/status/supervisor.json` with the process of each unit it runs. A unit that fails to move stays with launchd and is listed under `errors`.

Under the supervisor:

- A service that exits starts again after its ThrottleInterval (10 seconds unless the plist says otherwise). Quick exits in a row back off, doubling up to 5 minutes; a run that lasts a minute resets the backoff.
- **Stop** keeps a service down until **Start** or the next supervisor start. Under launchd a service cannot be stopped this way, because launchd restarts it at once.
- A run-once job runs once each time the supervisor starts.
- Restarting the supervisor restarts every service it runs.

To change what a unit runs, edit its plist in `~/.agentmesh/control/units/` and run `agentmesh-log control reload UNIT`. `insight/deploy_insight.py` does this for Insight. To give one unit back to launchd, run `agentmesh-log control release UNIT`. To give back all of them without the supervisor, run `agentmesh-log control panic-revert`. Both move the plist back to `~/Library/LaunchAgents` first.

## Choose a scope for a new project

When an agent starts working in a project the migration mapping does not cover, catch-up imports everything else and holds that project's memory back. The app asks once, in a notification and in the menu, where it should go: one button per scope (for example Personal or Work), or **Skip** to keep it out of the profile log.

Choosing a scope sends the request to the supervisor, which asks for Touch ID or your Mac password before it adds the mapping. The held-back memory imports on the next catch-up. This only adds a route for a waiting project; it never changes an existing one.

## Check keys and devices

Expand **Keys & devices** for a read-only inventory. The supervisor builds it from file metadata and the broker's content-free status; it never opens a key file or the keystore. Warnings come first, then:

- **Devices:** each paired device, with its platform and when it was last seen.
- **Agents:** each grant, by name, or `unnamed (…)` with the first characters of its reference when settings never named it, and what it may read.
- **Passkeys:** the passkeys the broker accepts for approvals, with their label, when they were added and from which device.
- **Key files:** each agent key file and the broker keystore, with its permissions (`0600` is owner-only), the agent it belongs to and its age.
- **Recovery and backup:** whether a recovery kit exists, and when the last backup was made.

| Finding | Kind | What to do |
| --- | --- | --- |
| A key file or the keystore is not a private, owner-only file | Warning | Restore owner-only permissions (`chmod 600`), or replace a symbolic link with the real file |
| A device has not been seen for over 14 days | Warning | Check that device, or revoke it if it is gone |
| No recovery kit is set up | Warning | Create one with the [profile-log runbook](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#devices-and-recovery) |
| No backup recorded, or the last backup is over 30 days old | Warning | Run `python -m agentmesh_log.backup create`, described in the [broker reference](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#broker) |
| The broker has not refreshed the inventory for a day | Warning | Check that the broker is running |
| A key file matches no grant | Info | Usually left over after an agent was removed |
| A secret grant is open | Info | None, unless you did not open it |
| No passkey is set up for approvals | Info | Needed only for approvals from the status page |

Each new warning sends one notification and turns the icon orange. Information never notifies.

## Know when it notifies

| Event | Notification |
| --- | --- |
| A job fails, goes down or conflicts | At once |
| A job stays failing or stuck for 10 minutes | Once, after the 10 minutes |
| A job you were notified about recovers | Once |
| The supervisor does not answer for 2 minutes | Once, and once more when it answers again |
| A new project needs a scope | Once per project, with a button per scope |
| A new key or device warning | Once per warning |

Jobs that are not installed never notify. The supervisor also sends its own alerts to a Discord webhook when one is configured.

## Remove the app

Quit the app from its menu, untick **Launch at login** first if it is on, then delete `~/Applications/AgentMesh Control.app` and `~/.agentmesh/bin/agentmesh-presence`. Without the helper, the supervisor refuses moves that need Touch ID.

Next, see the [control app reference](https://github.com/theidentify/AgentMesh/blob/main/control-app/README.md) for build details, or return to [Recall memory](./recall).
