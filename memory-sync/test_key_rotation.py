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
