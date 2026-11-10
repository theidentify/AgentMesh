# Control the worker (retired)

::: danger Retired
This page ran the memory-sync worker (`agentmesh.py`) that ingested sessions and exchanged packets. Packet sync, the exchange folder, the SQLite database and the worker were retired. Their code was
removed in SQLite retirement R5b on 2026-11-09, so these steps no longer apply to any AgentMesh version. The page's
earlier text is in the repository history.
:::

What replaced it:

- Devices share memory through the encrypted **profile log**: each device runs a broker that replicates through R2.
  See [concepts](../getting-started/concepts#how-agentmesh-works).
- Your agent sessions become memory through the scheduled [ingest and summary jobs](../use/ingest), run by the
  supervisor and shown in the [menu bar app](../use/control-app).
- A device that still runs its jobs from the `memory-sync` directory moves over with the
  [memory-sync migration runbook](../install/migrate-memory-sync).
