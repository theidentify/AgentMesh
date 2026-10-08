import importlib.util
import json

import pytest


def pair(tmp_path):
    MemoryAPI, ProfileStore = api_types()
    stores = {name: ProfileStore(tmp_path / name, name) for name in ('alpha', 'beta')}
    return MemoryAPI(stores, principal='alpha'), MemoryAPI(stores, principal='beta'), stores


def shared_policy():
    return {'read': ['alpha', 'beta'], 'retain': ['beta'], 'export': ['beta'], 'embed': ['local']}


def test_owner_revision_preserves_history_and_prevents_stale_overwrite(tmp_path):
    alpha, beta, stores = pair(tmp_path)
    initial = alpha.remember('Use provider A.', policy=shared_policy())
    revised = alpha.revise(initial['id'], initial['revision'], content='Provider must be configurable.')
    assert revised['id'] == initial['id']
    assert revised['parents'] == [initial['revision']]
    assert beta.get('alpha', initial['id'])['content'] == 'Provider must be configurable.'
    assert alpha.get('alpha', initial['id'], initial['revision'])['content'] == 'Use provider A.'
    from agentmesh_memory.core import Conflict
    with pytest.raises(Conflict):
        alpha.revise(initial['id'], initial['revision'], content='Stale overwrite.')
    assert len(stores['alpha'].records()) == 1


def test_revocation_denies_foreign_historical_content_and_evidence(tmp_path):
    alpha, beta, _ = pair(tmp_path)
    initial = alpha.remember('Shared statement.', policy=shared_policy(),
                             source={'text': 'Shared statement.', 'kind': 'source', 'locator': 'fixture:source'},
                             quote='Shared statement.')
    assert beta.evidence('alpha', initial['id'])[0]['quote'] == 'Shared statement.'
    revoked = alpha.revoke(initial['id'], initial['revision'])
    assert revoked['status'] == 'revoked'
    from agentmesh_memory.core import AccessDenied
    for revision in (None, initial['revision']):
        with pytest.raises(AccessDenied):
            beta.get('alpha', initial['id'], revision)
        with pytest.raises(AccessDenied):
            beta.evidence('alpha', initial['id'], revision)


def test_selective_retention_keeps_exact_origin_and_flags_source_correction(tmp_path):
    alpha, beta, _ = pair(tmp_path)
    original = alpha.remember('Provider A is required.', policy=shared_policy())
    retained = beta.retain('alpha', original['id'], original['revision'], content='My task currently needs provider A.')
    assert retained['owner'] == 'beta'
    assert retained['kind'] == 'derived'
    assert retained['derived_from'] == [{'owner': 'alpha', 'id': original['id'], 'revision': original['revision']}]
    assert retained['review_required'] is False
    alpha.revise(original['id'], original['revision'], content='Any configured provider is permitted.')
    current = beta.get('beta', retained['id'])
    assert current['review_required'] is True
    assert current['content'] == 'My task currently needs provider A.'
    assert current['revision'] == retained['revision']


def test_current_evidence_policy_cannot_be_bypassed_via_old_revision(tmp_path):
    alpha, beta, _ = pair(tmp_path)
    original = alpha.remember('Shared fact.', policy=shared_policy(),
                              source={'text': 'Evidence secret.', 'kind': 'source', 'locator': 'fixture:secret'},
                              quote='Evidence secret.')
    restricted = dict(shared_policy(), evidence=['alpha'])
    alpha.set_policy(original['id'], original['revision'], restricted)
    assert beta.get('alpha', original['id'])['content'] == 'Shared fact.'
    for revision in (None, original['revision']):
        assert beta.evidence('alpha', original['id'], revision) == [{'availability': 'redacted'}]


def test_deny_by_default_read_does_not_grant_retain_or_foreign_write(tmp_path):
    alpha, beta, stores = pair(tmp_path)
    from agentmesh_memory.core import AccessDenied
    private = alpha.remember('Private.')
    with pytest.raises(AccessDenied):
        beta.get('alpha', private['id'])
    readable = alpha.remember('Readable only.', policy={'read': ['beta']})
    assert beta.get('alpha', readable['id'])['content'] == 'Readable only.'
    with pytest.raises(AccessDenied):
        beta.retain('alpha', readable['id'], readable['revision'], content='Not authorized.')
    with pytest.raises(AccessDenied):
        beta.revise(readable['id'], readable['revision'], content='Foreign edit.')
    with pytest.raises(TypeError):
        beta.remember('Forged identity.', owner='alpha')
    with pytest.raises(TypeError):
        beta.remember('Fabricated confirmation.', confirmed=True)
    assert stores['beta'].records() == []


@pytest.mark.parametrize('kwargs', [
    {'content': ''}, {'content': 'x' * 8193}, {'content': 'bad\x00content'},
    {'content': 'Valid.', 'kind': 'confirmed'},
    {'content': 'Valid.', 'policy': {'read': ['../alpha']}},
    {'content': 'Valid.', 'policy': {'read': ['alpha'], 'retain': ['beta']}},
    {'content': 'Valid.', 'policy': {'read': ['alpha'], 'embed': ['cloud']}},
    {'content': 'Valid.', 'source': {'text': 'No quote.', 'kind': 'source', 'locator': 'fixture:1'}, 'quote': 'Fabricated.'},
])
def test_invalid_proposals_leave_no_knowledge_rows(tmp_path, kwargs):
    alpha, _, stores = pair(tmp_path)
    from agentmesh_memory.core import KnowledgeError
    with pytest.raises(KnowledgeError):
        alpha.remember(**kwargs)
    assert stores['alpha'].records() == []


def test_store_rejects_symlinks_unrelated_directories_and_identity_changes(tmp_path):
    _, ProfileStore = api_types()
    from agentmesh_memory.core import KnowledgeError
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    (unrelated / 'manual.txt').write_text('Keep me.')
    with pytest.raises(KnowledgeError):
        ProfileStore(unrelated, 'alpha')
    store = ProfileStore(tmp_path / 'alpha', 'alpha')
    with pytest.raises(KnowledgeError):
        ProfileStore(store.root, 'beta')
    symlink = tmp_path / 'symlink'
    symlink.symlink_to(store.root, target_is_directory=True)
    with pytest.raises(KnowledgeError):
        ProfileStore(symlink, 'alpha')
    assert (unrelated / 'manual.txt').read_text() == 'Keep me.'


def test_related_returns_authorized_exact_revision_without_bypassing_new_policy(tmp_path):
    alpha, beta, _ = pair(tmp_path)
    original = alpha.remember('Source requirement.', policy=shared_policy())
    retained = beta.retain('alpha', original['id'], original['revision'], content='Derived task context.')
    related = beta.related('beta', retained['id'])
    assert related[0]['relation'] == 'derived_from'
    assert related[0]['knowledge']['revision'] == original['revision']
    alpha.set_policy(original['id'], original['revision'], {'read': ['alpha']})
    assert beta.related('beta', retained['id']) == []
    assert beta.get('beta', retained['id'])['review_required'] is True


def test_corrected_revision_can_have_new_evidence_instead_of_stale_quote(tmp_path):
    alpha, _, _ = pair(tmp_path)
    original = alpha.remember('Use provider A.', source={'text': 'Use provider A.', 'kind': 'source',
                                                       'locator': 'fixture:first'}, quote='Use provider A.')
    correction = alpha.revise(original['id'], original['revision'], content='Any provider is allowed.',
                              source={'text': 'Correction: Any provider is allowed.', 'kind': 'user_authored',
                                      'locator': 'fixture:correction'}, quote='Any provider is allowed.')
    assert correction['evidence'][0]['quote'] == 'Any provider is allowed.'
    assert correction['evidence'][0]['locator'] == 'fixture:correction'
    unsourced = alpha.revise(original['id'], correction['revision'], content='Later unverified interpretation.')
    assert unsourced['evidence'] == []
    assert alpha.evidence('alpha', original['id'], original['revision'])[0]['quote'] == 'Use provider A.'


def test_private_conflict_does_not_disclose_ancestry_to_denied_profile(tmp_path):
    alpha, beta, stores = pair(tmp_path)
    original = alpha.remember('Private original.')
    alpha.revise(original['id'], original['revision'], content='Private branch one.')
    from agentmesh_memory.core import digest, AccessDenied, Conflict
    branch = json.loads(stores['alpha'].raw(original['id'], original['revision'])['data'])
    branch.pop('revision')
    branch.update(content='Private branch two.', parents=[original['revision']])
    branch['revision'] = digest(branch)
    stores['alpha'].put(branch, 'alpha')
    with pytest.raises(Conflict):
        alpha.get('alpha', original['id'])
    with pytest.raises(AccessDenied):
        beta.get('alpha', original['id'])
    with pytest.raises(AccessDenied):
        beta.get('alpha', original['id'], original['revision'])


def api_types():
    assert importlib.util.find_spec('agentmesh_memory.core') is not None, 'profile-owned API missing'
    from agentmesh_memory.core import MemoryAPI, ProfileStore
    return MemoryAPI, ProfileStore


def test_owner_remembers_evidenced_knowledge_and_reopens_separate_profile_store(tmp_path):
    MemoryAPI, ProfileStore = api_types()
    store = ProfileStore(tmp_path / 'alpha', 'alpha')
    api = MemoryAPI({'alpha': store}, principal='alpha')
    result = api.remember('Provider must be configurable.', kind='requirement', project='demo',
                          source={'text': 'Provider must be configurable.', 'kind': 'user_authored',
                                  'locator': 'fixture:request-1'}, quote='Provider must be configurable.')
    assert result['owner'] == 'alpha'
    assert result['id'].startswith('urn:uuid:')
    assert result['revision'].startswith('sha256:')
    assert result['verification'] == 'unverified'
    assert result['evidence'][0]['quote'] == 'Provider must be configurable.'
    reopened = MemoryAPI({'alpha': ProfileStore(tmp_path / 'alpha', 'alpha')}, principal='alpha')
    assert reopened.get('alpha', result['id']) == result
    assert all((store.root / name).is_dir() for name in ('sources', 'knowledge', 'indexes', 'state', 'exports'))
    assert len(list((store.root / 'sources').glob('*.txt'))) == 1
    assert json.loads(store.raw(result['id'], result['revision'])['data'])['content'] == result['content']
