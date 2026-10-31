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

- **Touch ID never comes from this app.** For a move, the supervisor runs its own `agentmesh-presence` child and trusts
  only that child's exit code. A client, this app included, cannot claim the check passed.
  - The sheet accepts Touch ID or the Mac password (`LAPolicyDeviceOwnerAuthentication`), the owner's choice on 2026-10-29.
  - Approve a sheet only if you started that move.
- The supervisor allows at most 3 presence prompts per 10 minutes, denials included, and one move at a time.

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

- The status wire shape is pinned by `profile-log/tests/control/fixtures/status.json`. The Python and Swift tests both
  read it.
- To check the client against this Mac's supervisor, run `AMC_LIVE=1 control-app/test.sh --filter liveStatus`. It calls
  only the read-only `status`.
- For an unbundled run: `swift build && .build/out/Products/Debug/AgentMeshControl`.

### Toolchain notes (CommandLineTools only)

- The macOS 27 SDK's SwiftUI `@State` is a macro, and its plugin (`SwiftUIMacros`) is not in CommandLineTools. View
  state therefore lives in the `@Observable` `StatusModel`, because `ObservationMacros` is available.
- Sheets inside a `MenuBarExtra` window are unreliable, so the move confirmation is an inline panel in the unit row.
