# macOS

Install AgentMesh on a Mac: the code and its venv, the bucket credentials in the login Keychain, and the broker. Then
hand everything to the supervisor and the menu bar app. Complete the [prerequisites](../getting-started/prerequisites)
first, and have a checkout of the repository.

## Install the code

A device runs a copy of `profile-log` taken from a known commit, not a live checkout, so pulling in the repository
never changes what runs. From the repository root:

```sh
mkdir -p ~/.agentmesh/app ~/.agentmesh/bin && chmod 700 ~/.agentmesh
git archive --format=tar HEAD profile-log | tar -x -C ~/.agentmesh/app
python3 -m venv ~/.agentmesh/venv
~/.agentmesh/venv/bin/pip install -r ~/.agentmesh/app/profile-log/requirements.txt \
  -r ~/.agentmesh/app/profile-log/requirements-s3.txt
```

Use the Python 3.11 or later that you checked in the prerequisites to create the venv. Put the copy on the venv's
import path, so that every service started with this Python finds `agentmesh_log` without further setup:

```sh
SITE="$(~/.agentmesh/venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
echo "$HOME/.agentmesh/app/profile-log" > "$SITE/agentmesh.pth"
```

Then add a small wrapper so that `agentmesh-log` runs the CLI. Save it as `~/.agentmesh/bin/agentmesh-log`:

```sh
#!/bin/sh
exec "$HOME/.agentmesh/venv/bin/python" -m agentmesh_log.cli "$@"
```

```sh
chmod 700 ~/.agentmesh/bin/agentmesh-log
export PATH="$HOME/.agentmesh/bin:$PATH"   # add this line to your shell profile too
agentmesh-log --help
```

`--help` lists the commands. Everything AgentMesh writes lives under `~/.agentmesh`. Set `AGENTMESH_HOME` to use
another folder, for example a disposable one for testing.

## Store the bucket credentials

The broker reads the bucket's access key ID and secret from one login Keychain item per value, under a service name
you choose. This guide uses `org.agentmesh.profile-log.r2`. Each command prompts for the value, so it never appears on
the command line or in your shell history:

```sh
security add-generic-password -s org.agentmesh.profile-log.r2 -a access-key-id -w
security add-generic-password -s org.agentmesh.profile-log.r2 -a secret-access-key -w
```

R2 access key IDs are 32 lowercase hex characters and secrets 64; the broker refuses anything else. The remote flags
then refer to the item as `--s3-credentials keychain:org.agentmesh.profile-log.r2`. The broker never reads AWS
environment variables or credential files.

## Create the account or join it

- **First device:** create the account and declare scopes and agents, as in [Quick
  start](../getting-started/quick-start#_2-create-the-account).
- **Another device:** pair it with a device that already holds the keys, as in [Pair a second
  device](../getting-started/quick-start#_6-pair-a-second-device).

The device keys are sealed in `~/.agentmesh/profile-log/keystore.sealed`, protected by a random key in the login
Keychain under `agentmesh.profile-log`.

## Run it in the background

Write the broker's LaunchAgent, then move it and the scheduled jobs under the supervisor. [Run in the
background](./background-service#macos) has the steps.

## Install the menu bar app

Build AgentMesh Control and the Touch ID helper the supervisor uses for protected changes:

```sh
control-app/bundle.sh
```

See [Menu bar app](../use/control-app) for what it shows and how to let it own the supervisor's login item.

## What gets installed

| Item | Path |
| --- | --- |
| Code | `~/.agentmesh/app/profile-log` |
| Python | `~/.agentmesh/venv` |
| Settings | `~/.agentmesh/settings.json` (shared declarations), `settings.local.json` (this device only) |
| Broker home | `~/.agentmesh/profile-log`: sealed keystore, encrypted replica, index snapshots, audit log, `sync-status.json` |
| Agent key files | `~/.agentmesh/agents/<name>.key` |
| Jobs | `~/.agentmesh/jobs`: job config, transcript cursors, summary state and metrics |
| Supervisor | `~/.agentmesh/control`; status in `~/.agentmesh/status`; logs in `~/.agentmesh/logs` |
| Menu bar app | `~/Applications/AgentMesh Control.app`, `~/.agentmesh/bin/agentmesh-presence` |

Keep `~/.agentmesh` owner-only (`0700`). Nothing under it belongs in Git or a support post.

Next, [run it in the background](./background-service).
