<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../memory-sync/assets/agentmesh-logo-dark.svg">
  <img src="../memory-sync/assets/agentmesh-logo.svg" alt="AgentMesh" width="384" height="80">
</picture>

# Profile-owned memory prototype

An opt-in standalone prototype. It does **not** migrate production, activate a
Hermes memory provider, create profiles in Hermes, install services, or alter
`memory-sync/`. Keep every runtime workspace outside the checkout.

## What works

- Each logical profile owns a SQLite authority and private sources.
- Stable knowledge IDs, immutable hashed revisions, optimistic corrections,
  revocation, inline source evidence and exact-revision provenance.
- Trusted-principal API with distinct read/evidence/retain/export permissions.
- Selective personal retention; changed, revoked, missing or pending origins
  mark derived memories `review_required`, without rewriting personal content.
- Keyword BM25, semantic exact/HNSW and hybrid reciprocal-rank fusion (k=60).
  Profile defaults and per-query overrides are independent of relation expansion.
- Provider-neutral embeddings; existing loopback Ollama models are supported.
  Model revision, dimensions and cosine space must match the explicit projection.
- Authorized JSON knowledge bundles, explicit HMAC or opt-in Ed25519 channels, transactional receipts,
  idempotent replay, bounded durable pending packets and visible ancestry conflicts.
- Receiver mirrors are separate from local authorities. Their indexes are rebuilt
  locally from authorized current objects; sources and vectors are not exchanged.

See [record and exchange contract](CONTRACT.md) and
[dependency and license boundaries](DEPENDENCIES.md).

## Prerequisites

Use Python 3.11+ with SQLite FTS5. Keyword retrieval and exact vector ranking use
the standard library. HNSW needs the optional pinned dependencies; a C++ build
toolchain may be needed if compatible wheels are unavailable. No script installs
OS packages or downloads a model.

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r profile-memory/requirements-dev.txt
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.
The core code is portable, but native Windows/Linux execution of **this new
prototype** has not yet been verified.

## Run a fixture-only demonstration

Choose a **new** private root each time. The demo refuses an existing directory.
It exercises serialized immutable bundles, isolated source/receiver stores,
retention, out-of-order corrections, replay, pending read guards, revocation and
conflicts. It uses an ephemeral random channel key and never prints that key.

```sh
.venv/bin/python profile-memory/agentmesh-memory.py \
  --root "$HOME/.agentmesh-profile-demo-1" demo
```

With an already-installed Ollama `bge-m3:latest` at loopback, add:

```sh
.venv/bin/python profile-memory/agentmesh-memory.py \
  --root "$HOME/.agentmesh-profile-demo-2" demo --with-embeddings
```

This exercises keyword/exact, semantic/exact, semantic/HNSW, hybrid/exact and
hybrid/HNSW against actual model output. Fixture correctness is not a production
accuracy guarantee, an ANN scalability benchmark, or a native remote-peer test.

## CLI

```sh
python profile-memory/agentmesh-memory.py --help
python profile-memory/agentmesh-memory.py --root /private/node-a --profile alpha init
python profile-memory/agentmesh-memory.py --root /private/node-b --profile beta init
python profile-memory/agentmesh-memory.py --root /private/node-a --profile alpha \
  remember --input /private/request.json
```

Example non-sensitive request (foreign permissions default to denied;
`evidence` defaults to the owner only, never to all readers; read always includes
the owner):

```json
{"content":"The provider must be configurable.","kind":"requirement","project":"demo","policy":{"read":["alpha","beta"],"evidence":["alpha","beta"],"retain":["beta"],"export":["beta"],"embed":["local"]}}
```

Use the actual returned ID and revision in later commands. `get`, `evidence`,
`related`, `retain`, `revise` and `revoke` have dedicated `--help`.
A revise request can include new `source`/`quote`; otherwise stale evidence is
cleared rather than relabeled as evidence for corrected content.

```sh
python profile-memory/agentmesh-memory.py --root /private/node-a --profile alpha \
  keygen --output /private/alpha-beta.key
python profile-memory/agentmesh-memory.py --root /private/node-a --profile alpha \
  export --recipient beta --id "$KNOWLEDGE_ID" --key-file /private/alpha-beta.key \
  --output /private/exchange/bundle.json
python profile-memory/agentmesh-memory.py --root /private/node-b --profile beta \
  apply --issuer alpha --key-file /private/alpha-beta.key --input /private/exchange/bundle.json
python profile-memory/agentmesh-memory.py --root /private/node-b --profile beta \
  retry --issuer alpha --key-file /private/alpha-beta.key
python profile-memory/agentmesh-memory.py --root /private/node-b --profile beta \
  status --issuer alpha
```

Provision keys through a trusted **private** channel, never Git, chat, or the
public bundle folder. File creation never overwrites an existing key or bundle.
The CLI pins recipient to the selected profile and requires an explicit trusted
issuer. Self-channel imports and shadowing an authority with a mirror are refused.
Full history is the default. `export --no-history` is only for explicit delta
exercises: missing ancestry stays pending until dependencies arrive and retry
commits it. Pending packets are not application ACKs.

### Experimental Ed25519 semantic channels

Signed mode is opt-in and depends on a separately supplied **trusted local**
identity backend (see [dependencies](DEPENDENCIES.md)). It reuses the existing
`Security`, `check_self`, `read_trust`, `typed` and `crypto` primitives, not the
SQL packet signing protocol. Compatibility was verified against committed
signing/wizard revision `b864e26a8540807e7dc61648154004d65183e171` (see dependencies).
Final upstream release/wizard readiness and real two-host operator fingerprint
approval remain activation gates. The prototype creates no production identities
or trust.

Start with fresh disposable source and mirror workspaces for this trial. Have
both fixture identities approved through the existing backend using full
fingerprints and explicit group/node/sender bindings. This CLI does not pair,
approve, discover peers or infer logical profile ownership from an approved key.
Supply the trusted local profile with `--profile` and every peer binding explicitly:

```sh
python profile-memory/agentmesh-memory.py --root /private/node-a --profile alpha \
  export --recipient beta --id "$KNOWLEDGE_ID" --output /private/exchange/signed.json \
  --security-dir /private/node-a-security --signed-backend /trusted/signed_packets.py \
  --peer-profile beta --peer-fingerprint "$BETA_FULL_FINGERPRINT" \
  --peer-group "$GROUP_UUID" --peer-node linux --peer-sender "$BETA_SENDER_UUID"
python profile-memory/agentmesh-memory.py --root /private/node-b --profile beta \
  apply --issuer alpha --input /private/exchange/signed.json \
  --security-dir /private/node-b-security --signed-backend /trusted/signed_packets.py \
  --peer-profile alpha --peer-fingerprint "$ALPHA_FULL_FINGERPRINT" \
  --peer-group "$GROUP_UUID" --peer-node mac --peer-sender "$ALPHA_SENDER_UUID"
```

`retry --issuer alpha` requires the same receiver-side signed flags. Do not pass
`--key-file` with signed mode. Incomplete options, unavailable crypto/backend,
wrong scope and unknown/revoked keys fail closed, without HMAC fallback. The CLI
never loads code selected by a bundle; `--signed-backend` executes operator-chosen
local Python and must be a trusted absolute file. Keep identity/trust directories
outside the exchange. They remain backend-managed and owner-only.

Mode and complete identity/profile scope are durably pinned in the canonical
SQLite store. A signed mirror cannot accept a later HMAC import/retry, even via
the old API or another recipient binding; publisher exports are also pinned per
recipient. Existing incompatible modes or unbound receipts/pending packets are
refused, not converted. Automated migration, key rotation and rebinding are not
implemented. `get` and `evidence` continue using knowledge ACLs; they do not need
a signing key. Node-key revocation blocks new imports, pending retry and duplicate
receipt authentication, **not** access to already imported knowledge. Knowledge
policy/object revocation is separate. Signatures do not encrypt content, prove
semantic truth or provide OS-user sandboxing/network identity.

API callers inject `SignedChannel(...)` as `authenticator=` instead of `key=`
into `export_bundle`, `apply_bundle` and `retry_pending`; see the exact signature
contract and JSON trust concurrency limitation in [CONTRACT.md](CONTRACT.md).

Receiver-owned projection and retrieval defaults:

```sh
python profile-memory/agentmesh-memory.py --root /private/node-b --profile beta index --issuer alpha
python profile-memory/agentmesh-memory.py --root /private/node-b --profile beta configure --mode hybrid --engine hnsw
python profile-memory/agentmesh-memory.py --root /private/node-b --profile beta search "configurable provider" --owner alpha
python profile-memory/agentmesh-memory.py --root /private/node-b --profile beta search "provider" --mode keyword
```

An unavailable/mismatched/stale semantic index produces an explicit error, never
a silent keyword fallback. Reindex after revisions or changes to the embedding
model. Relation expansion is optional and shares the result context budget.
Successful commands emit one JSON value on stdout. Diagnostics use stderr;
help is English ASCII with the `[o-A-o]` terminal mark, not a progress banner.

## API and trust boundary

Bind `MemoryAPI(stores, principal='beta')` from a **trusted application**. Do not
accept principal strings from a remote request body. `ProfileStore` and raw SQL
are privileged maintenance interfaces, not authorization boundaries. The local
CLI is for the operator, not an authenticated network service. Folder separation
is not a same-OS-user sandbox; an OS user who can read another database or key
can bypass the API. HMAC key holders can forge channel messages.

Before embedding or ranking, candidate IDs are filtered through current API
permissions. Historical permissions cannot override a current revocation or
read/evidence restriction. Authenticated incomplete packets conservatively block
foreign reads for their object IDs, including old revisions, until commit;
owner/admin inspection remains available. Revocation cannot erase copies already
retained or previously received by another party.

The owner-only evidence default does not rewrite grants already stored in older
prototype databases. Recreate disposable demo roots or explicitly restrict the
policy through the owner API before reusing earlier trial data.

Index rebuilds fence each disclosure batch and publication with a SQLite writer
reservation, rechecking current authorization, revisions and embedding approval.
Policy/import writes can wait or time out while an already authorized batch is in
flight; committed withdrawals stop later batches, not content already disclosed.
Keep the Ollama tag frozen during inference/indexing. Pre/post digest checks reject
ordinary tag changes but cannot prove immutable identity against an ABA tag swap;
see the precise limitations in [the contract](CONTRACT.md).

Imported evidence is an inline quote and source digest, not proof that the
receiver possesses the original source file. Content is unverified data, not an
instruction to agents. Automatic conflict resolution, production activation,
key rotation, membership discovery, arbitrary cross-owner
export closure, multimodal extraction, RDF/JSON-LD/SHACL conformance and native
Hermes write-provider integration remain separate follow-ups.

## Verification

```sh
python -m pytest -q memory-sync insight profile-memory/tests
```

Tests exercise real SQLite, loopback HTTP adapter fixtures and real `hnswlib`.
The labeled `agentmesh_memory.benchmark.run(root, provider)` corpus is separate
from the workflow demo. Synthetic fixture vectors prove mechanics only. Keep
benchmark results, fixture databases, source files and envelopes private.
