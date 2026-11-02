# AgentMesh Control

A macOS menu bar app for the AgentMesh supervisor (`org.agentmesh.control`), plus `agentmesh-presence`, the helper the
supervisor uses to ask for Touch ID or your Mac password.

## What the app may and may not do

- The app is a **plain client** of `~/.agentmesh/run/control.sock`. It has no authority of its own: every action is an
  ordinary socket request, audited with the app as the caller, exactly like the `agentmesh-log control` CLI.
- It shows each unit's health, backend (launchd or native), pid or last exit and job age, and offers only what the
  supervisor would accept:

  | Action | Who may ask for it | What checks it |
  |---|---|---|
  | Start, restart, stop, reset, maintenance on/off | Tier 1 | The supervisor's rate limits and the audit |
  | Move to supervisor or launchd (`unit.backend`) | Tier 2 | The supervisor's own presence check |
  | Open the Memory window (`memory.unlock`) | Tier 2 | The supervisor's own presence check, then an idle lock |

- **Touch ID never comes from this app.** For a move, the supervisor runs its own `agentmesh-presence` child and trusts
  only that child's exit code. A client, this app included, cannot claim the check passed.
  - The sheet accepts Touch ID or the Mac password (`LAPolicyDeviceOwnerAuthentication`), the owner's choice on 2026-10-29.
  - Approve a sheet only if you started that move.
- The supervisor allows at most 3 presence prompts per 10 minutes, denials included, and one move at a time.
- **Keys & devices is read-only.** It shows the supervisor's `keys.inventory` (tier 0) and offers no action. The
  supervisor builds it from `lstat` of key files and the broker's content-free status; it never opens a key file or the
  sealed keystore.

## Memory

**Memory…** in the menu opens a read-only browser of the memory tree: one level at a time, with `INDEX.md` page
summaries, `stale` and label badges, and pages or items rendered as markdown on the right.

- **Unlock** asks the supervisor for `memory.unlock`. The supervisor shows its own Touch ID / Mac password sheet
  (action `read`). Once unlocked, each read keeps it open; after 5 minutes without one the supervisor locks it again
  and the window drops what it showed. **Lock** locks it at once, and closing the window clears it.
- The supervisor reads through the broker with the agent key in `settings.local.json` `memory_tree.agent_key`, so that
  agent's grants, labels and the broker audit still apply. A secret item needs the broker's own presence window too.
- The app writes nothing to disk. Plaintext export is a CLI-only ceremony (`agentmesh-log control memory-export`).

## Menu bar icon

Three nodes and a hub ("A green"), with one corner badge for the most important state:

| State | Icon |
|---|---|
| Supervisor unreachable | Faded mark, empty ring |
| A unit failed, down or in conflict | Red dot |
| A unit failing or stuck, or a warning in Keys & devices | Orange dot |
| A new project needs a scope | Blue dot with a question mark |
| A unit is moving between launchd and the supervisor | Crossing arrows |
| Maintenance mode | Pause bars |
| A job is running | Green dot, and the hub turns into a spinning ring (still under Reduce Motion) |
| All healthy | Green dot |

The app polls every 10 s, every 5 s with the menu open, and every 2 s while a job runs, so short runs such as catch-up
(a few seconds) still show. A Keys & devices warning ranks below a failed unit and a project waiting for a scope.

The bottom of the menu shows the version, `AgentMesh Control v0.1 (<commit>)`: `bundle.sh` writes the commit it built
from as the bundle version, and an unbundled `swift run` build shows "dev build". The app has its own icon in
notifications, System Settings and Finder.

## Keys & devices

A collapsible section, polled at most once a minute and again whenever the menu opens. Its header counts the warnings
and shows when the inventory was taken. Inside, findings come first, then:

| Block | Shows |
|---|---|
| Devices | Each paired device: name, platform, and when it was last seen (or "this Mac", "revoked") |
| Agents | Each grant: its name, or `unnamed (<first 8 of key_ref>)`, what it reads, and an open secret window |
| Passkeys | Each passkey the broker accepts for approvals: label, when it was added and from which device |
| Key files | `profile-log/agents/*.key`, `agents/*.key` and the keystore: mode, matching agent, age |
| Recovery and backup | Whether a recovery kit exists, and the last `backup create` |

| Finding | Severity | Rule |
|---|---|---|
| `key_file_unsafe`, `keystore_unsafe` | warning | Not a regular file, a symlink, group/world bits set, wrong owner, or empty |
| `device_stale` | warning | Another, unrevoked device not seen for 14 days |
| `no_recovery` | warning | No recovery kit issued |
| `backup_never`, `backup_old` | warning | No backup recorded, or the last one is over 30 days old |
| `inventory_stale` | warning | The broker has not refreshed its `keys` section for a day |
| `key_file_orphan` | info | A `profile-log/agents/<id>.key` that matches no grant |
| `secret_grant_active` | info | A grant's secret window is open |
| `no_passkey` | info | The broker reports no passkeys |

Each new warning notifies once and turns the icon orange; info findings never notify. Against a supervisor older than
M3b (`unknown_op`), the section hides itself.

## Notifications

The installed app posts macOS notifications. It is the alert channel on a Mac without a Discord webhook
(`~/.config/agentmesh/discord-webhook`).

| Event | When it notifies |
|---|---|
| A unit becomes failed, down or in conflict | At once |
| A unit stays failing or stuck for 10 minutes | Once, after the 10 minutes. A single failed run of a 5-minute job stays quiet. |
| A unit that was notified about becomes healthy | Once ("recovered") |
| `control.sock` does not answer for 2 minutes | Once, and once more when it answers again |
| catch-up holds back a new project with no scope | Once per project, with a button per scope (Personal, Work, Skip) |
| A new warning appears in Keys & devices | Once per finding; it can notify again after it clears and comes back |

The app checks every 30 s with its menu closed and every 5 s with it open.

**New projects.** When an agent starts working in a project the migration mapping does not cover, catch-up imports
everything else and holds that project's memory back.
- The app asks once, in a notification and in the menu ("New project needs a scope"), where its memory should go.
- Choosing a scope sends `mapping.set`, and the supervisor raises its own Touch ID / password sheet before it adds the
  mapping. The held-back memory imports on the next catch-up.
- `mapping.set` only adds a scope for a waiting project, using a scope the mapping already has (or skip). It never
  changes an existing mapping.

- Units that are not installed never notify.
- macOS asks for permission on the first launch of the installed app.
- An unbundled `swift run` build posts nothing, because UserNotifications needs a bundle identifier.

The supervisor also sends its own alerts (a native unit's 3rd failed run in a row, its recovery, the restart-storm guard)
to the Discord webhook, when one is configured.

## Build and install

Use a Swift 6 toolchain on macOS 14 or later. CommandLineTools are enough; Xcode is not needed.

```bash
control-app/bundle.sh
```

This builds both products in release mode, ad-hoc signs them, and installs:
- `~/Applications/AgentMesh Control.app` (`LSUIElement`: menu bar only, no Dock icon). It refuses while the app is
  running, so quit it from its menu first.
- `$AGENTMESH_HOME/bin/agentmesh-presence` (default `~/.agentmesh/bin`). The directory and the file are mode 0700, and
  the file is replaced atomically. The supervisor refuses the helper if it is a symlink or group/world-writable, or if
  its directory is.

`control-app/bundle.sh --no-install` builds into `control-app/build/` only.

After installing, open the app from `~/Applications`, then tick **Launch at login** in its menu. Launch at login works
only from the installed bundle.

## Uninstall

1. Quit the app from its menu, and untick **Launch at login** first if it is on.
2. Delete `~/Applications/AgentMesh Control.app`.
3. Delete `~/.agentmesh/bin/agentmesh-presence`. The supervisor then answers `presence_unavailable` to moves, and
   `agentmesh-log control adopt|release` with typed console presence still works.

## Develop

```bash
control-app/test.sh
```

`test.sh` runs `swift test`, adding the swift-testing macro plugin path that a CommandLineTools-only toolchain does not
search.

- The status and inventory wire shapes are pinned by `profile-log/tests/control/fixtures/status.json` and
  `inventory.json`. The Python and Swift tests both read them.
- To check the client against this Mac's supervisor, run `AMC_LIVE=1 control-app/test.sh --filter liveStatus`. It calls
  only the read-only `status`.
- For an unbundled run: `swift build && .build/out/Products/Debug/AgentMeshControl`.

### Toolchain notes (CommandLineTools only)

- The macOS 27 SDK's SwiftUI `@State` is a macro, and its plugin (`SwiftUIMacros`) is not in CommandLineTools. View
  state therefore lives in the `@Observable` `StatusModel`, because `ObservationMacros` is available.
- Sheets inside a `MenuBarExtra` window are unreliable, so the move confirmation is an inline panel in the unit row.
