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
