# SQLite bounded writer readiness

This is an **opt-in writer adapter**, not a production-backend switch. Existing
worker configuration, PostgreSQL writers, services, schedules, exchange folders,
readers, exports, retention, and UI are unchanged. The older `summarize_memory.py`
command remains available for compatibility; its batch-derived keys are **not**
the production stable-key digest path. Do not activate it as a replacement.

## Native ingestion

`ingest_sessions.py` uses the pure OMP/Codex/Claude parsers. The storage adapter
applies production project inference, including Codex workspace/scratch and
Claude encoded-directory identities. Explicit projects override inference, not
Codex safety guards: missing metadata, guardian review, and subagent-source
sessions are skipped without cursor writes. Claude subagent directories and
symlinks remain excluded. Event identity, content, metadata, and hashes retain
parser semantics. Inserts, malformed-record quarantine, and cursor advancement
share one `BEGIN IMMEDIATE` transaction and explicit node-range ID allocation.
Complete-line processing, repeat/append/replacement/truncation dedupe, and
concurrent ingestion preserve existing observations; there is no retention or
source cleanup here.

## Bounded stable-key digest

`bounded_digest.py` collects full, project-contiguous user/assistant events with
explicit event, related-context, and total-context bounds. It rejects overflow
rather than truncating evidence or omitting existing rules. `bounded_protocol.py`
is a portable copy of the established bounded extraction/validation semantics;
it has no PostgreSQL or runtime-home imports. Existing identity is injected from
the database, including existing subproject scope keys and legacy key formats.
New keys are namespace-constrained in both the model schema and validation.
Corrections reuse a stable key; status can become active, superseded, resolved,
or deleted (a logical status, never row deletion).

Every accepted item cites unique integer source IDs and exact nonempty evidence
quotes. Secrets, altered quotes, incomplete reviewed-event coverage, changed
identity, and invalid scope fail closed. An optional conflict model is invoked
only for an explicit conflict, never as a retry for validation/network failures.
The model still owns semantic extraction: substring validation proves quote
fidelity, not logical entailment of every claim.

The short write transaction re-collects the complete task and related context.
Stale tasks do not write. Item/status, preserved metadata and evidence history,
source relationships, touched project/subproject summaries, per-event receipts,
and ID counters commit together or roll back together. Prior summary prose and
metadata are retained. The OS lock spans model calls without holding a long
SQLite write lock; direct concurrent apply is fenced by task revalidation.
Windows locking is implemented but requires verification on a real Windows host.

Coverage is **not** `id > cursor`: late peer arrivals and late low IDs remain
eligible. Existing consumer receipts and shared batch provenance are honored.
Explicit one-time `seed_coverage()` snapshots only currently present legacy IDs
through the legacy `hermes` checkpoint. It never covers peer ranges or future
arrivals implicitly. Run this migration only after the source snapshot and
legacy checkpoint are verified; do not reset production state for a smoke test.

## Explicit commands

Use an initialized, disposable database first. Paths below are placeholders;
no production database, resolver installation, or authentication path is assumed.

```sh
python bounded_digest.py disposable.db
python bounded_digest.py disposable.db --apply --seed-coverage
python bounded_digest.py disposable.db \
  --command-json '["/resolver/venv/bin/python","/code/digest_worker.py","--resolver-root","/resolver/source"]' \
  --model gpt-6-luna --metrics-log private-metrics.jsonl
# The previous command validates a live response but does not apply it.
# Add --apply only for an explicitly authorized isolated or deployed writer.
```

The worker uses an installed authenticated resolver, or a resolver root supplied
outside the repository (`--resolver-root` / `HERMES_AGENT_ROOT`). It makes one
direct Responses request without tools, implicit retries, or an agent session.
No tokens are copied into this repository. Unknown usage/cost stays null. Metrics
distinguish total/uncached/cached input, output/reasoning tokens, API calls,
cumulative fallback usage, maximum per-call input context, reviewed events, and
validation versus applied writes. Failed fallback usage is unknown, not a partial
first-call total. Provider diagnostics and source text are not logged.

### Claude worker, digest API and model benchmarks

`claude_digest_worker.py` speaks the same worker protocol through one
`claude --print` call with tools, MCP, hooks and session persistence disabled;
`--model` must be a full Claude model ID. It reports the actual cost when the
CLI returns one.

`digest_api.py` serves the worker protocol over loopback HTTP so the model is
chosen per call as `provider:model`. Providers are worker argv in a JSON file
(see `bench/providers.example.json`); without one, only `claude-code` is
configured. The scheduler uses it through `digest_api_client.py`, so switching
models is a config change:

```sh
python digest_api.py --providers providers.json --port 8765
python digest_scheduler.py db --root vault \
  --command-json '["python","digest_api_client.py","--url","http://127.0.0.1:8765"]' \
  --model claude-code:claude-haiku-5-5 --conflict-model claude-code:claude-sonnet-5-5
```

`digest_bench.py` sends identical fixture batches (`bench/digest_cases.json`)
to several models through the API and reports valid results, memory_key
stability across trials, tokens, cost and latency. Benchmarks call live models
and spend tokens; fixtures must stay synthetic.

```sh
python digest_bench.py --models claude-code:claude-haiku-5-5 \
  claude-code:claude-sonnet-5-5 codex:gpt-6-luna --trials 3 --out bench.jsonl
```

### Scheduling with failure alerts

`job_notify.py` wraps one scheduled run (for example from a launchd
`StartInterval` job) and posts to a Discord channel webhook only when the job
starts failing and when it recovers. Only the job name, exit code and the
job's own `status`/`action`/`error_class` identifiers are sent. The webhook
URL is read from a private (0600) file; without it, runs are only logged.

```sh
python job_notify.py --name summary --state-dir status \
  --webhook-file ~/.config/agentmesh/discord-webhook -- python summary_job.py
```

Real-snapshot verification may require an explicitly larger related-context
bound than the default; measure it and raise the bound deliberately. Do not work
around overflow by dropping old rules. Private snapshots and model responses
must remain outside Git.

## Activation gates still outstanding

- Wire the bounded adapter into a fixed-highwater daily/boot/backlog window owner;
  persist its ceiling and mark success only after the window drains **and** export
  succeeds. `run_once(highwater=...)` handles one batch, not that scheduler.
- Port readers/reasoning/callers, deterministic exports, and non-destructive
  retention policy in the next phase; consume the canonical metrics schema.
- Independent integrated review, real Windows execution, fresh peer application
  acknowledgment, and verified deployment/rollback precede any backend switch.

Verification: run the repository tests with a scratch `TMPDIR` and pytest
`--basetemp` under the same private scratch root. Tests use disposable synthetic
SQLite data; separate private real-transcript and live-model evidence must not
be confused with those fixtures.
