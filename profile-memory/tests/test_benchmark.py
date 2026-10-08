import importlib.util
import pytest


def test_fixture_runner_exercises_all_modes_and_reports_actual_privacy_checks(tmp_path):
    from agentmesh_memory.benchmark import run
    from test_retrieval import FixtureEmbedding
    report = run(tmp_path / 'evaluation', FixtureEmbedding())
    assert report['profiles'] == ['alpha', 'beta']
    assert report['query_count'] == 7
    assert set(report['modes']) == {'keyword', 'semantic/exact', 'semantic/hnsw', 'hybrid/exact', 'hybrid/hnsw'}
    assert all(report['checks'].values())
    assert report['embedding_usage']['text_count'] > report['query_count']
    assert report['embedding_usage']['monetary_cost'] is None
    assert report['hnsw_neighbor_recall_at_k'] == 1.0
    assert report['space']['provider'] == 'fixture'
    assert report['index_bytes'] > 0
    from agentmesh_memory.core import KnowledgeError
    with pytest.raises(KnowledgeError):
        run(tmp_path / 'evaluation', FixtureEmbedding())


def test_labeled_retrieval_metrics_are_computed_from_actual_ranked_ids():
    assert importlib.util.find_spec('agentmesh_memory.benchmark') is not None, 'benchmark runner missing'
    from agentmesh_memory.benchmark import metrics
    rows = [{'expected': 'a', 'returned': ['a', 'b', 'c']},
            {'expected': 'b', 'returned': ['c', 'b', 'd']},
            {'expected': 'z', 'returned': []}]
    result = metrics(rows, 3)
    assert result['queries'] == 3
    assert result['hit_at_1'] == pytest.approx(1 / 3)
    assert result['recall_at_k'] == pytest.approx(2 / 3)
    assert result['precision_at_k'] == pytest.approx(2 / 9)
    assert result['mrr'] == pytest.approx(0.5)
    assert result['k'] == 3
