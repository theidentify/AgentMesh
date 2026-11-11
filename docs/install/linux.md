# Linux

Run an AgentMesh broker on a Linux desktop. Complete the [prerequisites](../getting-started/prerequisites) first and
have a checkout of the repository.

::: warning Limits on Linux
- **No R2 sync yet.** The broker reads S3 credentials only from the macOS Keychain or Windows Credential Manager, so a
  Linux device cannot sync with a bucket. It can sync through a shared folder (`--filesystem DIR`) with devices that
  use the same folder.
- **Desktop only.** Device keys are sealed with a key in the Secret Service (GNOME Keyring or KWallet) through
  `secret-tool`. A headless server without a session bus has no key store, and `broker init` and `join` refuse to run.
- **No supervisor or menu bar app.** Run the broker as a systemd user service.
:::

## Install the code

Install `secret-tool` (the `libsecret-tools` or `libsecret` package on most distributions). Then, from the repository
root:

```sh
mkdir -p ~/.agentmesh/app ~/.agentmesh/bin && chmod 700 ~/.agentmesh
git archive --format=tar HEAD profile-log | tar -x -C ~/.agentmesh/app
python3 -m venv ~/.agentmesh/venv
~/.agentmesh/venv/bin/pip install -r ~/.agentmesh/app/profile-log/requirements.txt
SITE="$(~/.agentmesh/venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
echo "$HOME/.agentmesh/app/profile-log" > "$SITE/agentmesh.pth"
printf '#!/bin/sh\nexec "$HOME/.agentmesh/venv/bin/python" -m agentmesh_log.cli "$@"\n' > ~/.agentmesh/bin/agentmesh-log
chmod 700 ~/.agentmesh/bin/agentmesh-log
export PATH="$HOME/.agentmesh/bin:$PATH"   # add this line to your shell profile too
agentmesh-log --help
```

## Create the account or join it

Follow the [Quick start](../getting-started/quick-start), replacing the four S3 flags with `--filesystem
/absolute/path/to/shared`. Every device that shares the profile log this way must use the same folder, and the folder
must be private to you.

## Run it in the background

See [Linux](./background-service#linux) on the background page for a systemd user service.

Next, [ingest agent sessions](../use/ingest) or [recall memory](../use/recall).
