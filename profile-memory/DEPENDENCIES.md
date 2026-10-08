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
prototype. A model digest pins the installed revision for retrieval correctness;
it does not establish model-license clearance. Check the chosen model's terms
separately before redistributing it.

No project-wide license is assigned by this change. Existing brand artwork
provenance does not settle AgentMesh trademark availability or relicense other
code. Project license selection, trademark clearance and commercial-distribution
review remain owner decisions; this document makes no clearance claim.
