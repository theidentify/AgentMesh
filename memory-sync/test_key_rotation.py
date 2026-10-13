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
