# Triage of the round-2 persona reviews

Reports: `2026-10-12-round2-{community-user,professor,practitioner}.md` (Thai).
Round 1 status: professor 43/46 resolved, practitioner 16 resolved + 4 partial,
community user most resolved, none untouched.

## Guide fixes (this PR)

- **Professor** 33, 38, 42 (partials) and 47–54: accept all suggested wording.
- **Practitioner 23**: exchange unavailable also pauses automatic ingestion and
  summarization on this device; separate "peer offline, folder present" from
  "exchange folder unavailable"; manual `ingest` remains possible.
- **Practitioner 24**: signed trust is per receiver; a three-device group needs every
  receiver to approve the other two senders (six directions); wizard status reflects
  the recorded pair only.
- **Practitioner 27**: `watch`, the bootstrap loop and generated services force legacy
  summarization on their first cycle; managed worker does not. Irrelevant when
  summarization is off.
- **Practitioner 8, 9, 17 (partials)**: where no supported recovery exists, say so
  plainly, list what to preserve, and what to report; do not invent procedures.
- **Practitioner 25** (current behavior until the maintainer decides): revoking a key
  makes previously accepted packets from it invalid on the next cycle, which stops the
  managed worker. Warn before revocation; link the operator procedure.
- **Practitioner 26**: the installed `app` directory contains `baseline.jsonl.gz` and
  bootstrap manifests with private memory; treat the whole installation root as
  private; never share `app`.
- **Practitioner 22**: use a dedicated exchange folder per sync group.
- **Community new 1 / professor 54 / practitioner 10**: before running a bootstrap
  installer, decide which device ingests which source and check for identical
  absolute transcript paths; the installer enables default ingestion and starts the
  worker immediately (a code change to defer this is pending a maintainer decision).
- **Community new 2**: give Windows commands in their own shell syntax (Command Prompt
  one-liners or `^`), at least for Quick start, recall rules and new group.
- **Community new 3**: custom `--local-dir`/`--node` on Windows must be repeated on
  restart/update; `START-WINDOWS.cmd` only covers the default.
- **Community new 4**: how to stop using a Windows bootstrap installation (what to
  keep, what to delete, removing agent rules).
- **Community new 5**: the guide's reference link to `memory-sync/WORKFLOW.md` conflicts
  on identity wording; fix the guide side here and note the conflict (WORKFLOW.md is
  fixed in the code PR).
- **Community new 6**: read-only way to inspect `ingestion_errors` and cursors.
- **Community new 7**: repeat test needs a new file and a new message ID; check A's
  `inserted` count before watching B.
- **Community term table**: define capture, receipts/history (and where they live),
  transient/permanent (link), packet size terms, diff/backlog, ingestion cursor at
  first use.
- **Community partials 1, 7, 20/24, 22/27/35, 30**: tighten where the guide can, without
  inventing procedures.

## Code fixes (separate PR, Codex)

- **Practitioner 21**: re-running the bootstrap on a signed installation starts the
  loop without the signing directory; resolve security paths like
  `install_adopt.security_paths`.
- **Practitioner 22**: `setup-new` must refuse an exchange that already contains
  another group's packets.
- **Practitioner 26**: extracted bootstrap files (notably `baseline.jsonl.gz`) must be
  owner-only.
- **Community new 5**: align `memory-sync/WORKFLOW.md` identity wording with the
  OS-neutral model, and state precisely which identity is the summary writer.

## Needs a maintainer decision

- **Practitioner 25**: should packets accepted before a key was revoked stay accepted?
- **Ingest before first cycle**: should the bootstrap offer a way to review or limit
  ingestion roots before the worker starts?
