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
~/.agentmesh/                     0700
  settings.json                   0600, the central config (below)
  settings.local.json             optional per-device overrides (not synced)
  profile-log/                    the broker home (replaces ~/.agentmesh-profile-log/)
  agents/<name>.key               agent key files, 0600
```

No key material, recovery share or row content ever goes into `settings*.json`.
The files may be backed up or shared between the owner's devices. Device-specific
values (database path, `authority`) go in `settings.local.json`.

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

Standard library only (`curses` on macOS/Linux; on Windows, a numbered-menu
fallback, because `curses` is not in the Windows standard library). It edits
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

1. **Settings + `apply`:** schema, diff, presence, rendering the bridge config,
   `migrate --settings`, plus moving the rollout directory to `~/.agentmesh/`
   (nothing is deployed there yet).
2. **TUI:** overview, grid, mapping preview, pair approve.
3. **Insight tab:** a read-only overview.
4. **Insight editing:** pending changes, confirmed by `apply` in a terminal.

## Open questions for the owner

1. Should a project (repo) be able to have its own `.agentmesh/settings.json`, as
   Claude Code has `.claude/settings.json`? Recommendation: yes, but **routing
   only**. Such a file could only choose which existing scope this project's
   memories go to, within what the user-level file allows. It could never grant
   read access, because a cloned repo is untrusted.
2. Should `settings.json` be synced between devices, or kept separate per device?
   Recommendation: keep it per device for now. Devices already receive scope
   grants through pairing, so syncing the file adds little.
3. Is the Windows numbered-menu fallback acceptable, or should we add the
   `windows-curses` dependency?
