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

The owner is always included in read permission. Evidence defaults to read;
retain/export default to empty and must be subsets of read. Local embedding
requires explicit `embed: ["local"]`; no remote-provider egress is implemented.
A historical permissive revision cannot bypass a stricter current policy.
Selective retention records an exact origin revision and personal content, then
requires review when that origin changes, disappears, conflicts or is revoked.
No automatic rewrite, erasure or refresh of personal memory is promised.

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

Projection rebuilds are not knowledge writes or index synchronization. HNSW is
optional, ephemeral and reproducibly seeded; approximate retrieval does not
promise exact global ordering or quality. Context budgeting includes expanded
relations and returns truncation/partial-profile information.
