# Insight dashboard

Insight is a local, read-only status page for AgentMesh: the profile log, the transcript jobs and this device's
storage. It shows counts, states and times, never memory content. You need a repository checkout and Python 3.10 or
later; it uses only the standard library.

| View | What it shows |
| --- | --- |
| Overview | Job health and recent failures, memory counts, backup state, and which AI tools were active recently |
| Metrics | Summary runs: usage, cost and coverage, from `jobs/summary-metrics.jsonl` |
| Agents | Each AI tool's transcripts the summary follows: how many, and the latest modification time |
| Pipeline | The ingest and summary jobs, their failures over 7 days, the last scan and tick, and projects held back for want of a scope |
| Sync | Profile-log backup to R2, the devices in the profile log, and how AI apps reach memory |
| Profile log | Profiles, scopes, agent grants, devices, workspaces, the last apply and the last remote sync |
| Storage | Disk use under the AgentMesh home, the replica and the bucket, with cost and compute estimates |

A time less than an hour old counts as recent. A transcript's modification time or a job's last result is an
observation, not proof that something is running now.

## Start it

From the repository root:

```sh
python3 -u insight/server.py --port 0
```

It prints JSON with its `url` on `127.0.0.1`, its `pid` and `readonly: true`. Open the URL; stop the server with
Ctrl-C. A source that is missing shows as unavailable, never as demonstration data.

Insight reads the AgentMesh home from `--agentmesh-home`, otherwise `AGENTMESH_HOME`, otherwise `~/.agentmesh`. The
Profile log view reads only the content-free `profile-log-status.json` that `apply` and the broker write. Memory counts
come from a read-only agent, `agents/insight.key` by default (`--insight-agent-key` names another).

To keep it running, let the supervisor run it as the `insight` unit; `insight/deploy_insight.py` deploys a release and
reloads that unit.

::: warning
Insight binds only to `127.0.0.1` and is not an authenticated service. Do not expose it through a tunnel without the
Cloudflare Access setup in the [Insight reference](https://github.com/theidentify/AgentMesh/blob/main/insight/README.md#remote-access-via-cloudflare-tunnel).
:::

If a view shows something unexpected, start with [Troubleshooting](../troubleshooting).
