# AgentMesh user guide site — design

Date: 2026-10-12
Status: approved in brainstorming, pending spec review

## Goal

Publish a task-based **user guide** for AgentMesh as a static documentation site
on GitHub Pages. Phase A (this spec) covers end users who install and use
AgentMesh on their own devices. Operator (phase B) and developer (phase C)
material comes later.

## Decisions

| Topic | Decision |
| --- | --- |
| Audience | All three eventually; phase A = end users first |
| Language | English first; Thai may be added later via VitePress `locales` |
| Tool | VitePress `1.6.4` (exact pin, lockfile committed) |
| Hosting | GitHub Pages at `https://theidentify.github.io/AgentMesh/` |
| Existing docs | Stay where they are; the guide links to them on GitHub |

MkDocs Material was prototyped and rejected: its maintainers warn that MkDocs 2.0
removes plugins and themes with no migration path.

## Layout

The Node toolchain is scoped to `docs/` so the Python project gains no root
`package.json`.

```
docs/
  package.json                 # vitepress 1.6.4 only; scripts: dev, build, preview
  package-lock.json
  .vitepress/config.mts        # base '/AgentMesh/', sidebar, local search, srcExclude
  public/                      # agentmesh-icon.svg, logos (copied from memory-sync/assets)
  index.md                     # what AgentMesh is, what it does and does not do
  getting-started/
    concepts.md                # node, exchange folder, bootstrap, change packets, peers
    prerequisites.md           # Python 3.10+ with FTS5/JSON, Syncthing, device pairing
  install/
    macos.md
    linux.md
    windows.md
    background-service.md      # LaunchAgent, systemd user unit, Windows autostart
  use/
    recall.md                  # agentmesh recall; wiring into Claude/Codex/Hermes/OMP rules
    ingest.md                  # ingesting agent sessions
    status.md                  # agentmesh status, status/<node>.json, diagnose
    insight.md                 # running the Insight UI
  troubleshooting.md           # conflicts/quarantine, duplicate workers, Syncthing stalls
  privacy.md                   # what must never enter Git or be shared
  reference/index.md           # links to SYNC.md, OTA-UPDATES.md, SIGNED-SYNC.md, etc.
  superpowers/                 # specs and plans; excluded from the site via srcExclude
.github/workflows/docs.yml
```

`docs/.gitignore` ignores `node_modules/`, `.vitepress/dist/` and `.vitepress/cache/`.

## Build and deploy

`.github/workflows/docs.yml`:

- Triggers: `pull_request` and `push` to `main`, filtered to `docs/**` and the
  workflow file itself; plus `workflow_dispatch`.
- Build job: Node 22, `npm ci` and `npm run build` in `docs/`. VitePress fails the
  build on dead links, which acts as the link check.
- Deploy job: only on `main`, using `actions/upload-pages-artifact` and
  `actions/deploy-pages` with `pages: write` and `id-token: write` permissions.
- The repository owner must set **Settings → Pages → Source** to *GitHub Actions*
  once; the workflow does not change repository settings.

## Content rules

1. **Accurate commands.** Every command shown is checked against the actual
   argparse definitions or scripts (`agentmesh.py --help`, `setup_*.sh`,
   `bootstrap_windows.py`, `platform_service.py`) before it is written.
2. **No private data.** Use placeholders only (`/absolute/path/to/exchange`,
   `/path/to/mac.db`). No real hostnames, usernames, DSNs, group UUIDs or home paths.
3. **State verification limits honestly.** Linux-host and native Windows
   verification remain outstanding; pages say so where it matters, matching the
   repository README.
4. **No duplication of deep reference.** User pages explain the task and link to
   the existing in-repo document for depth, using absolute GitHub URLs to `main`.
5. **Scope boundary.** AgentMesh installs the memory-sync application, not Hermes
   or other agent applications; every install page says this.

## Out of scope for phase A

- Thai translation.
- Moving or rewriting the existing `memory-sync/`, `profile-memory/`, `insight/`
  documents.
- Operator topics (RC build/publish, OTA rollout, signed sync, security wizard,
  Mac replacement) and developer topics (architecture, writers/consumers contract).
- Custom domain.
- Changes to the repository README beyond one link to the published guide.

## Verification

- `npm run build` in `docs/` passes locally with no dead links.
- Each page is viewed in a local `vitepress preview` in light and dark mode and at
  phone width.
- A scripted grep over `docs/` finds no absolute user home paths, IP addresses
  other than `127.0.0.1`, or DSN strings with credentials.
- Commands in the guide are spot-checked against `--help` output.
