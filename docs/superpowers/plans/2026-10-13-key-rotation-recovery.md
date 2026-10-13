# Lost/Compromised Key Recovery (#9) Implementation Plan

> **For agentic workers:** execute task by task, in order. Steps use checkbox (`- [ ]`) syntax. The spec is
> `docs/superpowers/specs/2026-10-13-key-rotation-recovery-design.md`; read it **fully** first, including
> "Facts in the current code". Where the plan and the spec disagree, the spec wins. Stop and report
> instead of guessing.

**Goal:**
- `agentmesh identity-rotate` (journal plus `--resume`), `agentmesh peer-rotate`, and the read-only
  `agentmesh compromise-report`
- two receive-path fixes:
  - **R1:** match own packets before the signature check
  - **R2:** a signed manifest of legitimate old-key packets

**Architecture:**
- A new module `memory-sync/key_rotation.py` owns:
  - the proposal format (build, sign, validate)
  - `rotations.json`
  - the journal and its steps
  - the three operations
- `memory_sync.receive` gains R1 and R2, with small hooks. `signed_packets.Security` gains
  `verify_rotated`.
- It reuses:
  - `identity_backup`: `exchange_activity`, `private_mkdir`, and the leftover checks (add the journal
    names to #8's checks)
  - `key_protection`: `seal`, `console_passphrase`
  - `runtime_lock`, `worker_lifecycle`, `console_prompt`, `os_unlock.current_backend`

**Tech stack:** Python 3.11, the pinned `cryptography`, SQLite, pytest 8.4.2.

## Ground rules

- Work only in `/Users/krisanathep/Workspaces/AgentMesh/.claude/worktrees/encrypted-key-storage-28470f`
  on branch `feat/key-recovery`. Do not push, open PRs, merge, rebase or touch other branches or
  worktrees.
- Use disposable identities and DBs only. Never touch real identities, keychains or Credential Manager.
  Inject the OS backend (copy `FakeBackend` from `memory-sync/test_os_unlock.py`).
- No raw private key or passphrase ever goes to disk, argv, env, logs, errors or reports. The journal
  holds only a #6-sealed identity.
- Errors use fixed reasons, raised so that no lower exception keeps data (see
  `identity_backup.safe_operation` and `os_unlock.MacKeychainFile._run`). Add an explicit allowlist of
  upstream fixed reasons, as `identity_backup.UPSTREAM_REASONS` does.
- **Windows lessons from #8:**
  - Test fixtures create private directories with the product's helpers
    (`identity_backup.private_mkdir` / `signed.init_identity`), **never** a bare `mkdir`.
  - Subprocesses get an explicit `cwd`.
  - No giant pytest parameter ids.
  - Mark POSIX-only mocks with `skipif(os.name == 'nt')`.
- Test command (from `memory-sync/`):
  `UV_CACHE_DIR=/private/tmp/claude-501/uvcache uv run --no-project --with pytest==8.4.2 --with pytest-timeout --with-requirements requirements-security.txt python -m pytest -q -p no:cacheprovider --timeout 120 <files>`
  - Run it **also from the repo root** (`memory-sync/test_x.py`) before each commit.
  - The full suite has 3 known failures in `test_sqlite_consumers.py`; everything else must pass.
- Test-first for each task, then commit. End each commit message with
  `Co-Authored-By: GPT (Codex) <noreply@openai.com>`.
- Do **not** create GATES.md or any other bookkeeping file. The only report goes in
  `docs/superpowers/plans/2026-10-13-key-rotation-recovery.REPORT.md`.

## Tasks

### Task 1: R1, own packets before the signature check

**Files:** `memory-sync/memory_sync.py` (`receive`), and a new test file
`memory-sync/test_key_rotation.py`.

- [ ] **Failing tests:**
  - a signed-policy DB whose own published packet sits in `signed-changes/<own node>/`, after its own
    key is revoked in the local trust → `receive` reports `invalid == 0` and the file is treated as
    seen
  - a local-node file that differs from the outbox row by one byte (re-signed, or a different body) →
    still `invalid` (`unknown packet claiming local node`)
- [ ] **Implement:** in `receive`, after `validate_envelope` and the filename check, and **before**
  `_received_signed`/`security.verify`, when `p['node'] == config(c)['node']`:
  - an exact outbox match (`json.loads(row) == p`) means `continue`, the same way `_received_signed`
    `cached` is handled
  - a mismatch falls through to the existing code
- [ ] Run the full `test_memory_sync.py`, `test_signed_packets.py` and `test_security_regressions.py`
  and expect green. Commit.

### Task 2: Proposal format and `rotations.json`

- [ ] **Tests:**
  - build, sign and validate round trip
  - tampering with each field (old_key_id, new.*, reason, created_at, a manifest entry, the signature)
    → `invalid rotation proposal`
  - the exact field set, the size limit (16 MiB; use a small test override hook rather than building
    16 MiB)
  - `check_public(new)`
  - the domain separation: a signature over the same bytes with the packet `DOMAIN` doesn't validate
  - `rotations.json` read and write, with strict schema validation
- [ ] **Implement** the constants, `proposal(...)`, `check_proposal(doc)`, `read_rotations(dir)` and
  `add_rotation(dir, old_key_id, new_key_id, manifest)`.
- [ ] Commit.

### Task 3: R2, manifest acceptance in receive

**Files:** `memory-sync/signed_packets.py` (`Security.verify_rotated`) and
`memory-sync/memory_sync.py` (`receive`).

- [ ] **Tests:**
  - a peer with the old key revoked and a manifest entry accepts a not-yet-received old-key packet
    whose `receipt_digest` matches, verifying historically against the old public key, and applies it
  - a digest mismatch → refused (`invalid`)
  - an old-key packet not in the manifest → refused
  - no `rotations.json` → today's behaviour
  - a packet pending on causality behind a manifest packet converges after it
- [ ] **Implement:** `verify_rotated(p, group, folder)` reads `rotations.json` from `self.directory`.
  It requires:
  - `p['key_id']` revoked in the trust
  - an entry for that key_id with `packets[p['uuid']] == receipt_digest(p)`
  
  Then it calls `_verify(..., historical=True)`.
  
  In `receive`, call it only when `security.verify` raises the revoked-key error. Restructure as an
  explicit check, not by catching every exception: check `entry['revoked']` up front.
- [ ] Commit.

### Task 4: `identity-rotate` preparation (read-only checks)

- [ ] **Tests** for every refusal in spec steps 1–6:
  - leftovers, both #8's and the journal
  - a legacy DB, a binding mismatch
  - the old-key derivation order, and a lost identity with or without trust
  - an unexpected key in the pending outbox
  - the worker running (fake `worker_lifecycle.status`)
  - the cycle lock or lifetime lock held (take them in the test)
  - recent exchange activity
  - `STOPPED` and `RESIGN` not typed
  - a pending packet already in the exchange
  - a pending packet failing historical verification
  
  Assert that nothing changes on refusal: a byte snapshot of DIR, the DB and the exchange.
- [ ] **Implement** `prepare(...)`. It returns a plan object holding:
  - the binding, `old_key_id`, the old public fields (or None)
  - the trust (or None), the pending rows and the manifest
  - the held locks, as a context
- [ ] Commit.

### Task 5: Journal, steps and `--resume`

- [ ] **Tests:**
  - **The full run:** the trust has the old key revoked and the new one active, with exactly one active
    key per sender. The identity is the new sealed one. Pending rows are re-signed: uuid, body and
    checksum unchanged; they verify with the new key and not the old. Published rows are byte for byte
    unchanged. The proposal is written, and it validates and matches the manifest. The journal is
    gone.
  - **Crash injection:** raise after each step (trust, identity, DB, verify, proposal), then call it
    again with `resume=True` → the same final state as an uninterrupted run, compared byte for byte
    apart from timestamps.
  - **DB step:** an exchange file appearing for a pending uuid between the preparation and the
    transaction → the transaction rolls back, the error is reported, and `--resume` after the file is
    removed completes.
  - **OS item:** removed through a FakeBackend; a removal failure is reported with
    `os_unlock_item_removed: false`; a None backend gives `null`.
  - **No raw key on disk:** record every `write_local` and `os.open` payload.
  - **Lost-everything path:** the new trust holds only the new self entry, and the report has
    `trust_restored: false` and the re-pin step.
- [ ] **Implement** following the spec exactly (journal format, step order, idempotency, forward-only).
- [ ] Commit.

### Task 6: `peer-rotate`

- [ ] **Tests:**
  - **success:** writes `rotations.json` first, then the trust; exactly one active key
  - a wrong fingerprint changes nothing
  - refusals: an unknown old key, already rotated, the new key revoked here, own key or own node, a
    group mismatch, a tampered or oversized proposal
  - the competing-proposals warning count
  - an old key already revoked (step 0) is accepted
- [ ] **Implement and commit.**

### Task 7: `compromise-report`

- [ ] **Tests:**
  - `committed`, `in_manifest`, `forged_candidates` (on the rotated device), `receipts_without_file`,
    `filtered_by_since`, `unreadable`
  - `key_sha256` only, with no row contents or raw keys anywhere in the JSON (assert a row's content
    string is absent)
  - the DB, `-wal`, `-shm` and exchange are byte for byte unchanged, and no new files appear
- [ ] **Implement** with read-only SQLite as `install_inspect` does it (read that code), and
  `validate_envelope` plus the filename check. Commit.

### Task 8: End to end with two peers, and the #8 interplay

- [ ] **One test runs the spec's E2E scenario**, using real `memory_sync` capture/publish/receive
  cycles on two disposable DBs with signed policy and one exchange. Use the existing two-peer fixtures
  in `test_signed_packets.py` / `test_security_wizard.py`; read them and reuse what fits. Assert:
  - convergence
  - `invalid == 0` on both devices
  - the forged packet is refused at B and shows up in both reports
  - historical receipts are still verified
- [ ] **#8 interplay:**
  - `identity-restore` refuses `rotation-journal.json` and `.rotate-*`; extend `identity_backup`'s
    leftover check
  - **regression:** restoring a revoked key into an empty trust is accepted locally, and a peer refuses
    that key's new packets
- [ ] Commit.

### Task 9: CLI, errors, packaging, smoke, CI

- [ ] In `agentmesh.py`:
  - `identity-rotate` (`--security-dir --db --exchange --reason [--old-key] [--runtime] [--resume]`)
  - `peer-rotate` (`--security-dir --proposal [--exchange]`)
  - `compromise-report` (`--db --exchange --key [--security-dir] [--since]`)
  - Print the JSON report and return 0.
- [ ] In `cli_errors.py`: the reasons into `KEY_REASONS`, the three actions as key actions, the module
  into `TRUSTED_MODULES`.
- [ ] Add the module to `HIDDEN_MODULES` and to `build_package.py`'s list.
- [ ] In `smoke_standalone.py`:
  - `--help` for all three commands
  - without a console, `identity-rotate` and `peer-rotate` refuse and change nothing
  - `compromise-report` on a disposable DB and exchange gives an empty report
- [ ] In `.github/workflows/standalone-smoke.yml`, add `memory-sync/test_key_rotation.py` to the
  "Verify OS unlock backends" pytest list.
- [ ] Commit.

### Task 10: Documentation

- [ ] Add "Lost or compromised keys" to `memory-sync/KEY-PROTECTION.md`, with every item from the
  spec's Documentation section, in that order. Commit.

### Task 11: Verification and report

- [ ] Run the full suite from `memory-sync/` and from the repo root. Only the 3 known failures are
  allowed.
- [ ] Write the REPORT:
  - the commits and test counts
  - deviations from this plan and why
  - open questions
  - any spec point you could not implement as written
  
  Commit. Do not push.
