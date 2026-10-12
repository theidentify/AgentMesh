# Gates: OS unattended unlock

OWNS: memory-sync/**, .github/workflows/standalone-smoke.yml, docs/superpowers/plans/2026-10-12-os-unattended-unlock.REPORT.md, GATES.md

Scope: Execute tasks 1–10 in order, with isolated credential tests and one commit per task.

- [x] G1: Envelope, backends, commands, cache and callers pass focused tests
  CHECK: UV_CACHE_DIR=/private/tmp/claude-501/uvcache uv run --no-project --with pytest==8.4.2 --with pytest-timeout --with-requirements requirements-security.txt python -m pytest -q -p no:cacheprovider --timeout 120 test_os_unlock.py test_native_os_unlock.py test_key_protection.py test_signed_packets.py test_standalone_build.py && echo OS_UNLOCK_FOCUSED_OK
  EXPECT: OS_UNLOCK_FOCUSED_OK
  CWD: memory-sync
  EVIDENCE: automatic-evidence=v1; definition-sha256=d5169deb4bc8b7594cb5975326b4a3d5121bd1867157d71d807ac015cf3cc1a0; exit=0; EXPECT=matched; output-sha256=77751d67e329cb25bb91585ac9bb101d4f4a79a658ed26b63aa0ed369c02a9d6; output-bytes=293; shell=/bin/sh; cwd=/Users/krisanathep/Workspaces/AgentMesh/.claude/worktrees/encrypted-key-storage-28470f/memory-sync; path=3f93b802eba2/30 entries

- [x] G2: Full suite has no unexpected failures
  EVIDENCE: Final requested full-suite command: 728 passed, 16 skipped, 3 failures, 1 warning in 103.41s. Parsed failure-name set exactly matches the three documented test_sqlite_consumers temporary-symlink cases; report lists them.

- [x] G3: Safety constraints and spec deviations reviewed
  EVIDENCE: Source/command-history review, AST from-None audit, deprecated API and ACL-flag negative checks (with positive regex control), subprocess context/report tests, and three locally skipped native tests. Deviations recorded in REPORT; no real credential operations or native-test opt-in.

- [x] G4: Tasks committed in order on feat/os-unlock with required trailers and final report
  EVIDENCE: Verified exactly ten ordered task commits on feat/os-unlock, each ending in the required co-author trailer; Task 10 includes REPORT. Worktree clean before this evidence-only amendment; no push/merge/rebase or other branches touched.
