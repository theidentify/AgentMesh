# Encrypted Profile Log, Phase 4 (Enrollment, Keyring, Recovery) Implementation Plan

> **For agentic workers:** execute in order. The spec is
> `docs/superpowers/specs/2026-10-18-encrypted-profile-log-design.md` ("Identity and user presence", "Device
> enrollment", "Keyring", "Recovery"); read it fully first, then `profile-log/README.md`, `profile-log/FORMAT.md`
> (including the broker appendix) and the Phase 3 plan and REPORT. The spec wins on conflict, except where this
> plan records an owner decision. Stop and report instead of guessing on anything that affects a security gate;
> record other choices under Deviations in the REPORT.

**Goal:** more than one device can share a profile without the account root key living on daily devices. A new
device joins through a 6-digit code (or a QR fingerprint), and an existing device approves it and grants scopes.
Scope keys are distributed as signed age files. Devices can be removed, which rotates the affected scope keys.
Profiles can be archived or crypto-shredded. A Shamir 2-of-3 SLIP-39 recovery key can restore everything. Every
sensitive action requires user presence. The broker key store moves out of the plain 0600 file into the OS key
store.

## Owner decisions (2026-10-20)

- **Keyring format: real age via `pyrage==1.4.0`** (abi3 wheels for macOS, Linux and Windows). Each device gets
  an X25519 age identity in addition to its Ed25519 signing key. age gives confidentiality only, so **every
  keyring object is wrapped in an Ed25519-signed envelope** from an authorized device and checked before use.
  Everything sits behind a `Keyring` interface, so MLS could replace it later.
- **Recovery: SLIP-39 via `shamir-mnemonic==0.3.0`**, 2-of-3 groupless shares (word lists for paper or files).
  Hardware security keys and passkey PRF are deferred.
- **Pairing: 6-digit code plus a QR fingerprint, carried through the existing `Storage`** (R2 or the Syncthing
  folder). The QR is rendered in the terminal with `segno==1.6.6`. LAN/mDNS and Bluetooth are deferred.
- **Presence: a `Presence` interface with a console implementation** (typed confirmation), a 15-minute window
  per action class, and a log of every trigger. Touch ID and Windows Hello are later, separate work.
- Unchanged from Phase 3: nothing under `memory-sync/`, installers, services, workflows or `platform_service.py`
  changes; no auto-start and no rewiring of callers. Code is ported from `memory-sync/os_unlock.py`, never
  imported.

## Threat model additions

Storage, the relay and anyone with write access to storage are **untrusted**. They can drop, reorder or replay
objects and write arbitrary new ones, but they cannot make a device accept:

- a forged device;
- a forged scope key;
- a forged revocation;
- a forged recovery alert.

Pairing must resist a storage attacker who substitutes the joining request (machine-in-the-middle). The 6-digit
comparison uses commit-then-reveal, so the attacker gets exactly one 10⁻⁶ guess per pairing. Same-OS-user
malicious code is still out of scope, as in Phase 3. The OS key store raises the bar against copied files
(backups, synced folders), not against same-user processes.

## Design to implement

### New and changed modules (all under `profile-log/agentmesh_log/`)

```
agekeys.py        DeviceAgeIdentity (X25519 via pyrage), bech32 AGE-SECRET-KEY encode/decode for derived keys
registry_store.py signed device records, revocations, the account recovery record; load/verify from Storage
keyring.py        Keyring protocol + AgeKeyring: signed age grants of scope epochs; current-epoch selection
pairing.py        join request / approval protocol, commit-reveal SAS, QR payload, segno rendering
recovery.py       recovery secret, SLIP-39 split/combine, recovery identities, test, use (alerts + rotation)
presence.py       Presence protocol, ConsolePresence, windows, trigger log
lifecycle.py      remove device (revoke + rotate), archive profile, delete profile (crypto-shred)
oskeystore.py     OS-protected KeyStore: random K in macOS login keychain / Windows Credential Manager (ported)
identity.py       Registry gains revocation checks (replace the Phase 1 TODO(phase4))
broker/…          presence hooks at every TODO(phase4-presence); KeyStore selection; state gains keyring cache
cli.py            join / pair approve / device list|remove / recovery create|test|use / profile archive|delete
                  / presence open|status / keystore migrate
```

Add `pyrage==1.4.0`, `shamir-mnemonic==0.3.0` and `segno==1.6.6` to `profile-log/requirements.txt`. They are
already installed in the prepared interpreter. Import them only in the modules that need them.

### Records (normative; add to FORMAT.md as "Phase 4 records")

All records use strict canonical JSON with exact field sets. Each is signed as
`domain + typed(record without signature)`, with a distinct domain per record type
(`AgentMesh/<record>/Ed25519/v1\x00`). Object paths contain only opaque IDs.

- **Device record** `agentmesh-device-record-v1` at `registry/devices/{device_id}.json`. Fields: the device
  certificate, `age_recipient` (`age1…`), `issued_at`, and a signature by the **device's own key**. It binds the
  age recipient to the device. It is valid only if the embedded certificate verifies against the registry chain.
- **Revocation** `agentmesh-device-revocation-v1` at `registry/revocations/{device_id}.json`. Fields:
  `device_id`, `revoked_by` (a device ID, or `"recovery"`), `revoked_at`, `reason_code`, signature. It must be
  signed by a non-revoked device other than the subject, or by the recovery root. `Registry.verify_agent` and
  device-chain verification reject revoked devices **and every certificate they issued after `revoked_at`**.
  Agents of a revoked device are rejected outright.
- **Account recovery record** `agentmesh-recovery-record-v1` at `registry/recovery/{recovery_key_id}.json`.
  Fields: `account_id`, `recovery_signing_public` (Ed25519), `recovery_age_recipient`, `previous` (key ID or
  null), `issued_at`, and a signature by the **account root key**, or by the previous recovery signing key when
  it replaces one.
- **Keyring grant** `agentmesh-keyring-grant-v1` at
  `keyring/{profile_id}/{scope_id}/{key_id}/{recipient_device_id}.json`. Fields: `profile`, `scope`, `key_id`,
  `recipient_device` (or `"recovery"`), `issuer_device`, `issued_at`, `age` (base64url of an age file that
  encrypts the 32 raw scope private-key bytes to that one recipient), and a signature by the issuer device. On
  open, the recipient:
  1. verifies the signature and the issuer chain (not revoked at `issued_at`);
  2. decrypts with its own age identity;
  3. recomputes `key_id` from the private key and requires it to equal the record's `key_id`;
  4. requires the issuer to hold that scope or an ancestor in its own keyring (it can produce the key; checked
     through the issuer's own valid grant records).

  One grant per recipient keeps objects immutable and simple to revoke. Grants to `"recovery"` use the recovery
  age recipient and cover only **profile root epochs**, which unwrap every descendant through the existing wrap
  records.
- **Epoch record** `agentmesh-epoch-v1` at `keyring/{profile_id}/{scope_id}/epochs/{key_id}.json`. Fields:
  `profile`, `scope`, `key_id`, `previous_key_id`, `created_at`, `reason` (`create`, `rotate`, `revoke`,
  `recovery`), `issuer_device` and signature. Current epoch = the epoch record with no valid successor. Two
  valid successors (a concurrent rotation) resolve to the higher `(created_at, key_id)`. The losing epoch stays
  readable, and writers re-seal to the winner. This answers the Phase 1 open question "which epoch is
  current". Writers must use the current epoch, and the broker refuses to seal to a superseded one.
- **Pairing objects** under `pairing/{request_id}/`: `commit.json`, `approver.json`, `reveal.json` and
  `result.json` (see Pairing).
- **Recovery alert** `agentmesh-recovery-alert-v1` at `alerts/recovery/{ulid}.json`, signed by the recovery
  signing key. Every broker checks for alerts on `sync_once` and surfaces them through `whoami.alerts` and the
  CLI.
- **Profile state** `agentmesh-profile-state-v1` at `profiles/{profile_id}/state/{ulid}.json`. Fields: `state`
  (`active`, `archived` or `deleted`), `issuer_device`, signature. The latest valid record wins. `archived`
  refuses writes and allows reads. `deleted` makes every device destroy that profile's local keys and refuse it
  from then on.

### Account root and first device

`broker init` (first device) creates the account, the first device certificate (signed by the root), the
device record and age identity, the profiles, and the recovery secret (see Recovery). It writes the recovery
record signed by the root, and keyring grants for every scope epoch to this device plus recovery grants for
profile roots. It then prints the three SLIP-39 shares once and **destroys the account root private key**:
`BrokerState` no longer carries it. From then on, devices certify devices, and the recovery key can re-derive
authority. `keystore migrate` drops the root key from existing Phase 3 key stores after creating the recovery
record, and requires presence.

### Pairing (`pairing.py`)

Commit-then-reveal numeric comparison, in the style of the Bluetooth numeric comparison and ZRTP
short-authentication-string protocols:

1. **Joiner** (`agentmesh-log join --home H --storage …`) generates its Ed25519 device key, age identity,
   `request_id` (32 hex) and `Nj` (32 random bytes). It writes `commit.json` =
   {`request_id`, `joiner_signing_public`, `joiner_age_recipient`, `commitment` = SHA-256(domain ‖ typed({request_id,
   joiner keys, Nj}))}, self-signed. It shows the request ID and a QR of `agentmesh-pair:v1:{request_id}:{SHA-256
   fingerprint of commit.json}`, then waits.
2. **Approver** (`pair approve REQUEST_ID`, presence required) reads the commit and writes `approver.json` =
   {`request_id`, `approver_device_id`, `Na` (32 random bytes), the hash of commit.json}, signed by the approver
   device.
3. **Joiner** verifies the approver against the registry it trusts. On first join, the user confirms the
   approver's device-ID fingerprint shown on the approver screen. The joiner then writes `reveal.json` with
   `Nj`, self-signed.
4. Both sides check the commitment and compute **SAS = int(SHA-256(domain ‖ typed({commit hash, approver hash,
   Nj, Na}))[:4]) mod 10⁶**, shown as 6 digits. The approver asks the user whether the codes match. If the user
   pasted a QR fingerprint (`--fingerprint`), the full fingerprint must equal the commit hash instead.
5. **Approver** (presence) selects profiles and scopes, certifies the joiner device (`issuer` = approver), writes
   the device record on the joiner's behalf only after the joiner's self-signature verifies, and writes keyring
   grants for the selected scope epochs plus `result.json` (pointers only, signed). The joiner downloads the
   grants, verifies everything and builds its key store and broker state from them (scope tree metadata travels
   in a signed, age-encrypted `agentmesh-scope-meta-v1` grant for each profile). Then the joiner's `sync_once`
   catches up.

Pairing requests expire 10 minutes after `commit.json`. A request ID is used once. A second `approver.json`, or
any mismatch, aborts with a fixed error code. Reveal before approver, wrong `Nj`, swapped joiner keys and a
replayed approval all fail.

### Device removal, archive and delete (`lifecycle.py`)

- `device remove DEVICE_ID` (presence) writes the revocation, then rotates **every scope epoch the removed
  device held a grant for** (`ScopeTree.rotate` plus new epoch records, new wraps and grants to the remaining
  devices and recovery). The removed device can still open data it already had (documented), but cannot open any
  event sealed afterwards.
- `profile archive NAME` (presence) writes the `archived` state; writers refuse it.
- `profile delete NAME` (presence, typed name confirmation) writes the `deleted` state, deletes every local
  epoch key for that profile from the key store and drops its indexes and snapshots. Storage objects stay but are
  unreadable.

### Recovery (`recovery.py`)

- **Recovery secret**: 32 random bytes. HKDF-SHA256 derives the `recovery_signing` Ed25519 seed and the
  `recovery_age` X25519 scalar, with distinct info strings. The age identity is bech32-encoded as
  `AGE-SECRET-KEY-1…` (BIP-173, upper case) and round-trip tested with pyrage.
- **Split**: `shamir_mnemonic.generate_mnemonics(group_threshold=1, groups=[(2, 3)],
  master_secret=secret, passphrase=b'', extendable=True, iteration_exponent=1)`. Use the library's documented
  signature; if it differs, record it. The shares are printed once to the console, never written to disk unless
  `--out DIR` is given (each share to its own 0600 file, refused inside the broker home or replica).
- `recovery test` (presence) accepts 2 shares and reconstructs the secret in memory. It checks that the derived
  public keys equal the current recovery record and reports ok or mismatch. It never touches state, storage or
  keys.
- `recovery use` (presence) runs on a fresh or existing device with 2 shares:
  1. Reconstruct the secret, verify it against the recovery record, and decrypt the recovery grants to obtain
     profile root keys (descendants come through wraps).
  2. Write a recovery alert.
  3. Certify the current device with a revocation-capable recovery signature (a device record whose
     certificate issuer is `"recovery"`; extend the device certificate verification to accept a recovery
     issuer that is listed in a valid recovery record).
  4. Rotate **every** scope epoch, re-grant to the current device and to the new recovery key, and create a new
     recovery secret, printing new shares. Then write a recovery record signed by the old recovery key, which
     marks the old recovery key superseded.
  5. Other devices see the alert on their next sync. They keep their certificates, but must be re-granted the
     new epochs by an approver (`pair regrant`).

### Presence (`presence.py`)

- `Presence.confirm(action, summary) -> bool`. `ConsolePresence` prints a fixed-format summary and requires the
  user to type the action word (for example `remove`), using `input` injected for tests. Non-interactive stdin
  (not a TTY) is refused.
- Windows: an approval opens a window for its **action class** (`device`, `agent`, `recovery`, `export`,
  `profile`, `cross_profile`, `secret_read`, `first_access`) for 15 minutes. The window is stored in the key
  store (so it cannot be forged by writing a plain file). `presence open --class secret_read --minutes 15` opens
  one ahead of time for the unattended broker.
- Trigger log: each request appends `{ts, action_class, action, outcome, window_used}` to `presence.jsonl`
  (0600). It holds no plaintext.
- **Broker hooks** replace every `TODO(phase4-presence)`:
  - first access by a new agent — its first successful request needs a `first_access` window;
  - cross-profile recall — needs a `cross_profile` window in addition to `allow_cross_profile`;
  - returning `secret`-labelled results — needs a `secret_read` window in addition to `allow_secret`.

  Without a window the broker answers `presence_required` (a new fixed error code) and audits it. It never
  prompts from the daemon.
- CLI actions needing presence: device add (approve) and remove, agent grant and revoke, recovery
  create/test/use, export (none exists yet; reserve the class), profile archive and delete, keystore migrate.

### OS key store (`oskeystore.py`)

Port `memory-sync/os_unlock.py`'s two backends (macOS login keychain through `/usr/bin/security`, Windows
Credential Manager through ctypes). They hold a random 32-byte K under service `agentmesh.profile-log` and
account = the device ID. `OsKeyStore` seals the broker state file with ChaCha20-Poly1305 under K (AAD = format
plus device ID) and writes it 0600. `keystore migrate --to os` moves a Phase 3 file store (presence required)
and deletes the plaintext file only after a successful round trip. Tests use an injected fake backend and never
touch the real keychain. Add a manual smoke script `profile-log/tests/smoke_os_keystore.py`, not collected by
pytest, for the owner to run on the Mac and on Windows.

## Ground rules

- Work only in `/Users/krisanathep/Workspaces/AgentMesh/.claude/worktrees/bold-hofstadter-af7a0c` on branch
  `claude/epl-phase4`. Change only `profile-log/**` and
  `docs/superpowers/plans/2026-10-20-encrypted-profile-log-phase4.REPORT.md`. Do not touch `memory-sync/`,
  `profile-memory/`, installers, services, workflows or any other docs. Do not commit, push or create branches.
- No network. Use the prepared interpreter only (`~/.cache/agentmesh-logvenv/bin/python`). pyrage,
  shamir-mnemonic and segno are already installed. Do not pip install.
- Disposable data only: never read or modify `~/.hermes`, real identities, exchanges, the real keychain or
  Credential Manager, or the user's memory databases.
- TDD: write each failing test first and confirm it fails for the stated reason.
- Open text files with `encoding='utf-8'` explicitly (Windows CI runs cp1252 by default). Any subprocess with
  `text=True` also passes `encoding='utf-8'`.
- Never print, log or put in exception messages any private key, scope key, recovery secret, share or plaintext,
  except the deliberate one-time share printout.
- Keep all 250 existing tests passing and the Phase 1–3 wire formats unchanged. New formats are additive.
- No GATES.md or other working files left behind. Sockets: a test denied by the sandbox fails explicitly and is
  never skipped.

## Tasks

1. **Age identities + signed records:** `agekeys.py`, the device record, revocation and recovery record;
   `Registry` revocation checks.
2. **Keyring:** grant creation and opening with all four checks, epoch records, current-epoch selection, the
   recovery grant, the `Keyring` protocol.
3. **Presence:** the console implementation, windows, trigger log, broker hooks with `presence_required`.
4. **Pairing:** the commit-reveal protocol over `Storage`, SAS, QR payload and rendering, expiry and
   single use; joiner bootstrap from grants.
5. **Lifecycle:** device remove with rotation, archive, delete (crypto-shred).
6. **Recovery:** secret derivation, SLIP-39 split/combine, test, use (alert, rotation, new key).
7. **OS key store + migration;** first-device init drops the root key.
8. **CLI, end-to-end tests, README "Devices and recovery" section, FORMAT.md Phase 4 records, REPORT.**

## Acceptance gates

### Security (each needs a dedicated test the reviewer can mutation-test)

- **P4-1 Forged keyring grant:** a grant written to storage by a non-device, by a revoked device (issued after
  revocation), or by a device that does not hold the scope or an ancestor; a grant whose age payload holds a
  different key than `key_id`; and a correctly signed grant whose fields were tampered with. All are rejected and
  never used to open events.
- **P4-2 Pairing MITM:** a storage attacker who replaces `commit.json` with their own keys produces a different
  SAS (the test shows the codes differ and approval is refused when the user answers "no"). A reveal that does
  not match the commitment aborts. Reveal before approval aborts. A replayed `approver.json` from another request
  aborts. An expired request and a reused request ID are refused. The attacker cannot choose Nj after seeing Na:
  the test grinds 10,000 alternative reveals and every one fails the commitment.
- **P4-3 Grants are scoped:** a joiner approved for `work/client-a` can open `client-a` events and nothing in
  `client-b`, `work` root, `common` or `personal`, even with every object in storage available.
- **P4-4 Revocation:** after `device remove`, the removed device's agents fail broker handshake on every device
  after sync. Events sealed after removal cannot be opened with any key the removed device held (the test keeps
  a copy of its key store). Events from before removal remain readable by the remaining devices. A revocation
  signed by the subject itself, by a revoked device or by a non-device is ignored.
- **P4-5 Root key gone:** after `broker init` and after `keystore migrate`, no file under the broker home (or
  the key store contents) contains the account root private key in raw, hex or base64url form, and new devices
  are still certifiable.
- **P4-6 Recovery:** with all devices "lost" (homes deleted), `recovery use` with any 2 of the 3 shares on a
  fresh home restores readability of every profile's events, writes an alert seen by a surviving device on
  sync, rotates every epoch and issues new shares. Afterwards the old shares fail `recovery test` against the
  new record. One share alone fails. `recovery test` changes no file (checked by a full tree hash before and
  after).
- **P4-7 Presence:** every listed CLI action is refused when the console confirmation fails or stdin is not a
  TTY. The broker returns `presence_required` for first access, cross-profile and secret reads without a valid
  window, and succeeds within one. An expired window is refused. A window written by hand into a file outside
  the key store has no effect. Every trigger is logged.
- **P4-8 Profile lifecycle:** an archived profile refuses writes and still serves recall. A deleted profile has
  no local key bytes left (scan the key store and home) and its events cannot be opened, on this device and on
  another device after it syncs the state record.
- **P4-9 Epochs:** writers seal to the current epoch only. Concurrent rotations converge on the same winner on
  two devices, and events sealed to the losing epoch stay readable.
- **P4-10 OS key store:** with the fake backend, the state file holds no plaintext key material. A wrong K or a
  tampered file fails closed. Migration from the file store round-trips before deleting the plaintext file.

### Whole change

- The full suite passes from the repository root and from `profile-log/`. The three-OS CI passes. The reviewer
  reruns socket tests outside the sandbox.
- Two-device end-to-end test: init on A, join B with the SAS flow, grant `work/client-a`, write on both, sync
  through filesystem storage, recall on both, remove B, and check that A's later writes are unreadable to B.
- The REPORT maps every gate to tests, lists deviations, and lists the manual steps left for the owner: the OS
  key store smoke on the Mac and on Windows, and printing and storing the recovery shares.
- `git status` shows changes only under `profile-log/` and the REPORT file.
