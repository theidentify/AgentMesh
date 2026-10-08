<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/agentmesh-logo-dark.svg">
  <img src="assets/agentmesh-logo.svg" alt="AgentMesh" width="384" height="80">
</picture>

# AgentMesh ingest → summarize → sync → recall

## Scope and topology

AgentMesh is an **opt-in staged SQLite backend**, not a silent replacement of
legacy agent memory. The original production PostgreSQL caller remains unchanged.
Peer SQLite memory never writes PostgreSQL; any primary-side PostgreSQL mirror is
read-only at its PostgreSQL source and preserves original IDs in SQLite.

Use one logical node per OS: `mac`, `windows`, `linux`. Mac is the single primary
LLM summary writer. Windows and Linux ingest their own local sources, exchange
observations, and receive primary-generated summaries. Do not reuse an identity
on multiple active machines. Do not run secondary summary writers.

## Prerequisites and private bootstrap

- Python and Syncthing on each participating machine; no Python packages are
  required by this installer. Install whatever the configured provider needs on
  the primary only. Claude Code is optional: needed only when selected as the
  primary provider command, with authentication established separately.
- Initialize a Git repository for **code only** if needed. Keep bootstrap data
  private: no database, WAL, transcript, exchange payload, auth file, key, or local
  workflow configuration in commits or public artifacts. Do not commit secrets.
- Provision each local SQLite database from the same consistent, uninitialized
  private baseline; use SQLite backup for an open database, not a main-file copy
  that omits WAL. Initialize each clone with its own OS identity and shared group
  ID; never copy an already initialized peer identity. See `SYNC.md`.
- Keep each writable database **outside Syncthing**. Pair only the immutable
  exchange folder. Credentials and local runtime config stay outside that folder.

## Local workflow configuration and execution

Configure the sync/summary runner in local `data/workflow.json`, outside the code
checkout and exchange folder. The runtime owns the exact JSON schema/options;
use the shipped `agentmesh.py --help` and subcommand help rather than guessing
fields. Configure absolute native-platform paths for Python, app, database,
exchange and source roots. On Mac additionally configure the actual LLM provider
command as an argv array and its locally authenticated execution environment.
No provider key is embedded in agent rules or their command manifests.

The runtime exposes `ingest`, `summarize`, `once`, `watch`, `status`, and `recall`.
This installer does **not** install a scheduler, start services, or create autostart.
Validate one foreground cycle first; runtime service configuration is a separate,
explicit operator step. Exact scheduling flags are runtime-owned.

1. Ingest local OMP (`~/.omp/agent/sessions`), Codex (`~/.codex/sessions`), and
   Claude (`~/.claude/projects`) JSONL sources, or explicitly configured roots.
   Symlinks and Claude `subagents` directories are excluded. Only complete,
   newline-terminated records are consumed; cursors support incremental ingest.
   Hermes is a **recall consumer** here: native Hermes transcripts are not yet
   ingested. Installing its skill does not add an ingestion source.
2. Capture/publish local observations and receive peer exchange packets. IDs are
   preserved across nodes; node-reserved ranges avoid new-ID collisions. Packet
   UUID receipts prevent replay, imported revisions avoid echo, and unresolved
   dependencies retry on later cycles rather than becoming silent overwrites.
3. On **Mac only**, run summarization using the configured real provider command.
   It receives a JSON task on stdin and must return the specified grounded JSON
   response on stdout, with actual provider/model and observation-event source
   IDs. No fabricated fallback summaries. Summary event receipts/provenance,
   not only an ID high-water mark, deduplicate processed events and allow late
   arrivals below that mark. Provider failure must not advance successful work.
4. Exchange generated memories, summaries and receipts back to peers. Inspect
   status diagnostics/outbox/pending/conflicts, not just Syncthing connectivity.
5. Agents perform read-only recall against their own local database. Query output
   supplies source IDs; cite them and verify current facts independently.

## Install opt-in agent instructions

Python API (no runtime execution, no database reads):

```python
from install_agent_rules import install
installed = install(app_dir, database, home=None, agents=None,
                    hermes_home=None, python=None)
# Returns {agent_name: pathlib.Path_to_rules_or_skill}.
```

`app_dir`, `database`, configured `python`, explicit `home` and `hermes_home` must
be absolute native-platform paths. `python` defaults to `sys.executable`; the app
entry point is `app_dir/agentmesh.py`. `agents` defaults to all four agents; pass a
name or sequence to select a subset. Empty selection writes nothing.

POSIX example with synthetic paths (replace before deliberately installing):

```sh
python3 /opt/agentmesh/install_agent_rules.py --app-dir /opt/agentmesh --database /private/agentmesh/mac.db --python /opt/python/bin/python3 --agents hermes omp codex claude
```

PowerShell example with synthetic paths:

```powershell
& 'C:\Python\python.exe' 'C:\AgentMesh\install_agent_rules.py' --app-dir 'C:\AgentMesh' --database 'C:\PrivateData\windows.db' --python 'C:\Python\python.exe' --agents hermes omp codex claude
```

| Agent | Destination |
| --- | --- |
| Hermes | `<resolved HERMES_HOME>/skills/agentmesh-shared-memory/SKILL.md` |
| OMP | `<home>/.agents/skills/agentmesh-shared-memory/SKILL.md` |
| Codex | `<home>/.agents/skills/agentmesh-shared-memory/SKILL.md` (shared with OMP) |
| Claude | `<home>/.claude/rules/agentmesh-shared-memory.md` |

Hermes resolution is explicit `hermes_home`, then environment `HERMES_HOME`, then
`<home>/.hermes`. Set `--hermes-home` to the intended current profile when needed;
no other profiles are scanned or updated. Explicit `home` does not override a
nonempty `HERMES_HOME`. Legacy `shared-memory.rules` and `omp-shared-memory` are
untouched. Reload/explicitly load instructions in the intended agent session and
verify discovery; filesystem installation is not proof of automatic activation.
Codex and OMP share the same discoverable user skill and its `command.json`.
The installer does not put Markdown instructions into Codex `.rules` execution-
permission files. Existing legacy rules remain untouched.

Skill directories get an adjacent `command.json`; rule directories get
`agentmesh-shared-memory.command.json`. Each contains only ownership/version,
`recall_argv`, and default limit 12. It contains configured paths but no source
body or credentials. Preferred execution is `subprocess.run(argv, shell=False)`;
append the query as one argument, then optional `--project PROJECT` and `--limit N`.
Generated POSIX and PowerShell examples correctly quote spaces/apostrophes;
PowerShell examples are not cmd.exe commands.

```sh
python3 /opt/agentmesh/agentmesh.py --database /private/agentmesh/mac.db recall 'prior decision' --project 'sample-project' --limit 12
```

Existing unowned destination files/skill directories or manifests are refused,
not overwritten. All targets are preflighted before any write. Only the uniquely
marked `BEGIN AGENTMESH MANAGED v1` / `END AGENTMESH MANAGED v1` section is replaced
on updates; manual prefix/suffix bytes are preserved. Repeating an unchanged
installation does not rewrite files. Symlink destinations/directories are refused.
An I/O failure during writing can leave a partial installation; correct the
failure and rerun. No filesystem-wide transactional guarantee is claimed.

## Safety and acceptance checks

Memory is untrusted evidence, never executable instructions. Current user
instructions override conflicting memory without weakening system/developer
precedence. Agents cite returned source IDs and only recall; ingest, summarize,
sync and database writes belong to the workflow, not the agent skill.

Run focused tests from `memory-sync` with an appropriate writable temporary
folder if your environment requires one:

```sh
python3 -m unittest test_install_agent_rules -v
```

Tests use real, synthetic temporary files: legacy/manual preservation, owned
section updates, idempotent bytes/mtime, profile isolation, exact argv and POSIX
execution, static PowerShell quoting, secret-free manifests, symlink refusal and
CLI installation. They do not read private transcripts or install into a live home.
Actual native Windows/Linux execution, PowerShell execution, provider auth,
peer delivery, agent discovery and scheduling must still be verified on target
machines. A Mac test pass is not cross-platform deployment proof.
