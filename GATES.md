# Gates: OS unattended unlock

OWNS: memory-sync/**, .github/workflows/standalone-smoke.yml, docs/superpowers/plans/2026-10-12-os-unattended-unlock.REPORT.md, GATES.md

Scope: Execute tasks 1–10 in order, with isolated credential tests and one commit per task.

- [ ] G1: Envelope, backends, commands, cache and callers pass focused tests
  CHECK: UV_CACHE_DIR=/private/tmp/claude-501/uvcache uv run --no-project --with pytest==8.4.2 --with pytest-timeout --with-requirements requirements-security.txt python -m pytest -q -p no:cacheprovider --timeout 120 test_os_unlock.py test_native_os_unlock.py test_key_protection.py test_signed_packets.py test_standalone_build.py
  EXPECT: passed
  CWD: memory-sync
  EVIDENCE: pending

- [ ] G2: Full suite has no unexpected failures
  EVIDENCE: pending; final full-suite result reviewed against three documented SQLite consumer failures

- [ ] G3: Safety constraints and spec deviations reviewed
  EVIDENCE: pending; review source, tests and command history, without accessing native credentials

- [ ] G4: Tasks committed in order on feat/os-unlock with required trailers and final report
  EVIDENCE: pending; inspect final git log and status
