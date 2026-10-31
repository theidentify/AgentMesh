# Control plane milestone 3b (key and device inventory) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents are available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer the question the spec says nothing answers today: which keys and devices exist, how old they are, which
devices hold which grants, and when the last good backup was. The supervisor gets one read-only op, `keys.inventory`,
and the menu bar app shows it and notifies on new warnings.

**What M3b is:** the key inventory from spec milestone 3, read-only (tier 0). It adds no authority and changes no key.

**Not in M3b:** the `plan_digest` approval state machine and Discord/passkey approvals (M3c), device and grant ops
(M3d), expiry *enforcement* of any kind, and the legacy memory-sync key stack (M6).

**Spec:** `docs/superpowers/specs/2026-10-23-local-control-plane-design.md`. **Previous plan:**
`2026-10-29-control-plane-m3a-menubar.md`, merged and live.

---

## Design decisions made for M3b (read first)

1. **The supervisor never opens a key file or the sealed keystore.**
   - It holds no unlocked key material (spec, Owner decisions), and it does not need to read a key file to inventory it.
   - Key files are `lstat`ed only: kind (regular file, symlink, other), mode, owner uid, size > 0, mtime.
   - Everything that needs the verified registry or grants comes from the broker, which already has them unlocked.
2. **The broker publishes a content-free `keys` section in `profile-log-status.json`.**
   - It is written by the same hook and at the same rate (at most once a minute) as `overview`. Both come from
     `state`, so they never disagree.
   - Fields (no ids, no key bytes, no public keys, no memory content):
     - `agents`: `{name, key_ref, secret_until}` for **every** grant. `name` is `null` when settings never named the
       agent, so no `<id>.key` file looks orphaned just for being unnamed. `secret_until` is ISO or `null` (from
       `Grant.secret_until`, ms; 0 → `null`).
     - `recoveries`: `{count, latest_issued_at}` from `registry.recoveries`.
   - **`key_ref`, not `agent_id`** (found while building Task 1). `test_g8` already forbids any `agent_id` in the status
     file, which Insight serves and the tunnel can expose. `key_ref` = the first 16 bytes of
     SHA-256(`AgentMesh/key-inventory-ref/v1\0` + agent_id), as hex. The supervisor derives the same ref from a key
     file's name to match files to grants, and the id itself is never written.
   - **Passkeys are left out of M3b.** `Passkeys.credentials()` reloads the store and verifies the replica's records,
     which is too heavy for a once-a-minute hook under the broker lock. They come back with M3c, which needs passkeys
     anyway.
   - It is a new top-level section, so `status.valid` gains one schema entry. **Rollback note:** an older broker's
     `status.write` treats the file as invalid and rewrites it from its own sections only. That is the same,
     accepted cost every earlier new section had (`last_catch_up`), and the next write of each section restores it.
   - The status file stays "never an authority source": the inventory only *reports* from it.
3. **`backup create` records its last success** as a `last_backup` section: `{at, files, bytes}`. It records no
   destination path. A failed or declined backup writes nothing. Restore writes nothing.
4. **`keys.inventory` (tier 0) joins three sources, and each can be missing on its own.**
   - The status file (`overview.devices`, `overview.agents`, `keys`, `last_backup`), read through `settings.safe`.
   - `lstat` of `profile-log/agents/*.key`, `agents/*.key` (the named keys jobs use, such as `importer.key`), and
     `profile-log/keystore.sealed`.
   - The clock.
   - A missing or invalid source gives that part `null` plus a finding. It never fails the whole op.
5. **Findings are computed by the supervisor with fixed rules and stable ids.** Each is
   `{id, severity, kind, subject}`. `id` is `kind:subject`, so the app can notify once per finding.

   | kind | severity | rule |
   |---|---|---|
   | `key_file_unsafe` | warning | not a regular file, a symlink, `mode & 0o077`, wrong owner, or empty |
   | `keystore_unsafe` | warning | the same rule for `keystore.sealed` |
   | `key_file_orphan` | info | a `profile-log/agents/<id>.key` whose `key_ref(<id>)` matches no grant |
   | `device_stale` | warning | not this device, not revoked, and `last_seen_at` older than 14 days or `null` |
   | `secret_grant_active` | info | `secret_until` in the future |
   | `no_recovery` | warning | `recoveries.count == 0` |
   | `backup_never` / `backup_old` | warning | no `last_backup`, or older than 30 days |
   | `inventory_stale` | warning | the `keys` section is missing or older than 1 day (the broker is not writing it) |

   The 14- and 30-day thresholds are constants in `inventory.py`. Changing them later is `settings.set` work (M3d+).
6. **The app polls `keys.inventory` every 60 s, not with `status`.** It changes slowly, and `status` stays small.
   - A "Keys & devices" submenu lists devices (name, platform, last seen, revoked), agents (scopes, secret until),
     key files (path, mode, age), recovery, last backup, and the findings first.
   - The menu bar icon's `warning` state also covers warning findings.
   - A notification fires when a warning finding first appears, and never again for the same `id` while it stays.
     `info` findings never notify.

---

## Tasks

### Task 1: broker `keys` section

**Files:** `profile-log/agentmesh_log/status.py`, `profile-log/agentmesh_log/broker/service.py` (overview hook),
`profile-log/agentmesh_log/cli.py` (`on_overview`), tests in `profile-log/tests/test_status*.py`.

- [x] `status.keys(state)` builds the section from decision 2. `status.applied` writes it next to `overview`.
- [x] The broker hook passes `{overview, keys}` from one locked read, and `on_overview` writes both in one `status.write`.
- [x] `valid` accepts the section with an exact schema. Unknown fields, non-ISO times and negative counts are refused.
- [x] Tests (`tests/settings/test_status_keys.py`): the shape; `secret_until=0` → `null`; unnamed grants kept; latest
      recovery; the ref is stable and domain-separated; 9 validator refusals; no agent_id, public key or private key in
      the section. `test_g8`'s content check still passes with the section present.
- [x] Mutation gate: 12 mutants of `keys`, `key_ref`, `applied` and the validator entry, all killed (the one survivor,
      a non-text `name`, got its own test).

**Result (2026-10-31):** full suite 1344 passed, 10 skipped; the known node-only `test_p6_s12_close_cross_verifier`
deselected.

### Task 2: `last_backup`

**Files:** `profile-log/agentmesh_log/backup.py`, `status.py`, `profile-log/tests/test_backup*.py`.

- [x] After a successful `create`, `status.write(..., last_backup=dict(at, files, bytes))`. Best-effort, like every
      status write: it never fails a backup.
- [x] Only a backup of `$AGENTMESH_HOME/profile-log` is recorded. A backup of any other `--home` never claims to be
      this home's last backup (and the existing backup tests, which use temporary homes, never touch the real file).
- [x] Tests (`tests/test_backup_status.py`): success writes it; decline, failure, restore and another home do not; no
      output path, source path or content is recorded; 4 validator refusals.
- [x] Mutation gate: 8 mutants of `_record`, its call and the validator entry, all killed.

**Result (2026-10-31):** full suite 1354 passed, 10 skipped (the node-only test deselected).

### Task 3: `control/inventory.py` and the `keys.inventory` op

**Files:** new `profile-log/agentmesh_log/control/inventory.py`, `control/ops.py`, `control/layout.py`, new
`profile-log/tests/control/test_inventory.py`, `profile-log/tests/control/fixtures/inventory.json`,
`test_wire_contract.py`.

- [x] `layout`: `agent_keys` (`profile-log/agents`), `named_keys` (`agents`), `keystore`.
- [x] `inventory(layout, now)`: `lstat` only. `test_never_reads_a_key` makes `open`, `os.open`, `read_bytes` and
      `read_text` fail on any key path.
- [x] Findings exactly as in the table, each with a test on both sides of its threshold. The thresholds are pinned by
      `test_thresholds_match_the_plan`.
- [x] Each missing, invalid, tampered, unreadable or symlinked status file → `null` parts plus `inventory_stale` and
      `backup_never`. The op still answers. A key folder that is a symlink is one `key_file_unsafe` and is not walked.
- [x] Orphans are judged only for `profile-log/agents/<32 hex>.key` and only when the `keys` section is present.
- [x] `OPS['keys.inventory']=(0,frozenset())`. Unknown args are refused (`bad_args`).
- [x] `fixtures/inventory.json` pins the wire shape (a null `backup`, one warning and one info finding).
- [x] Mutation gate: 28 mutants of `inventory.py`. 7 survived the first run (a symlink with mode 0600, non-`.key`
      files, the three thresholds, the `status.valid` check, `except Exception`). Each got a test, and all 28 are now killed.

**Result (2026-10-31):** full suite 1392 passed, 10 skipped (the node-only test deselected). A read-only run on this
Mac: 3 devices, 5 key files and the keystore all 0600, findings `backup_never` and `inventory_stale` (the live broker
does not write `keys` until the deploy).

### Task 4: app model, presentation and watch

**Files:** `control-app/Sources/ControlCore/{Models,Presentation,Watch}.swift`, `ControlClient.swift`, tests in
`control-app/Tests/ControlCoreTests/`.

- [x] `Inventory` (with `Device`, `AgentGrant`, `KeyFile`, `Recovery`, `Backup`, `Finding`) decodes
      `fixtures/inventory.json`, the same file as Task 3, and an all-null body. `severity` and `kind` stay strings, so a
      newer supervisor's findings still decode. `ControlClient.inventory()` calls `keys.inventory`.
- [x] Presentation: `deviceLine` ("MacBook Pro · macos · seen 3d ago"), `modeText` (octal), `sortedFindings` (warning
      first), `findingText` (one line per kind, and `kind: subject` for an unknown kind).
- [x] `iconState(_:inventory:)`: a warning finding → `warning`, below `error` and `needsYou`. Info findings and a
      missing inventory leave the icon alone.
- [x] `Watch.update(inventory:)`: one notice per new warning `id`, none for `info`, none on a failed poll, and again
      only after that `id` has cleared.
- [x] Tests: 9 in `InventoryTests.swift` (53 in all pass). A mutation check of 7 mutants of the watch, the icon rule,
      the sorting and `modeText`: all killed.

### Task 5: app UI

**Files:** `control-app/Sources/AgentMeshControl/{StatusModel,MenuView}.swift`, new `KeysView.swift`.

- [x] `StatusModel` polls `keys.inventory` at launch, at most once a minute after that, and at once when the menu opens
      or Refresh is pressed. An older supervisor (`unknown_op`) hides the section for the session. A failed poll shows
      its error in the section and leaves notifications alone.
- [x] The menu bar icon uses `iconState(status, inventory:)`.
- [x] `KeysView`: a collapsible "Keys & devices" section, with an orange count of warnings and "checked Ns ago" in its
      title. Inside: findings first, then devices, agents (name or `unnamed (<ref prefix>)`, what they read, open
      secret windows), key files with mode, matching agent and age, the keystore, then recovery and the last backup.
- [x] 53 Swift tests pass; debug and release builds succeed. The view has no unit tests of its own: everything it
      shows comes from the ControlCore functions Task 4 tested.
- [ ] Owner check (at the Task 6 deploy; the live supervisor answers `unknown_op` until then): the real data from this Mac shows 3 devices, the named and id key files, recovery, and
      `backup_never` until the first backup.

### Task 6: docs and deploy

- [x] Spec amendment "Amendments from milestone 3b": decisions 1–6 as built (key_ref, no passkeys yet), and the
      open item on device last-seen resolved (the broker's `overview.devices`, from heartbeats).
- [x] Owner-run deploy script (`deploy-m3b.sh`, in the session scratchpad): a drift guard (a clean checkout on 79d7934,
      the merge commit on origin/main, the broker running), an import check that restores 79d7934 on failure, the app
      and helper rebuilt, the supervisor restarted until it answers `keys.inventory`, the broker restarted until it has
      a new pid, then up to 5 minutes for its first `keys` section. Rollback printed at the end. It refuses to run until
      REV is set to the merge commit.
- [ ] Record the results in this plan.
