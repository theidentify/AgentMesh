"""Real provisional identity backend; explicitly requested integration cannot skip."""
import copy
import os
import uuid
import pytest
from agentmesh_memory.core import ProfileStore, MemoryAPI, KnowledgeError
from agentmesh_memory import exchange

POLICY = {a: ['alpha', 'beta'] for a in ('read', 'evidence', 'export', 'retain')}
KEY = b'x' * 32

@pytest.fixture
def signed(tmp_path):
    from agentmesh_memory.auth import load_backend, SignedChannel
    path = os.environ.get('AGENTMESH_SIGNED_BACKEND')
    if not path:
        pytest.skip('optional provisional backend: set AGENTMESH_SIGNED_BACKEND')
    backend = load_backend(path)
    group = str(uuid.uuid4())
    dirs = [tmp_path / 'alice-security', tmp_path / 'bob-security']
    pubs = [backend.init_identity(dirs[0], group, 'mac'), backend.init_identity(dirs[1], group, 'linux')]
    for i in (0, 1):
        p = pubs[1-i]
        backend.approve(dirs[i], p, p['key_id'], p['group'], p['node'], p['sender'])
    def channel(i, **changes):
        p = pubs[1-i]
        fields = dict(peer_profile=('beta', 'alpha')[i], peer_fingerprint=p['key_id'],
                      peer_group=p['group'], peer_node=p['node'], peer_sender=p['sender'])
        fields.update(changes)
        return SignedChannel(backend=backend, security_dir=dirs[i], principal=('alpha', 'beta')[i], **fields)
    api = MemoryAPI({'alpha': ProfileStore(tmp_path / 'source', 'alpha')}, principal='alpha')
    mirror = ProfileStore(tmp_path / 'mirror', 'alpha')
    return backend, dirs, pubs, channel, api, mirror


def test_signed_sqlite_roundtrip(signed):
    backend, dirs, pubs, channel, api, mirror = signed
    item = api.remember('signed fact', policy=POLICY)
    bundle = exchange.export_bundle(api, 'beta', [item['id']], authenticator=channel(0))
    assert bundle['format'] == channel(0).format
    result = exchange.apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', authenticator=channel(1))
    assert result['status'] == 'applied'
    assert MemoryAPI({'alpha': mirror}, principal='beta').get('alpha', item['id']) == item


def test_persisted_signed_channel_rejects_hmac_downgrade(signed):
    backend, dirs, pubs, channel, api, mirror = signed
    item = api.remember('bound', policy=POLICY)
    legacy = exchange.export_bundle(api, 'beta', [item['id']], key=KEY)
    # Separate publisher store avoids deliberately mixing its durable send mode.
    signed_api = MemoryAPI({'alpha': ProfileStore(mirror.root.parent / 'signed-source', 'alpha')}, principal='alpha')
    signed_api.stores['alpha'].put(api.stores['alpha'].records()[0], 'alpha')
    signed_bundle = exchange.export_bundle(signed_api, 'beta', [item['id']], authenticator=channel(0))
    exchange.apply_bundle(mirror, signed_bundle, recipient='beta', trusted_issuer='alpha', authenticator=channel(1))
    with pytest.raises(KnowledgeError):
        exchange.apply_bundle(mirror, legacy, recipient='beta', trusted_issuer='alpha', key=KEY)
    with pytest.raises(KnowledgeError):
        exchange.retry_pending(mirror, recipient='beta', trusted_issuer='alpha', key=KEY)


@pytest.mark.parametrize('replay', [False, True])
def test_recheck_current_trust_inside_write_transaction(signed, replay):
    backend, dirs, pubs, channel, api, mirror = signed
    item = api.remember('transaction guarded', policy=POLICY)
    bundle = exchange.export_bundle(api, 'beta', [item['id']], authenticator=channel(0))
    receiver = channel(1)
    if replay:
        exchange.apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', authenticator=receiver)
    original = receiver.verify
    calls = []
    def revoke_after_initial_verify(unsigned, signature):
        original(unsigned, signature)
        calls.append(1)
        if len(calls) == 1:
            backend.revoke(dirs[1], pubs[0]['key_id'])
    receiver.verify = revoke_after_initial_verify
    with pytest.raises(KnowledgeError):
        exchange.apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', authenticator=receiver)
    assert len(mirror.records()) == (1 if replay else 0)


def test_signed_cli_fixture_roundtrip_pending_replay_and_revocation(signed, tmp_path):
    import json
    from test_cli import successful, invoke
    backend, dirs, pubs, channel, api, mirror = signed
    roots = [tmp_path / 'cli-source', tmp_path / 'cli-receiver']
    for root, profile in zip(roots, ('alpha', 'beta')):
        successful(root, profile, 'init')
    def flags(i):
        p = pubs[1-i]
        return ['--security-dir', str(dirs[i]), '--signed-backend', os.environ['AGENTMESH_SIGNED_BACKEND'],
                '--peer-profile', ('beta', 'alpha')[i], '--peer-fingerprint', p['key_id'],
                '--peer-group', p['group'], '--peer-node', p['node'], '--peer-sender', p['sender']]
    request = tmp_path / 'request.json'
    request.write_text(json.dumps(dict(content='Signed CLI fact.', policy=POLICY,
        source=dict(text='Signed CLI fact. private tail', kind='source', locator='fixture:quote'), quote='Signed CLI fact.')))
    item = successful(roots[0], 'alpha', 'remember', '--input', str(request))
    base = tmp_path / 'base-bundle.json'
    successful(roots[0], 'alpha', 'export', '--recipient', 'beta', '--id', item['id'], '--output', str(base), *flags(0))
    update = tmp_path / 'update.json'
    update.write_text(json.dumps(dict(content='Corrected signed CLI fact.')))
    child = successful(roots[0], 'alpha', 'revise', '--id', item['id'], '--expected', item['revision'], '--input', str(update))
    delta = tmp_path / 'delta-bundle.json'
    successful(roots[0], 'alpha', 'export', '--recipient', 'beta', '--id', item['id'], '--output', str(delta), '--no-history', *flags(0))
    assert successful(roots[1], 'beta', 'apply', '--issuer', 'alpha', '--input', str(delta), *flags(1))['status'] == 'pending'
    assert successful(roots[1], 'beta', 'apply', '--issuer', 'alpha', '--input', str(base), *flags(1))['status'] == 'applied'
    assert successful(roots[1], 'beta', 'retry', '--issuer', 'alpha', *flags(1))[0]['status'] == 'applied'
    evidence = successful(roots[1], 'beta', 'evidence', '--owner', 'alpha', '--id', item['id'], '--revision', item['revision'])
    assert evidence[0]['quote'] == 'Signed CLI fact.'
    assert successful(roots[1], 'beta', 'get', '--owner', 'alpha', '--id', item['id'])['revision'] == child['revision']
    assert successful(roots[1], 'beta', 'apply', '--issuer', 'alpha', '--input', str(delta), *flags(1))['status'] == 'duplicate'
    key = tmp_path / 'legacy.key'
    key.write_bytes(KEY)
    assert invoke(roots[1], 'beta', 'retry', '--issuer', 'alpha', '--key-file', str(key)).returncode == 1
    assert invoke(roots[0], 'alpha', 'export', '--recipient', 'beta', '--id', item['id'], '--output', str(tmp_path / 'downgrade.json'), '--key-file', str(key)).returncode == 1
    backend.revoke(dirs[1], pubs[0]['key_id'])
    denied = invoke(roots[1], 'beta', 'apply', '--issuer', 'alpha', '--input', str(delta), *flags(1))
    assert denied.returncode == 1 and denied.stdout == ''
    # Trust revocation blocks authentication, not previously imported knowledge.
    assert successful(roots[1], 'beta', 'get', '--owner', 'alpha', '--id', item['id'])['revision'] == child['revision']
    (tmp_path / 'public-result.json').write_text(json.dumps(dict(status='verified', original=item['id'], revision=child['revision'],
        checks=['export', 'pending', 'apply', 'evidence', 'retry', 'get', 'duplicate', 'downgrade-denied', 'revoked-replay-denied'])))


@pytest.mark.parametrize('field,value', [
    ('peer_fingerprint', 'f' * 64), ('peer_group', str(uuid.uuid4())),
    ('peer_node', 'windows'), ('peer_sender', str(uuid.uuid4())),
])
def test_operator_wrong_scope_fails_closed(signed, field, value):
    backend, dirs, pubs, channel, api, mirror = signed
    with pytest.raises(KnowledgeError):
        channel(1, **{field: value})


@pytest.mark.parametrize('field', ['issuer', 'recipient', 'format', 'content', 'signature', 'identity', 'encoding', 'extra'])
def test_signed_tampering_is_atomic(signed, field):
    backend, dirs, pubs, channel, api, mirror = signed
    item = api.remember('authenticated', policy=POLICY)
    bundle = exchange.export_bundle(api, 'beta', [item['id']], authenticator=channel(0))
    if field == 'content':
        bundle['records'][0]['content'] = 'tampered'
    elif field == 'signature':
        bundle['signature']['value'] = '0' * 128
    elif field == 'identity':
        bundle['signature']['issuer_identity']['sender'] = str(uuid.uuid4())
    elif field == 'encoding':
        bundle['signature']['encoding'] = 'sql-packets'
    else:
        bundle[field] = 'gamma'
    with pytest.raises(KnowledgeError):
        exchange.apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', authenticator=channel(1))
    assert mirror.records() == []
    with mirror.connection() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'exchange_%'").fetchall() == []


@pytest.mark.parametrize('side', [0, 1])
def test_revoked_self_or_peer_prevents_sign_verify_retry_and_replay(signed, side):
    backend, dirs, pubs, channel, api, mirror = signed
    sender, receiver = channel(0), channel(1)
    item = api.remember('before key revocation', policy=POLICY)
    bundle = exchange.export_bundle(api, 'beta', [item['id']], authenticator=sender)
    exchange.apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', authenticator=receiver)
    backend.revoke(dirs[0], pubs[side]['key_id'])
    backend.revoke(dirs[1], pubs[side]['key_id'])
    with pytest.raises(KnowledgeError):
        exchange.export_bundle(api, 'beta', [item['id']], authenticator=sender)
    with pytest.raises(KnowledgeError):
        exchange.apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', authenticator=receiver)
    with pytest.raises(KnowledgeError):
        exchange.retry_pending(mirror, recipient='beta', trusted_issuer='alpha', authenticator=receiver)
    assert MemoryAPI({'alpha': mirror}, principal='beta').get('alpha', item['id']) == item


def test_unapproved_peer_cannot_authenticate(signed):
    backend, dirs, pubs, channel, api, mirror = signed
    sender = channel(0)
    unsigned = dict(format=sender.format, issuer='alpha', recipient='beta', records=[], bundle_id='x')
    trust = backend.read_trust(dirs[0])
    del trust['peers'][pubs[1]['key_id']]
    backend.write_local(dirs[0] / 'trust.json', trust)
    with pytest.raises(KnowledgeError):
        sender.sign(unsigned)
    with pytest.raises(KnowledgeError):
        channel(0)


def test_wrong_logical_profile_and_recipient_identity_are_rejected(signed):
    backend, dirs, pubs, channel, api, mirror = signed
    sender, receiver = channel(0), channel(1)
    unsigned = dict(format=sender.format, issuer='alpha', recipient='beta', records=[], bundle_id='x')
    signature = sender.sign(unsigned)
    with pytest.raises(KnowledgeError):
        channel(1, peer_profile='gamma').verify(unsigned, signature)
    with pytest.raises(KnowledgeError):
        channel(0, peer_profile='gamma').sign(unsigned)
    signature['recipient_identity']['key_id'] = pubs[0]['key_id']
    with pytest.raises(KnowledgeError):
        receiver.verify(unsigned, signature)


@pytest.mark.parametrize('state', ['receipt', 'pending'])
def test_legacy_state_requires_fresh_signed_mirror(signed, state):
    backend, dirs, pubs, channel, api, mirror = signed
    item = api.remember('old root', policy=POLICY)
    root = exchange.export_bundle(api, 'beta', [item['id']], key=KEY)
    api.revise(item['id'], item['revision'], content='child')
    delta = exchange.export_bundle(api, 'beta', [item['id']], key=KEY, include_history=False)
    exchange.apply_bundle(mirror, root if state == 'receipt' else delta, recipient='beta', trusted_issuer='alpha', key=KEY)
    # Reproduce pre-integration database (no durable channel mode table).
    with mirror.connection() as conn:
        conn.execute('DROP TABLE exchange_channels')
    signed_api = MemoryAPI({'alpha': ProfileStore(mirror.root.parent / 'signed-source', 'alpha')}, principal='alpha')
    for r in root['records'] + delta['records']:
        signed_api.stores['alpha'].put(r, 'alpha')
    bundle = exchange.export_bundle(signed_api, 'beta', [item['id']], authenticator=channel(0))
    with pytest.raises(KnowledgeError):
        exchange.apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', authenticator=channel(1))


def test_signed_pending_revocation_blocks_reads_and_retry_reauthenticates(signed):
    backend, dirs, pubs, channel, api, mirror = signed
    sender, receiver = channel(0), channel(1)
    args = dict(recipient='beta', trusted_issuer='alpha', authenticator=receiver)
    item = api.remember('visible', policy=POLICY)
    exchange.apply_bundle(mirror, exchange.export_bundle(api, 'beta', [item['id']], authenticator=sender), **args)
    child = api.revise(item['id'], item['revision'], content='corrected')
    correction = exchange.export_bundle(api, 'beta', [item['id']], authenticator=sender, include_history=False)
    api.revoke(item['id'], child['revision'])
    revocation = exchange.export_bundle(api, 'beta', [item['id']], authenticator=sender, include_history=False)
    assert exchange.apply_bundle(mirror, revocation, **args)['status'] == 'pending'
    reader = MemoryAPI({'alpha': mirror}, principal='beta')
    with pytest.raises(KnowledgeError):
        reader.get('alpha', item['id'])
    exchange.apply_bundle(mirror, correction, **args)
    backend.revoke(dirs[1], pubs[0]['key_id'])
    with pytest.raises(KnowledgeError):
        exchange.retry_pending(mirror, **args)
    assert len(exchange.pending(mirror)) == 1


def test_signed_mirror_cannot_be_bypassed_via_different_recipient(signed):
    backend, dirs, pubs, channel, api, mirror = signed
    item = api.remember('signed', policy=POLICY)
    bundle = exchange.export_bundle(api, 'beta', [item['id']], authenticator=channel(0))
    exchange.apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', authenticator=channel(1))
    forged_source = MemoryAPI({'alpha': ProfileStore(mirror.root.parent / 'legacy-source', 'alpha')}, principal='alpha')
    policy = {a: ['alpha', 'beta', 'gamma'] for a in POLICY}
    forged = forged_source.remember('HMAC-only forged owner claim', policy=policy)
    legacy = exchange.export_bundle(forged_source, 'gamma', [forged['id']], key=KEY)
    with pytest.raises(KnowledgeError):
        exchange.apply_bundle(mirror, legacy, recipient='gamma', trusted_issuer='alpha', key=KEY)
    assert mirror.raw(forged['id']) is None


def test_signature_metadata_does_not_mutate_trusted_channel(signed):
    backend, dirs, pubs, channel, api, mirror = signed
    sender, receiver = channel(0), channel(1)
    unsigned = dict(format=sender.format, issuer='alpha', recipient='beta', records=[], bundle_id='x')
    signature = sender.sign(unsigned)
    signature['issuer_identity']['node'] = 'windows'
    signature['recipient_identity']['sender'] = str(uuid.uuid4())
    receiver.verify(unsigned, sender.sign(unsigned))


def test_unbound_old_other_recipient_state_cannot_enter_signed_mirror(signed):
    backend, dirs, pubs, channel, api, mirror = signed
    policy = {a: ['alpha', 'beta', 'gamma'] for a in POLICY}
    item = api.remember('old multi-recipient state', policy=policy)
    old = exchange.export_bundle(api, 'gamma', [item['id']], key=KEY)
    exchange.apply_bundle(mirror, old, recipient='gamma', trusted_issuer='alpha', key=KEY)
    with mirror.connection() as conn:
        conn.execute('DROP TABLE exchange_channels')
    signed_bundle = exchange.export_bundle(api, 'beta', [item['id']], authenticator=channel(0))
    with pytest.raises(KnowledgeError):
        exchange.apply_bundle(mirror, signed_bundle, recipient='beta', trusted_issuer='alpha', authenticator=channel(1))


@pytest.mark.parametrize('mutation', ['owner', 'acl', 'hash', 'extra', 'cross-owner', 'duplicate'])
def test_even_valid_ed25519_cannot_bypass_semantic_validation(signed, mutation):
    from agentmesh_memory.core import digest
    backend, dirs, pubs, channel, api, mirror = signed
    sender, receiver = channel(0), channel(1)
    item = api.remember('valid base', policy=POLICY)
    bundle = exchange.export_bundle(api, 'beta', [item['id']], authenticator=sender)
    record = bundle['records'][0]
    if mutation == 'owner':
        record['owner'] = 'beta'
    elif mutation == 'acl':
        record['policy']['evidence'] = ['alpha']
    elif mutation == 'hash':
        record['content'] = 'changed without revision hash'
    elif mutation == 'extra':
        record['sql'] = 'not semantic'
    elif mutation == 'cross-owner':
        record['derived_from'] = [dict(owner='gamma', id=item['id'], revision=item['revision'])]
    else:
        bundle['records'].append(copy.deepcopy(record))
    if mutation != 'hash':
        record['revision'] = digest({k: v for k, v in record.items() if k != 'revision'})
    unsigned = {k: v for k, v in bundle.items() if k not in ('signature', 'bundle_id')}
    unsigned['bundle_id'] = digest(unsigned)
    bundle = {**unsigned, 'signature': sender.sign(unsigned)}
    with pytest.raises(KnowledgeError):
        exchange.apply_bundle(mirror, bundle, recipient='beta', trusted_issuer='alpha', authenticator=receiver)
    assert mirror.records() == []


def test_signed_conflict_and_knowledge_revocation_remain_strict(signed):
    from agentmesh_memory.core import digest, Conflict
    backend, dirs, pubs, channel, api, mirror = signed
    sender, receiver = channel(0), channel(1)
    args = dict(recipient='beta', trusted_issuer='alpha', authenticator=receiver)
    item = api.remember('root', policy=POLICY)
    old = exchange.export_bundle(api, 'beta', [item['id']], authenticator=sender)
    exchange.apply_bundle(mirror, old, **args)
    left = api.revise(item['id'], item['revision'], content='left')
    fork = exchange.export_bundle(api, 'beta', [item['id']], authenticator=sender, include_history=False)
    exchange.apply_bundle(mirror, fork, **args)
    right = copy.deepcopy(fork['records'][0])
    right['content'] = 'right'
    right['revision'] = digest({k: v for k, v in right.items() if k != 'revision'})
    unsigned = {k: v for k, v in fork.items() if k not in ('signature', 'bundle_id')}
    unsigned['records'] = [right]
    unsigned['bundle_id'] = digest(unsigned)
    fork = {**unsigned, 'signature': sender.sign(unsigned)}
    assert exchange.apply_bundle(mirror, fork, **args)['status'] == 'conflict'
    assert exchange.apply_bundle(mirror, fork, **args)['original_status'] == 'conflict'
    with pytest.raises(Conflict):
        mirror.raw(item['id'])
    # Independent mirror exercises policy revocation and historical replay.
    fresh = ProfileStore(mirror.root.parent / 'revoked-mirror', 'alpha')
    api.revoke(item['id'], left['revision'])
    revoked = exchange.export_bundle(api, 'beta', [item['id']], authenticator=sender)
    assert exchange.apply_bundle(fresh, revoked, **args)['status'] == 'applied'
    exchange.apply_bundle(fresh, old, **args)
    with pytest.raises(KnowledgeError):
        MemoryAPI({'alpha': fresh}, principal='beta').get('alpha', item['id'])


def test_export_signs_inside_same_policy_snapshot_transaction(signed):
    from contextlib import contextmanager
    backend, dirs, pubs, channel, api, mirror = signed
    item = api.remember('permission at signing', policy=POLICY)
    store = api.stores['alpha']
    connection = store.connection
    first = [True]
    @contextmanager
    def change_policy_after_snapshot():
        with connection() as conn:
            yield conn
        if first[0]:
            first[0] = False
            api.set_policy(item['id'], item['revision'], {a: ['alpha'] for a in POLICY})
    store.connection = change_policy_after_snapshot
    sender = channel(0)
    original_sign = sender.sign
    def check_current_policy_at_sign(unsigned):
        with connection() as conn:
            import json
            current = json.loads(store.raw(item['id'], conn=conn)['data'])
            assert 'beta' in current['policy']['export'], 'policy changed between snapshot and signing'
        return original_sign(unsigned)
    sender.sign = check_current_policy_at_sign
    bundle = exchange.export_bundle(api, 'beta', [item['id']], authenticator=sender)
    assert bundle['records'][0]['revision'] == item['revision']
    # Revocation committed only after this authorized export transaction ended.
    assert 'beta' not in api.get('alpha', item['id'])['policy']['export']


def test_signed_mode_rejects_explicit_mixed_key_and_legacy_format(signed):
    backend, dirs, pubs, channel, api, mirror = signed
    item = api.remember('select one mode', policy=POLICY)
    with pytest.raises(KnowledgeError):
        exchange.export_bundle(api, 'beta', [item['id']], key=KEY, authenticator=channel(0))
    legacy = exchange.export_bundle(api, 'beta', [item['id']], key=KEY)
    with pytest.raises(KnowledgeError):
        exchange.apply_bundle(mirror, legacy, recipient='beta', trusted_issuer='alpha', authenticator=channel(1))
    assert mirror.records() == []


def test_real_signed_adapter_roundtrip_and_domain(signed):
    backend, dirs, pubs, channel, api, mirror = signed
    unsigned = dict(format='agentmesh-knowledge-bundle-v2', issuer='alpha', recipient='beta', records=[], bundle_id='example')
    sig = channel(0).sign(unsigned)
    assert isinstance(sig, dict) and len(sig['value']) == 128
    channel(1).verify(unsigned, sig)
    from agentmesh_memory.auth import DOMAIN
    _, Public, Invalid = backend.crypto()
    message = backend.typed(dict(envelope=unsigned, authentication={k: v for k, v in sig.items() if k != 'value'}))
    public = Public.from_public_bytes(bytes.fromhex(pubs[0]['public_key']))
    public.verify(bytes.fromhex(sig['value']), DOMAIN + message)
    with pytest.raises(Invalid):
        public.verify(bytes.fromhex(sig['value']), backend.DOMAIN + message)
