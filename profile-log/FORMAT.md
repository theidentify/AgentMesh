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
