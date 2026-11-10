# Troubleshooting

Find out why recall, sync or a job is not working, without exposing memory. Start with the overall picture:

```sh
agentmesh-log control status
cat ~/.agentmesh/profile-log/sync-status.json
```

On a Mac, the [menu bar app](./use/control-app) shows the same health, and [Insight](./use/insight) shows job history.

## Recall returns an error

Errors come back as one JSON line, `{"error": "CODE"}`, with exit code `1`.

| Code | Meaning | What to do |
| --- | --- | --- |
| `broker_unreachable` | No broker is listening at the address in the agent's key file | Start the broker (`agentmesh-log control start broker`, or see [Run in the background](./install/background-service)). There is no other source to fall back to. |
| `presence_required` | The request needs you: an agent's first request, or secret or cross-profile recall | Open the window it needs, for example `agentmesh-log presence open --home ~/.agentmesh/profile-log --class first_access` (or `secret_read`), then retry |
| `denied` | The grant does not cover a requested scope or profile, or the agent was revoked | Check the agent's `read` list in `settings.json`, or drop the `--scope` or `--profile` it cannot read. Any unreadable scope denies the whole request. |
| `broker_busy` / `busy` | The broker is rebuilding indexes or is overloaded | Retry after a moment |
| `quota_exceeded` | A workspace write quota for today is used up | Wait for the next UTC day, or ask a workspace admin |
| `invalid_request` | A malformed command or key file | Check the arguments and that the key file is the one `apply` wrote |

An empty `results` list is not an error: nothing matched.

## Sync does not run

`sync-status.json` in the broker home records the last attempt, without any bucket, endpoint or object names:

| `outcome` | Meaning | What to do |
| --- | --- | --- |
| `ok` | The last sync succeeded | Nothing |
| `credentials_missing` | The credential store item named by `--s3-credentials` is missing | Store it ([macOS](./install/macos#store-the-bucket-credentials), [Windows](./install/windows#store-the-bucket-credentials)); the broker retries without a restart |
| `credentials_refused` | The bucket rejected the credentials, or they are not in the expected format | Check the token is scoped to the bucket with Object Read & Write, and store it again |
| `remote_error` | The bucket did not answer | Check the network and the endpoint; the broker keeps running and retries |
| `error` | Anything else | Read `broker.err.log` in the logs folder |

`outbox` counts local objects the bucket does not have yet. It drops to `0` after a successful sync. If the file does
not change at all, the broker is not running with `--sync-interval`. A one-shot `agentmesh-log broker sync --home
~/.agentmesh/profile-log REMOTE` reports the same outcome and exits nonzero on a credential problem.

## A memory written on one device is missing on another

1. On the writing device, check that `sync-status.json` shows `ok` with `outbox: 0`.
2. On the reading device, check the same, then recall with an agent whose grant reads that scope.
3. Check that the reading device was granted that scope. Pairing grants only the scopes selected then, and a scope
   created after pairing does not reach an already paired device by itself. Grant it from a device that holds it with
   `pair regrant` (see [devices and
   recovery](https://github.com/theidentify/AgentMesh/blob/main/profile-log/README.md#devices-and-recovery)).

## A job fails or is stuck

`agentmesh-log control status` shows each job's last result. The job wrapper writes `job-<name>.json` to its
`--state-dir`, and the job's own status is `last-ingest.json` or `last-summary.json` in `~/.agentmesh/status`.

- **Held by maintenance or quota.** A wrapped job does not run while maintenance mode is on
  (`agentmesh-log control maintenance off`) or after the summary's daily LLM quota is used up
  (`agentmesh-log control quota` shows it).
- **The summary fails.** Check that `claude` runs for the account the job runs as, and that the summarizer agent has
  `allow_summarize`. A failed window keeps its cursor, so the next run retries it. See
  [Ingest agent sessions](./use/ingest).
- **A project's memory never appears.** Its project has no route in the migration mapping. It is listed in
  `~/.agentmesh/jobs/summary-unmapped.json`. Choose a scope in the menu bar app, or add a route under `migration` in
  `settings.json` and run `agentmesh-log apply`.
- **Ingestion refuses to start** with a workspace error. The job config, agent key or state directory points inside a
  team workspace home (`~/.agentmesh/ws/<id>/`). Ingestion is personal-only; point it at your personal home.
- **The job still runs from `memory-sync`.** Follow the [migration runbook](./install/migrate-memory-sync).

## The supervisor does not answer

The menu bar icon is faded, or `agentmesh-log control status` cannot connect.

- On a Mac where the menu bar app owns the login item, open its menu: if it says *The supervisor is not running*, tick
  **Run supervisor at login** and confirm. Do not `launchctl bootout` it; see [If the supervisor stops after the
  handover](./use/control-app#if-the-supervisor-stops-after-the-handover).
- Otherwise check `~/.agentmesh/logs/control.err.log`, and that `org.agentmesh.control` is loaded
  (`launchctl print "gui/$(id -u)/org.agentmesh.control"`) or that the `\AgentMesh\Control` task runs on Windows.
- To get every unit back under launchd or Task Scheduler without the supervisor, run
  `agentmesh-log control panic-revert`.

## Pairing fails

| Code | Meaning |
| --- | --- |
| `pairing_mismatch` | The six-digit codes or the fingerprint differed. Nothing was granted. Start again, and stop if it repeats. |
| `pairing_expired` | The request is older than ten minutes. Run `join` again. |
| `pairing_reused` | The request was already used. Run `join` again. |
| `pairing_denied` | The approving device refused it, or cannot grant those scopes. |

The approving device can grant only scopes it holds. A `server` or `write-only` device approves nothing.

## An administrative command says the home is locked

Administrative commands take an exclusive lock at `~/.agentmesh/profile-log/.lock`. A crashed command can leave it
behind. Check that no `agentmesh-log` administration command is running before removing it by hand.

## Request help without sharing memory

Share only:

- the command you ran, without its paths, and its exit code;
- the `error` code, or the `outcome` and counts from `sync-status.json`;
- the unit names, backends and states from `agentmesh-log control status`;
- your OS and Python version.

Do not share key files, `settings.json`, the broker home, logs, transcripts, recall results, the recovery kit, or any
credential. Device and agent IDs are not secret, but they identify your setup; leave them out unless asked.

Next, review [Privacy](./privacy) before sharing a help request or screenshot.
