# Control plane milestone 3c (passkey inventory) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents are available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap M3b left on purpose. `keys.inventory` shows which passkeys this profile trusts for approvals, when
each was added and from which device, and warns when there are none. The menu bar app shows them. It also takes the one
M3b follow-up: a key file whose grant is unnamed shows which unnamed agent it belongs to.

**What M3c is:** read-only (tier 0), like M3b. It adds no authority, and it changes no passkey and no key.

**Not in M3c:** adding or removing passkeys from the control plane, the approval state machine and Discord/passkey
approvals (renumbered M3d), device and grant ops (renumbered M3e), and passkey "last used" (not recorded anywhere today,
see decision 4).

**Spec:** `docs/superpowers/specs/2026-10-23-local-control-plane-design.md`. **Previous plan:**
`2026-10-31-control-plane-m3b-inventory.md`, merged and live on 2cf31f7.

---

## Design decisions made for M3c (read first)

1. **The broker counts passkeys; the supervisor still never opens the store or a key.**
   - Passkeys are signed records in `profile-log/replica/registry/passkeys/` and `passkey-removals/`. Which ones are
     active needs the verified device registry and ring, so only the broker (which has `state` unlocked) can decide.
   - `Passkeys.credentials()` is split: a module function `active(state, local, at)` does the work, and
     `credentials()` calls it with `self.store.load()`. The CLI, the bridge and their tests keep their behaviour.
2. **Cost is bounded by a fingerprint cache, which is why M3b left it out.**
   - The broker keeps the last result keyed by a fingerprint: the sorted `(name, size, mtime_ns)` of the two record
     folders and `registry/devices/` (where a revocation lands). It is a `scandir` of three small folders.
   - It recomputes when the fingerprint changes, and in any case at most once an hour (so a record that only just
     became valid by time, `timestamp > at`, appears within the hour).
   - The hook calls it under the same lock as `overview` and `keys`, once a minute at most, as today. A failure leaves
     the section out of that write (never fatal to sync, like the rest of the hook).
3. **A new top-level section `passkeys`** in `profile-log-status.json`:
   - `{generated_at, items: [{ref, label, created_at, issuer_device}]}`, sorted by `ref`.
   - `ref` is the first 16 hex characters of the credential hash, the same prefix `agentmesh-log connector passkey list` prints
     and `passkey remove` takes. No credential id, no public key, no rp_id.
   - `label` is the owner's own label (1–100 characters, no control characters, already enforced when the record is
     read). It is shown like a device name. **Owner decision (2026-11-01): include the label.** The file is served by
     Insight, and the owner accepted the label appearing there, as device names already do.
   - `issuer_device` is a device id, which `overview.devices` already publishes.
   - A separate section, not a field of `keys`, so the strict `keys` schema stays as M3b deployed it. Rollback cost is
     the accepted one: an older broker sees an unknown section and rewrites the file from its own sections.
4. **No "last used".** The bridge stores only the WebAuthn sign counter (`passkey_counters`), with no time. Recording a
   time would change the approval path, which is M3d's to touch.
5. **Findings** (same `kind:subject` ids as M3b):

   | kind | severity | rule |
   |---|---|---|
   | `no_passkey` | info | the `passkeys` section is present and has no items |

   - Info, not warning: passkeys are only needed for Insight approvals, and the profile works without them. Info
     findings never notify and never change the icon.
   - A missing `passkeys` section gives `passkeys: null` and no finding of its own; the broker being out of date is
     already `inventory_stale`.
6. **Key file rows gain `key_ref`** (the M3b follow-up). A `profile-log/agents/<id>.key` that matches a grant carries
   that grant's `key_ref` (already in the status file); otherwise `null`. The app labels an unnamed match as
   `unnamed (<first 8>)`, the same text the Agents list uses.

## Tasks

### Task 1: `passkeys.active` and the broker's `passkeys` section

- [x] Split `Passkeys.credentials()` into `active(state, local, at)`; the existing passkey tests pass unchanged. The
      label rule moved to `encoding.label_valid`, so the status validator (which the supervisor runs) checks labels
      with the record's own rule without importing `passkeys` and webauthn.
- [x] `status.passkeys(state, local, at)` builds the section; `status.valid` gains its schema (ref `[0-9a-f]{16}`,
      label via the same rule as the record, `created_at` ISO, issuer `[0-9a-f]{32}`).
- [x] The broker's fingerprint cache (it also holds the store fingerprint, so an `rp_id` change recounts) and the hook writes `passkeys` with `overview` and `keys`.
- [x] Tests (`tests/test_status_passkeys.py`, 26): the section is content-free (no credential id, public key or rp_id in the raw file; `test_g8` style), a
      removed passkey is gone, a record with a bad signature or a future timestamp is left out, the cache recomputes on
      a new record file and after an hour but not otherwise, and a failure leaves the other sections written.
- [x] Mutation gate on `status.passkeys`, the schema and the cache: 26 mutants, all killed. Two survived the first
      run and got a test each: a label with `\x1f` (the edge of the rule) and a record rewritten under the same name.
      Full suite: 1418 passed, 10 skipped (the node-only `test_p6_s12_close_cross_verifier` deselected).

### Task 2: `keys.inventory` gains passkeys and key file refs

- [x] `inventory()` returns `passkeys` (the section as the broker wrote it, `null` when missing) and `key_ref` on each
      key file row (`null` for a named key, an orphan, or no `keys` section).
- [x] `no_passkey` finding; the fixture `tests/control/fixtures/inventory.json` gains both fields (regenerated from
      `live_inventory`, with the fixed file times kept). The Swift tests still pass on it (53), since unknown keys are
      ignored until Task 3.
- [x] Tests for each case (4 new in `test_inventory.py`, plus the shape test), mutation gate: 9 mutants, all killed.
      Full suite: 1422 passed, 10 skipped.

### Task 3: app

- [x] `Passkey` model, `Inventory.passkeys`, `KeyFile.keyRef`; the fixture decodes; old payloads without the fields
      still decode (`null`). Both new fields default to nil, so an older supervisor's payload decodes unchanged.
- [x] `findingText` for `no_passkey`; the key file row shows `unnamed (<ref8>)` for an unnamed match. The label is one
      function, `agentLabel`, shared by the Agents list and the key file rows (`keyFileAgent`).
- [x] "Passkeys" block in "Keys & devices" between Agents and Key files: `label · added <age> · from <device name>`
      (`this Mac` for this device, the id's first 8 characters for an unknown one). "None set up" for an empty list.
- [x] Swift tests and mutation gate; debug and release builds. 57 tests pass (4 new, 2 extended). 11 mutants, all
      killed; the two that drop the new model fields are killed by the build (the memberwise init), the decode tests
      cover them as well.

### Task 4: docs and deploy

- [x] Spec amendment "Amendments from milestone 3c", and renumber approvals (M3d) and device and grant ops (M3e).
      The M3b plan keeps its original numbering; the amendment says so.
- [x] Owner-run `deploy-m3c.sh` (session scratchpad) on the same pattern as M3b: a drift guard on **2cf31f7** (the
      deployed checkout; 49fd007 on main only added docs), an import check, the app rebuilt, the supervisor restarted
      until `keys.inventory` returns `passkeys`, then the broker until it has a new pid, then up to 5 minutes for its
      first `passkeys` section, rollback printed. It refuses to run until REV is set to the merge commit.
- [ ] Owner check: the passkeys this Mac's `agentmesh-log connector passkey list` prints appear in the menu with the same refs.
- [ ] Record the results in this plan.
