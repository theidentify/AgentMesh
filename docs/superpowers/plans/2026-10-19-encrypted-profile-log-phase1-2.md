# Encrypted Profile Log, Phases 1–2 (Model + Storage) Implementation Plan

> **For agentic workers:** execute in order. The spec is
> `docs/superpowers/specs/2026-10-18-encrypted-profile-log-design.md`; read it fully first. The spec wins on
> conflict, except where this plan records an owner decision. Stop and report instead of guessing.

**Goal:** a standalone library that models account, profile, scope tree, device and agent identity, signed
and encrypted append-only events with sensitivity labels (Phase 1), and stores them through a
`put`/`get`/`list` interface with filesystem and S3-compatible backends (Phase 2). Pure library code: no
CLI, broker, enrollment, keyring format, recovery, migration or sync integration.

## Owner decisions (2026-10-19)

- **New package `profile-log/`** (Python package `agentmesh_log`). It imports nothing from `memory-sync/` or
  `profile-memory/`; ideas are ported, not imported. `profile-memory/` is left untouched; its retrieval code
  is a candidate for Phase 3.
- **Taxonomy for fixtures, examples and docs:** profiles `work` and `personal`; under `work`, one project
  per client (`client-a`, `client-b`); every profile has `common`. Task and tag are filters only. Nothing in
  the library hardcodes these names.
- **Crypto:** `cryptography==50.0.2` only (already pinned in `memory-sync/requirements-security.txt`).
  Ed25519 for signatures; HPKE (RFC 9180) base mode, suite `KEM.X25519` / `KDF.HKDF_SHA256` /
  `AEAD.CHACHA20_POLY1305` for encryption. ULID is implemented with the standard library. The age keyring
  format belongs to Phase 4 behind a `Keyring` interface and is **not** part of this plan.
- **S3:** `boto3` as an optional dependency, imported lazily only by the S3 backend; tests use `moto`, with
  no real cloud credentials or network.

## Design to implement

### Package layout

```
profile-log/
  README.md            what works, what does not, how to run tests
  FORMAT.md            wire formats, domains, verification order (normative)
  requirements.txt     cryptography==50.0.2
  requirements-s3.txt  boto3==1.43.114
  requirements-dev.txt pytest==8.4.2, moto[s3,server]==5.2.4 (+ the two files above via -r)
  agentmesh_log/
    __init__.py
    encoding.py   canonical typed encoding + strict JSON parse (port of signed_packets.typed/wire/parse)
    ulid.py       generate (monotonic within a process), parse/validate, timestamp
    model.py      Account, Profile, Scope, ScopeTree, labels
    keys.py       ScopeReadKey, ScopeWriteKey, key ids, wrapping child keys to the parent
    identity.py   DeviceIdentity, AgentIdentity, certificates, Registry (chain verification)
    events.py     seal_event / verify_event / open_event, object paths
    errors.py     typed errors (no secret material in messages)
    storage/
      __init__.py Storage protocol, key validation, errors
      filesystem.py
      s3.py
  tests/
```

### Identifiers

- `account_id`, `profile_id`, `scope_id`, `device_id`, `agent_id`: 128-bit random, lowercase hex (32
  chars), from `secrets`. Never derived from names. Names (`work`, `client-a`) live only in local,
  non-serialized-to-storage objects or inside encrypted payloads.
- Event IDs are ULIDs (26 chars Crockford base32, uppercase, 48-bit ms timestamp + 80-bit randomness).
  Parsing rejects wrong length, lowercase-only ambiguity is not accepted (canonical uppercase only),
  invalid characters and timestamps overflowing 48 bits. Generation is monotonic within a process for
  the same millisecond.

### Account and email

- `Account` holds `account_id` and an Ed25519 **account root** signing key (root of the identity chain in
  this phase; recovery and Shamir are Phase 4).
- **Email is never in any serialized public form.** `Account.public_record()` contains only
  `format`, `account_id`, `root_public_key`. Email may exist on the local object only. A test asserts the
  email string (and its lowercase form) appears in no public record, certificate, event envelope or
  stored object.

### Scope tree and keys

- `Profile` = the root scope of a tree. `Scope` has `scope_id`, `parent` (None for the profile root),
  `kind` (`profile`, `common`, `project`), local `name`, and a **default label**. Task/tag are not scopes.
- Each scope **epoch** has an independent random X25519 keypair (`generate`, never derived from the
  parent).
  - `ScopeReadKey` holds the private key.
  - `ScopeWriteKey` holds **only** the public key. It has no private material, no decrypt method, and
    cannot be converted into a read key.
  - `key_id` = lowercase hex SHA-256 of `b'AgentMesh/scope-key-id/v1\x00' + raw public key bytes`.
- **Wrapping:** a child scope's private key is HPKE-encrypted to the parent scope's public key.
  `info` = domain `b'AgentMesh/scope-wrap/v1\x00'` + canonical encoding of
  `{profile, parent_scope, parent_key_id, child_scope, child_key_id}`. A wrap record is a JSON object with
  those fields plus `format` and `ciphertext` (base64url, no padding).
  - Unwrapping requires the parent read key and checks that the unwrapped private key's public key
    matches `child_key_id`.
  - Holding a parent read key opens all descendants (by unwrapping step by step). Holding a child opens
    neither the parent nor siblings.
  - Rotating one scope = a new epoch keypair for that scope, wrapped to the current parent key, and the
    scope's direct children re-wrapped to the new key. Other branches are untouched.

### Device and agent identity

- `DeviceIdentity`: Ed25519 keypair, `device_id`. Its **device certificate** is signed by either the
  account root key (first device) or an already-certified device (approver). Fields: `format`,
  `account_id`, `device_id`, `device_public_key`, `issuer` (`account` or the approver's `device_id`),
  `issued_at` (integer ms), `signature`.
- `AgentIdentity`: an Ed25519 **sub-key** certified by its device. **Agent certificate** fields: `format`,
  `account_id`, `device_id`, `agent_id`, `agent_public_key`, `issued_at`, `signature` (device key).
  Agents hold no data keys; the library must not put scope keys on agent objects.
- `Registry(account_public_record)`: adds device certificates and verifies them against the root or an
  already-registered issuer. Rejects wrong account, unknown issuer, bad signature, duplicate `device_id`
  with a different key, and self-issued device certs. `verify_agent(cert)` checks the device is registered
  and the signature is by that device's key. Revocation and enrollment are out of scope (Phase 4); leave a
  `TODO(phase4)` in the registry where revocation will plug in, nothing more.
- Signatures always sign `domain + canonical_encoding(record without "signature")`, with distinct domains
  per record type (`AgentMesh/device-cert/Ed25519/v1\x00`, `AgentMesh/agent-cert/Ed25519/v1\x00`,
  `AgentMesh/event/Ed25519/v1\x00`) so no signature verifies as another type.

### Sensitivity labels

- `Label` ordered enum: `general < internal < secret`.
- `resolve_label(scope_default, detected=None, agent=None)` = the maximum of those given.
- `change_label(current, new, *, user_confirmed=False)`: raising is allowed; lowering raises
  `LabelLoweringRequiresConfirmation` unless `user_confirmed=True`.
- A revision event whose label is lower than the label of the event it revises must be created with
  `user_confirmed=True` (enforced in `seal_event` when the caller passes the previous label).
- Secret/PII detectors and credential redaction are Phase 3 (broker); out of scope.

### Event format (`agentmesh-event-v1`)

Envelope (all fields required, no extras):

| Field | Meaning |
| --- | --- |
| `format` | `agentmesh-event-v1` |
| `id` | ULID |
| `account` | `account_id` |
| `profile` | `profile_id` |
| `scope` | `scope_id` |
| `scope_key_id` | key id of the scope epoch it is encrypted to |
| `device` | writing device's `device_id` |
| `agent` | writing agent's `agent_id` |
| `agent_cert` | the full agent certificate (so events verify given a registry) |
| `ciphertext` | base64url (no padding) HPKE output |
| `signature` | hex Ed25519 by the agent sub-key |

- **Plaintext payload** (canonical JSON inside the ciphertext): `body` (string or JSON object), `label`,
  `created_at` (integer ms), `revises` (ULID or null), `tags` (list of strings), `task` (string or null).
  Label, tags and task are therefore not visible to storage.
- HPKE `info` = `b'AgentMesh/event/v1\x00'` + canonical encoding of the envelope **without** `ciphertext`
  and `signature`. Changing any header field makes decryption fail even if someone re-signs.
- `signature` covers the whole envelope except `signature` (so it covers the ciphertext).
- **Write path is the same for everyone:** `seal_event` takes a `ScopeWriteKey` (a read key exposes its
  write key). So a write-only device and a full reader produce identical formats.
- `verify_event(data, registry)` order: size limit (e.g. 1 MiB) → strict parse (duplicate keys,
  non-finite numbers, wrong types rejected) → exact field set → `format` → ids/ULID shape →
  agent cert via registry, and cert `account`/`device`/`agent` equal the envelope's → signature.
  It returns a verified envelope without decrypting (usable by a relay or worker with no keys).
- `open_event(data, registry, read_key)` = verify, then check `scope_key_id == read_key.key_id` and
  `scope`/`profile` match the key's scope, then HPKE-decrypt with `info`, strict-parse and validate the
  payload. Every failure raises a typed error; no partial plaintext is returned.
- Object path helper: `events/{profile_id}/{scope_id}/{ulid}.json` (opaque IDs only); wrap records:
  `keys/{profile_id}/{child_scope_id}/{child_key_id}.{parent_key_id}.json`.

### Storage interface (Phase 2)

```python
class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...   # new immutable object
    def get(self, key: str) -> bytes: ...               # ObjectNotFound if absent
    def list(self, prefix: str) -> Iterator[str]: ...   # keys under prefix, sorted, all pages
```

- No delete, no locks, no conditional writes, no server events.
- Key validation shared by both backends: 1–1024 chars, `[A-Za-z0-9._-]` segments separated by `/`, no
  leading/trailing `/`, no empty, `.` or `..` segment, no segment starting with `.` (keeps Syncthing
  temp/marker files out). Prefix validation is the same but allows an empty prefix and a trailing `/`.
- **Filesystem backend** (`FilesystemStorage(root)`): standard library only.
  - `put` writes a temp file in the target directory, fsyncs, then publishes with `os.link` (fails if the
    target exists) and removes the temp. Same bytes already present → no-op; different bytes →
    `ObjectExists`. Never overwrites.
  - Refuses symlinks anywhere under `root` on read and write. Creates directories as needed. Files are
    written owner-only where the OS supports it.
  - `list` walks the prefix, ignores dot-files/dirs and temp files, returns sorted keys.
  - Must work on Windows (no POSIX-only calls without a guard); the tests must not assume POSIX
    permissions on Windows.
- **S3 backend** (`S3Storage(bucket, *, prefix='', endpoint_url=None, region_name='auto', client=None,
  client_factory=None)`):
  - Lazy `import boto3` inside the constructor path that builds a client; a clear error if missing.
    `endpoint_url` lets it target R2 (`https://<account>.r2.cloudflarestorage.com`), B2, Garage or
    SeaweedFS. Credentials come from boto's normal chain; never logged or put in exceptions.
  - `put` = plain `put_object` (S3 has no exclusive create without conditional writes; document that
    immutability rests on unique ULID keys and, later, the Worker). `get` maps a missing key to
    `ObjectNotFound`. `list` uses the `list_objects_v2` paginator and strips the backend prefix.
- **Shared contract tests** run the same test class against both backends (S3 via `moto.mock_aws`), plus
  one S3 test through a real HTTP endpoint (`moto.server.ThreadedMotoServer` on 127.0.0.1, `endpoint_url`
  set, path-style addressing) to exercise the R2-style custom endpoint path. Pagination is tested with
  more than one page (small `MaxKeys` / page size).

## Ground rules

- Work only in `/Users/krisanathep/Workspaces/AgentMesh/.claude/worktrees/epl-phase1-2-80ffc3`
  (branch `claude/epl-phase1-2-80ffc3`). Change only `profile-log/**` and the REPORT file. **Do not touch
  `memory-sync/`, `profile-memory/`, installers, services, workflows or docs outside this plan.**
- **Do not commit, push, or create branches.** The reviewer commits.
- No network. Use the prepared interpreter for everything:
  `~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests`
  (Python 3.11, cryptography 50.0.2, boto3 1.43.114, moto 5.2.4 with server extras, pytest 8.4.2).
  Do not pip install anything.
- **Disposable data only.** Never read or modify `~/.hermes`, real identities, exchanges or keychains.
  Tests use `tmp_path`.
- **TDD:** write each failing test first, confirm it fails for the stated reason, then implement.
- Never print, log or put in exception messages any private key, scope key, plaintext body or credential.
- Match the compact style of the repo (see `memory-sync/signed_packets.py`).
- No GATES.md or other working files left behind. The report goes only in
  `docs/superpowers/plans/2026-10-19-encrypted-profile-log-phase1-2.REPORT.md`.

## Tasks

1. **Scaffold + encoding + ULID.** Package skeleton, requirements files, `encoding.py` (port with tests:
   injectivity examples, duplicate keys, non-finite, depth/size limits), `ulid.py` (format, parse
   rejection cases, monotonic generation, sort order matches time order).
2. **Model, labels, scope keys and wrapping.** Account (email never serialized), Profile/Scope/ScopeTree,
   labels, `ScopeReadKey`/`ScopeWriteKey`, wrap/unwrap, epochs and single-branch rotation.
3. **Device and agent identity + Registry.**
4. **Events:** `seal_event`, `verify_event`, `open_event`, payload validation, object paths, label
   lowering rule on revisions.
5. **Storage:** protocol, key validation, filesystem backend, S3 backend, shared contract tests, HTTP
   endpoint test.
6. **End-to-end test:** taxonomy fixture (`work`/`client-a`/`client-b`/`common`, `personal`/`common`),
   two devices, an agent per device, a write-only "phone" holding only a `client-a` write key. Events
   written through each backend, listed by prefix, fetched, verified and opened by the right readers only.
7. **Docs + REPORT:** `profile-log/README.md` and `FORMAT.md` (normative wire formats, domains,
   verification order, what storage can see). REPORT lists each acceptance gate with the test(s) proving
   it, deviations, and open questions.

## Acceptance gates

### Phase 1: model

- **P1-1 Tamper:** flipping any single envelope field (each of the 11, including one byte of `ciphertext`
  and of `signature`) makes `verify_event` or `open_event` fail. Parametrized over every field.
- **P1-2 Re-sign attack:** an attacker with their own valid agent cert who replaces `agent`/`device`/
  `agent_cert` and re-signs a victim's ciphertext gets a valid signature but `open_event` fails (header
  bound through HPKE `info`).
- **P1-3 Wrong key:** opening with a sibling scope's key, another profile's key, or an older epoch of the
  same scope fails with a typed error; no plaintext returned.
- **P1-4 Write-only cannot read:** a `ScopeWriteKey` can seal events that a reader opens, holds no private
  material (inspect attributes and pickling/serialization), and cannot open events or unwrap keys
  (type-level refusal plus attempt).
- **P1-5 Tree:** a parent read key unwraps every descendant; a child key cannot unwrap the parent or a
  sibling; a tampered wrap record or a wrong `child_key_id` fails; rotating `client-a` leaves `client-b`
  wrap records byte-identical.
- **P1-6 Identity chain:** agent cert signed by a different device, device cert from another account,
  unknown issuer, self-issued device cert, and cross-type signature reuse (device-cert signature presented
  as an agent cert, etc.) are all rejected.
- **P1-7 Labels:** resolution takes the maximum; lowering without confirmation is refused, both directly
  and on a revision event.
- **P1-8 Email/metadata privacy:** the email string never appears in any serialized public record, event
  envelope or stored object; labels, tags, task and body never appear in envelope plaintext.
- **P1-9 Strict parsing:** duplicate JSON keys, extra/missing fields, wrong types, oversize input and
  invalid ULIDs are rejected before signature work.

### Phase 2: storage

- **P2-1 Contract:** the same test class passes for filesystem and S3 (moto): put/get round trip,
  `ObjectNotFound`, sorted `list` by prefix across nested keys, empty prefix, prefix with no matches.
- **P2-2 Key safety:** traversal (`..`), absolute paths, backslashes, empty/dot segments and over-long
  keys are rejected by both backends before any I/O.
- **P2-3 Filesystem immutability:** identical re-put is a no-op; different bytes raise `ObjectExists` and
  leave the original intact; symlinked root or subdirectory is refused; dot-files and temp files are not
  listed.
- **P2-4 S3:** pagination across multiple pages; backend `prefix` isolation (two prefixes in one bucket
  do not see each other); custom `endpoint_url` against a local HTTP moto server; missing `boto3`
  produces a clear error (simulated) and the filesystem backend still imports without boto3.
- **P2-5 End to end:** task 6 passes on both backends.

### Whole change

- `~/.cache/agentmesh-logvenv/bin/python -m pytest -q -p no:cacheprovider profile-log/tests` passes from
  the repo root, and also from inside `profile-log/`.
- `git status` shows changes only under `profile-log/` and the REPORT file.
- No real credentials, network access or user data used.
