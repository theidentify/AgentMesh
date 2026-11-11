# Quick start

Create your AgentMesh account on a first device, grant an agent, remember and recall a test memory, then pair a
second device and find the same memory there. Complete the [prerequisites](./prerequisites) first. The steps use a
Mac; the [Windows](../install/windows) and [Linux](../install/linux) pages give the equivalents.

## Conventions

| Name | Meaning |
| --- | --- |
| `AM` | The AgentMesh home: `~/.agentmesh` (Windows: `%USERPROFILE%\.agentmesh`), or `AGENTMESH_HOME` if set |
| `H` | The broker home inside it: `~/.agentmesh/profile-log` |
| `agentmesh-log` | `PY -m agentmesh_log.cli`, where `PY` is the Python of the AgentMesh venv |
| `REMOTE` | The four remote flags below, the same on every command that talks to the bucket |

```sh
--s3-bucket BUCKET --s3-prefix v1/ --s3-endpoint https://ACCOUNT.r2.cloudflarestorage.com --s3-credentials keychain:org.agentmesh.profile-log.r2
```

The credential reference names an entry in the OS credential store, never the credentials themselves. On Windows it
is `wincred:NAME`.

## 1. Install the code

Follow [Install the code](../install/macos#install-the-code) and [Store the bucket
credentials](../install/macos#store-the-bucket-credentials) on the macOS page, then come back here. Afterwards
`agentmesh-log --help` lists the commands.

## 2. Create the account

```sh
agentmesh-log broker init --home ~/.agentmesh/profile-log --profile personal --profile work --out /Volumes/USB/agentmesh-kit
```

This creates the account, the two profiles and this device's keys, sealed with a key in the login Keychain. `--out`
writes the three recovery shares as separate files into a new folder, which must be outside `~/.agentmesh`. Copy
`~/.agentmesh/profile-log/replica/registry/account.json` next to them: recovery on a new device needs this trusted
copy of the account record. Then store the shares in separate places.

## 3. Declare scopes and an agent

Write `~/.agentmesh/settings.json` (owner-only, mode `0600`, in a `0700` folder):

```json
{
  "$schema": "agentmesh-settings-v1",
  "profiles": {
    "personal": {"scopes": {"inbox": {"label": "internal"}}},
    "work": {"scopes": {"common": {"label": "internal"}}}
  },
  "agents": {
    "recall": {"read": ["personal", "work"], "write": ["personal/inbox"], "default_write": "personal/inbox"}
  }
}
```

Preview and apply it:

```sh
agentmesh-log apply --dry-run
agentmesh-log apply
```

Apply prints the complete change, asks you to confirm at the console because it widens access, then creates the
scopes and the grant, and writes the agent's key file to `~/.agentmesh/agents/recall.key`. See [settings
schema](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#settings-schema-v1) for every field.

## 4. Run the broker and sync

```sh
agentmesh-log broker sync --home ~/.agentmesh/profile-log REMOTE
agentmesh-log broker run --home ~/.agentmesh/profile-log --sync-interval 120 REMOTE
```

`broker sync` uploads the account and scope records once and exits `0`. `broker run` keeps the broker in the
foreground and syncs every 120 seconds. Leave it running in this terminal for now;
[Run in the background](../install/background-service) makes it permanent.

## 5. Remember and recall

In a second terminal, open a presence window, because each agent's first request needs you:

```sh
agentmesh-log presence open --home ~/.agentmesh/profile-log --class first_access
agentmesh-log remember --agent-key ~/.agentmesh/agents/recall.key "The amber orchard check is a synthetic test memory."
agentmesh-log recall "amber orchard" --agent-key ~/.agentmesh/agents/recall.key
```

`remember` writes into the agent's default scope, `personal/inbox`. `recall` returns JSON with the memory in
`results` and its event ID. An empty `results` is not an error; it means nothing matched.

## 6. Pair a second device

On the second device, install the code and store the same bucket credentials. Then ask to join:

```sh
agentmesh-log join --home ~/.agentmesh/profile-log REMOTE
```

It prints a request ID and a QR code, and waits up to ten minutes. On the first device, approve it with exactly the
scopes the new device should receive:

```sh
agentmesh-log pair approve REQUEST_ID --home ~/.agentmesh/profile-log REMOTE --scope personal --scope work
```

Check that the approver fingerprint matches on both screens, compare the six-digit codes, and type `yes` only if they
match. A mismatch stops the pairing (`pairing_mismatch`) and grants nothing.

Settings and agent keys are never synced. On the second device, write its own `settings.json` with the agents it
needs, run `agentmesh-log apply`, then sync and recall:

```sh
agentmesh-log broker sync --home ~/.agentmesh/profile-log REMOTE
agentmesh-log presence open --home ~/.agentmesh/profile-log --class first_access
agentmesh-log recall "amber orchard" --agent-key ~/.agentmesh/agents/recall.key
```

Finding the memory written on the first device is the acceptance check. `agentmesh-log device list --home
~/.agentmesh/profile-log` on either device shows both devices.

## Next steps

- [Run in the background](../install/background-service): the broker, the jobs and the supervisor.
- [Ingest agent sessions](../use/ingest): turn your transcripts into memory.
- [Connect your agents](../use/recall#connect-your-agents) so they recall by themselves.
- Test the recovery kit with `agentmesh-log recovery test`; see [devices and
  recovery](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#devices-and-recovery).
