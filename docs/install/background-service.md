# Run in the background

Keep the broker and the transcript jobs running without a terminal. Finish installing on [macOS](./macos),
[Windows](./windows) or [Linux](./linux) and create or join the account first. You will have one background item, the
AgentMesh supervisor, that runs everything else as its children.

## What runs

| Unit | Kind | What it does |
| --- | --- | --- |
| `broker` | service | Answers agents and syncs the profile log (`broker run --sync-interval`) |
| `ingest` | job | Reports what the next summary will read ([details](../use/ingest#the-two-jobs)) |
| `summary` | job | Summarizes new transcript turns into memories, once a day and after a reboot |
| `retention` | job | Your own retention job, on devices that have one; the supervisor only wraps it |
| `bridge`, `insight`, tunnels | service | Optional: the AI-app bridge, the [Insight](../use/insight) dashboard and their tunnels |

Each unit starts life as its own launchd plist (on a Mac) or scheduled task (on Windows). `agentmesh-log control
consolidate --apply` then moves them under the **supervisor**, `org.agentmesh.control`, so the system lists one
background item. The supervisor restarts a service that exits (doubling the wait after quick exits, up to 5 minutes),
runs the jobs on their schedule through the job wrapper, and applies maintenance mode and the summary's daily quota.

## macOS

In plists, paths must be absolute: replace `HOME_DIR` below with your home folder's absolute path (`echo $HOME`).

### 1. The broker

Save this as `~/Library/LaunchAgents/org.agentmesh.profile-log.broker.plist`, with your bucket, endpoint and Keychain
service:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>org.agentmesh.profile-log.broker</string>
  <key>ProgramArguments</key>
  <array>
    <string>HOME_DIR/.agentmesh/venv/bin/python</string>
    <string>-m</string><string>agentmesh_log.cli</string>
    <string>broker</string><string>run</string>
    <string>--home</string><string>HOME_DIR/.agentmesh/profile-log</string>
    <string>--sync-interval</string><string>120</string>
    <string>--s3-bucket</string><string>BUCKET</string>
    <string>--s3-prefix</string><string>v1/</string>
    <string>--s3-endpoint</string><string>https://ACCOUNT.r2.cloudflarestorage.com</string>
    <string>--s3-credentials</string><string>keychain:org.agentmesh.profile-log.r2</string>
  </array>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>Umask</key><integer>63</integer>
  <key>StandardOutPath</key><string>HOME_DIR/.agentmesh/logs/broker.out.log</string>
  <key>StandardErrorPath</key><string>HOME_DIR/.agentmesh/logs/broker.err.log</string>
</dict>
</plist>
```

Keep this label: `connector approve` reads the broker home and the remote flags from this plist. The plist holds only
the Keychain reference, never the credentials. Check it and load it:

```sh
plutil -lint ~/Library/LaunchAgents/org.agentmesh.profile-log.broker.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/org.agentmesh.profile-log.broker.plist
cat ~/.agentmesh/profile-log/sync-status.json
```

`sync-status.json` shows `"outcome": "ok"` after the first sync. See [Troubleshooting](../troubleshooting#sync-does-not-run)
for the other outcomes.

### 2. The transcript jobs

Write the job config described in [Ingest agent sessions](../use/ingest#configuration) first. Each job runs through the
job wrapper. For `ingest`, save `~/Library/LaunchAgents/org.agentmesh.ingest.plist` with:

```xml
<key>Label</key><string>org.agentmesh.ingest</string>
<key>ProgramArguments</key>
<array>
  <string>HOME_DIR/.agentmesh/venv/bin/python</string>
  <string>-m</string><string>agentmesh_log.control.jobrun</string>
  <string>--name</string><string>ingest</string>
  <string>--state-dir</string><string>HOME_DIR/.agentmesh/production-status</string>
  <string>--</string>
  <string>HOME_DIR/.agentmesh/venv/bin/python</string>
  <string>-m</string><string>agentmesh_log.ingest</string><string>scan</string>
</array>
<key>StartInterval</key><integer>3600</integer>
<key>RunAtLoad</key><true/>
```

with the same plist header, `ProcessType`, `Umask` and log keys as the broker. `org.agentmesh.summary` is the same with
`--name summary` and `summary` in place of `scan`. Running the summary hourly is fine: it does its work once a day and
after a reboot, and otherwise ends `scheduled` without calling the model. Add `--webhook-file FILE` before `--` to send
failure and recovery alerts to a Discord webhook whose URL is in that file. Load both with `launchctl bootstrap` as
above.

### 3. The supervisor

Write the supervisor's own plist and load it:

```sh
agentmesh-log control plist --out ~/Library/LaunchAgents/org.agentmesh.control.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/org.agentmesh.control.plist
agentmesh-log control status
```

Then move the other units under it. Read the plan first; it changes nothing:

```sh
agentmesh-log control consolidate
agentmesh-log control consolidate --apply
```

`--apply` asks you to type `migrate`. For each unit it waits for a running job to finish, boots out the launchd job and
parks its plist in `~/.agentmesh/control/units/`; the supervisor starts it within a second. A plist the supervisor
cannot reproduce exactly stays with launchd and is listed as `skip`.

Finally, [install the menu bar app](../use/control-app#install-the-app). It can also [own the supervisor's login
item](../use/control-app#let-agentmesh-control-own-the-background-item), so System Settings shows AgentMesh Control
instead of a Python process.

### Change, release or stop a unit

| Task | Command |
| --- | --- |
| See every unit, its backend and health | `agentmesh-log control units` / `control status` |
| Run a job now, or restart a service | `agentmesh-log control start UNIT` / `control restart UNIT` |
| Stop a service until the next start | `agentmesh-log control stop UNIT` |
| Pause all jobs | `agentmesh-log control maintenance on --reason "…"`, then `off` |
| Change what a unit runs | Edit its plist in `~/.agentmesh/control/units/`, then `agentmesh-log control reload UNIT` |
| Give one unit back to launchd | `agentmesh-log control release UNIT` |
| Give every unit back without the supervisor | `agentmesh-log control panic-revert` |
| Read recent supervisor actions | `agentmesh-log control audit` |

Moving a service between launchd and the supervisor needs Touch ID or your Mac password, which the supervisor asks for
with its own sheet. Logs stay at the paths each plist names; the supervisor's own are
`~/.agentmesh/logs/control.{out,err}.log`.

## Windows

The Windows supervisor works the same way, over a named pipe, with Task Scheduler in place of launchd.

1. **Create a task per unit** in Task Scheduler, running as your user only when you are logged on:
   - `broker`: at log on, with **If the task fails, restart every 1 minute** set. The action runs the venv's
     `python.exe` with `-m agentmesh_log.cli broker run --home %USERPROFILE%\.agentmesh\profile-log --sync-interval 120`
     and the remote flags, using `--s3-credentials wincred:NAME`. Add `--log-dir` with an absolute folder: a task has no
     standard output, so the broker then writes `broker.out.log` and `broker.err.log` there. Write the home as an absolute path: the supervisor
     refuses an environment variable in a task's command.
   - `ingest` and `summary`: repeating every few minutes, running `-m agentmesh_log.control.jobrun --name ingest
     --state-dir ... -- PY -m agentmesh_log.ingest scan` (and `summary`).
2. **Register the supervisor**, from PowerShell:

   ```powershell
   agentmesh-log control task --out "$env:USERPROFILE\.agentmesh\control-task.xml"
   schtasks /Create /XML "$env:USERPROFILE\.agentmesh\control-task.xml" /TN "\AgentMesh\Control"
   schtasks /Run /TN "\AgentMesh\Control"
   ```

3. **Consolidate**: `agentmesh-log control consolidate`, then `--apply`, typing `migrate`. A task registered from an
   elevated window can be moved only from one; consolidate says `needs_elevation` and you re-run it elevated.

Moves that need presence are refused over the pipe, because Windows has no Touch ID helper. Run `consolidate`,
`release` and `panic-revert` in your own terminal.

## Linux

There is no supervisor on Linux yet (`control serve` answers `unsupported_platform`). Run the broker as a systemd user
service:

```ini
# ~/.config/systemd/user/agentmesh-broker.service
[Unit]
Description=AgentMesh broker

[Service]
ExecStart=%h/.agentmesh/venv/bin/python -m agentmesh_log.cli broker run --home %h/.agentmesh/profile-log --sync-interval 120 --filesystem /absolute/path/to/shared
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now agentmesh-broker.service
systemctl --user status agentmesh-broker.service
```

A user service runs while you are logged in, and needs your desktop session for the Secret Service. Run the transcript
jobs from systemd timers or cron with the same job-wrapper commands as on a Mac.

Next, [ingest agent sessions](../use/ingest).
