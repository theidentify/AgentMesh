# AgentMesh encrypted profile log v1 (normative)

## Encoding and limits

Records use UTF-8 JSON, emitted with sorted keys, compact separators, Unicode preserved,
and finite numbers only. `wire` validates and serializes; `parse` accepts bytes only and
rejects invalid UTF-8, duplicate keys, non-finite numbers (including overflow such as
`1e999`), invalid Unicode scalars, integers outside signed 64-bit range, structures deeper
than 64, more than 100,000 visited values, or input/encoded output exceeding 1 MiB.
Depth starts at zero; dictionary keys and values each count as visited values. Readers
need not receive canonical whitespace or key order: signatures use the typed encoding.

Canonical encoding for signatures and HPKE info is **typed binary**, not RFC 8785/JCS:

| Value | Bytes |
| --- | --- |
| null | ASCII `n` |
| false / true | ASCII `f` / `t` |
| integer | ASCII `i`, signed decimal (no leading zeros), `;` |
| float | ASCII `d`, IEEE-754 binary64 big-endian; signed zero preserved |
| string | ASCII `s`, decimal UTF-8 byte length, `:`, UTF-8 bytes |
| list | ASCII `l`, decimal item count, `:`, concatenated typed items |
| object | ASCII `o`, decimal pair count, `:`, typed key then typed value for each pair, keys sorted by UTF-8 bytes |

Only exact JSON types are accepted (booleans are distinct from integers). Hex is lowercase
and fixed-length. Base64url is canonical, without padding; noncanonical trailing bits are
rejected. Error classes derive from `LogError`; messages never contain input plaintext,
keys or credentials. Failure returns no partial plaintext.

## Identifiers

Account, profile, scope, device and agent IDs are independent `secrets`-generated 128-bit
values encoded as 32 lowercase hex characters. A profile is also its root scope, so those
two IDs coincide for the root. Names are never used as IDs or serialized public metadata.

Event IDs are 26-character uppercase Crockford base32 ULIDs using
`0123456789ABCDEFGHJKMNPQRSTVWXYZ`: 48-bit millisecond time followed by 80 random bits.
Overflow beyond 128 bits, wrong length, lowercase and other characters are invalid.
Generation is thread-serialized and monotonic within this process, including clock
rollback; exhausting randomness advances the logical time. ULID timestamps are public.

## Accounts and certificates

All records below require exactly the listed fields, with no extras.

- Account: `format="agentmesh-account-v1"`, `account_id`, `root_public_key` (32-byte
  Ed25519 public key in hex). Email is excluded, including hashes.
- Device certificate: `format="agentmesh-device-cert-v1"`, `account_id`, `device_id`,
  `device_public_key` (32-byte hex), `issuer` (`"account"` or a previously registered
  device ID), `issued_at` (nonnegative integer milliseconds below 2^63), `signature`
  (64-byte Ed25519 signature in hex).
- Agent certificate: `format="agentmesh-agent-cert-v1"`, `account_id`, `device_id`,
  `agent_id`, `agent_public_key` (32-byte hex), `issued_at` (same integer rules), `signature`.

Signature input is the domain concatenated with the typed encoding of the entire record
minus `signature`. Domains are exact byte strings including the trailing NUL:

| Record | Domain |
| --- | --- |
| Device certificate | `AgentMesh/device-cert/Ed25519/v1\x00` |
| Agent certificate | `AgentMesh/agent-cert/Ed25519/v1\x00` |
| Event | `AgentMesh/event/Ed25519/v1\x00` |

A registry is pinned to one account public record. Device certificates must verify with
that root or an already registered issuer. Wrong accounts, self-issued devices, unknown
issuers and duplicate device IDs with different keys are rejected. Re-registering a
correctly signed certificate for the same ID/key is allowed. Agent certificates must
verify with their registered device. Revocation is deferred to phase 4.

## Scope epochs and wraps

Every scope epoch is an independently generated X25519 keypair. A `ScopeWriteKey` contains
only `profile_id`, `scope_id` and raw public bytes; it cannot decrypt or unwrap. A
`ScopeReadKey` contains the private key and exposes its write key.

`key_id = hex(SHA256(b'AgentMesh/scope-key-id/v1\x00' + raw_public_key))`.

All encryption uses RFC 9180 HPKE **base mode**, DHKEM(X25519, HKDF-SHA256) (0x0020),
HKDF-SHA256 (0x0001), ChaCha20-Poly1305 (0x0003). The cryptography 50.0.2 `Suite` API
produces the 32-byte encapsulated key followed by the AEAD ciphertext and its 16-byte tag.
AAD is empty. Each encryption uses a fresh encapsulation. There are no additional nonces,
password KDFs or custom cryptographic primitives.

A wrap contains exactly `format="agentmesh-scope-wrap-v1"`, `profile`, `parent_scope`,
`parent_key_id`, `child_scope`, `child_key_id`, `ciphertext`, `auth`.
IDs are 32 hex characters; key IDs are 64 hex characters. `ciphertext` is base64url HPKE
output encrypting the child's 32 raw private-key bytes to the parent's public key.

Wrap HPKE info is `b'AgentMesh/scope-wrap/v1\x00'` plus typed encoding of exactly
`{profile, parent_scope, parent_key_id, child_scope, child_key_id}`. Unwrapping validates
record shape and IDs, requires a read key whose profile/scope/epoch match the parent
fields, verifies `auth` with a constant-time comparison **before decryption**, decrypts,
and recomputes the child key ID before returning a read key. Wrapping
across profiles or to the same scope is refused. Tree edges are maintained locally by
`ScopeTree`; a wrap does not independently certify a topology or authorize a grant.

Creating a wrap requires both the child's and parent's **read** keys. `auth` is a 32-byte
HMAC-SHA256 in lowercase hex over `b'AgentMesh/scope-wrap-auth/v1\x00'` concatenated with
the typed canonical encoding of the entire record without `auth` (including format and
ciphertext). Its 32-byte key is HKDF-SHA256 with input key material equal to the parent's
raw private-key bytes, `salt=None`, and
`info=b'AgentMesh/scope-wrap-auth-key/v1\x00' + bytes.fromhex(parent.key_id)`.
Unauthenticated legacy wraps and malformed or incorrect authentication tags are rejected.
This proves that the wrap was made by a holder of the parent read key; a holder of only
the parent write key cannot create an accepted replacement. Which epoch is current and
who may rotate are Phase 4 keyring decisions, not decided here. Authentication alone does
not prevent replay of an older legitimate wrap.

Rotation replaces one scope key, wraps it to the current parent, and re-wraps direct
children to it. Child private keys and unrelated branches do not change. Old epoch keys
remain available locally; rotation does not revoke access to already-readable history.

## Events

Envelope fields, all required:

| Field | Type / rule |
| --- | --- |
| format | `"agentmesh-event-v1"` |
| id | canonical ULID |
| account, profile, scope, device, agent | 32 lowercase hex characters |
| scope_key_id | 64 lowercase hex characters |
| agent_cert | complete agent certificate object |
| ciphertext | base64url HPKE output |
| signature | 64-byte Ed25519 signature in lowercase hex |

Plaintext is canonical JSON containing exactly:

| Field | Type / rule |
| --- | --- |
| body | string or JSON object |
| label | `"general"`, `"internal"`, `"secret"` |
| created_at | nonnegative integer milliseconds below 2^63, not bool |
| revises | canonical ULID or null |
| tags | list of strings |
| task | string or null |

HPKE info is `b'AgentMesh/event/v1\x00'` plus typed encoding of the envelope without
`ciphertext` and `signature`. It includes the complete agent certificate. The signature
uses the event domain above and covers the full envelope except `signature`, including
the ciphertext. Thus a different certified author re-signing captured ciphertext cannot
open it with altered identity headers.

Verification order is:

1. Byte/type and 1 MiB size limits, strict JSON parse and structure limits.
2. Exact envelope field set and format.
3. ID/ULID shapes, ciphertext encoding, signature shape and certificate field/type shapes.
4. Envelope account equality to the registry account; agent certificate chain via the
   registry and certificate account/device/agent equality to the envelope.
5. Event Ed25519 signature.

`verify_event` returns the envelope and needs no scope keys. `open_event` first performs
all those steps, then requires a read key and matching scope/profile/epoch, decrypts with
bound info, strictly parses and validates the complete payload, and only then returns it.

Labels are ordered `general < internal < secret`. Resolution takes the maximum supplied
scope-default/detected/agent label. Lowering requires `user_confirmed=True`; the seal path
applies this rule when a revision's caller provides `previous_label`. A stateless verifier
cannot discover the previous encrypted label. Task and tags are filters, never scope nodes.

## Object paths and storage visibility

- Events: `events/{profile_id}/{scope_id}/{ulid}.json`.
- Wraps: `keys/{profile_id}/{child_scope_id}/{child_key_id}.{parent_key_id}.json`.

Storage sees opaque IDs, epochs, public signing keys/certificates, signatures, ciphertext
sizes, event ULID timestamps, certificate issue timestamps, object paths and access patterns.
Wraps expose parent-child relationships. It never sees email, local names, payload body,
label, tags, task, revision link or payload creation time in plaintext.

Storage keys are 1–1024 characters, ASCII alphanumerics/underscore/hyphen/dot in slash-separated
segments, with no empty segment, leading/trailing slash, dot-starting segment, `.` or `..`.
Prefixes additionally allow the empty string and a single trailing slash. `list` uses string
prefix semantics and returns all matching keys sorted lexicographically. Filesystem lists
ignore dot-files/directories, including publication temp files and Syncthing markers.
Both backends skip stored names that fail key validation, so stray objects do not block
valid listings. Filesystem traversal still refuses symlinks, reparse points and non-regular
files it encounters. It starts at the directory named by the prefix's complete
slash-terminated components, checking each ancestor, and returns an empty iterator if that
directory does not exist. A partial last component is a string filter, not a directory:
`events/ab` starts at `events/`, while `events/ab/` starts at `events/ab/`.

Filesystem writes publish fsynced owner-only temporary files with `os.link` and remove the
temp. Identical existing bytes succeed; different bytes raise `ObjectExists`. Symlink/reparse
paths are refused. S3 uses plain PUT, without conditional writes: repeated keys can overwrite,
so immutable naming is a caller responsibility. S3 lists paginate all pages and strip the
backend namespace prefix. Missing objects raise `ObjectNotFound`; dependency, validation and
backend failures use typed errors with sanitized messages.

## Phase 3 broker appendix (separate from event FORMAT v1)

Broker IPC is a 4-byte unsigned big-endian byte length followed by canonical JSON.
Zero length and lengths over 1 MiB are rejected before a stream body is read.
Malformed JSON, duplicate keys and framing errors close the connection. Stream
reads and pipe polls time out after 30 seconds. Windows transports each entire
frame with `send_bytes`/`recv_bytes`, never pickle or multiprocessing authkeys.

Mutual authentication messages have exactly these fields:

| Message | Fields |
| --- | --- |
| hello | `format="agentmesh-broker-hello-v1"`, `client_nonce` |
| challenge | `format="agentmesh-broker-challenge-v1"`, `device_id`, `server_nonce`, `signature` |
| auth | `format="agentmesh-broker-auth-v1"`, `agent_cert`, `signature` |
| ready | `format="agentmesh-broker-ready-v1"`, `agent_id` |

Nonces are fresh 32-byte canonical unpadded base64url values. Challenge signatures
cover `b'AgentMesh/broker-challenge/Ed25519/v1\x00' + typed({device_id,
client_nonce, server_nonce})`. Auth signatures cover
`b'AgentMesh/broker-auth/Ed25519/v1\x00' + typed({device_id, agent_id,
client_nonce, server_nonce})`. Signatures are lowercase hex. Clients pin the device
public key; the broker verifies the registry chain, local device ID, granted agent
ID and pinned agent public key. Authentication failure returns
`{"error":"unauthenticated"}` and closes the connection; audit failure returns
`{"error":"unavailable"}` instead. Connections have independent pending writes.

Requests require `op` and permit only these operation-specific fields. Optional
fields may be omitted; omission uses the listed null/default behavior.

| Op | Fields and defaults |
| --- | --- |
| whoami | no additional fields |
| recall | required `query` (1–2048 characters); `project=null`, `task=null`, `limit=12` (1–100), `profiles=null`, `scopes=null` |
| remember | required `body`; `scope=null`, `tags=[]`, `task=null`, `label=null` |
| revise | remember fields plus required `revises` ULID |
| sign | required `signature` hex |

`profiles=null` means the grant's default profile. `scopes=null` means all readable
scopes within selected profiles; a named scope includes descendants. Unknown,
unreadable or out-of-profile scope names deny the entire request. Empty selection
lists select nothing. Multiple distinct profiles require cross-profile permission.
`project` is a body metadata filter, never a scope selector. A null write scope
selects `default_write`, except that writing to `common` requires an explicit name.

Successful `whoami` returns exactly `agent_id`, `read`, `write`, `default_profile`,
`allow_secret`; scope and profile values are local names. `remember`/`revise` return
exactly `stage="sign"`, `unsigned` (the existing v1 envelope without `signature`).
The client checks its author/certificate binding and can assert an opaque expected
scope ID with `BrokerClient.write(expected_scope=...)`. The pinned broker resolves
local scope names. A `sign` commits that session's pending envelope and returns
exactly `id`, `label`, `redactions` (a list of kinds). Any other request discards the
pending envelope, including an invalid request. Changed grants invalidate it.
No agent may confirm label lowering. Every error response contains only `error`,
one of `denied`, `invalid_request`, `unauthenticated`, `unavailable`,
`label_lowering_requires_confirmation`, `internal`.

Recall responses contain exactly `query`, `query_plan`, `candidate_counts`,
`results`, `evidence`. The query plan contains `query`, `project`, `task_refs`,
`intents`, `terms`. Candidate counts contain `durable`, `summaries=0`, `events=0`;
evidence is `[]`. Result fields are exactly `id`, `source_type="durable"`, `kind`,
`content`, `project`, `task_ref`, `confidence`, `scope`, `profile`, `label`, `tags`,
`created_at`, `updated_at`, `matched_terms`, `score`, `conflict`, `alternatives`.
Timestamps are ISO-8601 UTC; alternatives contain other head ULIDs. Memory bodies
are strings or objects with required string `kind` and `content`, optional
`project`, `task_ref`, numeric `confidence` in [0,1], and object `metadata`.
Nonconforming replica bodies become canonical JSON fact content.

`keystore.json` is canonical JSON with format `agentmesh-broker-state-v1` and
exact top-level fields `format`, `account`, `account_private_key`, `device_id`,
`device_private_key`, `certificates`, `trees`, `grants`, `index_master_key`,
`audit_key`. Private keys and the two 32-byte symmetric keys are lowercase hex.
Each tree contains `root`, `scopes`, `epochs`, `wraps`; each scope records the local
model fields with its label name. Epochs map scope IDs to oldest-to-newest private
keys, the last being current. Grants map agent IDs to `agent_id`,
`agent_public_key`, `read`, `write`, `default_write`, `default_profile`,
`allow_secret`, `allow_cross_profile`. Scope/profile references are opaque IDs.
The file is atomically replaced with a fsynced owner-only temporary file followed
by directory fsync on POSIX. This is a Phase 3 local private format, not relay data.

Agent files contain exactly `format="agentmesh-agent-key-v1"`, `agent_cert`,
`agent_private_key`, `device_public_key`, `address`. The broker writes them at grant
time and never reads them to service an agent. They contain no data keys.

Snapshots contain a 12-byte random nonce followed by ChaCha20-Poly1305 ciphertext
and tag of SQLite `serialize()` bytes. Their key is HKDF-SHA256 of
`index_master_key` with no salt and info `b'AgentMesh/index-snapshot/v1\x00'`
concatenated with the UTF-8 agent ID. AAD is `typed({agent_id, grant_digest,
replica_watermark})`; grant digests hash canonical grant JSON, and the watermark
hashes sorted replica paths plus SHA256 hashes of their bytes. Any failure loads
no snapshot into a new index and triggers a rebuild. SQLite pages stay in RAM.
The object hashes are cached at startup and updated on event/wrap puts and sync
downloads; requests never re-read replica contents merely to calculate this AAD.
The private SQLite schema now retains authorized revision-event rows as well as
visible heads. Snapshots from the older head-only schema rebuild. Append updates
only the affected revision component, and snapshots are flushed lazily on clean
close or explicit flush. Missing or stale snapshots are disposable accelerators;
the encrypted replica remains the durable source.

Audit lines contain `ts` (integer milliseconds), `agent_id` (or null), `op`,
`outcome`, `profiles`, `scopes`, `cross_profile`, `query_hmac`, `query_chars`,
`result_count`, `result_ids`, `redactions`, `label`. Outcomes are `ok`, `denied`,
`invalid`, `unauthenticated`, `error`. Query HMAC is hex HMAC-SHA256 over UTF-8 query
text with `audit_key`; absent queries use the empty string. Invalid operation
names are recorded as `invalid`, preventing arbitrary input text in the log.
`redactions` maps secret kinds to counts. Each response waits for audit flush/fsync.

## Phase 4 records

Phase 1–3 event, wrap and IPC frame encodings are unchanged. These additions use the
same strict JSON/typed encoding. Every signed record below has exactly `format`,
`signature` and the listed fields. For kind `K`, format is `agentmesh-K-v1` and the
signature input is `b'AgentMesh/K/Ed25519/v1\x00' + typed(record without signature)`.
`K` is the literal kind in the table. All timestamps are nonnegative integer milliseconds;
IDs retain the earlier hex/ULID rules. `age` is canonical unpadded base64url of a real
age file (pyrage 1.4.0), with exactly one X25519 recipient. Age supplies confidentiality;
the outer Ed25519 signature authenticates the complete object before decryption.

| Kind | Exact fields besides format/signature | Path |
| --- | --- | --- |
| device-record | certificate, age_recipient, issued_at | registry/devices/{device_id}.json |
| profile-recipient | device_id, profile_id, age_recipient, issued_at | registry/profile-recipients/{profile_id}/{device_id}.json |
| device-revocation | device_id, revoked_by, revoked_at, reason_code | registry/revocations/{device_id}.json |
| recovery-record | account_id, recovery_signing_public, recovery_age_recipient, previous, issued_at | registry/recovery/{recovery_key_id}.json |
| profile-recovery | profile_id, recovery_key_id, age_recipient, issued_at, age | registry/profile-recovery/{profile_id}/{recovery_key_id}.json |
| keyring-grant | profile, scope, key_id, recipient_device, issuer_device, issued_at, age | keyring/{profile}/{scope}/{key_id}/{recipient_device}.json |
| epoch | profile, scope, key_id, previous_key_id, created_at, reason, issuer_device | keyring/{profile}/{scope}/epochs/{key_id}.json |
| scope-meta | profile, recipient_device, issuer_device, issued_at, age | keyring/{profile}/metadata/{recipient_device}.json |
| recovery-history | id, profile, recovery_key_id, issuer_device, issued_at, age | keyring/{profile}/recovery/{recovery_key_id}/{id}.json |
| recovery-alert | account_id, id, recovery_key_id, device_id, issued_at | alerts/recovery/{id}.json |
| profile-state | profile, id, state, issuer_device | profiles/{profile}/state/{id}.json |

`registry/account.json` contains the earlier account public record. The local account
pin, not this relay copy, is authoritative. Registry reconstruction verifies records,
checks their paths, resolves certificate/recovery dependencies and rejects invalid
objects. Devices cache public signed registry records with their protected local state.

### Device and profile bindings

`device-record` is signed by the embedded certificate's device key; its certificate
must chain to the pinned account. Its generic age recipient is not used for profile
grants. `profile-recipient` is signed by its named, currently non-revoked device. Its
age identity is random, independent for each profile, and retained only in that
device's protected state. Profile recipient objects are immutable.

Device certificates additionally accept `issuer="recovery"`, verified against a signed
recovery record issued no later than the certificate. Revocations reject a subject's
own signature, unknown signers and signers revoked at the revocation time. Reasons are
`removed`, `recovery`, `compromised`; `revoked_by` is a device ID or `"recovery"`.
Live agents of revoked devices are always refused. Certificates issued by a device at
or after its revocation are refused; earlier chains and historical events remain
verifiable at their issue/event timestamps. This preserves pre-removal readability.

### Grants, metadata and epochs

Grant opening checks the issuer signature and chain at `issued_at`, scope topology,
recipient device, signed profile-recipient binding matching the supplied age identity,
and recomputed scope key ID after decrypting exactly 32 private-key bytes. The issuer
must have a valid grant for that scope or an ancestor, recursively rooted in an
account- or recovery-certified device's self-grant. Ordinary device certificates do
not bootstrap self-grant authority. Recovery grants are limited to profile roots and
use `recipient_device="recovery"` with the signed per-profile recovery recipient.

Metadata plaintext has exactly `root` and `scopes`; scopes contain the existing local
model fields with label names. Names stay inside age ciphertext. The recipient checks
the outer binding/signature, decrypts with its profile identity, validates the root,
then imports only keys from independently authorized grants. Pairing pins metadata to
the confirmed approver. Later profile imports additionally require the issuer to hold
a scope in that profile. Metadata contains no private keys.

Epoch reasons are `create`, `rotate`, `revoke`, `recovery`. `previous_key_id=null` is
allowed exactly for `create`. A successor must reference a valid predecessor whose
time is no later; unrooted chains are ignored. Current epoch is the valid leaf with
maximum `(created_at,key_id)`. Concurrent losing epochs stay readable. Brokers check
the current epoch when preparing and committing a write; stale pending writes are
refused and must be prepared again. Rotations publish wraps for the new epochs.

### Pairing transcript

All pairing kinds below have `format`, `signature` and these exact fields, and reside
under `pairing/{request_id}/`:

| Kind / filename | Fields |
| --- | --- |
| pair-commit / commit.json | request_id, joiner_signing_public, joiner_age_recipient, slot_recipients, commitment, issued_at |
| pair-approver / approver.json | request_id, approver_device_id, Na, commit_hash |
| pair-reveal / reveal.json | request_id, Nj |
| pair-result / result.json | request_id, approver_device_id, commit_hash, approver_hash, certificate_path, metadata_paths, grant_paths, slot_assignments |

The joiner self-signs commit and reveal; the approver signs approval and result.
`request_id` also identifies the new device. `Nj` and `Na` are 32 random bytes in hex.
`slot_recipients` is exactly eight distinct fresh age recipients. `slot_assignments`
maps granted profile IDs to distinct integer indices 0–7; only those local identities
survive successful join. The joiner publishes its signed profile-recipient records
before opening grants. The approver can stage grants to committed slots before these
records exist; opening still requires the published binding.

`commitment` is hex SHA256 of `b'AgentMesh/pairing-commitment/v1\x00'` plus typed encoding
of `{request_id,joiner_signing_public,joiner_age_recipient,slot_recipients,Nj}`.
Transcript hashes are SHA256 of canonical JSON wire bytes including signatures.
SAS is the first four hash bytes interpreted big-endian, modulo 1,000,000, zero-padded
to six decimal digits. Its hash input is `b'AgentMesh/pairing-sas/v1\x00'` plus typed
`{commit_hash,approver_hash,Nj,Na}`. QR payload is
`agentmesh-pair:v1:{request_id}:{commit_hash}`. Full QR fingerprints compare the commit
hash. The first-join approver fingerprint is SHA256 of typed
`{account:account_id,device:device_id,public:device_public_key_hex}`.

Requests expire 600,000 ms after commit; future timestamps, premature reveals,
commitment mismatch, replay and reuse abort. The approver persists transcript use
before approval and consumes it before comparing SAS. `certificate_path` points to
`pairing/{request_id}/certificate.json`, containing the existing device certificate
signed by the approver. The joiner itself publishes its self-signed device record.

### Recovery and deletion

A 32-byte random master secret is split using SLIP-39, group threshold 1, groups
`[(2,3)]`, empty passphrase, extendable=true, iteration exponent 1. HKDF-SHA256 with
no salt and 32-byte output derives separate Ed25519 and X25519 seeds using respectively
`b'AgentMesh/recovery_signing/v1\x00'` and `b'AgentMesh/recovery_age/v1\x00'`.
X25519 secrets use uppercase BIP-173 `AGE-SECRET-KEY-1…` encoding. The master secret
and shares are never part of broker state.

`recovery_key_id = SHA256(b'AgentMesh/recovery-key-id/v1\x00' + raw_signing_public)`
in hex. The first recovery record has `previous=null` and an account-root signature;
a replacement references the predecessor ID and is signed by its recovery signing key.
The current recovery record is the leaf with maximum `(issued_at,recovery_key_id)`.

Each profile recovery identity is independently random. `profile-recovery` is signed
by the named master recovery signing key; `age` encrypts the 32 raw profile recovery
age private-key bytes to that master's age recipient. Decryption must reproduce the
signed `age_recipient`. Root keyring grants target this profile identity.
`recovery-history` preserves root epochs and encrypted scope metadata across recovery
rotations: its plaintext has exactly `root`, `scopes`, `roots`, where roots is a list
of `{key_id,private}` with 32-byte private-key hex. It is encrypted to the profile
recovery identity and signed by a root-authorized device or by the named master
(`issuer_device="recovery"`). The latest valid ULID history per profile/master is used;
additional valid root grants supply later epochs. Descendants come through wraps.

Recovery use certifies a replacement device, publishes its profile recipients, imports
history, emits a signed recovery alert, rotates every accessible scope and creates
fresh master/profile recovery identities. It stages new recovery objects/history and
publishes the predecessor-signed replacement record last. Other devices retain their
certificates but need regrant for new epochs. Alerts are verified against their named
recovery record; forged alerts are ignored.

Profile states are `active`, `archived`, `deleted`, signed by a root-authorized device.
Latest valid ULID wins except that deletion is locally irreversible once seen. Archive
refuses writes while retaining reads. Delete requires two current shares to authorize
a new master, stages surviving-profile recovery objects, publishes the tombstone,
destroys the local profile age identity/epochs/snapshots, and publishes the replacement
recovery record. Peers destroy the same local capabilities on sync before importing
grants. No new recovery object is created for a deleted profile. Stored objects are
retained. Two old shares can still open the old profile recovery object and old grants;
this explicit exception requires destroying old shares or manually removing objects.

### Protected local state and presence

`agentmesh-broker-state-v2` adds `control` to the Phase 3 private state format and permits
`account_private_key=null`. Normal initialization/migration drops that private key.
Control contains generic `age_identity`, per-profile `profile_ages`, verified registry
cache, pairing-use pins, profile states/deleted IDs, alerts, presence windows and seen
agents. All private identities are sealed with the rest of the state; no derived
per-profile identities or plaintext recipient private keys are stored in the replica.

`keystore.sealed` has exactly `format="agentmesh-os-keystore-v1"`, `device_id`, `nonce`
(12-byte hex), `ciphertext` (hex). ChaCha20-Poly1305 uses a random 32-byte OS-stored K;
AAD is typed `{format,device_id}`. macOS uses service `agentmesh.profile-log`, account
=device ID; Windows uses target `agentmesh.profile-log/{device_id}`. Wrong K, altered
bytes or wrong device binding fails closed. Migration retains `keystore.json` until a
sealed read-back exactly matches the root-free state.

Presence classes: `device`, `agent`, `recovery`, `export`, `profile`, `cross_profile`,
`secret_read`, `first_access`. Protected windows default to 900,000 ms. Explicit window opening accepts integer
minutes from 1 through 15 and expires after `minutes * 60,000` ms. Console
confirmation requires TTY input of the fixed action word. Every trigger appends one
0600 JSONL record with exactly `ts`, `action_class`, `action`, `outcome`, `window_used`;
outcome is `approved` or `denied`. Recovery test/use and delete use one-shot approval,
which neither reads nor writes a presence window. Recovery test performs no other
writes. The daemon uses windows only and returns `presence_required` when missing.
Phase 4 `whoami` and CLI broker sync additionally expose `alerts`; existing Phase 3
responses retain their previous fields. No IPC framing or event format changes.

## Phase 5 migration bodies

Event envelope v1, signing domains, ciphertext and framing remain unchanged. Migrated
plaintext bodies use this exact convention (angle-bracket values describe types):

```text
{"kind": <item kind | "summary">, "content": <text>, "project": <str|null>,
 "task_ref": <str|null>, "confidence": <0..1>,
 "metadata": {
   "source": {"table": "memory_items"|"memory_summaries", "id": <int>,
              "scope": <source scope>, "scope_key": <str|null>,
              "memory_key": <str|null>, "version": <int|null>,
              "created_at": <source text|null>, "updated_at": <source text|null>},
   "row_hash": <64 lowercase hex>, "evidence_event_ids": [<int>, ...],
   "retired": false}}
```

Items preserve kind, content, project and confidence; task_ref is scope_key for task
scope, otherwise null. Evidence IDs are sorted memory_sources.event_id integers.
Summaries use kind summary, confidence 1.0, project=scope_key only for project scope,
and task_ref=scope_key only for task scope. Their integer IDs come from event_ids,
batch_event_ids and source_event_ids metadata lists; string native IDs and all other
metadata are dropped. Session summaries are skipped as not_recalled.

row_hash is SHA-256 over typed() of exactly `{table,id,kind,scope,scope_key,project,
content,status,confidence,memory_key,version,updated_at,evidence_event_ids,target_scope}`.
It exists only inside ciphertext. Payload created_at is the item's updated_at or
summary's created_at parsed as UTC (naive timestamps are UTC). Unparseable values use
import time and increment timestamp_fallback. More than five minutes in the future is
rejected. Source chronology is distinct from the event ULID used for historical
certificate validation and import ordering.

A retirement revises the current head, retaining source, setting retired=true and
content="". All recall modes hide retired heads. A scope move retires the old chain,
then creates a new root; there are no cross-scope revisions. The source ledger is
reconstructed from readable encrypted replica events, keyed by (table,id), with the
largest event ULID as head. No separate ledger file is authoritative.

### Import trust

Local grants add allow_import (bool, default false), included in the grant digest.
Private broker state control.import_agents is a sorted persistent set of agent IDs
that have held allow_import; saving grants and revoking agents preserves this set,
and broker revoke never prunes it. Local provenance requires current or historical
membership. Foreign provenance requires the same-account agent/device chain accepted
by ordinary historical event verification, including revocation at the event ULID time.
There is no replicated import authorization record. A compromised paired device can
forge or retire migrated rows in scopes whose keys it holds. This device-level trust
was approved on 2026-10-20; local broker remember/revise still reject reserved metadata
keys source, row_hash, evidence_event_ids and retired with invalid_request.

### Broker wire additions

`{"op":"import","source":{"table":TABLE,"id":INT},"row_hash":HEX,
"created_at":MILLISECONDS,"scope":NAME,"body":BODY_OR_NULL}` has exactly these fields.
It requires current allow_import and a write grant for the destination and any old
scope being retired. Same hash/live scope returns `{"outcome":"unchanged","id":ULID}`.
Null body for an unknown source returns unchanged with id=null. Otherwise it returns
`{"stage":"sign","unsigned":ENVELOPE}`; the existing sign request returns
`{"id":ULID,"label":LABEL,"redactions":COUNT,"outcome":"created"|"revised"|"retired"}`.
For a scope move, sign of the retirement returns another stage=sign response for the
new root; signing that completes the operation. Interrupted moves resume from the
persisted retirement. Secret detection and audit policy are unchanged; audit op is
import and contains no source IDs, hashes or body text.

A pending import reserves (table,id) for its session. A concurrent import returns
`{"error":"busy"}`. Busy is a new fixed error code; clients retry with bounded delay.
Disconnect or any non-sign operation releases the reservation. Existing error codes
and two-step signature checks remain in force.

`{"op":"import_ledger"}` also requires allow_import. Response:
`{"entries":[{"source":{"table":TABLE,"id":INT},"event":ULID,"chain":[ULID,...],
"scope":NAME,"status":"created"|"revised"|"retired","reason":null}],
"foreign_authority":BOOL}`. Entries are filtered to caller write scopes and contain
no bodies or hashes. foreign_authority reports observed foreign-device migration
provenance, allowing the migrator to require an explicit --take-over.

Recall accepts optional `"compat":true` (bool, default false). Compat durable selection
includes globals; all rows when neither project nor task is selected; project/scope_key
matches; and task scope_key matches, with ordinary text matching. Summaries require a
matching project or task source scope and are included without a text-match requirement.
Results retain broker fields and add source_type (durable|summary), source_ref
({table,id} or null), source (original source fields or null), scope_key and
evidence_event_ids. These support the local bridge's existing SQLite row shape and
local evidence expansion. candidate_counts.summaries counts selected summaries.
Non-migrated events have source_ref=null and remain durable. Without compat the old
result field set is unchanged. Index schema version 5 adds source_table, source_scope,
scope_key and retired; older snapshots rebuild.

## Phase 6 records

Phase 1–5 encodings are unchanged. These records use the Phase 4 per-kind signing
rule `AgentMesh/<kind>/Ed25519/v1\x00` plus typed unsigned fields, strict exact JSON
field sets, hex IDs, integer millisecond times, and canonical base64url ciphertext.
Every row below also has `format="agentmesh-<kind>-v1"` and `signature`.

| Kind | Fields | Path |
| --- | --- | --- |
| gateway | gateway_public_key, origin, issued_at, issuer_device | registry/gateway/{gateway_key_id}.json |
| outbox-key | device_id, outbox_public_key, issued_at | registry/outbox-keys/{device_id}.json |
| connector-policy | app_id, display_name_hash, oauth_client_id_hash, read, remember, max_label, allow_cross_profile, limits, state, issuer_device, issued_at | connectors/{app_id}/policy/{ulid}.json |
| connector-secret-window | app_id, not_before, not_after, issuer_device | connectors/{app_id}/secret/{ulid}.json |
| outbox-item | outbox_id, app_id, created_at, recipients, gateway_key_id | outbox/{outbox_id}.json |

`gateway_key_id` is hex SHA256 of `b'AgentMesh/gateway-key-id/v1\x00'` followed
by the raw Ed25519 public key. Origin is an HTTPS origin without path, credentials,
query or fragment. Gateway records are device-signed; outbox key records are
self-signed by the device. Outbox private X25519 keys remain in protected device
state. Outbox items use the gateway signing key.

Policy `read` lists `{profile,scopes}` with opaque IDs; null scopes denotes the
profile root and its descendants. `remember` is `{profile,scope}` or null.
`max_label` is general/internal; cross-profile is false. Limits are exact integers:
recall_limit_max 1–20, requests_per_minute 1–1000. State is active/revoked. Display
and OAuth client names are UTF-8 SHA256 hashes. Policy selection orders valid
records by signed issued_at then path; future records are ignored. This prevents
an old policy copied under a newer path from superseding a later signed policy.

Issuer authority follows the reviewer's scope-bounded decision of 2026-10-21.
A granting/widening issuer must be non-revoked at issued_at, have a valid read
grant, and hold every delegated scope or an ancestor (profile root for null
scopes). This includes the remember target. Narrowing/revocation may be signed
by any non-revoked reading device. Reactivating a revoked policy is widening.
Invalid records are ignored. Secret windows require scope authority over all
read entries of the policy current at not_before, a currently non-revoked issuer,
and a duration greater than zero and no longer than 3,600,000 ms. Expiry is
exclusive. Local record creation requires console presence; the signature is the
trusted device's attestation to that local check, as in Phase 4.

Outbox recipients are distinct `{device_id,ciphertext}` objects. HPKE uses the
v1 suite in base mode, empty AAD, and info
`b'AgentMesh/outbox/v1\x00' + typed({outbox_id,app_id,device_id})`. Ciphertext is
32-byte encapsulated key followed by AEAD ciphertext/tag. Plaintext is exactly
`{body,tags,task,label}`. Recipient keys are public X25519 keys, not scope keys.

### Bridge and broker additions

Gateway HTTP requests use POST `/bridge` and exactly `{tool,arguments}` as the JSON
body. Headers `X-Agentmesh-Key`, `X-Agentmesh-App`, `X-Agentmesh-Ts`,
`X-Agentmesh-Nonce`, and `X-Agentmesh-Signature` carry the public key ID, app ID,
integer decimal milliseconds, 16 random bytes in lowercase hex and signature.
The signed input is `b'AgentMesh/gateway-request/Ed25519/v1\x00'` plus typed
`{method,path,body_sha256,ts,nonce,app_id}`. SHA256 hashes the exact body bytes.
Clock tolerance is ±60,000 ms; nonce retention is 600,000 ms. The current gateway
record is the valid maximum `(issued_at,gateway_key_id)`. Transport requires the
current key; historical queued items validate against the key current at their
signed `created_at`. Historical signer timestamps retain Phase 4's stated limits.

Signed device administration requests have format `agentmesh-connector-admin-v1`,
exact fields `device_id,ts,nonce,action,payload,signature`, and the corresponding
Phase 4 domain. Actions are pending (`{code_hash}`), approve
(`{code_hash,app_id,policy_ref}`), and revoke (`{app_id,policy_ref}`). Nonces, clocks
and device chains are checked. Approval state in OAuth KV retains the signed
approval, bound to the pending request's creation and expiration times. Only the
hash of the six-digit code is persisted. A random browser cookie is likewise
stored only by its hash. R2 conditional creates serialize approval completion,
admin replay claims and per-app minute-rate slots. OAuth token/code storage and
refresh rotation are implemented by the pinned provider, not a custom protocol.

Local grants add `max_label` (default secret for pre-existing agents),
`connector_app_id` (default null) and `secret_until` (default zero). Connector
secret access additionally requires an active expiry and allow_secret; ordinary
agents retain their Phase 4 presence behavior. Per-app identities and the bridge
control agent ID are pinned in protected local state. `connector_window` accepts
only `{op,app_id}` from that bridge control identity; it validates the signed
window and active policy before setting the app grant's expiry. No ordinary app
agent can open a window. Per-app indexes filter labels before head selection,
counts or ranks. Changing effective access invalidates the index digest.

Remember accepts optional `outbox_id` only for connector agents. It must be a
canonical ULID; the broker reserves encrypted body metadata `outbox_id` and
`connector_app_id`, refusing caller-supplied values. Existing local outbox writes
return the prior ID. Independently drained events retain their ordinary v1
signatures and ciphertext; indexes collapse their shared outbox ID to the lowest
ULID after sync. Those private index fields never appear in recall results.

### Inbox metadata for already paired devices

An additive `agentmesh-connector-inbox-v1` record has exactly `format,signature,
profile,scope,recipient_device,issuer_device,issued_at,age` at
`connectors/inboxes/{profile}/{scope}/{recipient_device}.json`. It uses the
`connector-inbox` signing domain. The age payload contains the existing Scope
fields, with label name, for exactly the direct profile child named `inbox`.
It contains no keys. This is necessary because Phase 4's initial profile metadata
is immutable and does not update an already paired device's scope tree.

Publishing requires a profile-root-authorized issuer and an authorized recipient
with a signed per-profile age binding. Import verifies the root grant, signature,
recipient and path before decrypting, then accepts only the bound profile/scope,
direct root parent, project kind and inbox name. It never replaces existing scope
metadata or creates a duplicate local name. Normal authenticated root wraps and
epoch records supply keys; the metadata alone grants no decryption capability.
