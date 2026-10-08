"""Bounded cosine ranking over caller-authorized candidates only.

The caller MUST apply object/space authorization before providing candidates.
Nothing here loads a corpus or persists an index. Exact ranking uses stdlib;
HNSW requires optional hnswlib (tested with 0.8.0) and its NumPy dependency.
"""
import math

MAX_VECTORS = 10000
MAX_DIMENSION = 8192
MAX_VECTOR_VALUES = 2097152
MAX_ID_LENGTH = 1024


def validate_vectors(
    vectors: list[list[float]],
    expected_count: int | None = None,
    expected_dimension: int | None = None,
) -> list[list[float]]:
    """Return fresh L2-normalized float lists; reject invalid/zero vectors.

    Empty corpora are allowed. Integers and floats (not bools or strings) are
    accepted. Scaling before normalization avoids overflow and underflow.
    Limits: 10000 rows, 8192 dimensions, 2097152 total scalar values.
    """
    for value, low, high in (
        (expected_count, 0, MAX_VECTORS),
        (expected_dimension, 1, MAX_DIMENSION),
    ):
        if value is not None and (type(value) is not int or not low <= value <= high):
            raise ValueError('invalid expected size')
    if not isinstance(vectors, list) or len(vectors) > MAX_VECTORS:
        raise ValueError('vectors must be a bounded list')
    if expected_count is not None and len(vectors) != expected_count:
        raise ValueError('vector count mismatch')
    if sum(len(row) for row in vectors if isinstance(row, list)) > MAX_VECTOR_VALUES:
        raise ValueError('vector cell budget exceeds 2097152 values')
    result = []
    dimension = expected_dimension
    for row in vectors:
        if not isinstance(row, list) or not 1 <= len(row) <= MAX_DIMENSION:
            raise ValueError('invalid vector dimension')
        dimension = dimension if dimension is not None else len(row)
        if len(row) != dimension:
            raise ValueError('vector dimension mismatch')
        if any(type(x) not in (int, float) for x in row):
            raise ValueError('vector values must be numbers')
        try:
            values = [float(x) for x in row]
        except (OverflowError, ValueError) as exc:
            raise ValueError('invalid vector values') from exc
        if not all(math.isfinite(x) for x in values):
            raise ValueError('nonfinite vector')
        scale = max(abs(x) for x in values)
        if scale == 0:
            raise ValueError('zero vector')
        scaled = [x / scale for x in values]
        norm = math.hypot(*scaled)
        result.append([x / norm for x in scaled])
    return result


def _candidates(ids, vectors, query, limit):
    if (
        not isinstance(ids, list)
        or len(ids) > MAX_VECTORS
        or any(not isinstance(i, str) or not i or len(i) > MAX_ID_LENGTH for i in ids)
    ):
        raise ValueError('IDs must be bounded nonempty strings')
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate candidate IDs')
    if type(limit) is not int or not 0 <= limit <= MAX_VECTORS:
        raise ValueError('invalid ranking limit')
    rows = validate_vectors(vectors, expected_count=len(ids))
    q = validate_vectors([query], expected_dimension=len(rows[0]) if rows else None)[0]
    return rows, q


def rank_exact(
    ids: list[str], vectors: list[list[float]], query: list[float], limit: int,
) -> list[tuple[str, float]]:
    """Exact cosine ranking, score descending then ID ascending.

    limit=0 and empty corpora return []; inputs still undergo validation.
    """
    rows, q = _candidates(ids, vectors, query, limit)
    scores = [
        (identifier, max(-1.0, min(1.0, math.fsum(x * y for x, y in zip(row, q)))))
        for identifier, row in zip(ids, rows)
    ]
    return sorted(scores, key=lambda item: (-item[1], item[0]))[:limit]


def rank_hnsw(
    ids: list[str], vectors: list[list[float]], query: list[float], limit: int,
    *, ef: int = 64, m: int = 16, ef_construction: int = 100, seed: int = 42,
) -> list[tuple[str, float]]:
    """Build an ephemeral real HNSW index from authorized candidates.

    Approximate candidate selection; rerank returned candidates in float64
    cosine order with ID tie breaking. Ties outside the shortlist may differ.
    No persistent index, policy filtering, or exact fallback is performed.
    Construction and querying use one thread and explicit random_seed.
    Bounds: ef 1..10000, m 2..64, ef_construction 1..1000, uint32 seed.
    """
    for value, low, high in (
        (ef, 1, MAX_VECTORS), (m, 2, 64),
        (ef_construction, 1, 1000), (seed, 0, 2**32 - 1),
    ):
        if type(value) is not int or not low <= value <= high:
            raise ValueError('invalid HNSW parameter')
    rows, q = _candidates(ids, vectors, query, limit)
    if not rows or limit == 0:
        return []
    try:
        import hnswlib
    except ImportError as exc:
        raise RuntimeError('HNSW requires the optional hnswlib dependency') from exc
    index = hnswlib.Index(space='cosine', dim=len(q))
    index.init_index(
        max_elements=len(rows), ef_construction=ef_construction,
        M=m, random_seed=seed,
    )
    index.set_num_threads(1)
    order = sorted(range(len(ids)), key=lambda i: ids[i])
    index.add_items([rows[i] for i in order], order, num_threads=1)
    k = min(limit, len(rows))
    index.set_ef(max(ef, k))
    labels, _ = index.knn_query([q], k=k, num_threads=1)
    chosen = [int(i) for i in labels[0]]
    return rank_exact([ids[i] for i in chosen], [rows[i] for i in chosen], q, k)
