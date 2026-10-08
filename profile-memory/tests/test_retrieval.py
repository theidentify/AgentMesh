import pytest

from agentmesh_memory.core import MemoryAPI, ProfileStore, AccessDenied, KnowledgeError


def setup_pair(tmp_path):
    stores = {name: ProfileStore(tmp_path / name, name) for name in ('alpha', 'beta')}
    return MemoryAPI(stores, principal='alpha'), MemoryAPI(stores, principal='beta'), stores


def policy():
    return {'read': ['alpha', 'beta'], 'retain': ['beta'], 'export': ['beta'], 'embed': ['local']}


class FixtureEmbedding:
    """Synthetic vectors verify mechanics only; never model-quality evidence."""
    def __init__(self):
        self.calls = []
        self.space = {'provider': 'fixture', 'model': 'test', 'revision': 'fixture-v1',
                      'dimension': 2, 'metric': 'cosine'}

    def embed(self, texts):
        self.calls.extend(texts)
        return [[1.0, 0.0] if ('provider' in text or 'paraphrase' in text) else [0.0, 1.0] for text in texts]


def test_semantic_exact_uses_current_provider_bound_index_and_authorized_candidates(tmp_path):
    alpha, beta, stores = setup_pair(tmp_path)
    allowed = alpha.remember('Configurable provider.', policy=policy())
    alpha.remember('Private provider secret.', policy={'read': ['alpha'], 'embed': ['local']})
    alpha.remember('Forbidden embedding text.', policy={'read': ['alpha', 'beta']}, project='other')
    provider = FixtureEmbedding()
    indexed = alpha.rebuild_index(provider)
    assert indexed['indexed'] == 2
    assert 'Forbidden embedding text.' not in provider.calls
    response = beta.search('paraphrase', mode='semantic', provider=provider, project='default', vector_engine='exact')
    assert response['effective_mode'] == 'semantic'
    assert response['vector_engine'] == 'exact'
    assert response['index']['space'] == provider.space
    assert [r['id'] for r in response['results']] == [allowed['id']]
    assert response['results'][0]['ranking']['semantic_rank'] == 1
    assert response['results'][0]['ranking']['keyword_rank'] is None
    assert provider.calls[-1] == 'paraphrase'
    assert (stores['alpha'].root / 'indexes' / 'vectors.sqlite3').exists()


def test_hybrid_fuses_ranks_not_incomparable_raw_scores(tmp_path):
    alpha, beta, _ = setup_pair(tmp_path)
    relevant = alpha.remember('Configurable provider.', policy=policy())
    alpha.remember('Fruit and orchard.', policy=policy())
    provider = FixtureEmbedding()
    alpha.rebuild_index(provider)
    response = beta.search('provider paraphrase', mode='hybrid', provider=provider, vector_engine='hnsw', limit=1)
    assert response['results'][0]['id'] == relevant['id']
    assert response['results'][0]['score'] == pytest.approx(2 / 61)
    assert response['results'][0]['ranking'] == {'keyword_rank': 1, 'semantic_rank': 1}
    assert response['ranking'] == 'rrf(k=60)'
    assert response['vector_engine'] == 'hnsw'


def test_relation_expansion_is_independent_and_fits_same_context_budget(tmp_path):
    alpha, beta, _ = setup_pair(tmp_path)
    original = alpha.remember('Source provider rule.', policy=policy())
    retained = beta.retain('alpha', original['id'], original['revision'], content='Derived provider context.', project='personal')
    plain = beta.search('provider', project='personal', mode='keyword')
    assert 'relations' not in plain['results'][0]
    expanded = beta.search('provider', project='personal', mode='keyword', expand_relations=True)
    assert expanded['results'][0]['id'] == retained['id']
    assert expanded['results'][0]['relations'][0]['knowledge']['revision'] == original['revision']
    assert expanded['context_chars_used'] > plain['context_chars_used']


@pytest.mark.parametrize('mode,engine', [('semantic', 'exact'), ('semantic', 'hnsw'),
                                       ('hybrid', 'exact'), ('hybrid', 'hnsw')])
def test_all_vector_modes_refuse_stale_or_incompatible_space_without_query_egress(tmp_path, mode, engine):
    alpha, beta, _ = setup_pair(tmp_path)
    original = alpha.remember('Provider configurable.', policy=policy())
    provider = FixtureEmbedding()
    alpha.rebuild_index(provider)
    from agentmesh_memory.core import Unavailable
    provider.space['revision'] = 'fixture-v2'
    before = len(provider.calls)
    with pytest.raises(Unavailable):
        beta.search('paraphrase', mode=mode, vector_engine=engine, provider=provider)
    assert len(provider.calls) == before
    provider.space['revision'] = 'fixture-v1'
    alpha.revise(original['id'], original['revision'], content='Provider is interchangeable.')
    with pytest.raises(Unavailable):
        beta.search('paraphrase', mode=mode, vector_engine=engine, provider=provider)
    assert len(provider.calls) == before
    alpha.rebuild_index(provider)
    assert beta.search('paraphrase', mode=mode, vector_engine=engine, provider=provider)['results']


def test_semantic_rechecks_current_authorization_after_embedding_query(tmp_path):
    alpha, beta, _ = setup_pair(tmp_path)
    original = alpha.remember('Provider confidential after query.', policy=policy())
    provider = FixtureEmbedding()
    alpha.rebuild_index(provider)
    embed = provider.embed
    def changing_embed(texts):
        if texts == ['paraphrase']:
            alpha.set_policy(original['id'], original['revision'], {'read': ['alpha']})
        return embed(texts)
    provider.embed = changing_embed
    assert beta.search('paraphrase', mode='semantic', provider=provider)['results'] == []


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
