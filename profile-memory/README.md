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
- Authorized JSON knowledge bundles, scoped HMAC channels, transactional receipts,
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

Example non-sensitive request (all unspecified permissions fail closed except
`evidence`, which defaults to `read`; read always includes the owner):

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

Imported evidence is an inline quote and source digest, not proof that the
receiver possesses the original source file. Content is unverified data, not an
instruction to agents. Automatic conflict resolution, production activation,
key rotation/asymmetric signatures, membership discovery, arbitrary cross-owner
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
