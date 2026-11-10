# Windows

Install AgentMesh on Windows: the code and its venv, the bucket credentials in Credential Manager, and the broker.
Complete the [prerequisites](../getting-started/prerequisites) first and have a checkout of the repository. Use
PowerShell for every command on this page.

## Install the code

The layout mirrors the Mac: a copy of `profile-log` from a known commit under `%USERPROFILE%\.agentmesh\app`, and a venv
beside it. From the repository root:

```powershell
$AM = "$env:USERPROFILE\.agentmesh"
New-Item -ItemType Directory -Force "$AM\app", "$AM\bin" | Out-Null
git archive --format=zip -o "$AM\profile-log.zip" HEAD profile-log
Expand-Archive "$AM\profile-log.zip" -DestinationPath "$AM\app"; Remove-Item "$AM\profile-log.zip"
py -3 -m venv "$AM\venv"
& "$AM\venv\Scripts\python.exe" -m pip install --only-binary :all: `
  -r "$AM\app\profile-log\requirements.txt" -r "$AM\app\profile-log\requirements-s3.txt"
```

Put the copy on the venv's import path, and add a wrapper so that `agentmesh-log` runs the CLI:

```powershell
Set-Content "$AM\venv\Lib\site-packages\agentmesh.pth" "$AM\app\profile-log"
Set-Content "$AM\bin\agentmesh-log.cmd" "@`"%USERPROFILE%\.agentmesh\venv\Scripts\python.exe`" -m agentmesh_log.cli %*"
$env:Path = "$AM\bin;$env:Path"   # add the folder to your user Path too
agentmesh-log --help
```

`%USERPROFILE%\.agentmesh` relies on your account's own permissions; AgentMesh does not set ACLs on it. Keep it inside
your user profile.

## Store the bucket credentials

Store the bucket's access key ID and secret in Credential Manager under a name you choose, for example `agentmesh.r2`.
The command prompts for both values without echo:

```powershell
agentmesh-log r2-credentials store --ref wincred:agentmesh.r2
agentmesh-log r2-credentials check --ref wincred:agentmesh.r2
```

The remote flags then use `--s3-credentials wincred:agentmesh.r2`. The credentials never appear in a command line, a
task or a file.

## Create the account or join it

Follow the [Quick start](../getting-started/quick-start) with Windows paths: the broker home is
`$env:USERPROFILE\.agentmesh\profile-log` and agent key files are in `$env:USERPROFILE\.agentmesh\agents`. A Windows
device usually joins an account created on another device:

```powershell
agentmesh-log join --home "$AM\profile-log" --s3-bucket BUCKET --s3-prefix v1/ `
  --s3-endpoint https://ACCOUNT.r2.cloudflarestorage.com --s3-credentials wincred:agentmesh.r2
```

Approve the request on the other device and compare the six-digit codes. The device key is sealed in
`keystore.sealed`, protected by a key in Credential Manager (`agentmesh.profile-log/<device id>`). The broker listens
on a named pipe, `\\.\pipe\agentmesh-broker-<id>`, instead of a socket.

## Run it in the background

Create the broker and job tasks, register the supervisor and consolidate, as described in [Run in the
background](./background-service#windows). The menu bar app is macOS only; use `agentmesh-log control status` and
[Insight](../use/insight) instead.

## Hybrid recall credentials

For [hybrid recall](../use/recall#use-hybrid-recall) from Windows, store the Cloudflare Access service token the same
way:

```powershell
agentmesh-log r2-credentials store --ref wincred:org.agentmesh.embed --kind access
```

## What gets installed

| Item | Path under `%USERPROFILE%\.agentmesh` |
| --- | --- |
| Code | `app\profile-log` |
| Python | `venv` |
| CLI wrapper | `bin\agentmesh-log.cmd` |
| Settings | `settings.json`, `settings.local.json` |
| Broker home | `profile-log` |
| Agent key files | `agents\<name>.key` |
| Jobs, supervisor, status, logs | `jobs`, `control`, `status`, `logs` |

Next, [run it in the background](./background-service#windows).
