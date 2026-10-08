import pytest


def test_validate_normalizes_vectors():
    from agentmesh_memory.vectors import validate_vectors
    assert validate_vectors([[3, 4]], expected_count=1, expected_dimension=2) == [[0.6, 0.8]]


@pytest.mark.parametrize('vectors,count,dimension', [
    ([[0, 0]], None, None), ([[float('nan')]], None, None),
    ([[float('inf')]], None, None), ([[True]], None, None),
    ([['1']], None, None), ([[]], None, None),
    ([[1], [1, 2]], None, None), ([[1]], 2, None),
    ([[1]], None, 2), ('bad', None, None),
    ([[1]] * 10001, None, None), ([[1] * 8193], None, None),
    ([], -1, None), ([], None, 0),
])
def test_validate_rejects_invalid_vectors(vectors, count, dimension):
    from agentmesh_memory.vectors import validate_vectors
    with pytest.raises(ValueError):
        validate_vectors(vectors, count, dimension)


def test_exact_ranking_cosine_ties_and_empty():
    from agentmesh_memory.vectors import rank_exact
    assert rank_exact(['z', 'a', 'b'], [[2, 0], [1, 0], [0, 1]], [5, 0], 3) == [('a', 1.0), ('z', 1.0), ('b', 0.0)]
    assert rank_exact([], [], [1, 0], 2) == []
    assert rank_exact(['a'], [[1, 0]], [1, 0], 0) == []


@pytest.mark.parametrize('ids,vectors,query,limit', [
    (['a','a'], [[1],[1]], [1], 1), (['a'], [], [1], 1),
    ([1], [[1]], [1], 1), ([''], [[1]], [1], 1),
    (['a'], [[1]], [1], -1), (['a'], [[1]], [1], True),
    (['a'], [[1]], [1], 10001), (['a'], [[1]], [1, 0], 1),
    ('a', [[1]], [1], 1),
])
def test_exact_rejects_invalid_candidates(ids, vectors, query, limit):
    from agentmesh_memory.vectors import rank_exact
    with pytest.raises(ValueError):
        rank_exact(ids, vectors, query, limit)


def test_real_hnsw_ranks_authorized_candidates_deterministically():
    from agentmesh_memory.vectors import rank_hnsw
    ids = ['z', 'a', 'b', 'opposite']
    vectors = [[2, 0], [1, 0], [0, 1], [-1, 0]]
    expected = [('a', 1.0), ('z', 1.0), ('b', 0.0), ('opposite', -1.0)]
    assert rank_hnsw(ids, vectors, [3, 0], 4) == expected
    assert rank_hnsw(ids, vectors, [3, 0], 4) == expected
    assert rank_hnsw([], [], [1, 0], 4) == []


@pytest.mark.parametrize('kwargs', [
    {'ef': 0}, {'ef': 10001}, {'ef': True}, {'m': 1}, {'m': 65},
    {'ef_construction': 0}, {'ef_construction': 1001},
    {'seed': -1}, {'seed': 2**32}, {'seed': True},
])
def test_hnsw_rejects_unbounded_parameters(kwargs):
    from agentmesh_memory.vectors import rank_hnsw
    with pytest.raises(ValueError):
        rank_hnsw([], [], [1], 0, **kwargs)


def test_hnsw_missing_dependency_has_no_fallback(monkeypatch):
    import builtins
    from agentmesh_memory.vectors import rank_hnsw
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == 'hnswlib':
            raise ImportError('missing')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', unavailable)
    with pytest.raises(RuntimeError, match='hnswlib'):
        rank_hnsw(['a'], [[1]], [1], 1)


def test_total_vector_cell_budget_is_bounded_before_normalization():
    from agentmesh_memory.vectors import validate_vectors
    with pytest.raises(ValueError, match='budget'):
        validate_vectors([[1.0] * 4096] * 513)


def test_real_hnsw_top_k_matches_exact_on_seeded_fixture():
    # Verification of the implemented ranking path, not a recall guarantee.
    import random
    from agentmesh_memory.vectors import rank_exact, rank_hnsw
    rng = random.Random(1234)
    vectors = [[rng.uniform(-1, 1) for _ in range(32)] for _ in range(200)]
    ids = [f'fixture-{i:03d}' for i in range(200)]
    query = vectors[17]
    exact = rank_exact(ids, vectors, query, 5)
    approximate = rank_hnsw(ids, vectors, query, 5)
    assert [i for i, _ in approximate] == [i for i, _ in exact]
    assert [s for _, s in approximate] == pytest.approx([s for _, s in exact], abs=1e-12)
    assert approximate == rank_hnsw(ids, vectors, query, 5)
    allowed = [0, 20, 21]
    filtered = rank_hnsw([ids[i] for i in allowed], [vectors[i] for i in allowed], query, 10)
    assert set(i for i, _ in filtered) == {ids[i] for i in allowed}


def test_both_rankers_reject_invalid_candidates_and_queries():
    from agentmesh_memory.vectors import rank_exact, rank_hnsw
    for ranker in (rank_exact, rank_hnsw):
        for ids, vectors, query, limit in [
            (['duplicate', 'duplicate'], [[1], [1]], [1], 1),
            (['a'], [], [1], 1), (['a'], [[1]], [0], 1),
            (['a'], [[1]], [float('inf')], 1),
            (['a'], [[1]], [1, 0], 1), (['a'], [[1]], [1], -1),
        ]:
            with pytest.raises(ValueError):
                ranker(ids, vectors, query, limit)
