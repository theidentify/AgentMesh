# Prerequisites

Prepare Python, a private bucket and its credentials before you set up a device. Read [Concepts](./concepts) first.
You will have everything the [Quick start](./quick-start) needs.

## Python 3.11 or later

The broker and the jobs need Python 3.11 or later. Python 3.13 is a good choice.

| Platform | Where to get it |
| --- | --- |
| macOS | The [official macOS downloads](https://www.python.org/downloads/macos/) |
| Windows | The [official Windows downloads](https://www.python.org/downloads/windows/); check that `py -3` selects it |
| Linux | Your distribution's packages, or [Python's Unix guidance](https://docs.python.org/3/using/unix.html) |

Check the version:

```sh
python3 --version
```

AgentMesh runs from its own virtual environment with the exact versions pinned in `profile-log/requirements.txt` and
`profile-log/requirements-s3.txt` (boto3, for S3 sync). The platform pages create it.

## A private bucket

The profile log syncs through S3-compatible object storage. Cloudflare R2 is what AgentMesh is tested with.

1. Create a dedicated **private** bucket with no public access.
2. Create an API token with **Object Read & Write**, scoped to that bucket only.
3. Note the S3 endpoint (`https://<account>.r2.cloudflarestorage.com`), the bucket name, and a prefix for AgentMesh
   objects, for example `v1/`. Every device uses the same three values.

Keep the access key ID and the secret out of files, command lines and chat. You store them in the OS credential store
in the next step of your platform page, and the broker reads them from there.

::: warning Linux
The broker reads S3 credentials only from the macOS login Keychain or Windows Credential Manager. A Linux device can
run a broker and sync through a shared folder, but cannot sync with R2 yet. See [Linux](../install/linux).
:::

## The Claude Code CLI (for the summary)

The summary job runs `claude` in print mode, with no tools, to turn transcripts into memories. Install
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) and sign in on each device whose sessions you want to
ingest. A device that only recalls does not need it.

## Optional

- **Swift 6 and macOS 14 or later** to build the [menu bar app](../use/control-app). The Command Line Tools are
  enough.
- **Ollama** with `bge-m3` on one always-on device, for [hybrid recall](../use/recall#use-hybrid-recall).

Next, follow the [Quick start](./quick-start).
