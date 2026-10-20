# Profile-log Settings, Apply and Terminal UI Implementation Plan

> **For agentic workers:** execute in order. The spec is
> `docs/superpowers/specs/2026-10-20-profile-log-settings-and-admin-ui-design.md`; this plan implements its
> phasing steps 1 (settings + `apply`) and 2 (TUI). Read the spec fully, then `profile-log/README.md`
> (Broker, Devices and recovery, Migration from SQLite), `profile-log/FORMAT.md`, and the Phase 5 plan and
> REPORT. The spec wins on conflict, except where this plan records a decision. Stop and report instead of
> guessing on anything that affects a gate below; record other choices under Deviations in the REPORT.

**Goal:** the owner manages profiles, scopes, agents, migration routing and the memory-sync bridge from one
per-device file, `~/.agentmesh/settings.json`, through a terminal UI or a text editor. `agentmesh-log apply` turns
that desired state into enforced broker state. It shows a plain diff and asks for console presence before any
change that widens access. The broker never reads the settings file for authorization.

## Decisions (owner, 2026-10-20)

- All AgentMesh memory files live under one root: `~/.agentmesh/`, or `%USERPROFILE%\.agentmesh\` on Windows.
  `AGENTMESH_HOME` overrides it, and tests always set it.
- Settings work like Claude Code's. There are user, user-local, project and project-local files, merged per key
  with the precedence below. Settings are per device and never synced.
- Project files may only **route**, and only after the owner trusts the folder in the terminal.
- The terminal UI comes first: `curses` on macOS/Linux, and `windows-curses==2.4.2` on Windows. A numbered menu is
  the fallback, and both share one screen model. Presence is confirmed in the terminal running the admin process.
- Unchanged: memory-sync code, the bridge schema, installers, services, the live DB and `~/.hermes`. Moving the
  SQLite runtime into `~/.agentmesh` (spec phasing step 5) is **not** part of this plan.

## Layout

```
$AGENTMESH_HOME (default ~/.agentmesh)    0700; Windows: owner-only ACL check is out of scope, document it
  settings.json                            0600, user settings
  settings.local.json                      0600, device-only values
  profile-log/                             broker home (BrokerHome root)
  agents/<name>.key                        0600 agent key files written by apply
  apply.jsonl                              0600 append-only audit of apply outcomes (names, never keys/content)
```

Every file is refused if it is a symlink, not a regular file, not owned by the user, or not mode 0600 (POSIX), as
the bridge config is today. The root directory must be 0700 on POSIX.

## Settings schema v1 (normative; document in README "Settings")

`settings.json` (strict: unknown keys are rejected at every level, and duplicate JSON keys are rejected):

```json
{
  "$schema": "agentmesh-settings-v1",
  "profiles": {"<profile>": {"scopes": {"<path>": {"label": "general|internal|secret"}}}},
  "agents": {"<name>": {"read": ["<scope>"], "write": ["<scope>"], "default_write": "<scope>|null",
                        "allow_secret": false, "allow_cross_profile": false, "allow_import": false}},
  "migration": {"global": "<target>", "null_project": "<target>", "projects": {"<name>": "<target>"},
                "prefixes": [{"prefix": "<str>", "target": "<target>"}]},
  "routing": {"allowed_scopes": ["<scope>"]},
  "bridge": {"enabled": false, "recall": true, "dual_write": true, "profile": "<profile>",
             "timeout_seconds": 10, "recall_agent": "<name>", "import_agent": "<name>"}
}
```

- Every section is optional. A missing section means "no change to that area" for `apply`. It never means
  "delete everything".
- `<scope>` names follow the existing scope-name grammar (`work`, `work/common`, `work/client-a`). A `<target>` is a
  scope name or `"skip"`. Agent names are `[a-z0-9-]{1,32}`.
- `settings.local.json` uses the same schema plus `bridge.database` (absolute path), `bridge.authority` (bool) and
  `bridge.cli_argv` (absolute argv, the same rule as the bridge). Those three keys are valid **only** in the local
  file.
- **Precedence** (highest first): `<project>/.agentmesh/settings.local.json`, `<project>/.agentmesh/settings.json`,
  `settings.local.json`, `settings.json`. Objects merge per key. Lists replace, they do not concatenate. A project
  layer may contain **only** `{"project": {"name": "<project name>", "scope": "<scope>"}}`. Any other key in a
  project file makes that file invalid; it is ignored with one stderr line.
- **Key-material guard:** any string value containing `AGE-SECRET-KEY-`, `-----BEGIN`, or a 64-hex value under a
  key whose name ends in `key` makes the file invalid. Settings never carry keys.

## Design to implement (all under `profile-log/agentmesh_log/`)

```
settings.py   paths ($AGENTMESH_HOME), safe load (mode/owner/symlink/duplicates), schema, merge, key guard
apply.py      desired vs enforced diff, presence, ordered application, idempotence, audit, bridge rendering
projects.py   project trust (TTY), trust records in sealed state, routing overlay for the migration mapping
tui/model.py  screen model: overview, grid, mapping, pair; produces settings edits and pair actions only
tui/curses_ui.py, tui/menu_ui.py   two front ends over the model
cli.py        `settings show|validate`, `apply [--dry-run]`, `project trust PATH`, `admin` (TUI),
              `migrate … --settings` (alternative to --mapping)
presence.py   add action "apply" and "trust"
```

### Apply (`apply.py`)

1. Load and merge the settings. Load enforced state from the broker home's key store. Build a diff with one entry
   per change and a kind: `scope_create`, `agent_create`, `agent_widen`, `agent_narrow`, `agent_remove`,
   `bridge_enable`, `bridge_disable`, `bridge_update`, `mapping_change` and `unsupported`.
2. **Widening** means `scope_create`, `agent_create`, `agent_widen` (any added read/write scope or flag),
   `bridge_enable`, and any `bridge_update` that turns on `recall`, `dual_write` or `authority`. When any of
   these is present, `apply` prints the whole diff, then calls
   `PresenceManager.require('agent','apply',console=…,one_shot=True,summary=<diff>)`. One-shot means an
   already-open window **never** auto-approves an apply. If the console declines or there is no TTY, nothing is
   written and the command exits 1 with `presence_required`.
3. **Narrowing** (`agent_narrow`, `agent_remove`, `bridge_disable`) applies without presence, in the same
   transaction as the widening changes. `--dry-run` prints the diff and writes nothing.
4. `unsupported` entries are reported and never applied: deleting a scope or profile, changing an existing
   scope's label, renaming a profile, or a profile missing from the state. `apply` exits 1 if any are present,
   unless `--allow-unsupported-report` is given. That flag applies the rest and lists the unsupported entries.
5. Agents: the state records `control.agent_names = {name: agent_id}`, written only by `apply`. Creating an agent
   uses the same code path as `broker grant`; refactor that into one function both call. It writes
   `$AGENTMESH_HOME/agents/<name>.key` (0600, exclusive create; an existing file is an error). Widening and
   narrowing replace the `Grant` with the new sets, which changes the grant digest and so rebuilds the index.
   Removal calls `state.revoke`, which keeps the import-agent history, and deletes the key file. A name that
   changes is a removal plus a creation, and the diff says so.
6. Scopes are created through the same path as `broker scope add`, including keyring initialization and recovery
   history publication when `phase4` is set. Refactor it into one function.
7. Bridge: when the `bridge` section is enabled, render `<database>.profile-log.json` (0600, atomic replace) with
   the memory-sync bridge's **exact** field set. `agent_key` and `import_agent_key` are the key paths of
   `recall_agent` and `import_agent`. `mapping` points at a rendered `$AGENTMESH_HOME/migration-mapping.json`
   (0600) built from the effective migration plus trusted project routes. Disabling renders `enabled: false`. The
   file is never deleted.
8. Every non-dry-run apply, including a declined one and a no-op, appends one line to `apply.jsonl` with the diff kinds, names and
   outcome, and no key paths or content. A second apply with no changes prints `no changes` and asks nothing.

### Project trust (`projects.py`)

- `agentmesh-log project trust PATH` reads `PATH/.agentmesh/settings.json` and `settings.local.json`, shows their
  routing, and asks for presence (`one_shot`, action `trust`). It then records
  `control.trusted_projects[realpath] = sha256(both files' bytes)` in the sealed state.
- When the effective mapping is built (for `apply` bridge rendering and `migrate --settings --project-dir`), a
  trusted project's route adds `projects[<name>] = <scope>` **only if** `<scope>` is in `routing.allowed_scopes`
  and is an existing scope. A changed hash, an untrusted folder or a disallowed scope means the route is ignored,
  with one stderr line naming the folder. It is never an error that stops a migration.
- The precedence above applies between the two project files. A project route overrides the user mapping for that
  one project name only.

### Migrate with settings

`migrate plan|run|rows|catch-up|report --settings [--project-dir DIR …]` builds the same `Mapping` object from the
effective migration section, with the `format` field added. `--mapping` keeps working. Giving both is a usage
error.

### TUI (`agentmesh-log admin`)

- `tui/model.py` is pure and testable. Its screens are:
  - **Overview:** profiles and scopes as a tree; agents with R/W sets; devices with their revoked flag; migration
    counts per project and target from `migrate plan`, plus unmapped names. Names and counts only, never content.
  - **Agents × scopes:** a toggle grid and a new-scope dialog.
  - **Mapping:** each project with counts, current target and new target. The preview uses the import ledger
    (when the broker and import agent are available) and shows how many rows would be retired, re-created or
    newly created.
  - **Pair approve:** request ID entry, the approver fingerprint, the 6-digit code confirmation and a scope
    checklist. It calls the existing pairing functions with exactly the ticked scopes.
- Editing screens change only an in-memory copy of the **user** settings layer. "Save" writes `settings.json`
  atomically (0600) and then runs `apply` in the same terminal, which shows the diff and asks for presence. The
  TUI never writes broker state itself.
- Front ends: `curses_ui.py` (arrows, space to tick, `s` to save, `q` to quit) and `menu_ui.py` (numbered prompts).
  `admin` uses curses if `import curses` succeeds and stdout is a TTY. Otherwise it uses the menu. `--menu` forces
  the menu.
- Add `windows-curses==2.4.2; sys_platform == "win32"` to `profile-log/requirements.txt`. Import `curses` only
  inside `curses_ui.py`, behind a guarded import.

## Ground rules

- Work only in `/Users/krisanathep/Workspaces/AgentMesh/.claude/worktrees/epl-phase5-235b0b` (branch
  `claude/profile-log-settings-ui`). Change only `profile-log/**`, plus this plan's REPORT
  (`docs/superpowers/plans/2026-10-20-profile-log-settings-and-tui.REPORT.md`). You may add **one** memory-sync
  test file, `memory-sync/test_profile_log_settings_bridge.py`. It renders a bridge file with
  `agentmesh_log.apply` and checks that `profile_log_bridge.load_config` accepts it; it uses
  `pytest.importorskip('pyrage')`. Do not change any other memory-sync file, installers, workflows or other docs.
- **Do not commit, push or create branches.** The reviewer commits.
- No network, no pip install. Interpreter: `~/.cache/agentmesh-logvenv/bin/python`, with `PYTHONDONTWRITEBYTECODE=1`
  and `-p no:cacheprovider`. `windows-curses` is not installed locally; the guarded import and the menu fallback
  must make every test pass without it.
- **Never read or write the real `~/.agentmesh`, `~/.hermes`, real databases, keychains or identities.** Every test
  sets `AGENTMESH_HOME` to a temporary directory and uses the disposable file key store.
- TDD per task. Never print or log key material, row content or query text. Use explicit `encoding='utf-8'` for
  all text I/O.
- Keep every existing test passing. The existing CLI commands (`broker grant`, `scope add`, `migrate --mapping`)
  keep their behaviour.
- Curses rendering is exercised through the model and through an injected fake screen object. No real terminal is
  needed in tests.
- No GATES.md or other working files left behind.

## Tasks

1. **Settings.** `settings.py`: paths, safe load, strict schema, local-only keys, merge, key guard,
   `settings show|validate` (prints the merged result with `$schema`; never key paths beyond names).
2. **Shared admin functions.** Refactor scope creation and agent creation out of `cli.py` into functions that the
   CLI and `apply` both call, with no behaviour change (existing tests prove it).
3. **Apply.** Diff, classification, presence, narrowing, unsupported entries, agent names and key files, bridge
   rendering, audit, `--dry-run`, idempotence.
4. **Projects and migrate.** Trust, routing overlay and `migrate --settings`.
5. **TUI model**, then the menu front end, then the curses front end (fake screen), then the `admin` command.
6. **Docs and REPORT.** README "Settings and admin UI", and the REPORT mapping each gate to its tests, with
   deviations and owner steps. Owner steps include writing the first `settings.json` from the drafted
   `~/.agentmesh/migration-mapping.json`, and the Mac rollout from the Phase 5 REPORT, now using
   `~/.agentmesh/profile-log` as the home.

## Acceptance gates (each with a dedicated test the reviewer can mutation-test)

- **S-1 Untrusted file:** a symlinked file, a non-0600 file, a non-0700 root, unknown keys, duplicate keys, a
  local-only key in `settings.json`, and a string with key material are each refused with a fixed error, and
  nothing is applied.
- **S-2 Presence for widening:** each widening kind requires a TTY confirmation, **even with an open `agent`
  presence window**. Without a TTY, or when declined, the sealed state bytes are unchanged and no key file is
  written. A pure narrowing applies with no prompt. A mixed diff prompts once and applies everything on approval.
- **S-3 Idempotent:** a second apply reports `no changes`, prompts nothing and leaves the state bytes unchanged.
- **S-4 Settings are not authority:** after an apply, editing `settings.json` to add a scope to an agent's read
  set does not change what that agent can recall through the broker until an approved apply runs.
- **S-5 Agents:** creating an agent writes `agents/<name>.key` (0600) and `control.agent_names`. Its grant equals
  the declared sets. Removal revokes it (the next request is denied) and deletes the key file. Import-agent
  history survives removal (Phase 5 P5-7 regression). A rename shows up as remove plus create.
- **S-6 Scopes and unsupported entries:** a declared missing scope is created exactly as `broker scope add` does,
  including keyring objects under phase4. Deleting a scope, changing a label or adding an unknown profile is
  reported as `unsupported`, exits 1, and changes nothing.
- **S-7 Migration parity:** `migrate plan` and `run` with `--settings` give exactly the same results as `--mapping`
  with the equivalent file, on a synthetic DB.
- **S-8 Bridge rendering:** the rendered file has the bridge's exact field set, mode 0600 and the correct key
  paths. The memory-sync test proves `load_config` accepts it. Disabling renders `enabled: false`. Local-only
  values come only from `settings.local.json`.
- **S-9 Project trust:** an untrusted project file is ignored. Trust requires a TTY. A changed file is ignored
  until it is trusted again. A route to a scope not in `allowed_scopes` is ignored. Grant keys in a project file
  make it invalid. Precedence between the four layers follows the order above (one test per layer pair).
- **S-10 TUI writes only settings:** with a spy on the key store save and on broker state writes, the TUI's edit
  screens change only `settings.json`, and every state change goes through `apply`. Toggling a grid cell and
  saving produces exactly the expected settings diff.
- **S-11 TUI content-free:** with marker strings in DB row content, none appears in any rendered screen, whether
  menu or fake curses.
- **S-12 Pair screen:** the approve flow passes exactly the ticked scopes to the pairing grant. Unticking all
  scopes refuses to approve.
- **S-13 Fallback:** when `curses` fails to import, or stdout is not a TTY, `admin` uses the menu. `--menu` forces
  the menu. A scripted menu session creates a scope, grants it and saves, and the apply prompt appears.
- **S-14 Audit:** every non-dry-run apply (approved, declined, no-op) writes exactly one `apply.jsonl` line; `--dry-run` writes nothing at all, including no audit line, with no key paths,
  key material or content.

### Whole change

- The full profile-log suite passes from the repository root and from `profile-log/`. The memory-sync suite is
  unchanged except for the new test file. The reviewer reruns socket tests outside the sandbox, and three-OS CI
  passes (Windows installs `windows-curses`).
- `git status` shows changes only in the allowed files.
- The REPORT maps every gate to its tests, lists deviations and lists the owner steps.
