# Identity Backup and Verified Restore (#8) Implementation Plan

> **For agentic workers:** execute task by task, in order. Steps use checkbox (`- [ ]`) syntax. The spec is
> `docs/superpowers/specs/2026-10-13-identity-backup-restore-design.md`; read it **fully** first. Where
> this plan and the spec disagree, the spec wins. Stop and report instead of guessing.

**Goal:** add `agentmesh identity-backup {create,verify,drill,status}` and `agentmesh identity-restore`:
an encrypted portable backup with verified restore and drill evidence. It must never resurrect revoked
keys or let one identity run on two devices.

**Architecture:**
- A new module `memory-sync/identity_backup.py` owns:
  - the format (seal, open, validate)
  - the location checks
  - the no-clobber writer
  - the evidence file
  - the operations (`create`, `verify`, `drill`, `status`, `restore`)
- `agentmesh.py` wires the CLI. `cli_errors.py` allowlists the fixed reasons.
- It reuses:
  - `key_protection`: `console_passphrase`, `seal`/`check`/`open_sealed`, `MIN_PASSPHRASE`
  - `signed_packets`: `Security`, `write_local`, `read_local`, `read_trust`, `check_public`, `typed`,
    `hex_bytes`, `no_symlinks`, `private_directory`, `init_identity`, `approve`
  - `security_wizard`: `signed_ack`, `verify_ack`
  - `console_prompt` for typed words
  - `os_unlock.current_backend` for the stray-item check

**Tech stack:** Python 3.11, the pinned `cryptography` (Scrypt, AESGCM), pytest 8.4.2.

## Ground rules

- Work only in `/Users/krisanathep/Workspaces/AgentMesh/.claude/worktrees/encrypted-key-storage-28470f`
  on branch `feat/identity-backup`. Do not push, open PRs, merge, rebase or touch other branches or
  worktrees.
- Use disposable identities only. Never read or modify real identities, keychains or Credential
  Manager. The stray-item check must be injectable, and unit tests use a fake backend (copy the
  `FakeBackend` pattern from `memory-sync/test_os_unlock.py`).
- **No raw private key or passphrase ever touches disk, argv, env, logs, errors or reports.**
  Decryption is in memory. Every file written is either the encrypted backup or a #6-sealed identity.
- Raise errors with fixed reasons. Where a lower exception could carry data, raise outside the
  `except` block (see how `os_unlock.MacKeychainFile._run` does it).
- Test command (from `memory-sync/`):
  `UV_CACHE_DIR=/private/tmp/claude-501/uvcache uv run --no-project --with pytest==8.4.2 --with pytest-timeout --with-requirements requirements-security.txt python -m pytest -q -p no:cacheprovider --timeout 120 <files>`
  - The full suite has 3 known failures in `test_sqlite_consumers.py` (tmp-symlink). Everything else
    must pass.
- Test-first for each task, then commit. End each commit message with
  `Co-Authored-By: GPT (Codex) <noreply@openai.com>`.
- Match the surrounding style: compact, comments only for a non-obvious "why".
- Do **not** create GATES.md or any other agent bookkeeping file in the repo. Put your report only at
  `docs/superpowers/plans/2026-10-13-identity-backup-restore.REPORT.md`.

## File map

| File | Change |
|---|---|
| `memory-sync/identity_backup.py` | **new** |
| `memory-sync/test_identity_backup.py` | **new**: unit tests |
| `memory-sync/agentmesh.py` | `identity-backup {create,verify,drill,status}` and `identity-restore` |
| `memory-sync/cli_errors.py` | reasons, key actions, `TRUSTED_MODULES` |
| `memory-sync/standalone_build.py`, `memory-sync/build_package.py` | package `identity_backup` |
| `memory-sync/smoke_standalone.py` | frozen help/status/refusal checks |
| `memory-sync/KEY-PROTECTION.md` | "Backup and restore" section |

---

### Task 1: Format core (seal, open, validate)

**Files:** create `memory-sync/identity_backup.py` and `memory-sync/test_identity_backup.py`.

- [ ] **Step 1: Write failing tests.**
  - Round trip: `seal(manifest, private_bytes, trust, passphrase, n=2**14)` →
    `open_backup(doc, passphrase)` returns `(private_bytes, trust, trust_captured_at)`.
  - A wrong passphrase, tampered `ciphertext`/`nonce`, and a tampered manifest field (each of
    `key_id`, `sender`, `group`, `node`, `public_key`, `created_at`) raise exactly
    `ValueError('backup could not be opened: wrong passphrase or damaged file')`.
  - Malformed parameters are rejected **before the KDF**. Monkeypatch `Scrypt` to raise if called,
    then check: N = 2^30, N = 1000, r = 1, p = 2, a short salt, an unknown cipher/kdf/format, extra or
    missing fields, and a ciphertext over 1 MiB. Each raises
    `ValueError('invalid backup file')`.
  - The manifest is validated through `signed.check_public` with `format` added. A manifest whose
    `key_id != sha256(public_key)` is invalid.
  - After decryption: a `private_key` that doesn't derive `public_key` → open error; a trust without
    the self entry, or with a self entry that differs from the manifest → `invalid backup file`.
- [ ] **Step 2: Implement** with these constants and functions:

```python
FORMAT = 'agentmesh-identity-backup-v1'
FIELDS = {'format', 'manifest', 'kdf', 'n', 'r', 'p', 'salt', 'cipher', 'nonce', 'ciphertext'}
MANIFEST = {'key_id', 'public_key', 'sender', 'group', 'node', 'created_at'}
MAX_CIPHERTEXT = 1 << 20
OPEN_FAILED = 'backup could not be opened: wrong passphrase or damaged file'
INVALID = 'invalid backup file'

def associated_data(manifest): return FORMAT.encode() + b'\0' + signed.typed(manifest)
def check(doc): ...            # all structural checks; never derives a key
def seal(manifest, private_bytes, trust, passphrase, *, n=kp.SCRYPT_N): ...
def open_backup(doc, passphrase): ...   # -> (private_bytes, trust, trust_captured_at)
```

  - Use `key_protection._primitives()` for AESGCM, Scrypt and InvalidTag.
  - The plaintext is `signed.typed`-safe JSON bytes of
    `{"private_key", "trust", "trust_captured_at"}`. Use `signed.wire`/`signed.parse` if they fit;
    check what they do first.
  - Validate the decrypted trust with the same rules as `signed.read_trust`. Factor out a pure
    `signed.check_trust(obj)` from `read_trust`, keeping `read_trust`'s behaviour identical, and
    reuse it.
- [ ] **Step 3:** Run the tests until green. Commit: `feat: identity backup format with authenticated manifest (#8)`.

### Task 2: Location checks and the no-clobber writer

- [ ] **Tests:**
  - `unsafe_location(path)`:
    - refuses (raises `ValueError('backup location is inside a Syncthing folder')` or `'... a git
      work tree'`) for an ancestor `.stfolder`, an ancestor `.git` directory, and an ancestor `.git`
      **file**
    - also through a symlinked parent (it uses `realpath`)
    - case-insensitively on darwin and win32 (monkeypatch `sys.platform` and construct names like
      `.STFOLDER`)
    - returns `'cloud'` for a path under a known cloud root and `None` otherwise
    - Make the cloud roots a module tuple built from `Path.home()`:
      - `Library/Mobile Documents`
      - `Library/CloudStorage`
      - `Dropbox`, `OneDrive`, `Google Drive`
      - Windows `%OneDrive%` env when set
      
      Tests monkeypatch the tuple.
  - `write_new(path, data)`:
    - creates the file and refuses an existing one (`backup file already exists`)
    - with `os.link` monkeypatched to raise `OSError(errno.EPERM)`, falls back to an `O_EXCL` create,
      still refuses to clobber, and leaves no temp files
    - returns `'owner-only'`, or `'unsupported-by-filesystem'` when chmod or the ACL can't be applied
      (simulate by monkeypatching)
- [ ] **Implement and commit:** `feat: backup location checks and no-clobber writer (#8)`.

### Task 3: Evidence file and `status`

- [ ] **Tests:**
  - `record(security_dir, key_id, file_sha256, field, when)` writes `backup-status.json` through
    `write_local`.
  - `status(security_dir)` returns `state` `none`, then `verified`, then `drilled`, with ages.
  - A record for another key_id is ignored.
  - A malformed file → `invalid backup status`.
  - `Security(security_dir)` and `read_trust` still work with the extra file present.
  - `status` never reads backup files.
- [ ] **Implement and commit:** `feat: backup evidence and status (#8)`.

### Task 4: `create` and `verify`

- [ ] **Tests** (inject `ask=` and `confirm=` callables; default to `kp.console_passphrase` and a
  `console_prompt`-based typed-word confirmation):
  - **create:**
    - writes a backup whose manifest equals the live public fields
    - `verified_at` is recorded
    - the report has `status`, `key_id`, `file_sha256`, `verified_at`, `permissions`, and no path,
      key or passphrase
    - works for a raw identity, and for a protected identity with the identity passphrase
    - OS unlock is never consulted (pass a FakeBackend and assert it saw no calls)
    - refuses when `BACKUP` isn't typed, passphrases mismatch, the passphrase is short, or the backup
      passphrase equals the identity passphrase
    - refuses an existing output, a `.stfolder` or `.git` location, a cloud location without `CLOUD`,
      and no console (the default `ask` without a tty)
    - if the readback fails (monkeypatch `open_backup` to raise once), the new file is deleted and an
      error is raised
    - uses N = `kp.SCRYPT_N`, which can be overridden only by a test-only keyword
  - **verify:**
    - success records `verified_at` only when `--security-dir`'s key_id matches
    - a mismatch records nothing and reports `key_id_mismatch`
    - a low N gives a `warning`
- [ ] **Implement and commit:** `feat: identity-backup create and verify with readback evidence (#8)`.

### Task 5: `drill`

- [ ] **Tests:**
  - The drill passes and records `drilled_at`.
  - Temp directories are gone afterwards, including after an injected failure in the middle
    (monkeypatch `security_wizard.verify_ack` to raise).
  - **No raw key on disk:** monkeypatch `signed.write_local` and `os.open` to record every written
    payload, and assert no recorded bytes contain the private key hex.
  - Works when `tempfile.gettempdir()` sits under a symlinked parent (simulate with a symlink in
    `tmp_path`, and use `realpath`).
  - The drill never touches the real security directory except `backup-status.json`.
- **Implementation notes:**
  - Seal the drill identity in the #6 form with `key_protection.seal`, a random in-memory
    passphrase and `n=2**14`.
  - Write it in the identity format `init_identity` produces, plus a trust with the self entry and
    the disposable peer, both pinned through `signed.approve`.
  - Create the disposable peer with `signed.init_identity` on another node slot in the same group.
  - Sign a probe with `Security.sign`, verify it with the peer's `Security.verify`, build the ACK with
    `security_wizard.signed_ack`, and check it with `security_wizard.verify_ack`.
  - Read those functions' signatures and build the minimal probe and receipt they expect, mirroring
    `test_security_wizard.py`.
- [ ] **Commit:** `feat: disposable restore drill with signed roundtrip (#8)`.

### Task 6: `restore`

- [ ] **Tests:** cover every spec bullet under Testing → restore. At minimum:
  - **Success:**
    - same key_id, sender, group, node and public_key
    - the new identity passphrase unlocks it; the backup passphrase does not
    - the report lists pinned, skipped, kept_revoked, imported_revoked and `stray_os_unlock_item`
      (from an injected FakeBackend, true and false)
  - **Refusals:**
    - a target with `identity.json`
    - a target in `.stfolder`, `.git` or inside the exchange
    - a leftover `.restore-*`
    - a leftover `trust.json.pre-restore`
    - own key revoked in the current trust
    - the current trust holds this key with different fields
    - the current trust has an active entry for this slot under another key
    - the current trust holds another group
    - an exchange without `.stfolder`
    - an exchange without this group's `status/*.json`
    - recent activity (status, signed-changes, changes) or a future mtime (set with `os.utime`)
    - activity appearing between the first check and adopt (monkeypatch a hook to touch a file)
    - `FENCED` not typed
    - a new passphrase equal to the backup passphrase
  - **Peers:**
    - a revoked backup peer is imported as revoked without a prompt, and `signed.approve()` on it then
      raises
    - an active backup peer is pinned by typing the full fingerprint, Enter skips it, and a wrong
      fingerprint skips it
    - a slot conflict skips the peer
    - an existing current entry is kept, and revocation from either side wins
  - **Atomicity:**
    - an exception after trust is replaced and before identity is written restores `trust.json` from
      `trust.json.pre-restore`
    - staging is always removed
    - on success, `pre-restore` is removed
  - **No raw key on disk:** use the same write-recording technique as Task 5.
  - **Fresh process:** a subprocess loads `Security(target, unlock=<new passphrase>)` and signs.
- [ ] **Implement and commit:** `feat: verified identity restore with trust reconciliation and fencing (#8)`.

### Task 7: CLI, errors, packaging, smoke

- [ ] In `agentmesh.py`:
  - `identity-backup` with `mode` ∈ {create, verify, drill, status} and the options `--security-dir`,
    `--file`, `--output` (validate per mode)
  - `identity-restore --file --security-dir --exchange`
  - Print the JSON report and return 0. Exit codes follow the existing commands.
- [ ] In `cli_errors.py`:
  - add every new fixed reason to `KEY_REASONS` (import a `REASONS` frozenset from
    `identity_backup`)
  - add `'identity-backup'` and `'identity-restore'` to the key actions
  - add `'identity_backup'` to `TRUSTED_MODULES`
- [ ] Add `identity_backup` to `standalone_build.HIDDEN_MODULES` and `identity_backup.py` to the list
  in `build_package.py`. `test_package.py` enforces the latter.
- [ ] In `smoke_standalone.py`, after the #6/#7 blocks:
  - `identity-backup --help` and `identity-restore --help`
  - `identity-backup status --security-dir <locked_identity>` → `state == 'none'`
  - `identity-backup create` with no console → exit 1, and no file is created
  - `identity-restore` with no console into a new temp target → exit 1, and the target is unchanged
- [ ] CLI tests in `test_identity_backup.py` cover:
  - status output
  - a fixed-reason error on stderr with no secret
  - both new actions are classified as key actions
- [ ] Commit: `feat: identity-backup and identity-restore CLI, packaging and smoke (#8)`.

### Task 8: Documentation

- [ ] Add a "Backup and restore" section to `memory-sync/KEY-PROTECTION.md` covering everything the
  spec's Documentation section lists, plus:
  - the metadata exposure of the manifest
  - evidence can be forged by same-user code
  - Python can't wipe memory
  - FENCED is the real control and the exchange check is a heuristic
  - restore can't see revocations held by peers until the live roundtrip
- Commit: `docs: identity backup and restore (#8)`.

### Task 9: Verification and report

- [ ] Run the full suite: only the 3 known failures are allowed.
- [ ] `git grep -n "private_key" memory-sync/identity_backup.py`: review every hit and confirm none
  writes raw key material to disk.
- [ ] Write `docs/superpowers/plans/2026-10-13-identity-backup-restore.REPORT.md` with:
  - the commits and test counts
  - deviations from this plan and why
  - open questions
  
  Commit: `test: verify identity backup and record report (#8)`. Do not push.
