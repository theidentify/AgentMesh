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

## Phase A completion (added 2026-10-12)

Review of the first draft found two problems: user-facing commands missing from the
guide, and pages that start with steps before telling the reader why they are there.

### Introductions

- **Site level.** New `docs/introduction.md` ("What is AgentMesh"), first in the sidebar
  after Overview and the target of the home page's *Get started* button. It covers:
  the problem (agent memory is siloed per agent and per device), what AgentMesh does
  about it, who the guide is for, what AgentMesh is not, and a reading path through
  the guide (Concepts → Prerequisites → Install → Use → Help).
- **Page level.** Every guide page opens, before its first `##` heading, with a lead of
  2–4 sentences answering: what this page helps you do and why it matters; what you
  need first (linked); what you will have at the end. Pages that already have a
  diagram keep it after the lead.
- **Continuity.** Every page ends by pointing to the next logical page.
- Writing follows the `drawing-flow-diagrams` report rules (conclusion-first titles,
  prose walks diagrams in arrow order, values in prose match the diagram).

### New pages

| Page | Covers | Source of truth |
| --- | --- | --- |
| `getting-started/quick-start.md` | Shortest path for someone who already received a bootstrap: check prerequisites → install → check status → first recall → connect agents. Links out for every detail. | existing guide pages |
| `install/new-group.md` | Starting a new, empty sync group without a bootstrap (`setup-new --local-dir --exchange --node`), and when to use it instead of a supplied bootstrap. | `agentmesh.py`, `install_setup.py` |
| `install/security.md` | Signed sync for users: what it protects, `wizard-status`, `wizard-resume`; operator activation stays in the reference. | `SECURITY-WIZARD.md`, `SIGNED-SYNC.md`, `security_wizard.py` |
| `install/update-remove.md` | Updating and removing on macOS, Linux and Windows while keeping data; Windows `windows-upgrade` / `windows-rollback` / `windows-uninstall` at user level. | `README-SETUP.md`, `README-WINDOWS.md`, `WINDOWS-INSTALLATION.md`, `MAC-REPLACEMENT.md`, setup scripts |
| `use/worker.md` | Running and controlling the worker: `once`, `watch`, `worker-run`, `worker-start`, `worker-stop`, `worker-status`, `inspect-install`. | `agentmesh.py`, worker modules |

Still out of scope: `adopt-install`, `summarize`, `mac-replace`, `mac-rollback`,
PostgreSQL mirroring (operator topics, phase B).

Every command, flag and behavior on these pages must be verified against the code;
where code and existing docs disagree, the code wins and the page says only what
the code does.

## Device identity wording (added 2026-10-12)

The design does not tie a device's identity to its operating system. In code, a
device's identity is one of three **slots** (`mac`, `windows`, `linux`;
`memory_sync.RANGES`) plus the sync group UUID. The slot names are labels only:
the platform wrappers pick the matching slot by default, but `bootstrap_windows.py
--node` and `setup-new --node` accept any slot on any OS. A profile-ID model exists
only in the opt-in `profile-memory/` prototype.

Guide wording:

- Use the neutral term **device identity** in prose. State once, in Concepts, that
  today a device identity is one of three slot values `mac`, `windows`, `linux`,
  that these are labels and not operating systems, and that a group therefore has
  at most three devices, each with a different identity.
- Never imply "one device per OS". Two Macs, or a Linux source and a Mac
  destination, are valid when they use different identities.
- Show the literal values only where a command needs them (`--node mac`).
- Mention profile-owned memory only on the Reference page, as an experiment.

## Persona review (added 2026-10-12)

After writing, three independent reviewers read the built guide. Each is a Codex
run (`codex exec -s read-only`) with a persona prompt kept in
`docs/superpowers/reviewers/`:

| Persona | File | Looks for |
| --- | --- | --- |
| Open-source community user, impatient and easily confused | `community-user.md` | where they get lost, missing steps, unexplained terms, frustration |
| University professor proofreading a thesis | `professor.md` | wrong or inconsistent wording, grammar, terminology drift, unclear claims |
| Industry computer-science and mathematics practitioner who must operate it | `practitioner.md` | technical imprecision, unverifiable claims, missing failure modes, things they could not actually run |

Reviewers write their reports in Thai (quotes, locations, commands and suggested English wording stay verbatim). They only report; they do not edit. Findings are triaged against this spec and
the code before any change; a finding that conflicts with the code is rejected.
