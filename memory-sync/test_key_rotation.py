"""Recovery fixtures keep generated secrets in memory or sealed on disk."""
import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path

import pytest
import identity_backup as backup
import key_protection as kp
import memory_sync as sync
import signed_packets as signed
import sqlite_memory as backend

GROUP = '00000000-0000-4000-8000-000000000009'


def sealed_identity(directory, node, *, sender=None):
    backup.private_mkdir(directory)
    key = signed.crypto()[0].generate()
    pub = key.public_key().public_bytes_raw()
    public = dict(format='agentmesh-peer-key-v1', group=GROUP, node=node,
                  sender=sender or str(uuid.uuid4()), key_id=hashlib.sha256(pub).hexdigest(), public_key=pub.hex())
    phrase = os.urandom(32).hex()
    signed.write_local(directory / 'identity.json', {**public, 'protection': kp.seal(public, key.private_bytes_raw(), phrase, n=2**14)})
    signed.write_local(directory / 'trust.json', {'format': 'agentmesh-trust-v1', 'peers': {public['key_id']: {**public, 'revoked': False}}})
    return signed.Security(directory, unlock=lambda: phrase)


@pytest.fixture
def peers(tmp_path):
    root = Path(os.path.realpath(tmp_path))
    a, b = root / 'a.db', root / 'b.db'
    backend.init_database(a)
    with sync.connect(a) as c, c:
        c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(1,'fact','global','baseline')")
    with sync.connect(a) as c, sqlite3.connect(b) as d:
        c.backup(d)
    sync.initialize(a, 'mac', GROUP)
    sync.initialize(b, 'windows', GROUP)
    sa, sb = sealed_identity(root / 'a-keys', 'mac'), sealed_identity(root / 'b-keys', 'windows')
    for local, peer in ((sa, sb), (sb, sa)):
        p = peer.public
        signed.approve(local.directory, p, p['key_id'], p['group'], p['node'], p['sender'])
    ex = root / 'exchange'
    backup.private_mkdir(ex)
    backup.private_mkdir(ex / '.stfolder')
    return a, b, ex, sa, sb


def publish(peers, content='first'):
    a, b, ex, sa, sb = peers
    with sync.connect(a) as c, c:
        c.execute('UPDATE memory_items SET content=? WHERE id=1', (content,))
    sync.capture(a, security=sa)
    sync.publish(a, ex, security=sa)
    with sync.connect(a) as c:
        row = c.execute('SELECT uuid,packet FROM _sync_outbox ORDER BY rowid DESC LIMIT 1').fetchone()
    return ex / 'signed-changes' / 'mac' / (row[0] + '.json'), signed.parse(row[1])


def test_own_published_revoked_bytes_are_seen(peers):
    path, p = publish(peers)
    a, b, ex, old, sb = peers
    new = sealed_identity(old.directory.with_name('new-keys'), 'mac', sender=old.public['sender'])
    signed.approve(new.directory, old.public, old.public['key_id'], GROUP, 'mac', old.public['sender'])
    signed.revoke(new.directory, old.public['key_id'])
    assert sync.receive(a, ex, security=new)['invalid'] == 0


@pytest.mark.parametrize('damage', ['whitespace', 'numeric', 'body', 'missing'])
def test_own_packet_requires_exact_bytes(peers, damage):
    path, p = publish(peers)
    if damage == 'whitespace': path.write_bytes(path.read_bytes() + b' ')
    elif damage == 'numeric':
        raw = path.read_bytes()
        changed = raw.replace(b'"id":1', b'"id":1.0')
        assert changed != raw
        assert json.loads(changed) == json.loads(raw)
        path.write_bytes(changed)
    elif damage == 'body':
        p['body'][0]['row']['content'] = 'different'
        path.write_bytes(signed.wire(p))
    else:
        with sync.connect(peers[0]) as c, c:
            c.execute('DELETE FROM _sync_outbox')
    assert sync.receive(peers[0], peers[2], security=peers[3])['invalid'] == 1


def test_proposal_roundtrip_and_registry(peers):
    import key_rotation as rotation
    a, b, ex, sa, sb = peers
    path, p = publish(peers)
    manifest = ex / 'test.manifest'
    rotation.write_manifest(manifest, [(p['uuid'], signed.receipt_digest(p))])
    doc = rotation.proposal(sa, sb.public['key_id'], 'planned', manifest)
    assert rotation.check_proposal(doc, manifest) == doc
    rotation.add_rotation(sb.directory, doc['old_key_id'], sa.public['key_id'], manifest)
    entry = rotation.read_rotations(sb.directory)['rotations'][doc['old_key_id']]
    assert entry['manifest_count'] == 1
    assert entry['manifest_sha256'] == doc['manifest_sha256']


@pytest.mark.parametrize('field', ['old_key_id', 'new', 'reason', 'created_at', 'manifest_sha256', 'manifest_count', 'signature', 'extra', 'missing', 'domain'])
def test_proposal_tampering(peers, field):
    import key_rotation as rotation
    sa, sb = peers[3:]
    manifest = peers[2] / 'test.manifest'
    rotation.write_manifest(manifest, [])
    doc = rotation.proposal(sa, sb.public['key_id'], 'planned', manifest)
    if field == 'new': doc['new'] = {**doc['new'], 'sender': str(uuid.uuid4())}
    elif field == 'missing': del doc['reason']
    elif field == 'extra': doc['extra'] = True
    elif field == 'manifest_count': doc[field] = True
    elif field == 'reason': doc[field] = 'lost'
    elif field == 'created_at': doc[field] = '2020-01-01T00:00:00Z'
    elif field == 'domain':
        doc['signature'] = sa.sign_bytes(signed.DOMAIN + signed.typed({k: v for k, v in doc.items() if k != 'signature'})).hex()
    else: doc[field] = '0' * len(doc[field])
    with pytest.raises(ValueError, match='^invalid rotation proposal$'):
        rotation.check_proposal(doc, manifest)


@pytest.mark.parametrize('damage', ['order', 'duplicate', 'uuid', 'digest', 'newline', 'oversize', 'sha', 'count'])
def test_manifest_refusals(tmp_path, damage):
    import key_rotation as rotation
    path = Path(os.path.realpath(tmp_path)) / 'manifest'
    pairs = [(str(uuid.UUID(int=i)), 'a'*64) for i in (1, 2)]
    rotation.write_manifest(path, pairs)
    expected = rotation.read_manifest(path)
    raw = path.read_bytes()
    if damage == 'order': raw = b''.join(reversed(raw.splitlines(keepends=True)))
    elif damage == 'duplicate': raw += raw.splitlines(keepends=True)[-1]
    elif damage == 'uuid': raw = raw.replace(b'00000000-', b'zzzzzzzz-')
    elif damage == 'digest': raw = raw.replace(b'aaaa', b'zzzz')
    elif damage == 'newline': raw = raw[:-1]
    path.write_bytes(raw)
    kwargs = dict(sha256=expected['sha256'], count=expected['count'])
    if damage == 'oversize': kwargs['_max_bytes'] = 10
    elif damage == 'sha': kwargs['sha256'] = '0'*64
    elif damage == 'count': kwargs['count'] = 3
    with pytest.raises(ValueError, match='^invalid rotation manifest$'):
        rotation.read_manifest(path, **kwargs)


def test_streamed_manifest_exceeds_typed_limit(tmp_path):
    import key_rotation as rotation
    path = Path(os.path.realpath(tmp_path)) / 'manifest'
    pairs = [(str(uuid.UUID(int=i)), 'a'*64) for i in range(60000)]
    rotation.write_manifest(path, pairs)
    result = rotation.read_manifest(path)
    assert result['count'] == 60000
    assert result['packets'] == dict(pairs)


@pytest.mark.parametrize('damage', ['extra', 'format', 'entry', 'key', 'count'])
def test_registry_strict_schema(peers, damage):
    import key_rotation as rotation
    directory = peers[3].directory
    key = peers[4].public['key_id']
    doc = {'format': rotation.ROTATIONS, 'rotations': {key: dict(new_key_id=peers[3].public['key_id'], approved_at=backup.now(), manifest_sha256='a'*64, manifest_count=0)}}
    if damage == 'extra': doc['extra'] = 1
    elif damage == 'format': doc['format'] = 'bad'
    elif damage == 'entry': doc['rotations'][key]['extra'] = 1
    elif damage == 'key': doc['rotations']['bad'] = doc['rotations'].pop(key)
    else: doc['rotations'][key]['manifest_count'] = True
    signed.write_local(directory / 'rotations.json', doc)
    with pytest.raises(ValueError, match='^invalid rotation registry$'):
        rotation.read_rotations(directory)


@pytest.mark.parametrize('case', ['accepted', 'digest', 'absent', 'no-registry', 'signature', 'corrupt-file'])
def test_revoked_manifest_receive(peers, case):
    import key_rotation as rotation
    a, b, ex, sa, sb = peers
    path, p = publish(peers)
    manifest = ex / 'approved.manifest'
    digest = '0'*64 if case == 'digest' else signed.receipt_digest(p)
    rotation.write_manifest(manifest, [] if case == 'absent' else [(p['uuid'], digest)])
    if case != 'no-registry':
        rotation.add_rotation(sb.directory, sa.public['key_id'], sb.public['key_id'], manifest)
    signed.revoke(sb.directory, sa.public['key_id'])
    if case == 'signature':
        p['signature'] = '0'*128
        path.write_bytes(signed.wire(p))
    if case == 'corrupt-file':
        (sb.directory / 'rotations' / (sa.public['key_id'] + '.manifest')).write_bytes(b'')
    result = sync.receive(b, ex, security=sb)
    assert result['applied'] == int(case == 'accepted')
    assert result['invalid'] == int(case != 'accepted')


def test_manifest_causality_and_lazy_cache(peers, monkeypatch):
    import key_rotation as rotation
    a, b, ex, sa, sb = peers
    first, p = publish(peers)
    raw = first.read_bytes()
    first.unlink()
    second, q = publish(peers, 'second')
    manifest = ex / 'approved.manifest'
    pairs = sorted([(p['uuid'], signed.receipt_digest(p)), (q['uuid'], signed.receipt_digest(q))])
    rotation.write_manifest(manifest, pairs)
    rotation.add_rotation(sb.directory, sa.public['key_id'], sb.public['key_id'], manifest)
    signed.revoke(sb.directory, sa.public['key_id'])
    calls = []
    original = rotation.read_manifest
    def count(*a, **kw):
        calls.append(1)
        return original(*a, **kw)
    monkeypatch.setattr(rotation, 'read_manifest', count)
    assert sync.receive(b, ex, security=sb)['pending'] == 1
    first.write_bytes(raw)
    assert sync.receive(b, ex, security=sb)['applied'] == 2
    assert len(calls) == 1
    with sync.connect(b) as c:
        assert c.execute('SELECT content FROM memory_items WHERE id=1').fetchone()[0] == 'second'


def ready(peers):
    import time
    a, b, ex, sa, sb = peers
    publish(peers)
    backup.private_mkdir(ex / 'status')
    signed.write_local(ex / 'status' / 'windows.json', {'group': GROUP})
    for path in (ex / 'signed-changes' / 'mac').glob('*.json'):
        os.utime(path, (time.time() - 1000, time.time() - 1000))
    with sync.connect(a) as c, c:
        c.execute("UPDATE memory_items SET content='pending' WHERE id=1")
    sync.capture(a, security=sa)


def snapshot(peers):
    a, b, ex, sa, sb = peers
    paths = [a, Path(str(a)+'-wal'), Path(str(a)+'-shm')]
    paths += list(sa.directory.rglob('*')) + list(ex.rglob('*'))
    return {str(p): p.read_bytes() for p in paths if p.is_file()}


@pytest.mark.parametrize('case', ['ok', 'lost-identity', 'lost-all'])
def test_prepare_derives_old_key_and_keeps_state(peers, case):
    import key_rotation as rotation
    ready(peers)
    a, b, ex, sa, sb = peers
    if case != 'ok': (sa.directory / 'identity.json').unlink()
    if case == 'lost-all': (sa.directory / 'trust.json').unlink()
    before = snapshot(peers)
    with rotation.prepare(sa.directory, a, ex, old_key=sa.public['key_id'] if case == 'lost-all' else ('0'*64 if case == 'ok' else None), confirm=lambda w: w) as plan:
        assert plan['old_key_id'] == sa.public['key_id']
        assert len(plan['pending']) == 1
        assert len(plan['manifest']) == 1
    assert snapshot(peers) == before


@pytest.mark.parametrize('case', ['journal', 'stage', 'restore', 'pre-restore', 'legacy', 'binding', 'unexpected', 'STOPPED', 'RESIGN', 'exchange', 'legacy-exchange', 'signature', 'recent', 'lost-no-key', 'cycle-lock', 'lifetime-lock', 'running'])
def test_prepare_refusals_unchanged(peers, case, monkeypatch):
    import key_rotation as rotation
    import worker_lifecycle as worker
    import runtime_lock
    from contextlib import ExitStack
    ready(peers)
    a, b, ex, sa, sb = peers
    expected = None
    runtime = None
    if case in ('journal', 'stage', 'restore', 'pre-restore'):
        name = {'journal': 'rotation-journal.json', 'stage': '.rotate-test', 'restore': '.restore-test', 'pre-restore': 'trust.json.pre-restore'}[case]
        (sa.directory / name).write_bytes(b'{}')
    if case == 'legacy':
        with sync.connect(a) as c, c: c.execute('DROP TABLE _sync_security')
    if case == 'binding':
        with sync.connect(a) as c, c: c.execute("UPDATE _sync_security SET node='linux'")
    if case in ('unexpected', 'signature', 'exchange', 'legacy-exchange'):
        with sync.connect(a) as c, c:
            row = c.execute('SELECT uuid,packet FROM _sync_outbox WHERE published=0').fetchone()
            p = signed.parse(row[1])
            if case in ('unexpected', 'signature'):
                p['key_id' if case == 'unexpected' else 'signature'] = '0' * (64 if case == 'unexpected' else 128)
                c.execute('UPDATE _sync_outbox SET packet=? WHERE uuid=?', (sync.canonical(p), row[0]))
            else:
                dest = ex / ('changes' if case == 'legacy-exchange' else 'signed-changes')
                if not dest.exists(): backup.private_mkdir(dest)
                if not (dest / 'mac').exists(): backup.private_mkdir(dest / 'mac')
                file = dest / 'mac' / (row[0] + '.json')
                file.write_bytes(row[1].encode())
                os.utime(file, (1, 1))
    if case == 'recent':
        next((ex / 'signed-changes' / 'mac').glob('*.json')).touch()
    if case == 'lost-no-key':
        (sa.directory / 'identity.json').unlink()
        (sa.directory / 'trust.json').unlink()
    if case in ('running', 'lifetime-lock'):
        runtime = a.parent / 'runtime.json'
        signed.write_local(runtime, {'database': str(a), 'exchange': str(ex), 'node': 'mac'})
        monkeypatch.setattr(worker, 'status', lambda p: {'state': 'running' if case == 'running' else 'stopped'})
    with ExitStack() as stack:
        if case == 'cycle-lock': stack.enter_context(runtime_lock.lock(a.parent / 'worker.lock', timeout=0))
        if case == 'lifetime-lock':
            directory = worker.control({'database': str(a)}, create=True)
            stack.enter_context(runtime_lock.lock(directory / 'lifetime.lock', timeout=0))
        before = snapshot(peers)
        with pytest.raises(ValueError):
            with rotation.prepare(sa.directory, a, ex, runtime=runtime, confirm=lambda word: '' if word == case else word):
                pytest.fail('refusal required')
        assert snapshot(peers) == before


class FakeBackend:
    def __init__(self):
        self.items, self.calls, self.fail = {}, [], False
    def delete(self, key_id):
        self.calls.append(key_id)
        if self.fail: raise RuntimeError('backend unavailable')
        self.items.pop(key_id, None)


def rotate_args(peers):
    phrase = os.urandom(32).hex()
    return phrase, dict(ask=lambda *a: phrase, confirm=lambda w: w, backend=FakeBackend(), _test_n=2**14)


def test_rotation_full_and_sealed_writes(peers, monkeypatch):
    import key_rotation as rotation
    ready(peers)
    a, b, ex, sa, sb = peers
    with sync.connect(a) as c:
        before = {r['uuid']: (r['packet'], r['published']) for r in c.execute('SELECT * FROM _sync_outbox')}
    phrase, kwargs = rotate_args(peers)
    writes = []
    original = signed.write_local
    def record(path, value, **kw):
        raw = signed.wire(value)
        assert sa.key.private_bytes_raw().hex().encode() not in raw, 'raw key written'
        assert phrase.encode() not in raw, 'passphrase written'
        assert 'private_key' not in raw.decode(), 'raw key field written'
        writes.append((Path(path).name, value))
        return original(path, value, **kw)
    monkeypatch.setattr(signed, 'write_local', record)
    report = rotation.identity_rotate(sa.directory, a, ex, reason='planned', **kwargs)
    assert report['status'] == 'rotated'
    assert report['resigned'] == 1 and report['manifest_packets'] == 1
    assert report['os_unlock_item_removed'] is True
    assert kwargs['backend'].calls == [sa.public['key_id']]
    new = signed.Security(sa.directory, unlock=lambda: phrase)
    assert new.public['sender'] == sa.public['sender']
    trust = signed.read_trust(sa.directory)
    assert trust['peers'][sa.public['key_id']]['revoked']
    assert sum(not p['revoked'] for p in trust['peers'].values() if p['sender'] == sa.public['sender']) == 1
    with sync.connect(a) as c:
        for row in c.execute('SELECT * FROM _sync_outbox'):
            old = signed.parse(before[row['uuid']][0])
            p = signed.parse(row['packet'])
            if row['published']: assert row['packet'] == before[row['uuid']][0]
            else:
                assert all(p[k] == old[k] for k in ('uuid', 'body', 'checksum'))
                new.verify(p, GROUP, 'mac')
                with pytest.raises(ValueError): sb.verify(p, GROUP, 'mac')
    proposal_path = ex / report['proposal']
    doc = signed.parse(signed.read_local(proposal_path))
    rotation.check_proposal(doc, proposal_path.with_suffix('.manifest'))
    assert not (sa.directory / 'rotation-journal.json').exists()
    assert not list(sa.directory.glob('.rotate-*'))
    assert sync.receive(a, ex, security=new)['invalid'] == 0
    journal = next(value for name, value in writes if name == 'rotation-journal.json')
    assert 'protection' in journal['new_identity']


@pytest.mark.parametrize('step', ['trust', 'identity', 'db', 'verify', 'proposal'])
def test_crash_resume_replays_and_fences(peers, step):
    import key_rotation as rotation
    import runtime_lock
    ready(peers)
    a, b, ex, sa, sb = peers
    phrase, kwargs = rotate_args(peers)
    def crash(name):
        if name == step: raise RuntimeError('injected crash')
    with pytest.raises(ValueError):
        rotation.identity_rotate(sa.directory, a, ex, reason='compromised', _after_step=crash, **kwargs)
    journal = signed.parse(signed.read_local(sa.directory / 'rotation-journal.json', private=True))
    with pytest.raises(ValueError, match='rotation in progress'):
        sa.sign({'group': GROUP, 'node': 'mac', 'uuid': str(uuid.uuid4()), 'body': [{}]})
    with pytest.raises(ValueError):
        rotation.identity_rotate(sa.directory, a, ex, reason='planned', **kwargs)
    with pytest.raises(ValueError):
        rotation.identity_rotate(sa.directory, a, ex, resume=True, **{**kwargs, 'confirm': lambda w: ''})
    with runtime_lock.lock(a.parent / 'worker.lock', timeout=0):
        with pytest.raises(ValueError, match='worker or cycle'):
            rotation.identity_rotate(sa.directory, a, ex, resume=True, **kwargs)
    report = rotation.identity_rotate(sa.directory, a, ex, resume=True, **kwargs)
    assert report['new_key_id'] == journal['new_identity']['key_id']
    assert report['reason'] == 'compromised'
    assert report['resigned'] == 1
    assert signed.parse(signed.read_local(sa.directory / 'identity.json', private=True)) == journal['new_identity']
    assert not (sa.directory / 'rotation-journal.json').exists()


@pytest.mark.parametrize('case', ['lost', 'failure', 'none'])
def test_rotation_lost_and_os_cleanup(peers, case):
    import key_rotation as rotation
    ready(peers)
    a, b, ex, sa, sb = peers
    phrase, kwargs = rotate_args(peers)
    if case == 'lost':
        (sa.directory / 'identity.json').unlink()
        (sa.directory / 'trust.json').unlink()
        kwargs['old_key'] = sa.public['key_id']
    elif case == 'failure': kwargs['backend'].fail = True
    else: kwargs['backend'] = None
    report = rotation.identity_rotate(sa.directory, a, ex, reason='lost', **kwargs)
    assert report['trust_restored'] == (case != 'lost')
    assert report['os_unlock_item_removed'] is ({'lost': True, 'failure': False, 'none': None}[case])
    if case == 'lost': assert len(signed.read_trust(sa.directory)['peers']) == 1
    if case == 'failure': assert any('by hand' in s for s in report['next'])


def test_transaction_exchange_race_rolls_back_and_resumes(peers):
    import key_rotation as rotation
    ready(peers)
    a, b, ex, sa, sb = peers
    phrase, kwargs = rotate_args(peers)
    with sync.connect(a) as c:
        before = [tuple(r) for r in c.execute('SELECT uuid,packet FROM _sync_outbox WHERE published=0')]
    file = ex / 'signed-changes' / 'mac' / (before[0][0] + '.json')
    def race(step):
        if step == 'identity':
            file.write_bytes(before[0][1].encode())
            os.utime(file, (1, 1))
    with pytest.raises(ValueError, match='pending packet is already'):
        rotation.identity_rotate(sa.directory, a, ex, reason='planned', _after_step=race, **kwargs)
    with sync.connect(a) as c:
        assert [tuple(r) for r in c.execute('SELECT uuid,packet FROM _sync_outbox WHERE published=0')] == before
    file.unlink()
    assert rotation.identity_rotate(sa.directory, a, ex, resume=True, **kwargs)['status'] == 'rotated'


def rotation_offer(peers):
    import key_rotation as rotation
    a, b, ex, sa, sb = peers
    path, packet = publish(peers)
    new = sealed_identity(sa.directory.with_name('offer-key'), 'mac', sender=sa.public['sender'])
    for parent in (ex / 'pairing', ex / 'pairing' / 'rotations'):
        backup.private_mkdir(parent)
    manifest = ex / 'pairing' / 'rotations' / (new.public['key_id'] + '.manifest')
    rotation.write_manifest(manifest, [(packet['uuid'], signed.receipt_digest(packet))])
    doc = rotation.proposal(new, sa.public['key_id'], 'compromised', manifest)
    proposal = manifest.with_suffix('.json')
    signed.write_local(proposal, doc)
    return proposal, new


@pytest.mark.parametrize('revoked', [False, True])
def test_peer_rotation_success_order_and_competition(peers, revoked, monkeypatch, capsys):
    import key_rotation as rotation
    path, new = rotation_offer(peers)
    sa, sb = peers[3:]
    if revoked: signed.revoke(sb.directory, sa.public['key_id'])
    signed.write_local(path.with_name('competing.json'), signed.parse(path.read_bytes()))
    writes = []
    original = signed.write_local
    def record(path, value, **kw):
        writes.append(Path(path).name)
        return original(path, value, **kw)
    monkeypatch.setattr(signed, 'write_local', record)
    report = rotation.peer_rotate(sb.directory, path, confirm=lambda: new.public['key_id'])
    assert report['status'] == 'peer-rotated'
    assert report['competing_proposals'] == 1
    assert 'more than one rotation' in capsys.readouterr().err
    assert writes.index('rotations.json') < writes.index('trust.json')
    trust = signed.read_trust(sb.directory)
    assert trust['peers'][sa.public['key_id']]['revoked']
    assert not trust['peers'][new.public['key_id']]['revoked']
    assert sync.receive(peers[1], peers[2], security=sb)['applied'] == 1


@pytest.mark.parametrize('case', ['fingerprint', 'unknown', 'already', 'new-revoked', 'own-key', 'own-node', 'group', 'tampered', 'oversize', 'slot'])
def test_peer_refusals_no_changes(peers, case):
    import key_rotation as rotation
    path, new = rotation_offer(peers)
    sa, sb = peers[3:]
    doc = signed.parse(path.read_bytes())
    trust = signed.read_trust(sb.directory)
    if case == 'unknown': del trust['peers'][sa.public['key_id']]
    elif case in ('already', 'new-revoked'):
        trust['peers'][new.public['key_id']] = {**new.public, 'revoked': case == 'new-revoked'}
    elif case in ('own-key', 'own-node', 'group'):
        if case == 'own-key': doc['old_key_id'] = sb.public['key_id']
        elif case == 'own-node': doc['new']['node'] = sb.public['node']
        else: doc['new']['group'] = str(uuid.uuid4())
        doc['signature'] = new.sign_bytes(rotation.DOMAIN + signed.typed({k: v for k, v in doc.items() if k != 'signature'})).hex()
    elif case == 'tampered': doc['reason'] = 'lost'
    elif case == 'slot':
        other = sealed_identity(sa.directory.with_name('other-slot'), 'mac')
        trust['peers'][other.public['key_id']] = {**other.public, 'revoked': False}
    signed.write_local(sb.directory / 'trust.json', trust)
    signed.write_local(path, doc)
    if case == 'oversize': path.write_bytes(b' ' * (signed.MAX_BYTES + 1))
    before = {p.name: p.read_bytes() for p in sb.directory.iterdir() if p.is_file()}
    with pytest.raises(ValueError):
        rotation.peer_rotate(sb.directory, path, confirm=lambda: '0'*64 if case == 'fingerprint' else new.public['key_id'])
    assert {p.name: p.read_bytes() for p in sb.directory.iterdir() if p.is_file()} == before
    assert not (sb.directory / 'rotations').exists()


def all_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_compromise_report_readonly_and_redacted(peers):
    import key_rotation as rotation
    a, b, ex, sa, sb = peers
    path, p = publish(peers, 'row-content-must-never-appear')
    assert sync.receive(b, ex, security=sb)['applied'] == 1
    manifest = ex / 'review.manifest'
    rotation.write_manifest(manifest, [(p['uuid'], signed.receipt_digest(p))])
    rotation.add_rotation(sb.directory, sa.public['key_id'], sb.public['key_id'], manifest)
    fake = dict(p, uuid=str(uuid.uuid4()))
    fake = sa.sign({k: v for k, v in fake.items() if k not in ('signature', 'key_id', 'sender')})
    forged = path.with_name(fake['uuid'] + '.json')
    forged.write_bytes(signed.wire(fake))
    unreadable = path.with_name('broken.json')
    unreadable.write_bytes(b'{')
    missing = str(uuid.uuid4())
    with sync.connect(b) as c, c:
        c.execute('INSERT INTO _sync_receipts VALUES(?,?)', (missing, 'a'*64))
        c.execute('INSERT INTO _sync_receipt_envelopes VALUES(?,?)', (missing, 'b'*64))
        c.execute('INSERT INTO _sync_receipts VALUES(?,?)', (str(uuid.uuid4()), 'c'*64))
    before = all_bytes(a.parent)
    report = rotation.compromise_report(b, ex, sa.public['key_id'], security_dir=sb.directory)
    assert len(report['committed']) == 1
    item = report['committed'][0]
    assert item['uuid'] == p['uuid'] and item['in_manifest'] is True
    assert item['changes'][0]['key_sha256'] == hashlib.sha256(sync.canonical(p['body'][0]['key']).encode()).hexdigest()[:16]
    assert 'row-content-must-never-appear' not in json.dumps(report)
    assert report['receipts_without_file'] == 1
    assert report['legacy_receipts'] == 1
    assert report['unreadable'] == 1
    assert report['uncommitted'][0]['uuid'] == fake['uuid']
    own = rotation.compromise_report(a, ex, sa.public['key_id'])
    assert [p['uuid'] for p in own['forged_candidates']] == [fake['uuid']]
    assert all_bytes(a.parent) == before
    future = rotation.compromise_report(b, ex, sa.public['key_id'], since='2099-01-01T00:00:00Z')
    assert future['filtered_by_since'] == 2
    assert future['committed'] == []
    assert future['receipts_without_file'] == 1
    assert all_bytes(a.parent) == before


@pytest.mark.parametrize('case', ['filename', 'folder', 'group', 'oversize', 'body'])
def test_compromise_report_hostile_files(peers, case):
    import key_rotation as rotation
    a, b, ex, sa, sb = peers
    path, p = publish(peers)
    if case == 'filename': path.rename(path.with_name(str(uuid.uuid4()) + '.json'))
    elif case == 'folder':
        backup.private_mkdir(path.parent.parent / 'linux')
        path.rename(path.parent.parent / 'linux' / path.name)
    else:
        if case == 'group': p['group'] = str(uuid.uuid4())
        elif case == 'body': p['body'][0]['table'] = 'secret-untrusted-table'
        path.write_bytes(b' ' * (signed.MAX_BYTES + 1) if case == 'oversize' else signed.wire(p))
    before = all_bytes(a.parent)
    report = rotation.compromise_report(a, ex, sa.public['key_id'])
    assert report['unreadable'] == 1
    assert report['committed'] == []
    assert 'secret-untrusted-table' not in json.dumps(report)
    assert all_bytes(a.parent) == before


def test_two_peer_recovery_end_to_end(peers):
    import key_rotation as rotation
    a, b, ex, sa, sb = peers
    earlier, earlier_packet = publish(peers, 'earlier receipt')
    assert sync.receive(b, ex, security=sb)['applied'] == 1
    ready(peers)
    signed.revoke(sb.directory, sa.public['key_id'])
    phrase, kwargs = rotate_args(peers)
    report = rotation.identity_rotate(sa.directory, a, ex, reason='compromised', **kwargs)
    rotation.peer_rotate(sb.directory, ex / report['proposal'], confirm=lambda: report['new_fingerprint'])
    new = signed.Security(sa.directory, unlock=lambda: phrase)
    assert sync.cycle(a, ex, security=new)['receive']['invalid'] == 0
    received = sync.cycle(b, ex, security=sb)['receive']
    assert received['invalid'] == 0 and received['applied'] == 2
    with sync.connect(a) as c, sync.connect(b) as d:
        assert sync.rows(c) == sync.rows(d)
    assert sb.verify_received(earlier_packet, GROUP, 'mac', signed.receipt_digest(earlier_packet)) == earlier_packet
    # Model possession of the stolen old key, without using the now-fenced signer.
    forged = {k: v for k, v in earlier_packet.items() if k != 'signature'}
    forged['uuid'] = str(uuid.uuid4())
    forged['signature'] = sa.key.sign(signed.DOMAIN + signed.typed(forged)).hex()
    (earlier.parent / (forged['uuid'] + '.json')).write_bytes(signed.wire(forged))
    assert sync.receive(b, ex, security=sb)['invalid'] == 1
    own = rotation.compromise_report(a, ex, sa.public['key_id'], security_dir=sa.directory)
    peer = rotation.compromise_report(b, ex, sa.public['key_id'], security_dir=sb.directory)
    assert [p['uuid'] for p in own['forged_candidates']] == [forged['uuid']]
    assert [p['uuid'] for p in peer['uncommitted']] == [forged['uuid']]
    assert all(p['uuid'] != forged['uuid'] for p in peer['committed'])


@pytest.mark.parametrize('name', ['rotation-journal.json', '.rotate-test.manifest'])
def test_restore_refuses_rotation_leftovers(peers, name):
    a, b, ex, sa, sb = peers
    target = sa.directory.with_name('restore-target')
    backup.private_mkdir(target)
    (target / name).write_bytes(b'{}')
    before = all_bytes(a.parent)
    with pytest.raises(ValueError, match='unfinished'):
        backup.check_target(target, ex)
    assert all_bytes(a.parent) == before


def test_restore_revoked_key_into_empty_trust_cannot_reauthorize_peer(peers):
    import os_unlock
    ready(peers)
    a, b, ex, sa, sb = peers
    phrase, new_phrase = os.urandom(32).hex(), os.urandom(32).hex()
    manifest = {k: v for k, v in sa.public.items() if k != 'format'}
    manifest['created_at'] = backup.now()
    doc = backup.seal(manifest, sa.key.private_bytes_raw(), signed.read_trust(sa.directory), phrase, n=2**14)
    file = a.parent / 'sealed-backup.json'
    signed.write_local(file, doc)
    signed.revoke(sb.directory, sa.public['key_id'])
    target = a.parent / 'restored'
    class Missing:
        def load(self, key_id): raise os_unlock.OSUnlockError(os_unlock.MISSING)
    replies = iter((phrase, new_phrase, new_phrase))
    report = backup.restore(file, target, ex, ask=lambda *a: next(replies), confirm=lambda w: w,
                            peer_confirm=lambda p: '', backend=Missing(), _test_n=2**14)
    assert report['status'] == 'restored'
    restored = signed.Security(target, unlock=lambda: new_phrase)
    assert restored.public == sa.public
    assert sync.publish(a, ex, security=restored)['published'] == 1
    assert sync.receive(b, ex, security=sb)['invalid'] == 2


def test_recovery_cli_errors_and_packaging(peers, capsys):
    import agentmesh
    import cli_errors
    import key_rotation as rotation
    import standalone_build
    import build_package
    a, b, ex, sa, sb = peers
    ready(peers)
    before = snapshot(peers)
    assert agentmesh.main(['identity-rotate', '--security-dir', str(sa.directory), '--db', str(a), '--exchange', str(ex), '--reason', 'planned']) == 1
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err.splitlines()[-1])['reason'] == 'passphrase entry requires an interactive console'
    assert snapshot(peers) == before
    assert agentmesh.main(['compromise-report', '--db', str(a), '--exchange', str(ex), '--key', sa.public['key_id']]) == 0
    assert json.loads(capsys.readouterr().out)['committed'] == []
    for action in ('identity-rotate', 'peer-rotate', 'compromise-report'):
        assert cli_errors.report(ValueError(rotation.INVALID), action)['reason'] == rotation.INVALID
        assert 'sensitive' not in json.dumps(cli_errors.report(ValueError('sensitive'), action))
        with pytest.raises(SystemExit) as error: agentmesh.main([action, '--help'])
        assert error.value.code == 0
        capsys.readouterr()
    assert 'key_rotation' in cli_errors.TRUSTED_MODULES
    assert 'key_rotation' in standalone_build.HIDDEN_MODULES
    assert 'key_rotation.py' in build_package.FILES


def test_recovery_source_smoke(tmp_path):
    import smoke_standalone
    import sys
    import agentmesh
    smoke_standalone.smoke_recovery([sys.executable, agentmesh.__file__], Path(os.path.realpath(tmp_path)))


def test_large_manifest_peer_approval_and_receive(peers):
    import key_rotation as rotation
    path, new = rotation_offer(peers)
    sa, sb = peers[3:]
    with sync.connect(peers[0]) as c:
        packet = signed.parse(c.execute('SELECT packet FROM _sync_outbox').fetchone()[0])
    pairs = [(str(uuid.UUID(int=i)), 'a'*64) for i in range(59999)]
    pairs.append((packet['uuid'], signed.receipt_digest(packet)))
    rotation.write_manifest(path.with_suffix('.manifest'), sorted(pairs))
    signed.write_local(path, rotation.proposal(new, sa.public['key_id'], 'planned', path.with_suffix('.manifest')))
    report = rotation.peer_rotate(sb.directory, path, confirm=lambda: new.public['key_id'])
    assert report['manifest_packets'] == 60000
    assert sync.receive(peers[1], peers[2], security=sb)['applied'] == 1


@pytest.mark.parametrize('field', sorted(signed.PUBLIC_FIELDS))
def test_every_new_public_proposal_field_authenticated(peers, field):
    import key_rotation as rotation
    path, new = rotation_offer(peers)
    doc = signed.parse(path.read_bytes())
    doc['new'][field] = str(uuid.uuid4()) if field in ('sender', 'group') else 'windows' if field == 'node' else '0'*len(doc['new'][field])
    with pytest.raises(ValueError, match='^invalid rotation proposal$'):
        rotation.check_proposal(doc, path.with_suffix('.manifest'))


def test_peer_manifest_changed_during_confirmation_is_refused(peers):
    import key_rotation as rotation
    path, new = rotation_offer(peers)
    sb = peers[4]
    before = all_bytes(sb.directory)
    def confirm():
        path.with_suffix('.manifest').write_bytes(b'')
        return new.public['key_id']
    with pytest.raises(ValueError, match='invalid rotation manifest'):
        rotation.peer_rotate(sb.directory, path, confirm=confirm)
    assert all_bytes(sb.directory) == before


@pytest.mark.parametrize('case', ['raw-in-memory', 'absent-directory'])
def test_rotation_old_identity_variants_without_raw_disk(peers, case, monkeypatch):
    import key_rotation as rotation
    import shutil
    ready(peers)
    a, b, ex, sa, sb = peers
    phrase, kwargs = rotate_args(peers)
    if case == 'absent-directory':
        shutil.rmtree(sa.directory)
        kwargs['old_key'] = sa.public['key_id']
    else:
        original = signed.read_local
        def read(path, **kw):
            value = original(path, **kw)
            if Path(path) == sa.directory / 'identity.json' and signed.parse(value)['key_id'] == sa.public['key_id']:
                return signed.wire({**sa.public, 'private_key': sa.key.private_bytes_raw().hex()})
            return value
        monkeypatch.setattr(signed, 'read_local', read)
    report = rotation.identity_rotate(sa.directory, a, ex, reason='lost', **kwargs)
    assert report['status'] == 'rotated'
    assert report['trust_restored'] is (case != 'absent-directory')


@pytest.mark.parametrize('case', ['recent', 'pending-file', 'lifetime'])
def test_resume_rechecks_remaining_fences(peers, case, monkeypatch):
    import key_rotation as rotation
    import runtime_lock
    import worker_lifecycle as worker
    from contextlib import ExitStack
    ready(peers)
    a, b, ex, sa, sb = peers
    phrase, kwargs = rotate_args(peers)
    def crash(step):
        if step == 'trust': raise RuntimeError('crash')
    with pytest.raises(ValueError): rotation.identity_rotate(sa.directory, a, ex, reason='planned', _after_step=crash, **kwargs)
    if case == 'recent': next((ex / 'signed-changes' / 'mac').glob('*.json')).touch()
    if case == 'pending-file':
        with sync.connect(a) as c:
            row = c.execute('SELECT uuid,packet FROM _sync_outbox WHERE published=0').fetchone()
        path = ex / 'signed-changes' / 'mac' / (row[0] + '.json')
        path.write_bytes(row[1].encode())
        os.utime(path, (1, 1))
    with ExitStack() as stack:
        if case == 'lifetime':
            runtime = a.parent / 'runtime.json'
            signed.write_local(runtime, {'database': str(a), 'exchange': str(ex), 'node': 'mac'})
            kwargs['runtime'] = runtime
            monkeypatch.setattr(worker, 'status', lambda p: {'state': 'stopped'})
            stack.enter_context(runtime_lock.lock(worker.control({'database': str(a)}, create=True) / 'lifetime.lock', timeout=0))
        before = snapshot(peers)
        with pytest.raises(ValueError): rotation.identity_rotate(sa.directory, a, ex, resume=True, **kwargs)
        assert snapshot(peers) == before


def test_cleanup_crash_after_staging_removal_can_resume(peers, monkeypatch):
    import key_rotation as rotation
    ready(peers)
    a, b, ex, sa, sb = peers
    phrase, kwargs = rotate_args(peers)
    original = Path.unlink
    def fail(path, *args, **kw):
        if path.name == 'rotation-journal.json': raise OSError('injected unlink failure')
        return original(path, *args, **kw)
    monkeypatch.setattr(Path, 'unlink', fail)
    with pytest.raises(ValueError): rotation.identity_rotate(sa.directory, a, ex, reason='planned', **kwargs)
    assert not list(sa.directory.glob('.rotate-*'))
    monkeypatch.setattr(Path, 'unlink', original)
    assert rotation.identity_rotate(sa.directory, a, ex, resume=True, **kwargs)['status'] == 'rotated'


def test_rotation_error_drops_secret_context(peers):
    import key_rotation as rotation
    ready(peers)
    secret = os.urandom(32).hex()
    def broken(*args): raise RuntimeError(secret)
    with pytest.raises(ValueError) as caught:
        rotation.identity_rotate(peers[3].directory, peers[0], peers[2], reason='planned', confirm=lambda w: w, ask=broken, backend=None)
    assert str(caught.value) == rotation.FAILED
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert not (peers[3].directory / 'rotation-journal.json').exists()


def test_same_scope_extra_active_key_refuses_before_journal(peers):
    import key_rotation as rotation
    ready(peers)
    a, b, ex, sa, sb = peers
    other = sealed_identity(sa.directory.with_name('extra-active'), 'mac', sender=sa.public['sender'])
    signed.approve(sa.directory, other.public, other.public['key_id'], GROUP, 'mac', sa.public['sender'])
    phrase, kwargs = rotate_args(peers)
    before = snapshot(peers)
    with pytest.raises(ValueError, match='active identity'):
        rotation.identity_rotate(sa.directory, a, ex, reason='planned', **kwargs)
    assert snapshot(peers) == before


def test_manifest_publication_syncs_parent_directory(tmp_path, monkeypatch):
    import key_rotation as rotation
    import stat
    if os.name == 'nt': pytest.skip('POSIX directory fsync; Windows atomic replace path')
    path = Path(os.path.realpath(tmp_path)) / 'durable.manifest'
    directories = []
    original = os.fsync
    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode): directories.append(True)
        return original(fd)
    monkeypatch.setattr(os, 'fsync', fsync)
    rotation.write_manifest(path, [])
    assert directories, 'manifest publication must durably precede registry/proposal'


def test_streamed_manifest_cannot_follow_symlinks(tmp_path):
    import key_rotation as rotation
    if os.name == 'nt': pytest.skip('POSIX symlink fixture; Windows reparse guards are separately tested')
    root = Path(os.path.realpath(tmp_path))
    target, link = root / 'target', root / 'link'
    target.write_bytes(b'')
    link.symlink_to(target)
    with pytest.raises(ValueError, match='invalid rotation manifest'):
        rotation.read_manifest(link)


@pytest.mark.parametrize('lost', [False, True])
def test_prejournal_failure_removes_only_owned_staging(peers, lost, monkeypatch):
    import key_rotation as rotation
    import shutil
    ready(peers)
    a, b, ex, sa, sb = peers
    phrase, kwargs = rotate_args(peers)
    if lost:
        shutil.rmtree(sa.directory)
        kwargs['old_key'] = sa.public['key_id']
    before = snapshot(peers)
    original = signed.write_local
    def fail(path, *args, **kw):
        if Path(path).name == 'rotation-journal.json': raise OSError('injected before journal')
        return original(path, *args, **kw)
    monkeypatch.setattr(signed, 'write_local', fail)
    with pytest.raises(ValueError): rotation.identity_rotate(sa.directory, a, ex, reason='planned', **kwargs)
    assert snapshot(peers) == before
    assert not list(sa.directory.glob('.rotate-*'))
    if lost: assert not sa.directory.exists()


def test_every_rotation_write_contains_no_unsealed_secret(peers, monkeypatch):
    import key_rotation as rotation
    ready(peers)
    a, b, ex, sa, sb = peers
    phrase, kwargs = rotate_args(peers)
    original = os.fdopen
    written = []
    class Recorder:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): self.stream.__enter__(); return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def __getattr__(self, name): return getattr(self.stream, name)
        def write(self, data):
            written.append(bytes(data))
            return self.stream.write(data)
    def fdopen(fd, mode='r', *args, **kw):
        stream = original(fd, mode, *args, **kw)
        return Recorder(stream) if 'b' in mode and ('w' in mode or '+' in mode) else stream
    monkeypatch.setattr(os, 'fdopen', fdopen)
    report = rotation.identity_rotate(sa.directory, a, ex, reason='planned', **kwargs)
    new = signed.Security(sa.directory, unlock=lambda: phrase)
    secrets = (phrase.encode(), sa.key.private_bytes_raw(), sa.key.private_bytes_raw().hex().encode(),
               new.key.private_bytes_raw(), new.key.private_bytes_raw().hex().encode())
    outputs = written + list(all_bytes(a.parent).values()) + [json.dumps(report).encode()]
    safe = all(secret not in output for secret in secrets for output in outputs)
    assert safe, 'unsealed secret reached a write or report'
    assert written


def test_production_rotation_seal_uses_production_n(peers, monkeypatch):
    import key_rotation as rotation
    ready(peers)
    a, b, ex, sa, sb = peers
    original, calls = kp.seal, []
    def seal(public, raw, phrase, *, n):
        calls.append(n)
        return original(public, raw, phrase, n=2**14)
    monkeypatch.setattr(kp, 'seal', seal)
    phrase, kwargs = rotate_args(peers)
    kwargs.pop('_test_n')
    rotation.identity_rotate(sa.directory, a, ex, reason='planned', **kwargs)
    assert calls == [kp.SCRYPT_N]


@pytest.mark.parametrize('step', ['trust', 'identity', 'db', 'verify', 'proposal'])
def test_crashed_replay_is_byte_identical_to_uninterrupted_replay(peers, tmp_path_factory, step):
    import key_rotation as rotation
    from contextlib import closing
    ready(peers)
    a, b, ex, sa, sb = peers
    phrase, kwargs = rotate_args(peers)
    def first_crash(name):
        if name == 'trust': raise RuntimeError('journal checkpoint')
    with pytest.raises(ValueError): rotation.identity_rotate(sa.directory, a, ex, reason='planned', _after_step=first_crash, **kwargs)
    clone = Path(os.path.realpath(tmp_path_factory.mktemp('replay-reference')))
    # Both branches start from the same sealed journal and DB. All directories
    # use the production private helper; no raw identity fixture is created.
    for source in sorted(a.parent.rglob('*'), key=lambda p: (len(p.parts), str(p))):
        destination = clone / source.relative_to(a.parent)
        if source.is_dir(): backup.private_mkdir(destination)
        elif source.is_file() and source.name not in (a.name, a.name+'-wal', a.name+'-shm'):
            destination.write_bytes(source.read_bytes())
            destination.chmod(0o600)
            signed.windows_private(destination, provision=True)
            os.utime(destination, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns))
    with closing(sqlite3.connect(a)) as source, closing(sqlite3.connect(clone / a.name)) as destination:
        source.backup(destination)
    reference = rotation.identity_rotate(clone / sa.directory.name, clone / a.name, clone / ex.name, resume=True, **kwargs)
    def crash(name):
        if name == step: raise RuntimeError('replay crash')
    with pytest.raises(ValueError): rotation.identity_rotate(sa.directory, a, ex, resume=True, _after_step=crash, **kwargs)
    resumed = rotation.identity_rotate(sa.directory, a, ex, resume=True, **kwargs)
    assert resumed == reference
    assert all_bytes(sa.directory) == all_bytes(clone / sa.directory.name)
    assert all_bytes(ex) == all_bytes(clone / ex.name)
    with rotation.readonly(a) as original, rotation.readonly(clone / a.name) as reference_db:
        # Database contents, including packet TEXT bytes and binding, must match;
        # physical SQLite page layout is not part of packet/recovery identity.
        for table in ('_sync_outbox', '_sync_security', '_sync_history', '_sync_shadow', 'memory_items'):
            assert [tuple(r) for r in original.execute('SELECT * FROM '+table)] == [tuple(r) for r in reference_db.execute('SELECT * FROM '+table)]


@pytest.mark.parametrize('suffix', ['-wal', '-shm'])
def test_readonly_refuses_incomplete_sidecars_without_creating_files(peers, suffix):
    import key_rotation as rotation
    from contextlib import closing
    a, b, ex, sa, sb = peers
    isolated = a.parent / 'incomplete.db'
    with closing(sqlite3.connect(a)) as source, closing(sqlite3.connect(isolated)) as target:
        source.backup(target)
    Path(str(isolated) + suffix).write_bytes(b'')
    before = all_bytes(a.parent)
    with pytest.raises(ValueError, match='database WAL sidecars are incomplete'):
        rotation.compromise_report(isolated, ex, sa.public['key_id'])
    assert all_bytes(a.parent) == before


def test_peer_rotate_refuses_replacing_its_own_key_even_with_matching_scope(peers):
    # A forged proposal reusing this device's public node and sender would match its own trust entry.
    import key_rotation as rotation
    a, b, ex, sa, sb = peers
    attacker = sealed_identity(sb.directory.with_name('attacker-key'), 'windows', sender=sb.public['sender'])
    for parent in (ex / 'pairing', ex / 'pairing' / 'rotations'):
        if not parent.exists(): backup.private_mkdir(parent)
    manifest = ex / 'pairing' / 'rotations' / (attacker.public['key_id'] + '.manifest')
    rotation.write_manifest(manifest, [])
    path = manifest.with_suffix('.json')
    signed.write_local(path, rotation.proposal(attacker, sb.public['key_id'], 'compromised', manifest))
    before = {p.name: p.read_bytes() for p in sb.directory.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match="cannot replace this device's own key"):
        rotation.peer_rotate(sb.directory, path, confirm=lambda: attacker.public['key_id'])
    assert {p.name: p.read_bytes() for p in sb.directory.iterdir() if p.is_file()} == before


def test_check_proposal_rejects_a_manifest_that_no_longer_matches(peers):
    import key_rotation as rotation
    path, new = rotation_offer(peers)
    doc = signed.parse(path.read_bytes())
    rotation.check_proposal(doc, path.with_suffix('.manifest'))
    path.with_suffix('.manifest').write_bytes(b'')
    with pytest.raises(ValueError):
        rotation.check_proposal(doc, path.with_suffix('.manifest'))


def test_resign_refuses_a_signature_that_changes_the_body(peers, monkeypatch):
    import key_rotation as rotation
    ready(peers)
    a, b, ex, sa, sb = peers
    with sync.connect(a) as c:
        before = [tuple(r) for r in c.execute('SELECT uuid,packet,published FROM _sync_outbox ORDER BY uuid')]
    original = rotation._ReplaySecurity.sign
    def altering(self, legacy):
        changed = dict(legacy)
        changed['body'] = list(legacy['body']) + list(legacy['body'])
        return original(self, changed)
    monkeypatch.setattr(rotation._ReplaySecurity, 'sign', altering)
    phrase, kwargs = rotate_args(peers)
    with pytest.raises(ValueError):
        rotation.identity_rotate(sa.directory, a, ex, reason='planned', **kwargs)
    with sync.connect(a) as c:
        assert [tuple(r) for r in c.execute('SELECT uuid,packet,published FROM _sync_outbox ORDER BY uuid')] == before


@pytest.mark.parametrize('protocol', ['legacy', 'indexed'])
def test_rotation_converges_with_and_without_the_indexed_protocol(peers, protocol):
    # The indexed (conflict-resolution) receive path authorizes packets separately; R1/R2 must hold there too.
    import key_rotation as rotation
    import resolution_store as store
    a, b, ex, sa, sb = peers
    if protocol == 'indexed':
        for db in (a, b):
            with sync.connect(db) as c, c:
                store.migrate(c)
    ready(peers)  # one published old-key packet b has not received, plus one pending packet
    phrase, kwargs = rotate_args(peers)
    report = rotation.identity_rotate(sa.directory, a, ex, reason='compromised', **kwargs)
    rotation.peer_rotate(sb.directory, ex / report['proposal'], confirm=lambda: report['new_fingerprint'])
    new_a = signed.Security(sa.directory, unlock=lambda: phrase)
    sync.publish(a, ex, security=new_a)
    received_b = sync.receive(b, ex, security=sb)
    received_a = sync.receive(a, ex, security=new_a)
    assert not received_a.get('invalid') and not received_b.get('invalid'), (received_a, received_b)
    assert received_b['applied'] == 2


@pytest.fixture
def indexed_peers(peers):
    import resolution_store as store
    for db in peers[:2]:
        with sync.connect(db) as c, c:
            store.migrate(c)
    return peers


def evidence_rows(db):
    with sync.connect(db) as c:
        return [tuple(r) for r in c.execute(
            'SELECT uuid,envelope_digest,envelope_json,key_id FROM _sync_packet_evidence ORDER BY uuid')]


def pending_texts(db):
    with sync.connect(db) as c:
        return dict(c.execute('SELECT uuid,packet FROM _sync_outbox WHERE published=0'))


@pytest.mark.parametrize('lost', [False, True])
def test_publish_rotation_preserves_indexed_v2(indexed_peers, lost):
    import key_rotation as rotation
    import resolution_apply as apply
    a, b, ex, sa, sb = indexed_peers
    ready(indexed_peers)
    with sync.connect(a) as c, c:
        apply._index_local(c, sa)
    evidence = evidence_rows(a)
    pending = pending_texts(a)
    phrase, kwargs = rotate_args(indexed_peers)
    words = []
    def confirm(word):
        words.append(word)
        return word
    kwargs['confirm'] = confirm
    if lost:
        (sa.directory / 'identity.json').unlink()
        (sa.directory / 'trust.json').unlink()
        kwargs['old_key'] = sa.public['key_id']
    report = rotation.identity_rotate(sa.directory, a, ex, reason='lost' if lost else 'planned', **kwargs)
    assert words == ['STOPPED', 'PUBLISH']
    assert report['published_pending'] == len(pending) and report['manifest_packets'] == 2
    assert not pending_texts(a)
    for uid, text in pending.items():
        assert (ex / 'signed-changes' / 'mac' / (uid + '.json')).read_bytes() == text.encode('utf-8')
    assert evidence_rows(a) == evidence
    rotation.peer_rotate(sb.directory, ex / report['proposal'], confirm=lambda: report['new_fingerprint'])
    new = signed.Security(sa.directory, unlock=lambda: phrase)
    assert sync.receive(a, ex, security=new)['invalid'] == 0
    result = sync.receive(b, ex, security=sb)
    assert result['invalid'] == 0 and result['applied'] == 2


def pending_resolution(peers, monkeypatch):
    """Real activation/prepare/commit flow; all identities sealed, OS backend fake."""
    import os_unlock as ou
    import protocol_activation as activation
    import resolution_apply as apply
    from test_os_unlock import FakeBackend as UnlockBackend
    from test_protocol_activation import enable
    from test_resolution_apply import edit, retain, resolve
    a, b, ex, sa, sb = peers
    p = edit(a, dict(x=1, y=2), sa)
    q = edit(b, dict(x=3, z=4), sb)
    retain(a, [p, q], sa)
    # Install an OS-wrapped key using only a fake, in-memory backend. No passphrase
    # or private key is written to disk by the fixture.
    backend = UnlockBackend()
    wrapping = os.urandom(32)
    backend.store(sa.public['key_id'], wrapping)
    identity = signed.parse(signed.read_local(sa.directory / 'identity.json', private=True))
    identity['os_unlock'] = ou.seal(sa.public, sa.key.private_bytes_raw(), backend.name, wrapping)
    signed.write_local(sa.directory / 'identity.json', identity)
    monkeypatch.setattr(ou, 'current_backend', lambda: backend)
    ou.reset_cache()
    runtime = sa.directory / 'resolution-runtime.json'
    signed.write_local(runtime, dict(app_root=str(sa.directory), database=str(a), exchange=str(ex),
                                    node='mac', security_dir=str(sa.directory), protocol_capability=1))
    enable(activation, runtime)
    # resolve() reuses the already activated database, preserving the real signer.
    r = resolve(apply, a, [p, q], dict(x=5, y=2, z=4), sa)
    ou.reset_cache()
    backup.private_mkdir(ex / 'status')
    signed.write_local(ex / 'status' / 'windows.json', {'group': GROUP})
    return p, q, r


def test_publish_rotation_pending_v3_converges(indexed_peers, monkeypatch):
    import key_rotation as rotation
    from test_resolution_apply import vector
    a, b, ex, sa, sb = indexed_peers
    p, q, r = pending_resolution(indexed_peers, monkeypatch)
    pending = pending_texts(a)
    evidence = evidence_rows(a)
    assert r['format'] == signed.RESOLUTION_FORMAT
    phrase, kwargs = rotate_args(indexed_peers)
    report = rotation.identity_rotate(sa.directory, a, ex, reason='planned', **kwargs)
    assert report['published_pending'] == 2 and report['manifest_packets'] == 2
    assert evidence_rows(a) == evidence
    for uid, text in pending.items():
        assert (ex / 'signed-changes' / 'mac' / (uid + '.json')).read_bytes() == text.encode('utf-8')
    manifest = rotation.read_manifest((ex / report['proposal']).with_suffix('.manifest'))['packets']
    assert manifest == {v['uuid']: signed.receipt_digest(v) for v in (p, r)}
    # B has never committed P: it has its own conflicting Q only.
    with sync.connect(b) as c:
        assert not c.execute('SELECT 1 FROM _sync_receipts WHERE uuid=?', (p['uuid'],)).fetchone()
    rotation.peer_rotate(sb.directory, ex / report['proposal'], confirm=lambda: report['new_fingerprint'])
    sync.publish(b, ex, security=sb)
    result = sync.receive(b, ex, security=sb)
    assert result['invalid'] == result['pending'] == result['conflict'] == 0
    assert vector(a) == vector(b) == dict(x=5, y=2, z=4)
    assert sync.receive(a, ex, security=signed.Security(sa.directory, unlock=lambda: phrase))['invalid'] == 0
    with sync.connect(b) as c:
        assert c.execute('SELECT envelope_digest FROM _sync_packet_dispositions WHERE packet_uuid=?',
                         (p['uuid'],)).fetchone()[0] == signed.envelope_digest(p)


@pytest.mark.parametrize('approved', [True, False])
def test_publish_rotation_manifest_evidence_authorization(indexed_peers, monkeypatch, approved):
    import key_rotation as rotation
    import resolution_apply as apply
    from test_resolution_apply import retain, receive, vector
    a, b, ex, sa, sb = indexed_peers
    p, q, r = pending_resolution(indexed_peers, monkeypatch)
    # Hold both packets while authorized; holding is deliberately not a receipt.
    retain(b, [p, r], sb)
    new = sealed_identity(sa.directory.with_name('offer-key'), 'mac', sender=sa.public['sender'])
    for parent in (ex / 'pairing', ex / 'pairing' / 'rotations'):
        backup.private_mkdir(parent)
    manifest = ex / 'pairing' / 'rotations' / (new.public['key_id'] + '.manifest')
    packets = [r, p] if approved else [r]
    rotation.write_manifest(manifest, sorted((v['uuid'], signed.receipt_digest(v)) for v in packets))
    proposal = manifest.with_suffix('.json')
    signed.write_local(proposal, rotation.proposal(new, sa.public['key_id'], 'planned', manifest))
    rotation.peer_rotate(sb.directory, proposal, confirm=lambda: new.public['key_id'])
    before = vector(b)
    if approved:
        assert receive(apply, b, r, sb).status == 'applied'
        assert vector(b) == dict(x=5, y=2, z=4)
    else:
        with pytest.raises(ValueError):
            receive(apply, b, r, sb)
        assert vector(b) == before
        with sync.connect(b) as c:
            assert c.execute('SELECT count(*) FROM _sync_packet_dispositions').fetchone()[0] == 0


@pytest.mark.parametrize('point', ['before-file', pytest.param('after-file', marks=pytest.mark.skipif(os.name == 'nt', reason='POSIX directory fsync fault')), 'after-commit'])
def test_publish_rotation_crash_resume_identical(indexed_peers, monkeypatch, point):
    import key_rotation as rotation
    a, b, ex, sa, sb = indexed_peers
    ready(indexed_peers)
    pending = pending_texts(a)
    phrase, kwargs = rotate_args(indexed_peers)
    # Crash at real filesystem publication boundaries, not at a simulated DB update.
    original = os.fsync
    def crash_fsync(fd):
        original(fd)
        if point == 'before-file' and not os.path.isdir(ex / 'signed-changes' / 'mac'):
            return
        import stat
        info = os.fstat(fd)
        if point == 'before-file' and stat.S_ISREG(info.st_mode):
            raise OSError('injected publish crash')
        if point == 'after-file' and stat.S_ISDIR(info.st_mode):
            raise OSError('injected publish crash')
    def step(name):
        if name == 'identity' and point != 'after-commit':
            monkeypatch.setattr(os, 'fsync', crash_fsync)
        if name == 'publish' and point == 'after-commit':
            raise RuntimeError('injected publish crash')
    with pytest.raises(ValueError):
        rotation.identity_rotate(sa.directory, a, ex, reason='planned', _after_step=step, **kwargs)
    monkeypatch.setattr(os, 'fsync', original)
    for uid, text in pending.items():
        path = ex / 'signed-changes' / 'mac' / (uid + '.json')
        assert not path.exists() or path.read_bytes() == text.encode('utf-8')
    assert not list((ex / 'signed-changes' / 'mac').glob('*.tmp'))
    if point != 'after-commit': assert pending_texts(a) == pending
    report = rotation.identity_rotate(sa.directory, a, ex, resume=True, **kwargs)
    assert report['published_pending'] == len(pending)
    assert not pending_texts(a)
    for uid, text in pending.items():
        assert (ex / 'signed-changes' / 'mac' / (uid + '.json')).read_bytes() == text.encode('utf-8')


def test_publish_rotation_existing_different_file_refuses(indexed_peers):
    import key_rotation as rotation
    a, b, ex, sa, sb = indexed_peers
    ready(indexed_peers)
    uid = next(iter(pending_texts(a)))
    path = ex / 'signed-changes' / 'mac' / (uid + '.json')
    path.write_bytes(b'different immutable bytes')
    os.utime(path, (1, 1))
    before = snapshot(indexed_peers)
    _, kwargs = rotate_args(indexed_peers)
    with pytest.raises(ValueError, match='pending packet is already'):
        rotation.identity_rotate(sa.directory, a, ex, reason='planned', **kwargs)
    assert snapshot(indexed_peers) == before
