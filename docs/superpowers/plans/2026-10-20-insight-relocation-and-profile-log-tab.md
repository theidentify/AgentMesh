# Insight after the `~/.agentmesh` relocation, plus a read-only Profile log tab: plan

> **For agentic workers:** execute in order. Read `insight/README.md`, `insight/server.py`, `insight/adapters.py`,
> `insight/metrics.py`, `insight/syncthing_transport.py`, their tests, then
> `docs/superpowers/specs/2026-10-20-profile-log-settings-and-admin-ui-design.md` (Insight tab, phasing step 3),
> `profile-log/agentmesh_log/{apply,cli,settings}.py`, `profile-log/agentmesh_log/tui/__init__.py` and
> `profile-log/agentmesh_log/migrate/{run,mapping}.py`. Stop and report instead of guessing on anything that
> affects a gate. Do not commit. Do not touch the live machine: no reads of `~/.agentmesh`, `~/.hermes`, the live
> plist or Syncthing config from tests or from your own commands. Tests use temporary directories only.

**Goal:** (1) every Insight view reads the post-relocation layout under `~/.agentmesh` and falls back to the
legacy `~/.hermes/omp-memory` layout per item; Hermes cron data stays under `--home`. (2) A read-only
"Profile log" tab shows profiles/scopes, agents with R/W, devices, migration counts per project and target, and
the last apply / catch-up outcome, from a small content-free status file that `agentmesh-log apply` and
`agentmesh-log migrate catch-up` write.

## Layout facts (Mac, 2026-10-20, after `mac-relocate`)

| Data | Legacy path (under `--home`, default `~/.hermes`) | New path (under `--agentmesh-home`, default `~/.agentmesh`) |
| --- | --- | --- |
| Peer status ACKs | `omp-memory/sync/status/{mac,windows}.json` | `exchange/status/{mac,windows}.json` |
| Syncthing exchange folder (transport match) | `omp-memory/sync` | `exchange` |
| Bounded-digest coordinator state | `omp-memory/.bounded_digest_state.json` | `jobs/.bounded_digest_state.json` |
| Live summary metrics log | `omp-memory/summary-metrics.jsonl` | `jobs/summary-metrics.jsonl` (not yet written; see D2) |
| Historical summary metrics | — | `backups/archive/summary-metrics.jsonl.pre-relocate-<ts>-<hex>` (read-only, 0400) |
| Historical smoke metrics | `omp-memory/summary-smoke-test-metrics.jsonl` | `backups/archive/summary-smoke-test-metrics.jsonl.pre-relocate-<ts>-<hex>` |
| Hermes cron, usage audit, `state.db` | `cron/jobs.json`, `cron/usage_audit.jsonl`, `state.db` | unchanged, stay under `--home` |
| Profile-log status (new) | — | `profile-log-status.json` |

Known gap, out of scope here: the live `jobs/agentmesh-production.json` still has
`summary.metrics_path = ~/.hermes/omp-memory/summary-metrics.jsonl`, so new bounded runs would land in the
legacy directory. Insight must therefore read the legacy live log too (D2). Re-pointing that config is a
production change for the owner, not part of this plan.

## Decisions

- **D1 Explicit layout.** `server.py` gains `--agentmesh-home PATH` (default: `$AGENTMESH_HOME`, else
  `~/.agentmesh`). `make_server(home, port, sqlite=None, dsn=None, primary=None, agentmesh_home=None)` keeps its
  existing positional signature. A single resolver `adapters.layout(home, agentmesh_home)` returns, per item,
  the chosen path and a source label `agentmesh`, `legacy` or `missing`. Rule per item: use the new path when it
  (or, for directories, the directory) exists, else the legacy path if it exists, else `missing` (new path
  reported as the intended one). Each adapter uses the resolver; no adapter builds `omp-memory` paths itself.
  API responses may expose the labels, never absolute paths.
- **D2 Summary metrics, read in place, nothing moves (default; owner to confirm before deploy).** Live runs are
  the union of every existing file among `<agentmesh-home>/jobs/summary-metrics.jsonl` and
  `<home>/omp-memory/summary-metrics.jsonl`; history is every
  `<agentmesh-home>/backups/archive/summary-metrics.jsonl.pre-relocate-*` (sorted by name). Records are merged
  oldest first (history, then legacy live, then new live) and de-duplicated by `run_id`, later file winning.
  The same rule applies to `summary-smoke-test-metrics.jsonl`. `--metrics-log PATH` (repeatable) replaces the
  whole automatic list when given. The archive is only opened read-only; Insight never copies, moves or
  chmods. `metrics.report()` accepts one path or a list of paths; the `metrics.py` CLI keeps working.
- **D3 Peer status containment** moves from "inside `--home`" to "inside the chosen status root", which must
  itself be inside `--agentmesh-home` (new) or `--home` (legacy). Symlink, non-regular-file and 64 KiB refusals
  are unchanged.
- **D4 Transport.** `syncthing_transport.observe(home, exchange=None)` matches the Syncthing folder against the
  resolved exchange path. Syncthing config discovery is unchanged.
- **D5 Status file.** New module `profile-log/agentmesh_log/status.py`. Path `$AGENTMESH_HOME/profile-log-status.json`,
  mode 0600, written with the existing `atomic_write`, refused when the path or root is unsafe
  (`settings.safe(..., missing=True)`). Format:

  ```json
  {"format": "agentmesh-profile-log-status-v1", "updated_at": "<UTC ISO>",
   "overview": {"generated_at": "...",
                "profiles": [{"name": "work", "scopes": [{"path": "work/common", "label": "general"}]}],
                "agents": [{"name": "codex", "read": ["work/common"], "write": ["work/common"],
                            "default_write": "work/common", "allow_secret": false,
                            "allow_cross_profile": false, "allow_import": false}],
                "devices": [{"device_id": "...", "revoked": false}]},
   "migration": {"generated_at": "...", "projects": [{"project": "AgentMesh", "items": 3, "summaries": 1,
                                                       "target": "work/common"}], "unmapped": ["name"]},
   "last_apply": {"at": "...", "outcome": "approved", "changes": {"agent_widen": 1}},
   "last_catch_up": {"at": "...", "outcome": "ok", "outcomes": {"created": 2}, "skipped": {"mapping_skip": 1},
                     "unmapped": ["name"], "errors": ["ValueError"], "busy_retries": 0,
                     "timestamp_fallback": 0, "secret_labelled": 0}}
  ```

  Names, labels, booleans, counts, timestamps and exception class names only. Never key material, key file
  paths, agent ids, public keys, database paths, row ids, row hashes, row bodies, error messages or settings
  values other than the names above. `target` is a scope path, `skip`, `unmapped` or `mixed` (as the TUI
  computes it). Writers read the current file, replace only their own sections, and write atomically; an
  unreadable or invalid existing file is replaced rather than repaired. A status write failure never changes
  the result or exit code of `apply` or `catch-up` and never raises out of them.
- **D6 Writers.**
  - `apply_settings` (not `--dry-run`, which stays strictly read-only): after the audit line, write
    `last_apply` (outcome and per-kind change counts), and, when broker state was loaded, `overview` from the
    state as it is after the apply. When settings have a bridge database and a mapping, also refresh
    `migration` (via `migrate.mapping.plan` and the same per-project target logic as `tui.load_model`; factor
    that logic into one shared function used by both).
  - `migrate catch-up`: after `run(...)` returns, or after the disabled-bridge early return
    (`outcome: "disabled"`), or when it raises (`outcome: "error"`, `errors: [<class name>]`, then re-raise),
    write `last_catch_up` and refresh `migration`. `outcome` is `unmapped` when `unmapped` is non-empty,
    `error` when `errors` is non-empty, else `ok`. Catch-up cannot load broker state, so it never writes
    `overview`.
- **D7 Insight reader.** `adapters.profile_log_status(agentmesh_home, now=None)` opens only
  `<agentmesh-home>/profile-log-status.json`: refuse symlinks and non-regular files, `O_NOFOLLOW`, bounded read
  (256 KiB), JSON parse, `format` must match. It projects through an allow-list: strings must be `str`, ≤ 256
  chars, without control characters (else dropped / `null`); counts must be non-negative `int` ≤ 2**53-1; lists
  are capped at 500 entries with a `truncated` flag; unknown keys are dropped; `device_id` is shortened to its
  first 12 characters. Adds `age_seconds` for `updated_at`, `last_apply.at` and `last_catch_up.at`, and
  `available`/`availability` like `peer_status`. Response goes through `redact()`. Insight never lists or opens
  anything under `profile-log/`, `agents/`, `settings*.json` or `apply.jsonl`.
- **D8 UI.** New nav button `data-view="profile-log"` and `<section id="profile-log">`, using existing
  `table`/`cards`/`pill`/`esc` helpers: cards (status file age, last apply outcome, last catch-up outcome,
  unmapped projects), tables for Profiles & scopes, Agents (R / W / default write / flags), Devices
  (active/revoked), Migration (project, items, summaries, target), Last catch-up outcomes. Notice text: read-only
  snapshot written by `agentmesh-log apply` and `migrate catch-up`; edit in a terminal with
  `agentmesh-log admin` / `agentmesh-log apply`. Fetched from `GET /api/profile-log` (no parameters; any
  parameter is 400) with the same 15 s cache as metrics. No forms, no POST, no new external assets.
- **D9** README for Insight documents `--agentmesh-home`, the per-item fallback, D2, and the new tab;
  `profile-log/README.md` documents the status file and its content rules.

## Acceptance gates

Each gate needs a passing test (new or existing) named in the REPORT.

- **G1 New layout:** with only the new layout present in a temp `agentmesh-home` (and an empty
  `<home>/omp-memory`), Peer Status rows, the Pipeline `coordinator` and Metrics `cron.coordinator_available`
  read from the new paths and report `agentmesh` as source.
- **G2 Legacy fallback:** with only the legacy layout, the same views read the legacy paths (all existing
  insight tests keep passing unchanged or with only the call-site change), source `legacy`.
- **G3 Mixed:** per-item choice (for example peer status new, coordinator legacy) works; a missing item is
  `missing` and its view reports unavailable/unknown, not an exception.
- **G4 Cron stays under `--home`:** `cron/jobs.json`, `cron/usage_audit.jsonl`, `state.db` are read from
  `--home` even when the same names exist under `--agentmesh-home`.
- **G5 Metrics merge (D2):** archive history + legacy live + new live are merged and de-duplicated by
  `run_id` with the later file winning; `--metrics-log` replaces the automatic list; a corrupt line still
  fails explicitly as today; the archive file's mode and mtime are unchanged after a read.
- **G6 Containment:** a status file that is a symlink, or a status directory that resolves outside both homes,
  is refused (`unavailable or malformed`), for both layouts.
- **G7 Transport:** `observe` matches the folder at the resolved exchange; a Syncthing folder still configured
  at the legacy path is reported as `exchange folder not configured` when the new exchange exists.
- **G8 Status writer content:** after an approved `apply` and after a `catch-up` in temp homes, the file has
  format v1, mode 0600, the sections above, and contains none of: the agent key file contents or paths, agent
  ids, public keys, the DB path, any row content or row hash (assert by searching the raw bytes for each
  fixture value).
- **G9 Writer isolation:** `apply --dry-run` creates or changes no status file; an unwritable/symlinked status
  path leaves apply's and catch-up's return value and exit code exactly as without the status feature; a
  failing catch-up records `outcome: error` with the class name only and still raises.
- **G10 Reader projection:** malformed JSON, wrong format, oversize file, symlink, non-int counts, control
  characters, extra keys and lists over 500 are handled per D7; injected fields such as `key`, `content`,
  `agent_key`, `path` never reach the API response.
- **G11 Endpoint:** `GET /api/profile-log` returns 200 with the projection, 400 with any parameter, and
  POST/PUT/DELETE remain 405; the host/origin checks apply.
- **G12 Never opens secrets:** a test that patches `os.open`/`open` (or uses a sentinel tree with
  `profile-log/keystore.sealed`, `agents/x.key`, `settings.json`, `apply.jsonl` made unreadable, mode 000)
  proves the Profile log reader and all relocated adapters open none of them.
- **G13 UI:** `index.html` has the nav button and section; `qa.mjs` / `security_qa.mjs` (if runnable locally)
  still pass, and a new check confirms the tab renders a fixture without `innerHTML` of unescaped fields.
- **G14 Regression:** `cd insight && python3 -m unittest` passes;
  `~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests/settings profile-log/tests/migrate`
  passes (run with `PYTHONPATH=profile-log` from the repo root if needed), plus any new profile-log tests.

## Out of scope

Deploying to the live Insight plist; changing `jobs/agentmesh-production.json`; moving or copying archived
metrics; Windows; Insight editing (spec phasing step 4).

## Deliverable

Code and tests as above, and `docs/superpowers/plans/2026-10-20-insight-relocation-and-profile-log-tab.REPORT.md`
with a gate table (gate → test names → result) and any deviations. Do not commit.
