# AgentMesh profile record and exchange v1

This is the implemented JSON contract, not a claim of RDF/JSON-LD/SHACL
conformance. SQLite table names, integer row IDs and filesystem paths are not
knowledge identity. Authority belongs to the named profile, not the receiver's
personal-memory database.

## Local layout

```text
node-root/
  profiles/<profile>/
    sources/                    private immutable source text
    knowledge/knowledge.sqlite3 authoritative revisions and heads
    indexes/                    rebuildable local projections
    state/profile.json          logical profile marker
    exports/                    explicitly authorized immutable bundles
  mirrors/<recipient>/<issuer>/
    knowledge/knowledge.sqlite3 imported owner ledger and receipts
    indexes/                    authorized receiver-partitioned projections
    state/profile.json          issuer identity, not a new local authority
```

Do not synchronize live SQLite/WAL/SHM files or private sources. A separate
private exchange may carry authorized bundle JSON files via manual copy or
Syncthing; the prototype does not configure Syncthing, start a transport worker,
or prove delivery to a peer that is offline. Keep channel keys outside exchange.

## Revision record

All fields below are mandatory in an exported record; extra fields are rejected:

| Field | Meaning |
| --- | --- |
| `id` | Stable `urn:uuid:` knowledge identity |
| `owner` | Lowercase validated logical profile ID |
| `revision` | `sha256:` digest of the canonical record without this field |
| `parents` | Immutable predecessor digests; roots use an empty list |
| `content` | Bounded nonempty text, treated as data |
| `kind` | fact, requirement, decision, preference, observation or derived |
| `project` | Bounded application project label |
| `status` | active or revoked |
| `verification` | unverified; never an automatic claim of truth |
| `evidence` | Inline source hash, kind, locator, quote and availability |
| `derived_from` | Exact owner/ID/revision provenance references |
| `policy` | Explicit read/evidence/retain/export/embed permissions |

Canonical encoding is UTF-8 JSON, sorted keys, compact separators, Unicode
preserved and non-finite numbers forbidden. This is a Python-defined v1 encoding,
not a claim of RFC 8785 canonicalization. Revisions are immutable; a correction
creates a child and requires an expected head. Concurrent heads remain conflicts.
The public read response also adds derived review/redaction metadata; those
presentation fields are not part of the signed canonical revision.

The owner is always included in read permission. Evidence defaults to the owner
only; granting content read does not implicitly disclose quotes, locators or
source hashes to another profile. Retain/export default to empty and must be
subsets of read. Local embedding
requires explicit `embed: ["local"]`; no remote-provider egress is implemented.
A historical permissive revision cannot bypass a stricter current policy.
Selective retention records an exact origin revision and personal content, then
requires review when that origin changes, disappears, conflicts or is revoked.
No automatic rewrite, erasure or refresh of personal memory is promised.

Defaults apply when normalizing newly submitted policies, not as an implicit
migration of stored records. Earlier prototype workspaces may already contain explicit
normalized evidence grants inherited from read. Recreate disposable workspaces,
or have the owner explicitly revise their policies; this change does not erase
historical grants or silently rewrite immutable revisions.

Source persistence follows ownership and expected-revision validation. Ordinary
write failures roll back the ledger and remove only source files newly created
by that write. Existing sources remain untouched. SQLite plus source files are
not a crash-atomic two-phase commit, and existing orphan files are not swept.

## Scoped envelope

`export_bundle(api, recipient, ids, key=..., include_history=True)` exports only
objects approved for that recipient. Current roots, selected ancestry and required
dependencies must be authorized; otherwise the entire export fails closed.
Cross-owner derivations are refused rather than pretending one issuer owns all
records. This bounds v1; multi-issuer closure is not implemented.

The envelope fields are `format`, `issuer`, `recipient`, `records`, `bundle_id`
and `signature`. `format: agentmesh-knowledge-bundle-v1` embeds the protocol
version; there is no separate version/heads field. Heads are derived from the
record ancestry. Read, evidence **and** export must permit the recipient.
The bundle ID hashes the canonical body
before bundle ID/signature are attached; the HMAC-SHA256 signature covers that
ID-bearing body. No SQL packets, source files, live indexes, vectors, raw
transcripts or credentials are sent.

`apply_bundle(store, bundle, recipient=..., trusted_issuer=..., key=...)` pins the
channel independently of payload declarations, validates exact schemas and
revision hashes, and commits a complete packet atomically. Import checks include
identity, authorization, dependency/ancestry consistency and bounded field sizes.
HMAC provides shared-key channel authentication, not asymmetric provenance:
**every holder of the key can forge a message**. Key provisioning, rotations and
network identity are not provided by this prototype.

| Result | Meaning |
| --- | --- |
| `applied` | Records and durable receipt committed in one transaction |
| `pending` | Valid authenticated packet retained, dependencies absent; not applied |
| `conflict` | Packet committed but divergent owner heads remain visible |
| `duplicate` | Previously applied/conflicted receipt found; `original_status` retained |

A bundle is bounded to 128 records and 1 MiB. Persisted pending envelopes are
bounded to 128 packets and 1 MiB total. Invalid signatures or schema errors do
not create knowledge or receipts. Pending retry runs to a fixed point.

Authenticated pending records conservatively block foreign API reads, evidence,
retention and search for their object IDs until the packet commits. Blocks persist
through restart and are removed transactionally with the corresponding pending
packet. Owner/admin inspection stays available. Objects with pending state cannot
be re-exported. This includes an out-of-order revocation: an older readable head
is not exposed while its revocation waits for missing ancestry.

## Opt-in signed semantic v2

The revision-record schema and ACL/dependency/pending rules remain v1. Signed
mode changes the envelope `format` to `agentmesh-knowledge-bundle-v2` and replaces
the HMAC hex string with this exact `signature` object:

```json
{"format":"agentmesh-semantic-signature-v2","encoding":"agentmesh-typed-v1","issuer_identity":{"group":"<canonical UUID>","node":"mac","sender":"<canonical UUID>","key_id":"<full lowercase SHA256 fingerprint>"},"recipient_identity":{"group":"<same group UUID>","node":"linux","sender":"<canonical UUID>","key_id":"<full lowercase SHA256 fingerprint>"},"value":"<128 lowercase hex characters: 64-byte Ed25519 signature>"}
```

No additional signature fields are accepted. Let `unsigned` be the entire
ID-bearing envelope without `signature`, and `metadata` be the signature object
without `value`. The signed message is exactly:

```python
b'AgentMesh/semantic-knowledge/Ed25519/v2\x00' + backend.typed({
    'envelope': unsigned,
    'authentication': metadata,
})
```

The displayed `\x00` denotes the single NUL byte in the Python domain literal.
The injective bounded `backend.typed` encoding is **not** RFC 8785/JCS. The
existing canonical JSON bundle/revision hashes are unchanged. Both logical
issuer/recipient and every identity/encoding/version field are signed. This is a
distinct semantic domain, not a SQL change packet or a wrapper around backend SQL
`Security.sign/verify`. Neither signatures nor HMAC encrypt data.

Construct an operator-trusted adapter:

```python
from agentmesh_memory.auth import SignedChannel, load_backend
channel = SignedChannel(
    backend=load_backend('/absolute/trusted/signed_packets.py'),
    security_dir='/private/receiver-security', principal='beta',
    peer_profile='alpha', peer_fingerprint=alpha_full_fingerprint,
    peer_group=group_uuid, peer_node='mac', peer_sender=alpha_sender_uuid,
)
apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha',
             authenticator=channel)
retry_pending(mirror, recipient='beta', trusted_issuer='alpha',
              authenticator=channel)
```

Publisher adapters use `principal='alpha'` and the explicitly pinned beta profile
and identity. `export_bundle(..., authenticator=publisher_channel)` uses the same
hook. Supplying both `key` and `authenticator` is an error. HMAC remains explicit
`key=...`/`--key-file` v1 compatibility, never an automatic signed fallback.
The trusted-local authenticator hook exposes `format`, canonical `scope`, logical
`principal`/`peer_profile`, `sign(unsigned)`, `verify(unsigned, signature)` and
`reauthenticate()`; incoming packets never instantiate an authenticator or select
backend code. Backend absence and crypto absence fail closed in signed mode.

`SignedChannel` reuses backend `Security.public`, its actual Ed25519 `.key`,
`check_self`, validated `read_trust`, `typed` and `crypto`. Both ends require the
exact peer fingerprint/group/node/sender in current nonrevoked trust; the group
must also match the local identity. Logical profile ownership is an independent
operator binding, **not inferred from machine-key approval**. No second key
provisioning/pairing system or network membership service is introduced.

`exchange_channels` stores canonical mode/full profile-and-identity bindings in
SQLite, keyed by issuer/recipient/direction. Export pins its selected publisher
channel inside the same `BEGIN IMMEDIATE` transaction as its ACL/dependency
snapshot and signature generation. A signed receiver ledger stays isolated to one recipient and full
channel scope, including through old API calls with another recipient. Mode or
scope changes, even approved key rotation, are refused. Signed activation refuses
unbound existing receipts/pending packets rather than automatically converting
or abandoning them. Use fresh disposable stores; rotation/rebinding/migration
are not implemented. Raw maintenance SQL remains privileged and can bypass pins.

Every import authenticates before validation and again inside `BEGIN IMMEDIATE`,
before mutation or returning a replay receipt. Pending retries use the explicitly
supplied selected channel, reauthenticate even an empty queue and verify each
persisted envelope again; a revoked-key duplicate is not a success. SQLite
serializes canonical knowledge/mode writers, but external JSON trust updates are
**not atomically locked by SQLite**: a concurrent update after the trust read
cannot be retroactively fenced. Coordinated cross-file trust locking is not
implemented; this is not a claim of atomic trust revocation with knowledge commit.

Node-key revocation blocks future authentication, including duplicate receipts
and pending retries. It does not erase imports, revoke their knowledge ACLs or
make historical quotes independently true. Knowledge policy/object revocation
remains a separate semantic operation. If a key is revoked with a packet pending,
its conservative read block remains until an explicit maintenance decision; the
prototype does not silently discard the pending packet. Profile directories are
not an OS-user sandbox; no production transport, two-host identity service or
wizard activation is provided by this integration.

## Retrieval projections

Keyword search uses current authorized objects and SQLite FTS5/BM25. Semantic
ranking uses the same authorization boundary and checks revision/content hashes,
provider/model/revision/dimension/metric metadata, then uses cosine exact ranking
or real HNSW. Hybrid fuses ranks with RRF k=60, not incomparable raw scores.
Relations are knowledge provenance, not HNSW navigation edges.

Owner projection is `indexes/vectors.sqlite3`. A foreign reader's explicitly
rebuilt projection is `indexes/vectors-<principal>.sqlite3`; it contains only that
reader's authorized, locally-embeddable current objects and does not overwrite
the owner's projection. Retrieval prefers the reader projection when present;
otherwise an available owner projection may serve authorized candidate IDs.
A stale or incompatible projection fails explicitly and must be rebuilt.

Rebuild revalidates revision, active status, read authorization and embedding
approval immediately before every batch, inside a SQLite `BEGIN IMMEDIATE`
reservation on the source authority/mirror. Canonical policy/revision/pending
writers cannot commit while that batch is being disclosed to the provider.
Writers may wait or time out; already authorized in-flight disclosure cannot be
undone. Once a policy change commits, a later stale/unapproved batch aborts.
All selected records are revalidated under the same reservation before projection
publication, so a changed final batch cannot publish a stale rebuild. API instances
sharing the canonical database use the same SQLite fence; direct filesystem or
noncanonical writers are outside the trust boundary.

The Ollama adapter checks the installed tag digest before and after each embedding
request and rejects a changed digest without accepting vectors or pinning their
dimension. The response contains no independently verifiable model digest. These
checks do **not** establish immutable model identity under an ABA tag swap
(A -> B -> A between checks) or a dishonest provider. Model administration must
keep the tag frozen for the duration of inference/indexing. Strict adversarial
revision binding needs an immutable server-side reference or coordinated model
administration; neither is claimed here.

Projection rebuilds are not knowledge writes or index synchronization. HNSW is
optional, ephemeral and reproducibly seeded; approximate retrieval does not
promise exact global ordering or quality. Context budgeting includes expanded
relations and returns truncation/partial-profile information.
