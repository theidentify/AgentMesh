# Receive Chain Order (0.3.2) Implementation Plan

> **For agentic workers:** read `docs/superpowers/specs/2026-10-16-receive-chain-order-design.md` fully. The
> spec wins on conflict. Stop and report a real design gap instead of guessing.

## Ground rules

These are the same as `docs/superpowers/plans/2026-10-16-receive-backlog-capture.md`:
- **Branch and isolation:** work on branch `fix/receive-chain-order` in this worktree; disposable data
  only, never `~/.hermes` or real databases (that includes no shared-memory recall).
- **Method:** TDD; run tests from `memory-sync/` and from the repo root; use Windows-safe fixtures.
- **Commits:** one per task, ending with `Co-Authored-By: GPT (Codex) <noreply@openai.com>`.
- **Output and git:** no GATES.md; the REPORT goes only at
  `docs/superpowers/plans/2026-10-16-receive-chain-order.REPORT.md`; no push, merge or rebase.

## Tasks

1. **Failing tests:** linear attempts for a shuffled single-key chain (legacy and indexed),
   interleaved chains, and unchanged pending/conflict/invalid outcomes. Commit.
2. **Dependency-driven retries** in receive, as the spec describes. Commit.
3. **Benchmark chain mode,** the full suite from both directories, and the REPORT. Commit.
