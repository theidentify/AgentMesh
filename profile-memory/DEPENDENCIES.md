# Dependency and licensing boundaries

The profile prototype does not vendor dependency implementations, compiled
libraries, Ollama itself or model weights. Installing a dependency is separate
from checking out the repository. The optional pinned development/HNSW stack is:

| Component | Verified package | Installed license metadata/text |
| --- | --- | --- |
| pytest | 8.4.2 | MIT; test dependency only |
| hnswlib | 0.8.0 | Apache License 2.0; optional real HNSW engine |
| NumPy | 1.26.4 | BSD-3-Clause for NumPy, plus distribution-specific bundled notices |

These descriptions were checked against the installed distribution license files,
not inferred from a package name. They are not a legal compatibility opinion.
The installed NumPy wheel's notices also list OpenBLAS/LAPACK, GCC runtime
library with its exception, and libquadmath terms. Do not describe every bundled
binary as simply BSD or omit those notices from a redistributed package.

The standard-library keyword/exact paths do not import NumPy or hnswlib.
HNSW requires them explicitly; absence produces an availability error rather
than pretending exact ranking is HNSW. No vendor code was copied into this module.
If distributing a bundled installer or binary later, review and preserve the
actual installed artifact's licenses, notices and applicable distribution
requirements, not just this summary table. That packaging is not delivered here.

`bge-m3:latest` is only a configurable name for a model already present in the
operator's local Ollama instance. No weights are included or downloaded by this
prototype. The adapter checks the installed model digest before and after embed
under an operator-frozen tag; those checks are not immutable identity proof against
an ABA tag swap and do not establish model-license clearance. Check model terms
separately before redistributing it.

## Optional experimental signing dependency

Signed semantic exchange additionally requires `cryptography==46.0.7` and an
operator-supplied absolute trusted local identity backend module. The backend is
not vendored or copied into `profile-memory`; it must expose `Security(directory)`
with `.public`, `.key`, `.check_self()`, plus `read_trust`, `typed` and `crypto`.
`.key` must be the actual Ed25519 private key. The adapter does not call the
backend's SQL packet `sign`/`verify` or use its SQL domain/format.

```sh
uv venv --python 3.11 /private/semantic-signing-venv
uv pip install --python /private/semantic-signing-venv/bin/python \
  -r profile-memory/requirements-dev.txt cryptography==46.0.7
AGENTMESH_SIGNED_BACKEND=/absolute/trusted/signed_packets.py \
  /private/semantic-signing-venv/bin/python -m pytest -q \
  profile-memory/tests/test_auth.py profile-memory/tests/test_signed_exchange.py
```

`AGENTMESH_SIGNED_BACKEND` is a test dependency selection only; the CLI requires
explicit `--signed-backend`. Without the environment variable, backend integration
tests skip as optional; with it, missing/incompatible backend or crypto fails the
tests, never skips. Standard-library HMAC paths do not import cryptography.

The adapter was first exercised against a private provisional snapshot and then
independently verified against committed upstream signing/wizard revision
`b864e26a8540807e7dc61648154004d65183e171`; its `memory-sync/signed_packets.py`
SHA256 is `0a6a6977c0bd9134e5bf8d7f060d55aa7780d69b416f4522121a8574e14ff273`.
The committed backend is byte-identical to that snapshot. Combined tests executed
with the actual committed module and cryptography, not a fabricated backend.
This is compatibility evidence for that revision, not production activation,
upstream final release/wizard readiness or real two-host operator trust approval.
Reverify any later upstream contract before activation.

Installed signing-environment metadata reports `cryptography 46.0.7` as
`Apache-2.0 OR BSD-3-Clause`, `cffi 2.1.1` as `MIT-0`, and `pycparser 3.1` as
`BSD-3-Clause`; the installed distributions include their license files.
Review actual wheel/bundled/transitive notices before redistribution; no binary
bundle or legal compatibility clearance is delivered by this integration.

No project-wide license is assigned by this change. Existing brand artwork
provenance does not settle AgentMesh trademark availability or relicense other
code. Project license selection, trademark clearance and commercial-distribution
review remain owner decisions; this document makes no clearance claim.
