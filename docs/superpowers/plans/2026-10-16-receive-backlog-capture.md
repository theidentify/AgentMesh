# Receive Backlog Capture (0.3.1) Implementation Plan

> **For agentic workers:** execute in order. The spec is
> `docs/superpowers/specs/2026-10-16-receive-backlog-capture-design.md`; read it fully first. The spec wins on
> conflict. Stop and report instead of guessing.

**Goal:** remove the per-packet full-DB capture from receive (targeted capture), make long receives
interruptible by `worker-stop`, and let the Windows installer recover from a failed stop.

## Ground rules

- Work in `/Users/krisanathep/Workspaces/AgentMesh/.claude/worktrees/fervent-blackwell-fcbf07` on branch
  `fix/receive-capture-backlog` (based on main b5ce417). Do not push, open PRs, merge, rebase or touch other
  branches/worktrees.
- **Disposable data only.** Never read or modify `~/.hermes`, real databases, identities, exchanges,
  launchd jobs or the running Mac worker.
- **TDD:** write each failing test first and confirm it fails for the stated reason, then implement.
- Tests run from `memory-sync/` **and** from the repo root:
  `UV_CACHE_DIR=/private/tmp/claude-501/uvcache uv run --no-project --with pytest==8.4.2 --with pytest-timeout --with-requirements requirements-security.txt python -m pytest -q -p no:cacheprovider --timeout 300 <files>`
  - Known unrelated failure on main: `test_digest_api.py::test_client_envelope_satisfies_bounded_extract_command`.
    Everything else must pass.
- **Windows-safe fixtures:**
  - create private directories with the product helpers (`identity_backup.private_mkdir` /
    `signed.init_identity`), never a bare `mkdir`
  - give every subprocess an explicit `cwd`
  - no giant pytest param ids
  - mark POSIX-only mocks as such
- Commit after each task, ending with `Co-Authored-By: GPT (Codex) <noreply@openai.com>`. No GATES.md. The
  report goes only in `docs/superpowers/plans/2026-10-16-receive-backlog-capture.REPORT.md`.
- Match the surrounding compact style.

## Tasks

1. **Reproduction tests (legacy and indexed):** a backlog receive with a bounded number of `rows()`
   calls. They must fail on the current code. Commit as `test:`.
2. **Targeted `_capture(keys=...)`** with a shared row-normalization helper; **`_packet_keys`** covering
   legacy, v2 and v3 change/resolution bodies (fallback `None` → full capture); wire both receive paths.
   Add the correctness tests from the spec. Commit.
3. **Interrupt plumbing** `run_once → cycle → receive`, wired in `worker_lifecycle.run`, with unit and
   lifecycle tests. Commit.
4. **Installer:** a failed `replace/stop` returns to `ready`; `windows-recover` supports the
   `replace/stop` shape. Add tests, and update the recovery section of WINDOWS-INSTALLATION.md
   (re-enable a hand-disabled task right before recover and upgrade). Commit.
5. **Benchmark script plus REPORT:**
   - put the benchmark script under `memory-sync/support/` (it generates a disposable DB of 200k+ rows
     and a 200-packet backlog)
   - record before/after receive CPU times
   - run the full suite from both directories
   - report deviations and open questions
   
   Commit.
