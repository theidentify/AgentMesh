# AgentMesh User Guide Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a task-based English end-user guide for AgentMesh as a VitePress 1.6.4 site deployed to GitHub Pages.

**Architecture:** A self-contained VitePress project lives in `docs/` (own `package.json` and lockfile; no root Node files). Pages are plain Markdown that link to the existing in-repo reference documents on GitHub. One GitHub Actions workflow builds on every PR that touches `docs/` (dead links fail the build) and deploys to Pages from `main`.

**Tech Stack:** VitePress 1.6.4, Node 22 (CI), GitHub Actions (`actions/configure-pages@v5`, `actions/upload-pages-artifact@v3`, `actions/deploy-pages@v4`).

**Spec:** `docs/superpowers/specs/2026-10-12-user-guide-site-design.md`

---

## Verification model

Docs have no unit tests. Each task's "test" is: `npm run build` in `docs/` succeeds (VitePress fails on dead internal links), plus the privacy check script from Task 1, plus checking every command against the real CLI. Run `npm run build` from the worktree root as `npm --prefix docs run build`.

## Facts gathered from the code (use these; do not invent options)

- Unified CLI: `python3 memory-sync/agentmesh.py [--database DB] [--exchange EX] [--config CFG] <action>`. `--database` is required for every action except `inspect-install`.
- `recall QUERY [--project P] [--limit N]` — read-only, default limit 12.
- `ingest [--root ROOT] [--project P]` — incremental; default discovery roots (`memory-sync/ingest_sessions.py:12`): `omp → ~/.omp/agent/sessions`, `codex → ~/.codex/sessions`, `claude → ~/.claude/projects` (Claude `subagents` dirs skipped).
- `status` (no options) — local memory and peer-sync state.
- `diagnose --runtime RUNTIME [--json]` — read-only, redacted; exit 0 only when status is ok.
- `once [--postgres]`, `watch [--postgres] [--interval N]` — ingest + exchange + configured summarization.
- `worker-start|worker-status|worker-stop --runtime RUNTIME` — managed worker lifecycle.
- `windows-autostart --runtime RUNTIME (--enable|--disable) [--dry-run] [--gui] [-y]`.
- Agent rules: `memory-sync/install_agent_rules.py --app-dir APP --database DB [--python ABS_PY] [--agents hermes omp codex claude]`. Targets (`install_agent_rules.py:106-109`): Hermes `<profile>/skills/agentmesh-shared-memory/SKILL.md`; OMP and Codex `~/.agents/skills/agentmesh-shared-memory/SKILL.md`; Claude `~/.claude/rules/agentmesh-shared-memory.md`.
- Install wrappers and defaults: `memory-sync/README-SETUP.md` (macOS/Linux), `memory-sync/README-WINDOWS.md` and `memory-sync/WINDOWS-INSTALLATION.md` (Windows).
- Background service: `memory-sync/platform_service.py generate ...` per `README-SETUP.md`.
- Conflicts: independent edits to the same row or natural key are quarantined; a zero exit code does not mean no conflicts; inspect `status/<node>.json` (`README-WINDOWS.md`, `SYNC.md` "Safety and conflicts").
- Insight: `python3 -u insight/server.py --port 0`, binds 127.0.0.1 (`insight/README.md`).

Re-run the relevant `--help` before writing each page; if it disagrees with this list, the code wins.

## File map

| File | Responsibility |
| --- | --- |
| `docs/package.json`, `docs/package-lock.json` | VitePress pin and scripts |
| `docs/.gitignore` | Ignore `node_modules`, `dist`, `cache` |
| `docs/.vitepress/config.mts` | Site config: base, nav, sidebar, search, srcExclude, edit links |
| `docs/public/*.svg` | Logo/icon copied from `memory-sync/assets/` |
| `docs/scripts/check-privacy.sh` | Fails if pages contain home paths, private IPs or credentialed DSNs |
| `docs/index.md` … `docs/reference/index.md` | Guide pages (one responsibility each, per spec layout) |
| `.github/workflows/docs.yml` | Build on PR, deploy on `main` |
| `README.md` | One link to the published guide |

---

### Task 1: Scaffold VitePress project and privacy check

**Files:**
- Create: `docs/package.json`, `docs/.gitignore`, `docs/.vitepress/config.mts`, `docs/index.md`, `docs/public/agentmesh-icon.svg`, `docs/public/agentmesh-logo.svg`, `docs/public/agentmesh-logo-dark.svg`, `docs/scripts/check-privacy.sh`

- [ ] **Step 1: Create `docs/package.json`**

```json
{
  "name": "agentmesh-docs",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vitepress dev",
    "build": "vitepress build",
    "preview": "vitepress preview",
    "check": "sh scripts/check-privacy.sh && vitepress build"
  },
  "devDependencies": {
    "vitepress": "1.6.4"
  }
}
```

- [ ] **Step 2: Install and create the lockfile**

Run: `npm --prefix docs install`
Expected: `docs/package-lock.json` created; `vitepress@1.6.4` in `docs/node_modules`.

- [ ] **Step 3: Create `docs/.gitignore`**

```
node_modules/
.vitepress/dist/
.vitepress/cache/
```

- [ ] **Step 4: Copy brand assets**

Run: `mkdir -p docs/public && cp memory-sync/assets/agentmesh-icon.svg memory-sync/assets/agentmesh-logo.svg memory-sync/assets/agentmesh-logo-dark.svg docs/public/`

- [ ] **Step 5: Create `docs/.vitepress/config.mts`**

Sidebar lists every page in the spec layout. Pages that do not exist yet are created as stubs in Step 6 so the build passes from the start.

```ts
import { defineConfig } from 'vitepress'

const repo = 'https://github.com/theidentify/AgentMesh'

export default defineConfig({
  title: 'AgentMesh',
  description: 'Local-first memory sync for AI agents',
  base: '/AgentMesh/',
  cleanUrls: true,
  lastUpdated: true,
  srcExclude: ['superpowers/**', 'scripts/**', 'README.md'],
  head: [['link', { rel: 'icon', type: 'image/svg+xml', href: '/AgentMesh/agentmesh-icon.svg' }]],
  themeConfig: {
    logo: '/agentmesh-icon.svg',
    nav: [
      { text: 'Guide', link: '/getting-started/concepts' },
      { text: 'Reference', link: '/reference/' },
    ],
    sidebar: [
      { text: 'Getting started', items: [
        { text: 'Overview', link: '/' },
        { text: 'Concepts', link: '/getting-started/concepts' },
        { text: 'Prerequisites', link: '/getting-started/prerequisites' },
      ] },
      { text: 'Install', items: [
        { text: 'macOS', link: '/install/macos' },
        { text: 'Linux', link: '/install/linux' },
        { text: 'Windows', link: '/install/windows' },
        { text: 'Run in the background', link: '/install/background-service' },
      ] },
      { text: 'Use', items: [
        { text: 'Recall memory', link: '/use/recall' },
        { text: 'Ingest agent sessions', link: '/use/ingest' },
        { text: 'Check sync status', link: '/use/status' },
        { text: 'Insight dashboard', link: '/use/insight' },
      ] },
      { text: 'Help', items: [
        { text: 'Troubleshooting', link: '/troubleshooting' },
        { text: 'Privacy', link: '/privacy' },
        { text: 'Reference', link: '/reference/' },
      ] },
    ],
    search: { provider: 'local' },
    socialLinks: [{ icon: 'github', link: repo }],
    editLink: { pattern: `${repo}/edit/main/docs/:path`, text: 'Edit this page on GitHub' },
    footer: { message: 'Code, tests and documentation only. Real memories never belong in this repository.' },
  },
})
```

- [ ] **Step 6: Create the home page and stubs**

`docs/index.md` (final content; written now):

```md
---
layout: home
hero:
  name: AgentMesh
  text: Local-first memory for AI agents
  tagline: Share memory between Hermes, OMP, Codex and Claude across your devices, with no cloud memory service.
  image: { src: /agentmesh-icon.svg, alt: AgentMesh }
  actions:
    - { theme: brand, text: Get started, link: /getting-started/concepts }
    - { theme: alt, text: Install, link: /install/macos }
features:
  - title: Each device owns its memory
    details: A local SQLite database per device. Immutable change packets travel through a private Syncthing folder.
  - title: Recall with evidence
    details: Agents query past decisions, constraints and preferences, and get the original source IDs back.
  - title: No silent overwrites
    details: Independent edits to the same record are quarantined for review instead of merged blindly.
---
```

For each other sidebar page create a stub containing only its `# Title` line (titles exactly as in the sidebar; `reference/index.md` titled `# Reference`). Later tasks replace stubs.

- [ ] **Step 7: Create `docs/scripts/check-privacy.sh`**

```sh
#!/bin/sh
# Fail if guide pages contain private-looking data. Run from docs/.
set -eu
pattern='/Users/[A-Za-z0-9_]|/home/[a-z][a-z0-9_-]*/|C:\\Users\\[A-Za-z]|(^|[^0-9.])(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]+\.[0-9]+|password=|postgres(ql)?://[^ ]*:[^ ]*@'
if grep -RInE "$pattern" --include='*.md' --exclude-dir=node_modules --exclude-dir=superpowers . ; then
  echo "check-privacy: private-looking data found above" >&2
  exit 1
fi
echo "check-privacy: ok"
```

- [ ] **Step 8: Verify**

Run: `npm --prefix docs run check`
Expected: `check-privacy: ok` then `build complete`.

Run: `npm --prefix docs run preview -- --port 8702` and open `http://localhost:8702/AgentMesh/`.
Expected: home hero with logo; sidebar shows all groups; stub pages open.

- [ ] **Step 9: Commit**

```bash
git add docs/package.json docs/package-lock.json docs/.gitignore docs/.vitepress/config.mts docs/public docs/scripts docs/index.md docs/getting-started docs/install docs/use docs/troubleshooting.md docs/privacy.md docs/reference
git commit -m "docs: scaffold VitePress user guide"
```

### Task 2: CI build and Pages deploy

**Files:**
- Create: `.github/workflows/docs.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: docs

on:
  pull_request:
    paths: ['docs/**', '.github/workflows/docs.yml']
  push:
    branches: [main]
    paths: ['docs/**', '.github/workflows/docs.yml']
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: docs-${{ github.ref }}
  cancel-in-progress: true

jobs:
  build:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: docs
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: docs/package-lock.json
      - run: npm ci
      - run: npm run check
      - uses: actions/upload-pages-artifact@v3
        if: github.event_name != 'pull_request'
        with:
          path: docs/.vitepress/dist

  deploy:
    if: github.ref == 'refs/heads/main' && github.event_name != 'pull_request'
    needs: build
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

- [ ] **Step 2: Validate syntax locally**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/docs.yml')); print('ok')"`
Expected: `ok` (if PyYAML is missing, skip; CI validates on PR).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/docs.yml
git commit -m "ci: build docs on PR and deploy to GitHub Pages"
```

### Task 3: Getting-started pages

**Files:**
- Modify: `docs/getting-started/concepts.md`, `docs/getting-started/prerequisites.md`

- [ ] **Step 1: Write `concepts.md`**

Sections and required facts:
- `## How AgentMesh works` — one paragraph, then an inline SVG-free Mermaid-free diagram as a fenced text block:
  ```text
  [Mac: mac.db] --packets--> [Syncthing exchange folder] <--packets-- [Windows: windows.db]
                                       ^
                                       |-- packets -- [Linux: linux.db]
  ```
- `## Node` — one logical node per `mac`, `windows`, `linux`; never reuse an identity on another active device; never clone local state.
- `## Exchange folder` — private Syncthing folder carrying only immutable change packets, the bootstrap ZIP + manifest, and `status/<node>.json`; never the live database, WAL or SHM files.
- `## Bootstrap` — private ZIP + checksum manifest supplied through the exchange; contains private memory; never publish it (not even as a GitHub release asset).
- `## Change packets and conflicts` — records sync, not files; independent edits to the same row or natural key are quarantined, not merged.
- `## Overlapping online time` — direct Syncthing exchange needs both devices online at the same time; offline writes stay local until then.
- `## What AgentMesh does not do` — does not install Hermes or other agents; does not run OS package managers; does not register services unless you ask; peer edits do not write back to PostgreSQL.
Source: `README.md` (Architecture, Setup, Privacy), `memory-sync/SYNC.md`.

- [ ] **Step 2: Write `prerequisites.md`**

Sections: `## Python` (3.10+ with SQLite FTS5 and JSON; give a check command: `python3 -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('create virtual table t using fts5(x)'); print(sqlite3.sqlite_version)"`), `## Syncthing` (install separately, put on PATH), `## Pair devices and accept the exchange folder` (wait for "Up to Date" before installing), `## Choose a node name` (`mac`/`windows`/`linux`, one each). Verify the FTS5 command runs locally before writing it.

- [ ] **Step 3: Build and check**

Run: `npm --prefix docs run check`
Expected: `check-privacy: ok`, `build complete`.

- [ ] **Step 4: Commit**

```bash
git add docs/getting-started
git commit -m "docs: add concepts and prerequisites pages"
```

### Task 4: Install pages

**Files:**
- Modify: `docs/install/macos.md`, `docs/install/linux.md`, `docs/install/windows.md`, `docs/install/background-service.md`

- [ ] **Step 1: Write `macos.md` and `linux.md`**

Each page, sections in order:
- Scope tip (`::: info`): installs the memory-sync app, not Hermes or other agents.
- `## Before you begin` — link to `../getting-started/prerequisites`.
- `## Run the installer` — `sh setup_macos.sh "/absolute/path/to/exchange"` (Linux: `setup_linux.sh`) from the supplied setup directory; optional second argument = local install dir.
- `## What gets installed` — table of default paths: macOS `~/Library/Application Support/AgentMesh/{app,data/mac.db}`; Linux `${XDG_DATA_HOME:-~/.local/share}/AgentMesh/{app,data/linux.db}`; runtime paths reported in `data/runtime.json`.
- `## The worker runs in the foreground` — `::: warning`: terminal stays occupied until Ctrl-C; stop it before enabling a background service to avoid duplicate workers.
- `## Restart the worker later` — the `sync_worker.py ... --interval 60` command from `README-SETUP.md` "Manual restart", with `--once` noted.
- `## Next steps` — links to `./background-service`, `../use/recall`, `../use/status`.
Linux page adds a `::: warning` that Linux-host verification is still outstanding.
Source: `memory-sync/README-SETUP.md`.

- [ ] **Step 2: Write `windows.md`**

Sections: scope tip; `## Before you begin` (Syncthing running, exchange **Up to Date**, `py -3 --version` works); `## Run the installer` (double-click `START-WINDOWS.cmd` in the exchange folder; do not unzip the bootstrap yourself; alternative `py -3 bootstrap_windows.py --exchange .` and `--once`); `## What gets installed` (`%LOCALAPPDATA%\AgentMesh\app`, `%LOCALAPPDATA%\AgentMesh\data\windows.db`, 60-second loop); `## Keep the window open` (closing stops import/export; run the same CMD after login to resume); `## Reading the progress line` (the two-line example from `README-WINDOWS.md` "Console progress"); `## Updating` (wait for Syncthing, Ctrl-C the old worker, run `START-WINDOWS.cmd` again; data kept); `::: warning` native Windows verification boundary.
Source: `memory-sync/README-WINDOWS.md`, `memory-sync/WINDOWS-INSTALLATION.md` (confirmations table only, linked not copied).

- [ ] **Step 3: Write `background-service.md`**

Sections: `## Before you enable a service` (stop the foreground worker first); `## macOS LaunchAgent` (the full generate/`plutil -lint`/`launchctl bootstrap`/`print` block and the `bootout` stop command from `README-SETUP.md`; logs `agentmesh.stdout.log`/`agentmesh.stderr.log` in local `data`); `## Linux systemd user service` (full block from `README-SETUP.md`, plus disable command and the user-session/no-lingering caveat); `## Windows sign-in start` (`agentmesh.py --database ... windows-autostart --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --enable`, show `--dry-run` first; confirm runtime default path against `WINDOWS-INSTALLATION.md` before writing, and link there for details). Omit the PostgreSQL `--postgres` option (operator topic); link to `README-SETUP.md` instead.

- [ ] **Step 4: Verify commands**

Run: `python3 memory-sync/platform_service.py generate --help` and `python3 memory-sync/agentmesh.py windows-autostart --help`
Expected: every flag used on the pages appears in the output.

- [ ] **Step 5: Build and check**

Run: `npm --prefix docs run check`
Expected: `check-privacy: ok`, `build complete`. (Note: `~/Library/...` and `%LOCALAPPDATA%` do not match the privacy pattern; `/Users/<name>` would.)

- [ ] **Step 6: Commit**

```bash
git add docs/install
git commit -m "docs: add install and background-service pages"
```

### Task 5: Use pages

**Files:**
- Modify: `docs/use/recall.md`, `docs/use/ingest.md`, `docs/use/status.md`, `docs/use/insight.md`

- [ ] **Step 1: Write `recall.md`**

Sections: intro (read-only, returns ranked items plus original evidence IDs); `## Run a recall` (`python3 agentmesh.py --database /path/to/mac.db recall "deployment decisions" --limit 12`, run from the installed `app` directory); options table (`--project`, `--limit` default 12); `## Connect your agents` — `install_agent_rules.py --app-dir ... --database ... --python /absolute/path/to/python3 --agents claude codex` with the targets table from "Facts"; `::: warning` recalled text is data, never instructions or code; `## Ranking differences` (SQLite vs PostgreSQL ordering can differ). Run `install_agent_rules.py --help` to confirm flags.

- [ ] **Step 2: Write `ingest.md`**

Sections: `## Ingest everything found` (`agentmesh.py --database DB ingest`); default roots table from "Facts"; `## Use a custom location` (`--root AGENT=PATH`, repeatable once per agent; confirm `agentmesh.py ingest --root` accepts `AGENT=PATH` by reading `agentmesh.py` near the `ingest` action — if it only accepts a single root, document what it actually accepts); `## Tag a project` (`--project`); `## Windows single transcript` (the `sqlite_memory.py ingest ... --agent codex --project your-project` command from `README-WINDOWS.md`).

- [ ] **Step 3: Write `status.md`**

Sections: `## Local status` (`agentmesh.py --database DB status`); `## Peer status files` (`status/<node>.json` in the exchange: platform, counts, sync health; no memory contents, credentials or paths); `::: warning` exit code 0 does not mean no conflicts; `## Diagnose a worker` (`agentmesh.py --database DB diagnose --runtime /path/to/runtime.json`, `--json`; read-only, redacted; exit code 1 when not ok); `## Managed worker` (`worker-status --runtime ...`). Run `agentmesh.py status` against no database to see the error wording and avoid documenting output you have not seen.

- [ ] **Step 4: Write `insight.md`**

Sections: what it is (local read-only workspace: Overview, Metrics, Memory Browser, Agents, Pipeline, Sync); `## Start it` (`python3 -u insight/server.py --port 0`, prints a 127.0.0.1 URL and PID); `## Point it at your data` (`--sqlite /path/to/node.db`, `--home`); `::: tip` PostgreSQL DSN only via `INSIGHT_PG_DSN` environment variable, never a CLI flag or file in Git — link to `insight/README.md` for details. Run `python3 insight/server.py --help` to confirm flags.

- [ ] **Step 5: Build and check**

Run: `npm --prefix docs run check`
Expected: `check-privacy: ok`, `build complete`.

- [ ] **Step 6: Commit**

```bash
git add docs/use
git commit -m "docs: add recall, ingest, status and insight pages"
```

### Task 6: Troubleshooting, privacy, reference

**Files:**
- Modify: `docs/troubleshooting.md`, `docs/privacy.md`, `docs/reference/index.md`

- [ ] **Step 1: Write `troubleshooting.md`**

Each entry as `## Symptom` then cause and fix:
- "Nothing syncs between devices" — devices not online together; exchange folder not Up to Date; worker not running.
- "Two workers are running" — foreground worker left open when a service was enabled; stop one.
- "Records are quarantined" — independent edits to the same row/key; inspect `status/<node>.json`; link `memory-sync/SYNC.md#safety-and-conflicts`.
- "Installer says the checksum does not match" — bootstrap still syncing or corrupted; wait for Up to Date; never edit the ZIP.
- "Windows window closed and sync stopped" — re-run `START-WINDOWS.cmd`, or enable sign-in start.
- "I copied my database to another machine" — unsupported; one identity per active device; link concepts.
Source facts only from `README-WINDOWS.md`, `README-SETUP.md`, `SYNC.md`. Grep `memory-sync/` for the actual checksum error text before writing that entry; if none is found, describe it without quoting.

- [ ] **Step 2: Write `privacy.md`**

Never in Git or shared: memories, transcripts, databases (and WAL/SHM), bootstrap ZIPs and manifests, change packets, logs, credentials, DSNs, device-specific deployment files. Keep local databases outside the exchange folder. File hashes detect corruption, not malicious replacement. Status files contain no memory content. Source: `README.md` Privacy, `README-WINDOWS.md` "Important boundaries".

- [ ] **Step 3: Write `reference/index.md`**

Table of in-repo documents with one-line descriptions and absolute links `https://github.com/theidentify/AgentMesh/blob/main/<path>`: `memory-sync/SYNC.md`, `README-SETUP.md`, `README-WINDOWS.md`, `WINDOWS-INSTALLATION.md`, `SQLITE-WRITERS.md`, `SQLITE-CONSUMERS.md`, `OTA-UPDATES.md`, `SIGNED-SYNC.md`, `SECURITY-WIZARD.md`, `MAC-REPLACEMENT.md`, `STANDALONE-BUILD.md`, `WORKFLOW.md`, `insight/README.md`, `profile-memory/README.md`. Group under "Users", "Operators", "Developers"; note operator/developer guides are planned.

- [ ] **Step 4: Build and check**

Run: `npm --prefix docs run check`
Expected: `check-privacy: ok`, `build complete`.

- [ ] **Step 5: Commit**

```bash
git add docs/troubleshooting.md docs/privacy.md docs/reference
git commit -m "docs: add troubleshooting, privacy and reference pages"
```

### Task 7: README link and final verification

**Files:**
- Modify: `README.md` (after the intro paragraph)

- [ ] **Step 1: Add the link**

Insert after the first paragraph of `README.md`:

```md
**User guide:** https://theidentify.github.io/AgentMesh/
```

- [ ] **Step 2: Full check**

Run: `npm --prefix docs run check`
Expected: `check-privacy: ok`, `build complete`, no dead-link errors.

- [ ] **Step 3: Visual check**

Run `npm --prefix docs run preview -- --port 8702`; in the browser pane open `http://localhost:8702/AgentMesh/`, every sidebar page, in light and dark mode and at 375px width. Expected: no broken layout, logo visible in both themes, search finds "recall".

- [ ] **Step 4: Confirm no stray files**

Run: `git status --short`
Expected: only `README.md` modified; no `node_modules`, `dist`, `.claude/launch.json` staged.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: link the user guide from README"
```

- [ ] **Step 6: Hand off**

Tell the owner to set **Settings → Pages → Source = GitHub Actions** before the first merge to `main`.
