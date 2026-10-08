import pytest

from agentmesh_memory.core import MemoryAPI, ProfileStore, AccessDenied, KnowledgeError


def setup_pair(tmp_path):
    stores = {name: ProfileStore(tmp_path / name, name) for name in ('alpha', 'beta')}
    return MemoryAPI(stores, principal='alpha'), MemoryAPI(stores, principal='beta'), stores


def policy():
    return {'read': ['alpha', 'beta'], 'retain': ['beta'], 'export': ['beta'], 'embed': ['local']}


def test_profile_default_is_persisted_and_request_override_does_not_mutate_it(tmp_path):
    alpha, beta, stores = setup_pair(tmp_path)
    original = alpha.remember('Configurable provider.', policy=policy())
    beta.configure_retrieval(mode='semantic', vector_engine='hnsw')
    reopened = MemoryAPI(stores, principal='beta')
    from agentmesh_memory.core import Unavailable
    with pytest.raises(Unavailable):
        reopened.search('provider')
    response = reopened.search('provider', mode='keyword')
    assert response['effective_mode'] == 'keyword'
    assert response['results'][0]['id'] == original['id']
    with pytest.raises(Unavailable):
        reopened.search('provider')
    assert alpha.search('provider')['effective_mode'] == 'keyword'


@pytest.mark.parametrize('query,kwargs', [('', {}), ('ok', {'limit': True}),
    ('ok', {'limit': 0}), ('ok', {'context_chars': -1}),
    ('ok', {'mode': 'unknown'}), ('ok', {'vector_engine': 'unknown'}),
    ('ok', {'owners': '../alpha'}), ('ok', {'project': []})])
def test_search_rejects_invalid_options(tmp_path, query, kwargs):
    _, beta, _ = setup_pair(tmp_path)
    with pytest.raises(KnowledgeError):
        beta.search(query, **kwargs)


def test_keyword_search_bounds_context_and_reports_offline_requested_profiles(tmp_path):
    alpha, beta, _ = setup_pair(tmp_path)
    alpha.remember('provider ' * 500, policy=policy())
    response = beta.search('provider', mode='keyword', context_chars=256, owners=['alpha', 'offline'])
    assert response['results'] == []
    assert response['truncated'] is True
    assert response['context_chars_used'] <= 256
    assert response['partial_profiles'] == ['offline']


def test_keyword_search_filters_permissions_projects_and_old_revisions(tmp_path):
    alpha, beta, _ = setup_pair(tmp_path)
    record = alpha.remember('TASK-42 old provider requirement.', project='demo', policy=policy())
    current = alpha.revise(record['id'], record['revision'], content='TASK-42 provider must be configurable.')
    alpha.remember('TASK-42 private provider secret.', project='demo')
    alpha.remember('TASK-42 other project provider.', project='other', policy=policy())
    obsolete = alpha.remember('TASK-42 revoked provider.', project='demo', policy=policy())
    alpha.revoke(obsolete['id'], obsolete['revision'])
    response = beta.search('TASK-42 provider', project='demo', mode='keyword', limit=5)
    assert response['requested_mode'] == response['effective_mode'] == 'keyword'
    assert response['vector_engine'] is None
    assert response['index']['ready'] is True
    assert [result['id'] for result in response['results']] == [record['id']]
    assert response['results'][0]['revision'] == current['revision']
    assert response['results'][0]['ranking']['keyword_rank'] == 1
    assert beta.search('old requirement', project='demo', mode='keyword')['results'] == []
