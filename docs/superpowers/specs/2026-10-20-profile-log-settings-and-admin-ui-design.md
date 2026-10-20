# Profile-log settings file and admin UI: design

Status: draft for owner review, 2026-10-20. Builds on the encrypted profile log
(`docs/superpowers/specs/2026-10-18-encrypted-profile-log-design.md`) and Phase 5
(`docs/superpowers/plans/2026-10-20-encrypted-profile-log-phase5.md`, PR #55).

## Problem

All profile-log administration is CLI flags and hand-edited JSON today:

- Scopes: `broker scope add`.
- Agent grants: `broker grant --read … --write …`.
- Scopes for a joining device: `pair approve --scope`.
- Migration routing: a `mapping.json` file.
- The dual-write and recall bridge: `<db>.profile-log.json`.

The owner asked for a UI to tick scopes and manage categories, and proposed a
central config like Claude Code's `~/.claude/settings.json`.

## Owner decisions (2026-10-20)

- **Form:** a terminal UI first. A "Profile log" tab in Insight follows later.
- **First version covers:**
  - an overview;
  - ticking agent read/write per scope, and creating scopes;
  - editing the project mapping, with a preview;
  - choosing scopes when approving a paired device.
- **Presence:** confirmed in the terminal that runs the admin process, as the
  Phase 4 console presence already works. A UI click alone never authorizes.
- **Central config:** `~/.agentmesh/settings.json`, in the style of Claude Code.
- **One home for all memory files:** everything AgentMesh keeps about memory lives
  under `~/.agentmesh/` (`%USERPROFILE%\.agentmesh\` on Windows). That covers the
  new profile-log files now, and later the SQLite runtime that is in
  `~/.hermes/omp-memory/sqlite-sync/` today.
- **Project settings like Claude Code:** a repository may carry
  `.agentmesh/settings.json` (shared) and `.agentmesh/settings.local.json`
  (personal, git-ignored), with Claude-style precedence.
- **Settings stay per device** for now; they are not synced.
- **Windows TUI:** `windows-curses==2.4.2` (pinned), so Windows gets the same
  full-screen UI as the Mac; the numbered menu is the fallback.

## Design

### Two layers: declared settings and enforced state

The settings file is easy to edit by hand, but any process running as the same OS
user can also edit it. So the file is **desired state**, never authority:

| | `~/.agentmesh/settings.json` | Broker state (`keystore.sealed`) |
| --- | --- | --- |
| Holds | names, agents' intended grants, mapping, bridge options, UI preferences | keys, certificates, effective grants, presence windows |
| Edited by | the owner (editor or TUI) | `agentmesh-log apply`, after presence |
| Read by broker for authorization | **never** | always |

`agentmesh-log apply` reads the settings and computes a diff against the enforced
state. It prints that diff in plain words and asks for presence in the terminal for
every change that widens access. Only then does it write the state. Narrowing
access (revoke, remove a write scope) applies without presence, as revoke does
today. A tampered settings file can therefore only *propose* changes the owner sees
and confirms; it can never grant access silently.

### File layout

```
~/.agentmesh/                     0700 (Windows: %USERPROFILE%\.agentmesh, owner-only ACL)
  settings.json                   0600, the central config (below)
  settings.local.json             0600, this device's values (database path, authority)
  profile-log/                    the broker home: keystore.sealed, replica/, audit, index snapshots
  agents/<name>.key               agent key files, 0600
  memory/                         phase 5 below: the SQLite authority DB (+ -wal/-shm) and backups
  identity/                       phase 5 below: the signed-packet node identity
  logs/                           worker and broker logs
```

No key material, recovery share or row content ever goes into `settings*.json`.
Settings are **per device** and are not synced: devices receive scope grants
through pairing, so syncing the file would add little. They may still be backed
up. `~/.agentmesh-profile-log/`, created during the Phase 5 rollout preparation,
moves to `~/.agentmesh/` (nothing is deployed there yet).

### Project settings (like Claude Code)

| File | Shared? | Purpose |
| --- | --- | --- |
| `~/.agentmesh/settings.json` | no (per device) | profiles, scopes, agents, mapping, bridge |
| `~/.agentmesh/settings.local.json` | no | device-only values |
| `<repo>/.agentmesh/settings.json` | committed | this project's routing |
| `<repo>/.agentmesh/settings.local.json` | git-ignored | personal routing override |

Precedence, highest first, as in Claude Code: project local, project shared, user
local, user. Merging is per key. A project file may set only **routing**:
`{"project": {"name": "…", "scope": "work/common"}}`, which says which existing
scope this repository's memories go to and which name it has in the mapping. That
scope must be one the user-level file already allows. Grant, agent, bridge and
profile keys in a project file are rejected, because a cloned repository is
untrusted. The first time a project file is seen, `apply` or the TUI asks the
owner to trust that folder in the terminal, like Claude Code's trust prompt. It
records the trust by path and content hash, and asks again when the file changes.

### `settings.json` shape (v1)

```json
{
  "$schema": "agentmesh-settings-v1",
  "profiles": {
    "work":     {"scopes": {"common": {"label": "general"}}},
    "personal": {"scopes": {"common": {"label": "general"}}}
  },
  "agents": {
    "importer": {"write": ["work/common", "personal/common"], "allow_import": true},
    "recall":   {"read": ["work"], "write": ["work/common"], "default_profile": "work"}
  },
  "migration": {
    "global": "work/common",
    "null_project": "work/common",
    "projects": {"home": "personal/common", "localgov": "work/common"},
    "prefixes": [{"prefix": "AgentMesh", "target": "work/common"}]
  },
  "bridge": {"enabled": false, "recall": true, "dual_write": true, "profile": "work",
             "timeout_seconds": 10}
}
```

`settings.local.json` holds `bridge.database`, `bridge.authority` and
`bridge.cli_argv`. The memory-sync bridge keeps its strict schema. `apply` *renders*
`<db>.profile-log.json` (0600) from these settings, so the bridge needs no new
parser and keeps its current fail-closed checks. The Phase 5 mapping becomes the
`migration` section and keeps its exact semantics. `migrate` accepts
`--settings` in place of `--mapping`.

### Terminal UI (`agentmesh-log admin`)

Full-screen `curses` UI (arrow keys, checkboxes) on macOS and Linux, where
`curses` is part of Python's standard library. Windows Python has no `curses`, so profile-log adds a
Windows-only dependency `windows-curses==2.4.2; sys_platform == "win32"` (released
2026-03-31; wheels for CPython 3.10–3.14, win32 and win_amd64). The import is
guarded: if `curses` cannot be imported (no wheel, for example on Windows ARM64,
or the package is missing), the same screens run as a numbered menu (type a number,
press Enter). Both front ends share one screen model, so behaviour and presence
prompts are identical. CI runs the numbered menu on all three OSes and the curses
screens on macOS, Linux and Windows. It edits
`settings.json`, then runs the same `apply` step. Screens:

1. **Overview:**
   - profiles and scopes, as a tree;
   - agents, with their read/write scopes;
   - devices (active or revoked);
   - migration status: rows per project and target, unmapped names, and the
     last catch-up outcome. Names and counts only, never content.
2. **Agents × scopes:** a grid with `R`/`W` toggles, plus a "new scope" action.
3. **Mapping:** each project with its counts and target (`work/common`,
   `personal/common`, a project scope, or `skip`). **Preview** shows how many
   events a change would retire and re-create. On apply, it runs `migrate catch-up`.
4. **Pair approve:** shows the request, the 6-digit code or QR fingerprint, and a
   scope checklist. It runs the existing commit-reveal flow.

Every apply shows the diff and asks for presence in the same terminal. The TUI is
a front end over existing APIs (`BrokerState`, grants, `pairing`, `migrate`), not a
second implementation.

### Insight tab (later)

A "Profile log" tab in Insight shows the overview **read-only** at first, from a
small JSON status file that `apply` and catch-up write (names and counts only).
Editing from the web comes after. A click would only create a *pending change* in
`settings.json`, which the owner confirms in a terminal by running
`agentmesh-log apply`. This keeps Insight free of keys and of authority.

## Security notes

- Settings files are untrusted input. Use strict schemas, refuse symlinks, and check
  0600 and owner on POSIX. The `apply` diff is the only path that widens access.
- Never put agent key contents in settings. Agent keys stay in `agents/*.key` files
  (0600), as today.
- `apply` writes an audit line per change (names and outcome), like the broker
  audit log.
- The Phase 5 limitations are unchanged: same-OS-user malicious code is out of
  scope; it can tamper with the files, but it cannot pass console presence without
  the owner.

## Phasing

1. **Settings + `apply`:** schema, diff, presence, project settings with trust,
   rendering the bridge config, `migrate --settings`, and the profile-log home at
   `~/.agentmesh/profile-log/`.
2. **TUI:** overview, grid, mapping preview, pair approve.
3. **Insight tab:** a read-only overview.
4. **Insight editing:** pending changes, confirmed by `apply` in a terminal.
5. **Move the SQLite runtime into `~/.agentmesh/`** (database, identity, backups,
   logs, then the app copy). **This touches the live sync path:** installers,
   generated services, `sync_worker`, OTA, and Windows Task Scheduler. It needs
   its own plan and explicit owner approval, with these steps:
   1. stop the worker;
   2. copy with SQLite's backup API;
   3. verify counts and hashes;
   4. switch the service paths;
   5. restart;
   6. keep the old directory read-only until verified on the Mac and Windows.

   Symlinks are not an option, because the code refuses them.

## Open questions for the owner

None. All questions were answered on 2026-10-20.
