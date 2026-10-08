import json
import pytest
from agentmesh_memory.core import ProfileStore, MemoryAPI, AccessDenied, KnowledgeError, Conflict

KEY = b'channel-key-only-not-asymmetric!!!'
POLICY = {action: ['alpha', 'beta'] for action in ('read', 'evidence', 'export', 'retain')}

@pytest.fixture
def pair(tmp_path):
    source = ProfileStore(tmp_path / 'source', 'alpha')
    mirror = ProfileStore(tmp_path / 'mirror', 'alpha')
    return MemoryAPI({'alpha': source}, principal='alpha'), mirror

def exchange():
    from agentmesh_memory import exchange
    return exchange

def resign(bundle):
    import hmac, hashlib
    from agentmesh_memory.core import canonical, digest
    bundle.pop('signature', None)
    bundle.pop('bundle_id', None)
    bundle['bundle_id'] = digest(bundle)
    bundle['signature'] = hmac.new(KEY, canonical(bundle).encode(), hashlib.sha256).hexdigest()
    return bundle

def test_pending_revocation_blocks_current_and_historical_reads_until_dependency_commit(pair, tmp_path):
    api, mirror = pair
    source = api.remember('Original visible fact.', policy=POLICY)
    exchange().apply_bundle(mirror, exchange().export_bundle(api, 'beta', [source['id']], key=KEY),
                            recipient='beta', trusted_issuer='alpha', key=KEY)
    beta_store = ProfileStore(tmp_path / 'beta-local', 'beta')
    reader = MemoryAPI({'alpha': mirror, 'beta': beta_store}, principal='beta')
    retained = reader.retain('alpha', source['id'], source['revision'], content='Derived interpretation.')
    child = api.revise(source['id'], source['revision'], content='Corrected visible fact.')
    child_delta = exchange().export_bundle(api, 'beta', [source['id']], key=KEY, include_history=False)
    api.revoke(source['id'], child['revision'])
    revocation_delta = exchange().export_bundle(api, 'beta', [source['id']], key=KEY, include_history=False)
    assert exchange().apply_bundle(mirror, revocation_delta, recipient='beta', trusted_issuer='alpha', key=KEY)['status'] == 'pending'
    for revision in (None, source['revision']):
        with pytest.raises(AccessDenied):
            reader.get('alpha', source['id'], revision)
        with pytest.raises(AccessDenied):
            reader.evidence('alpha', source['id'], revision)
    assert reader.search('visible', mode='keyword', owners=['alpha'])['results'] == []
    assert reader.get('beta', retained['id'])['review_required'] is True
    with pytest.raises(AccessDenied):
        exchange().export_bundle(MemoryAPI({'alpha': mirror}, principal='alpha'), 'beta', [source['id']], key=KEY)
    exchange().apply_bundle(mirror, child_delta, recipient='beta', trusted_issuer='alpha', key=KEY)
    with pytest.raises(AccessDenied):
        reader.get('alpha', source['id'])
    assert exchange().retry_pending(mirror, recipient='beta', trusted_issuer='alpha', key=KEY)[0]['status'] == 'applied'
    with pytest.raises(AccessDenied):
        reader.get('alpha', source['id'])
    with mirror.connection() as conn:
        assert conn.execute('SELECT count(*) FROM exchange_pending_blocks').fetchone()[0] == 0


@pytest.mark.parametrize('mutation', ['content', 'signature', 'issuer', 'recipient', 'bundle_id', 'extra'])
def test_tamper_and_wrong_channel_are_atomic(pair, mutation):
    api, mirror = pair
    item = api.remember('secret', policy=POLICY)
    bundle = exchange().export_bundle(api, 'beta', [item['id']], key=KEY)
    if mutation == 'content':
        bundle['records'][0]['content'] = 'forged'
    elif mutation == 'extra':
        bundle['table'] = 'heads'
    else:
        bundle[mutation] = 'forged'
    with pytest.raises(KnowledgeError):
        exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert mirror.records() == []


@pytest.mark.parametrize('action', ['read', 'evidence', 'export'])
def test_export_requires_each_recipient_permission(pair, action):
    api, _ = pair
    policy = dict(POLICY)
    policy[action] = ['alpha']
    if action == 'read':
        policy = {k: ['alpha'] for k in POLICY}
    item = api.remember('private', policy=policy)
    with pytest.raises(AccessDenied):
        exchange().export_bundle(api, 'beta', [item['id']], key=KEY)


def test_export_requires_owning_principal(pair):
    api, mirror = pair
    item = api.remember('shared', policy=POLICY)
    foreign = MemoryAPI(api.stores, principal='beta')
    with pytest.raises(AccessDenied):
        exchange().export_bundle(foreign, 'beta', [item['id']], key=KEY)


@pytest.mark.parametrize('key', [b'short', 'not-bytes'])
def test_export_rejects_weak_key(pair, key):
    api, _ = pair
    item = api.remember('shared', policy=POLICY)
    with pytest.raises(KnowledgeError):
        exchange().export_bundle(api, 'beta', [item['id']], key=key)


@pytest.mark.parametrize('field,value', [
    ('id', 'urn:uuid:bad'), ('revision', 'sha256:bad'), ('owner', 'beta'),
    ('parents', ['sha256:bad']), ('parents', 'not-list'), ('content', 3),
    ('kind', 'unknown'), ('project', ''), ('status', 'confirmed'),
    ('verification', 'confirmed'), ('evidence', [{}]), ('derived_from', [{}]),
    ('policy', {'read': ['beta']}), ('confirmed', True),
])
def test_resigned_invalid_record_is_rejected_before_write(pair, field, value):
    from agentmesh_memory.core import digest
    api, mirror = pair
    item = api.remember('valid', policy=POLICY)
    bundle = exchange().export_bundle(api, 'beta', [item['id']], key=KEY)
    record = bundle['records'][0]
    record[field] = value
    if field != 'revision':
        record['revision'] = digest({k: v for k, v in record.items() if k != 'revision'})
    resign(bundle)
    with pytest.raises(KnowledgeError):
        exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert mirror.records() == []


def test_export_history_replay_cannot_resurrect_revoked_record(pair):
    api, mirror = pair
    root = api.remember('old', policy=POLICY)
    old_bundle = exchange().export_bundle(api, 'beta', [root['id']], key=KEY)
    child = api.revise(root['id'], root['revision'], content='new')
    revoked = api.revoke(root['id'], child['revision'])
    bundle = exchange().export_bundle(api, 'beta', [root['id']], key=KEY)
    assert {r['revision'] for r in bundle['records']} == {root['revision'], child['revision'], revoked['revision']}
    assert exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)['status'] == 'applied'
    assert exchange().apply_bundle(mirror, old_bundle, recipient='beta', trusted_issuer='alpha', key=KEY)['status'] == 'applied'
    assert exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY) == {
        'status': 'duplicate', 'original_status': 'applied', 'bundle_id': bundle['bundle_id']}
    with pytest.raises(AccessDenied):
        MemoryAPI({'alpha': mirror}, principal='beta').get('alpha', root['id'])
    with mirror.connection() as conn:
        assert conn.execute('SELECT count(*) FROM revisions').fetchone()[0] == 3
        assert conn.execute('SELECT count(*) FROM exchange_receipts').fetchone()[0] == 2


def test_out_of_order_bundle_is_persisted_whole_and_retryable(pair):
    api, mirror = pair
    root = api.remember('root', policy=POLICY)
    root_bundle = exchange().export_bundle(api, 'beta', [root['id']], key=KEY)
    child = api.revise(root['id'], root['revision'], content='child')
    unrelated = api.remember('unrelated', policy=POLICY)
    delta = exchange().export_bundle(api, 'beta', [root['id'], unrelated['id']], key=KEY, include_history=False)
    assert exchange().apply_bundle(mirror, delta, recipient='beta', trusted_issuer='alpha', key=KEY)['status'] == 'pending'
    assert mirror.records() == []
    reopened = ProfileStore(mirror.root, 'alpha')
    assert exchange().pending(reopened) == [delta]
    with reopened.connection() as conn:
        assert conn.execute('SELECT count(*) FROM exchange_receipts').fetchone()[0] == 0
    exchange().apply_bundle(reopened, root_bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    results = exchange().retry_pending(reopened, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert [r['status'] for r in results] == ['applied']
    assert exchange().pending(reopened) == []
    assert json.loads(reopened.raw(root['id'])['data'])['revision'] == child['revision']


def test_divergent_children_preserve_heads_and_conflict_receipt(pair):
    from copy import deepcopy
    api, mirror = pair
    root = api.remember('root', policy=POLICY)
    exchange().apply_bundle(mirror, exchange().export_bundle(api, 'beta', [root['id']], key=KEY), recipient='beta', trusted_issuer='alpha', key=KEY)
    left = api.revise(root['id'], root['revision'], content='left')
    left_bundle = exchange().export_bundle(api, 'beta', [root['id']], key=KEY)
    exchange().apply_bundle(mirror, left_bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    right = deepcopy(left)
    right.pop('review_required')
    right['content'] = 'right'
    right['revision'] = __import__('agentmesh_memory.core', fromlist=['digest']).digest({k:v for k,v in right.items() if k != 'revision'})
    bundle = resign(dict(left_bundle, records=[right]))
    assert exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)['status'] == 'conflict'
    with pytest.raises(Conflict):
        mirror.raw(root['id'])
    assert exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)['original_status'] == 'conflict'
    assert len(mirror.records()) == 2


@pytest.mark.parametrize('mode', ['duplicate', 'new-root', 'header'])
def test_identity_and_parent_headers_reject_atomically(pair, mode):
    from copy import deepcopy
    from agentmesh_memory.core import digest
    api, mirror = pair
    root = api.remember('root', policy=POLICY)
    base = exchange().export_bundle(api, 'beta', [root['id']], key=KEY)
    exchange().apply_bundle(mirror, base, recipient='beta', trusted_issuer='alpha', key=KEY)
    record = deepcopy(base['records'][0])
    if mode == 'duplicate':
        bundle = resign(dict(base, records=[record, record]))
    else:
        record['content'] = 'changed'
        if mode == 'header':
            record['parents'] = [root['revision']]
            record['project'] = 'forged-project'
        record['revision'] = digest({k:v for k,v in record.items() if k != 'revision'})
        bundle = resign(dict(base, records=[record]))
    with pytest.raises(KnowledgeError):
        exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert mirror.records() == base['records']


def test_derived_export_closes_exact_revision_dependencies(pair):
    from agentmesh_memory.core import digest
    api, mirror = pair
    source = api.remember('source', policy=POLICY)
    derived = api.retain('alpha', source['id'], source['revision'], content='derived')
    record = json.loads(api.stores['alpha'].raw(derived['id'])['data'])
    record['policy'] = source['policy']
    record['revision'] = digest({k:v for k,v in record.items() if k != 'revision'})
    # A separately authored derived root with an exportable policy.
    record['id'] = 'urn:uuid:00000000-0000-4000-8000-000000000001'
    record['revision'] = digest({k:v for k,v in record.items() if k != 'revision'})
    api.stores['alpha'].put(record, 'alpha')
    bundle = exchange().export_bundle(api, 'beta', [record['id']], key=KEY)
    assert {r['id'] for r in bundle['records']} == {source['id'], record['id']}
    delta = resign(dict(bundle, records=[record]))
    assert exchange().apply_bundle(mirror, delta, recipient='beta', trusted_issuer='alpha', key=KEY)['status'] == 'pending'
    assert mirror.records() == []
    exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert len(mirror.records()) == 2


@pytest.mark.parametrize('mode', ['count', 'bytes', 'records-type', 'non-json', 'bad-id', 'bad-history'])
def test_bounded_bundle_and_export_inputs(pair, mode):
    api, mirror = pair
    item = api.remember('valid', policy=POLICY)
    if mode in ('bad-id', 'bad-history'):
        with pytest.raises(KnowledgeError):
            exchange().export_bundle(api, 'beta', ['../path'] if mode == 'bad-id' else [item['id']], key=KEY, include_history='yes' if mode == 'bad-history' else True)
        return
    bundle = exchange().export_bundle(api, 'beta', [item['id']], key=KEY)
    if mode == 'count':
        bundle['records'] *= 129
    elif mode == 'bytes':
        bundle['records'][0]['content'] = 'x' * 1048576
    elif mode == 'records-type':
        bundle['records'] = 'not-a-list'
    else:
        bundle['records'][0]['content'] = object()
    if mode != 'non-json':
        resign(bundle)
    with pytest.raises(KnowledgeError):
        exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert mirror.records() == []
    with mirror.connection() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'exchange_%'").fetchall() == []


@pytest.mark.parametrize('mode', ['missing', 'private', 'cross-owner', 'revoked'])
def test_derived_export_denies_inaccessible_dependencies(pair, mode):
    from agentmesh_memory.core import digest
    api, mirror = pair
    source = api.remember('source', policy=POLICY if mode != 'private' else None)
    record = json.loads(api.stores['alpha'].raw(source['id'])['data'])
    record.update(id='urn:uuid:00000000-0000-4000-8000-000000000099', kind='derived',
                  policy=__import__('agentmesh_memory.core', fromlist=['policy_for']).policy_for('alpha', POLICY),
                  derived_from=[{'owner': 'gamma' if mode == 'cross-owner' else 'alpha',
                                 'id': 'urn:uuid:00000000-0000-4000-8000-000000000098' if mode == 'missing' else source['id'],
                                 'revision': source['revision']}])
    record['revision'] = digest({k:v for k,v in record.items() if k != 'revision'})
    api.stores['alpha'].put(record, 'alpha')
    if mode == 'revoked':
        api.revoke(source['id'], source['revision'])
    with pytest.raises(AccessDenied):
        exchange().export_bundle(api, 'beta', [record['id']], key=KEY)


def test_non_ascii_signature_raises_core_error(pair):
    api, mirror = pair
    item = api.remember('claim', policy=POLICY)
    bundle = exchange().export_bundle(api, 'beta', [item['id']], key=KEY)
    bundle['signature'] = 'ฉ' * 64
    with pytest.raises(KnowledgeError):
        exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert mirror.records() == []


def test_pending_queue_is_bounded_and_replays_do_not_grow_it(pair):
    api, mirror = pair
    item = api.remember('root', policy=POLICY)
    for index in range(128):
        item = api.revise(item['id'], item['revision'], content=f'child {index}')
        bundle = exchange().export_bundle(api, 'beta', [item['id']], key=KEY, include_history=False)
        assert exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)['status'] == 'pending'
    assert exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)['status'] == 'pending'
    assert len(exchange().pending(mirror)) == 128
    item = api.revise(item['id'], item['revision'], content='over quota')
    overflow = exchange().export_bundle(api, 'beta', [item['id']], key=KEY, include_history=False)
    with pytest.raises(KnowledgeError):
        exchange().apply_bundle(mirror, overflow, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert mirror.records() == []
    assert len(exchange().pending(mirror)) == 128


def test_derived_export_includes_current_source_for_review_required(pair):
    from agentmesh_memory.core import digest
    api, mirror = pair
    source = api.remember('source', policy=POLICY)
    record = json.loads(api.stores['alpha'].raw(source['id'])['data'])
    record.update(id='urn:uuid:00000000-0000-4000-8000-000000000077', kind='derived',
                  derived_from=[{'owner': 'alpha', 'id': source['id'], 'revision': source['revision']}])
    record['revision'] = digest({k:v for k,v in record.items() if k != 'revision'})
    api.stores['alpha'].put(record, 'alpha')
    updated = api.revise(source['id'], source['revision'], content='updated source')
    bundle = exchange().export_bundle(api, 'beta', [record['id']], key=KEY)
    assert updated['revision'] in {r['revision'] for r in bundle['records']}
    exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert MemoryAPI({'alpha': mirror}, principal='beta').get('alpha', record['id'])['review_required']


def test_export_preserves_all_conflicting_heads(pair):
    from agentmesh_memory.core import digest
    api, mirror = pair
    root = api.remember('root', policy=POLICY)
    left = api.revise(root['id'], root['revision'], content='left')
    right = {k:v for k,v in left.items() if k != 'review_required'}
    right['content'] = 'right'
    right['revision'] = digest({k:v for k,v in right.items() if k != 'revision'})
    api.stores['alpha'].put(right, 'alpha')
    bundle = exchange().export_bundle(api, 'beta', [root['id']], key=KEY)
    assert {r['revision'] for r in bundle['records']} == {root['revision'], left['revision'], right['revision']}
    assert exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)['status'] == 'conflict'
    with pytest.raises(Conflict):
        mirror.raw(root['id'])


def test_import_denies_derived_dependency_already_revoked(pair):
    from agentmesh_memory.core import digest
    api, mirror = pair
    source = api.remember('source', policy=POLICY)
    template = exchange().export_bundle(api, 'beta', [source['id']], key=KEY)
    api.revoke(source['id'], source['revision'])
    exchange().apply_bundle(mirror, exchange().export_bundle(api, 'beta', [source['id']], key=KEY), recipient='beta', trusted_issuer='alpha', key=KEY)
    record = dict(template['records'][0])
    record.update(id='urn:uuid:00000000-0000-4000-8000-000000000033', kind='derived',
                  derived_from=[{'owner': 'alpha', 'id': source['id'], 'revision': source['revision']}])
    record['revision'] = digest({k:v for k,v in record.items() if k != 'revision'})
    bundle = resign(dict(template, records=[record]))
    with pytest.raises(AccessDenied):
        exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert mirror.raw(record['id']) is None


def test_duplicate_historical_dependency_does_not_become_head(pair):
    from agentmesh_memory.core import digest
    api, mirror = pair
    source = api.remember('source', policy=POLICY)
    old = exchange().export_bundle(api, 'beta', [source['id']], key=KEY)
    updated = api.revise(source['id'], source['revision'], content='updated')
    exchange().apply_bundle(mirror, exchange().export_bundle(api, 'beta', [source['id']], key=KEY), recipient='beta', trusted_issuer='alpha', key=KEY)
    record = dict(old['records'][0])
    record.update(id='urn:uuid:00000000-0000-4000-8000-000000000022', kind='derived',
                  derived_from=[{'owner': 'alpha', 'id': source['id'], 'revision': source['revision']}])
    record['revision'] = digest({k:v for k,v in record.items() if k != 'revision'})
    bundle = resign(dict(old, records=[record, *old['records']]))
    assert exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)['status'] == 'applied'
    assert json.loads(mirror.raw(source['id'])['data'])['revision'] == updated['revision']
    assert MemoryAPI({'alpha': mirror}, principal='beta').get('alpha', record['id'])['review_required']


def test_retry_pending_drains_dependency_chain_regardless_of_bundle_order(pair):
    from agentmesh_memory.core import digest
    api, mirror = pair
    root = api.remember('root', policy=POLICY)
    root_bundle = exchange().export_bundle(api, 'beta', [root['id']], key=KEY)
    first = api.revise(root['id'], root['revision'], content='first')
    first_bundle = exchange().export_bundle(api, 'beta', [root['id']], key=KEY, include_history=False)
    api.revise(root['id'], first['revision'], content='second')
    second_bundle = exchange().export_bundle(api, 'beta', [root['id']], key=KEY, include_history=False)
    for index in range(1000):
        record = second_bundle['records'][0]
        record['content'] = f'second candidate {index}'
        record['revision'] = digest({k:v for k,v in record.items() if k != 'revision'})
        resign(second_bundle)
        if second_bundle['bundle_id'] < first_bundle['bundle_id']:
            break
    else:
        pytest.fail('could not produce reversed lexical arrival order')
    for bundle in (first_bundle, second_bundle):
        assert exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)['status'] == 'pending'
    exchange().apply_bundle(mirror, root_bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    results = exchange().retry_pending(mirror, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert exchange().pending(mirror) == []
    assert {r['status'] for r in results} == {'applied'}
    assert len(results) == 2


def test_authenticated_roundtrip_preserves_owner_evidence_and_no_source_file(pair):
    api, mirror = pair
    item = api.remember('semantic claim', policy=POLICY,
                        source={'text': 'original semantic claim private tail', 'kind': 'source', 'locator': 'https://example.invalid'},
                        quote='semantic claim')
    bundle = exchange().export_bundle(api, 'beta', [item['id']], key=KEY)
    result = exchange().apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', key=KEY)
    assert result['status'] == 'applied'
    view = MemoryAPI({'alpha': mirror}, principal='beta').get('alpha', item['id'])
    assert view == item
    assert list((mirror.root / 'sources').iterdir()) == []
    assert bundle['format'] == 'agentmesh-knowledge-bundle-v1'
    assert set(bundle) == {'format', 'issuer', 'recipient', 'records', 'bundle_id', 'signature'}
