# Rotation with Conflict Resolution Implementation Plan

> **For agentic workers:** execute in order. The spec is
> `docs/superpowers/specs/2026-10-13-rotation-with-conflict-resolution-design.md`; read it fully, plus the
> #9 spec it amends (`2026-10-13-key-rotation-recovery-design.md`) and the #35 design
> (`2026-10-12-conflict-resolution-design.md`, section "Signing and revocation decision"). The new spec
> wins on conflict.

**Goal:** replace RESIGN with PUBLISH (publish pending old-key packets unchanged and list them in the
manifest), make rotation work for v2 and v3 packets with the resolution store active, document the
manifest-as-authorized-evidence rule, and make `compromise-report` aware of v3 packets.

**Files:**
- `memory-sync/key_rotation.py`
- `memory-sync/test_key_rotation.py`
- `memory-sync/KEY-PROTECTION.md`
- `memory-sync/SIGNED-SYNC.md`
- possibly `memory-sync/smoke_standalone.py`

## Ground rules

These are the same as the #9 plan (`docs/superpowers/plans/2026-10-13-key-rotation-recovery.md`, Ground
rules), unchanged:
- disposable data only
- no secrets anywhere
- fixed reasons
- Windows-safe fixtures
- TDD
- run the tests from `memory-sync/` **and** from the repo root
- commit after each task with `Co-Authored-By: GPT (Codex) <noreply@openai.com>`
- no GATES.md
- REPORT only at `docs/superpowers/plans/2026-10-13-rotation-with-conflict-resolution.REPORT.md`
- no push

Branch: `fix/rotation-resolution-packets` in the same worktree.

## Tasks

1. **Failing tests first, on DBs with `resolution_store.migrate`:**
   - pending v2, rotated → files published byte-identically and `_sync_packet_evidence` digests
     unchanged
   - a pending v3 resolution packet → rotation succeeds and the resolution converges at the peer
   - manifest evidence accepted, and evidence not on a manifest refused
   - crash and `--resume` during the publish step
   - a pending file that exists with different bytes refuses
   
   Build the v3 scenario with the real flow from `test_resolution_e2e.py` /
   `test_conflict_commands.py`. Read those first and reuse their helpers.
   
   Confirm the tests fail for the stated reason. Commit as `test:`.
2. **Implement PUBLISH in `key_rotation.py`:**
   - format-aware preparation checks: `packet_domain`; checksum rules per format
   - the manifest includes pending rows
   - the transactional, idempotent publish step reuses `memory_sync.publish`'s write and collision
     logic. Factor out a helper rather than duplicating it.
   - the report field `published_pending`
   - the typed word `PUBLISH`
   
   Update the existing #9 tests and smoke for the new word and field. Commit.
3. **compromise-report:** v3 handling, `kind`, `superseded`. Tests. Commit.
4. **Docs:**
   - KEY-PROTECTION.md: replace the RESIGN wording; explain that pending packets are published unchanged
     and listed in the manifest.
   - SIGNED-SYNC.md: the manifest-as-authorized-evidence rule next to revocation.
   
   Commit.
5. **Verify:**
   - the full suite from both directories (only the 3 known `test_sqlite_consumers` failures)
   - `git grep -n "RESIGN" memory-sync` leaves only historical mentions, if any
   - the REPORT
   
   Commit.
