import copy
import importlib.abc
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest
import memory_sync as sync
import signed_packets as signed
import sqlite_memory as backend
from test_memory_sync import GROUP, insert_family


@pytest.fixture
def secure_peers(tmp_path):
    a, b = tmp_path / 'a.db', tmp_path / 'b.db'
    backend.init_database(a)
    with sync.connect(a) as c, c:
        c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(1,'fact','global','baseline')")
    with sync.connect(a) as c, sqlite3.connect(b) as d:
        c.backup(d)
    sync.initialize(a, 'mac', GROUP); sync.initialize(b, 'windows', GROUP)
    da, db = tmp_path / 'keys-a', tmp_path / 'keys-b'
    pa = signed.init_identity(da, GROUP, 'mac')
    pb = signed.init_identity(db, GROUP, 'windows')
    signed.approve(da, pb, pb['key_id'], GROUP, 'windows', pb['sender'])
    signed.approve(db, pa, pa['key_id'], GROUP, 'mac', pa['sender'])
    ex = tmp_path / 'exchange'; ex.mkdir(); (ex / '.stfolder').mkdir()
    return a, b, ex, signed.Security(da), signed.Security(db)


def packet(peers):
    a, b, ex, sa, sb = peers
    with sync.connect(a) as c, c: c.execute("UPDATE memory_items SET content='signed incoming' WHERE id=1")
    assert sync.capture(a, security=sa)['changes'] == 1
    assert sync.publish(a, ex, security=sa)['published'] == 1
    path = next((ex / 'signed-changes' / 'mac').glob('*.json'))
    return path, signed.parse(path.read_bytes())


def assert_unreceived(b):
    with sync.connect(b) as c:
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0] == 0
        assert c.execute('SELECT content FROM memory_items WHERE id=1').fetchone()[0] == 'baseline'
        assert c.execute('SELECT importing FROM _sync_config').fetchone()[0] == 0


def test_two_way_eight_tables_provenance_replay_no_echo(secure_peers):
    a, b, ex, sa, sb = secure_peers
    for src, dst, ss, ds, suffix in ((a, b, sa, sb, 'first'), (b, a, sb, sa, 'reverse')):
        ids = insert_family(src, suffix)
        assert sync.cycle(src, ex, security=ss)['capture']['changes'] == 8
        assert sync.receive(dst, ex, security=ds)['applied'] == 1
        with sync.connect(src) as c, sync.connect(dst) as d:
            assert sync.rows(c) == sync.rows(d)
            assert d.execute('SELECT event_id FROM memory_sources WHERE memory_id=?', (ids['memory_items'],)).fetchone()[0] == ids['observation_events']
            assert d.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert not d.execute('PRAGMA foreign_key_check').fetchall()
        assert sync.receive(dst, ex, security=ds)['applied'] == 0
        assert sync.capture(dst, security=ds)['changes'] == 0
    assert len(list(ex.rglob('*.json'))) == 2


@pytest.mark.parametrize('field', ['format', 'encoding', 'group', 'node', 'sender', 'uuid', 'body', 'checksum', 'key_id', 'signature', 'extra'])
def test_every_security_field_tampering_rejected(secure_peers, field):
    a, b, ex, sa, sb = secure_peers
    path, p = packet(secure_peers)
    if field == 'body':
        p['body'][0]['row']['content'] = 'tampered'
        p['checksum'] = signed.hashlib.sha256(signed.typed(p['body'])).hexdigest()
    elif field in ('group', 'sender', 'uuid'): p[field] = str(uuid.uuid4())
    elif field == 'node': p[field] = 'windows'
    elif field in ('signature', 'checksum', 'key_id'): p[field] = '0' * len(p[field])
    elif field == 'extra': p[field] = 'untrusted public key'
    else: p[field] = 'different protocol'
    path.write_bytes(signed.wire(p))
    assert sync.receive(b, ex, security=sb)['invalid'] == 1
    assert_unreceived(b)


def test_no_trust_from_packet_public_key_or_unknown_key(secure_peers):
    a, b, ex, sa, sb = secure_peers
    path, p = packet(secure_peers)
    trust = signed.read_trust(sb.directory)
    del trust['peers'][sa.public['key_id']]
    signed.write_local(sb.directory / 'trust.json', trust)
    assert sync.receive(b, ex, security=sb)['invalid'] == 1
    assert_unreceived(b)


def test_revocation_live_and_rotation_same_sender(secure_peers):
    a, b, ex, sa, sb = secure_peers
    path, old = packet(secure_peers)
    signed.revoke(sb.directory, sa.public['key_id'])
    assert sync.receive(b, ex, security=sb)['invalid'] == 1
    with pytest.raises(ValueError):
        signed.approve(sb.directory, sa.public, sa.public['key_id'], GROUP, 'mac', sa.public['sender'])
    new_dir = sa.directory.with_name('rotated')
    public = signed.init_identity(new_dir, GROUP, 'mac', sender=sa.public['sender'])
    signed.approve(sb.directory, public, public['key_id'], GROUP, 'mac', public['sender'])
    new = signed.Security(new_dir)
    legacy = {k: old[k] for k in ('group', 'node', 'uuid', 'body')}
    legacy.update(format=sync.FORMAT, checksum=sync.digest(old['body']))
    fresh = new.sign(legacy)
    assert sb.verify(fresh, GROUP, 'mac') == fresh
    with pytest.raises(ValueError): sb.verify(old, GROUP, 'mac')
    # Old unpublished packets cannot be re-signed transparently after local revoke.
    signed.revoke(sa.directory, sa.public['key_id'])
    with pytest.raises(ValueError): sync.capture(a, security=sa)


@pytest.mark.parametrize('scope', ['group', 'node', 'sender', 'fingerprint'])
def test_explicit_approval_checks_every_scope(secure_peers, scope):
    a, b, ex, sa, sb = secure_peers
    values = dict(fingerprint=sa.public['key_id'], group=GROUP, node='mac', sender=sa.public['sender'])
    values[scope] = 'windows' if scope == 'node' else '0' * 64 if scope == 'fingerprint' else str(uuid.uuid4())
    with pytest.raises(ValueError): signed.approve(sb.directory, sa.public, **values)


def test_same_allocation_slot_cannot_bind_different_machine(secure_peers):
    a, b, ex, sa, sb = secure_peers
    other = signed.init_identity(sa.directory.with_name('other-mac'), GROUP, 'mac')
    with pytest.raises(ValueError): signed.approve(sb.directory, other, other['key_id'], GROUP, 'mac', other['sender'])


@pytest.mark.parametrize('damage', ['unsigned', 'folder', 'filename', 'symlink-file', 'symlink-folder', 'too-large', 'duplicate', 'nan', 'infinity', 'overflow', 'surrogate', 'deep', 'partial'])
def test_hostile_file_and_encoding_rejected_without_receipt(secure_peers, damage):
    a, b, ex, sa, sb = secure_peers
    path, p = packet(secure_peers)
    if damage == 'unsigned':
        p = {k: p[k] for k in ('group', 'node', 'uuid', 'body')}; p.update(format=sync.FORMAT, checksum=sync.digest(p['body']))
        path.write_bytes(signed.wire(p))
    elif damage == 'folder':
        dest = ex / 'signed-changes' / 'linux'; dest.mkdir(); path.rename(dest / path.name)
    elif damage == 'filename': path.rename(path.with_name(str(uuid.uuid4()) + '.json'))
    elif damage == 'symlink-file':
        outside = ex.parent / 'outside.json'; outside.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(outside)
    elif damage == 'symlink-folder':
        outside = ex.parent / 'outside-folder'; path.parent.rename(outside); (ex / 'signed-changes' / 'mac').symlink_to(outside, target_is_directory=True)
    elif damage == 'too-large': path.write_bytes(b' ' * (signed.MAX_BYTES + 1))
    elif damage == 'duplicate': path.write_bytes(path.read_bytes().replace(b'"format":', b'"format":"bad","format":', 1))
    elif damage == 'surrogate': path.write_bytes(b'{"bad":"\\ud800"}')
    elif damage == 'deep': path.write_bytes(b'[' * 70 + b'0' + b']' * 70)
    elif damage == 'partial': path.write_bytes(path.read_bytes()[:40])
    else: path.write_bytes(('{"bad":' + {'nan': 'NaN', 'infinity': 'Infinity', 'overflow': '1e400'}[damage] + '}').encode())
    assert sync.receive(b, ex, security=sb)['invalid'] == 1
    assert_unreceived(b)


@pytest.mark.parametrize('operation', ['capture', 'publish', 'receive', 'cycle', 'worker'])
def test_signed_database_refuses_unsigned_downgrade(secure_peers, operation):
    a, b, ex, sa, sb = secure_peers
    sync.capture(a, security=sa)
    with pytest.raises(ValueError):
        if operation == 'capture': sync.capture(a)
        elif operation == 'worker':
            import sync_worker
            sync_worker.run_once(a, ex)
        else: getattr(sync, operation)(a, ex)


def test_unsigned_backlog_refuses_activation_atomically(secure_peers):
    a, b, ex, sa, sb = secure_peers
    with sync.connect(a) as c, c: c.execute("UPDATE memory_items SET content='legacy' WHERE id=1")
    sync.capture(a)
    with sync.connect(a) as c:
        before = c.execute('SELECT uuid,packet FROM _sync_outbox').fetchall()
    with pytest.raises(ValueError, match='backlog'): sync.cycle(a, ex, security=sa)
    with sync.connect(a) as c:
        assert [tuple(x) for x in c.execute('SELECT uuid,packet FROM _sync_outbox')] == [tuple(x) for x in before]
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchall()
    assert sync.publish(a, ex)['published'] == 1
    assert sync.receive(b, ex)['applied'] == 1
    # Retained legacy archive is not re-applied or treated as signed trust.
    assert sync.cycle(a, ex, security=sa)['receive']['invalid'] == 0
    assert sync.cycle(b, ex, security=sb)['receive']['invalid'] == 0
    assert next((ex / 'changes' / 'mac').glob('*.json')).exists()


def test_database_scope_and_private_directory_in_exchange_fail(secure_peers):
    a, b, ex, sa, sb = secure_peers
    with pytest.raises(ValueError): sync.cycle(a, ex, security=sb)
    inside = ex / 'keys'
    signed.init_identity(inside, GROUP, 'mac')
    with pytest.raises(ValueError): sync.cycle(a, ex, security=signed.Security(inside))
    with sync.connect(a) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchall()


def test_publish_symlink_refused_and_crash_retry_is_immutable(secure_peers, monkeypatch):
    a, b, ex, sa, sb = secure_peers
    with sync.connect(a) as c, c: c.execute("UPDATE memory_items SET content='retry' WHERE id=1")
    sync.capture(a, security=sa)
    outside = ex.parent / 'outside'; outside.mkdir()
    (ex / 'signed-changes').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError): sync.publish(a, ex, security=sa)
    assert not list(outside.iterdir())
    (ex / 'signed-changes').unlink()
    original = sync.os.replace
    def broken(*args): raise OSError('offline')
    monkeypatch.setattr(sync.os, 'replace', broken)
    assert sync.publish(a, ex, security=sa)['unavailable']
    monkeypatch.setattr(sync.os, 'replace', original)
    assert sync.publish(a, ex, security=sa)['published'] == 1
    path = next(ex.rglob('*.json')); data = path.read_bytes()
    with sync.connect(a) as c, c: c.execute('UPDATE _sync_outbox SET published=0')
    assert sync.publish(a, ex, security=sa)['published'] == 1
    assert path.read_bytes() == data
    assert sync.receive(b, ex, security=sb)['applied'] == 1


def test_conflict_rollback_and_signed_receipt_covers_header(secure_peers):
    a, b, ex, sa, sb = secure_peers
    path, p = packet(secure_peers)
    with sync.connect(b) as c, c: c.execute("UPDATE memory_items SET content='local' WHERE id=1")
    assert sync.receive(b, ex, security=sb)['conflict'] == 1
    with sync.connect(b) as c:
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0] == 0
        assert c.execute('SELECT content FROM memory_items WHERE id=1').fetchone()[0] == 'local'
        assert c.execute('SELECT importing FROM _sync_config').fetchone()[0] == 0
        assert all(r[0] == 'Conflict' for r in c.execute('SELECT message FROM _sync_diagnostics'))
    # Receipt identity includes authenticated scope, not just content checksum.
    changed = copy.deepcopy(p); changed['sender'] = str(uuid.uuid4())
    assert signed.receipt_digest(changed) != signed.receipt_digest(p)


def test_signature_checked_even_after_successful_replay(secure_peers):
    a, b, ex, sa, sb = secure_peers
    path, p = packet(secure_peers)
    assert sync.receive(b, ex, security=sb)['applied'] == 1
    signed.revoke(sb.directory, sa.public['key_id'])
    assert sync.receive(b, ex, security=sb)['invalid'] == 1
    with sync.connect(b) as c:
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0] == 1


def test_out_of_order_signed_chain_pending_then_applied(secure_peers):
    a, b, ex, sa, sb = secure_peers
    first, _ = packet(secure_peers); data = first.read_bytes(); first.unlink()
    with sync.connect(a) as c, c: c.execute("UPDATE memory_items SET content='second' WHERE id=1")
    sync.cycle(a, ex, security=sa)
    assert sync.receive(b, ex, security=sb)['pending'] == 1
    assert_unreceived(b)
    first.write_bytes(data)
    assert sync.receive(b, ex, security=sb)['applied'] == 2
    assert sync.receive(b, ex, security=sb)['applied'] == 0


def test_typed_encoding_golden_unicode_and_numeric_rules():
    assert signed.typed({'b': 1.0, 'a': [None, True, False, -2, 'é']}) == b'o2:s1:al5:ntfi-2;s2:\xc3\xa9s1:bd\x3f\xf0\x00\x00\x00\x00\x00\x00'
    assert signed.typed({'x': 'é', '𐀀': 'ไทย'}) == signed.typed({'𐀀': 'ไทย', 'x': 'é'})
    assert signed.typed('é') != signed.typed('e\u0301')
    assert signed.typed(1) != signed.typed(1.0)
    assert signed.typed(0.0) != signed.typed(-0.0)
    assert signed.typed(signed.parse(b'1e0')) == signed.typed(1.0)
    for bad in (float('nan'), float('inf'), 2**63, -(2**63)-1, '\ud800'):
        with pytest.raises(ValueError): signed.typed(bad)
    with pytest.raises(ValueError): signed.typed([None] * signed.MAX_VALUES)
    with pytest.raises(ValueError): signed.parse(b'{"a":1,"\\u0061":2}')


def test_json_presentation_change_does_not_change_signature(secure_peers):
    a, b, ex, sa, sb = secure_peers
    path, p = packet(secure_peers)
    path.write_text(json.dumps(p, ensure_ascii=True, indent=4), encoding='utf-8')
    assert sync.receive(b, ex, security=sb)['applied'] == 1


def test_permissions_no_reinit_and_public_only_cli(secure_peers):
    a, b, ex, sa, sb = secure_peers
    assert signed.Security(sa.directory).public == sa.public
    with pytest.raises(FileExistsError): signed.init_identity(sa.directory, GROUP, 'mac')
    if os.name != 'nt':
        for name in ('identity.json', 'trust.json'):
            path = sa.directory / name
            assert path.stat().st_mode & 0o777 == 0o600
            path.chmod(0o644)
            with pytest.raises(ValueError): signed.Security(sa.directory)
            path.chmod(0o600)
    output = ex.parent / 'public.json'
    result = subprocess.run([sys.executable, signed.__file__, '--security-dir', str(sa.directory), 'export-public', '--output', str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    public = signed.parse(output.read_bytes())
    assert public == sa.public and 'private_key' not in public
    assert 'private_key' not in result.stdout and 'private_key' not in result.stderr


def test_missing_crypto_fail_closed_but_legacy_still_operates(tmp_path):
    # Real subprocess denies crypto imports; not an in-process fake signature provider.
    code = '''import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('cryptography'): raise ImportError('blocked for test')
sys.meta_path.insert(0, Deny())
import memory_sync, signed_packets, sqlite_memory
from pathlib import Path
root=Path(sys.argv[1]); db=root/'legacy.db'
sqlite_memory.init_database(db)
memory_sync.initialize(db,'mac','00000000-0000-4000-8000-000000000001')
assert memory_sync.cycle(db,root/'exchange')['capture']['changes']==0
try: signed_packets.Security(root/'missing')
except ValueError as e: assert 'requires cryptography' in str(e)
else: raise AssertionError('strict crypto fallback')
print('legacy works; strict fails closed')
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], capture_output=True, text=True, cwd=Path(sync.__file__).parent)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'legacy works; strict fails closed'


def test_strict_worker_status_is_not_a_signed_application_receipt(secure_peers):
    import sync_worker
    a, b, ex, sa, sb = secure_peers
    packet(secure_peers)
    report = sync_worker.run_once(b, ex, security_dir=sb.directory)
    assert report['cycle']['receive']['applied'] == 1
    assert json.loads((ex / 'status' / 'windows.json').read_text())['sync']['received'] == 1


def test_package_allowlist_contains_security_but_never_keys():
    import build_package
    for name in ('signed_packets.py', 'SIGNED-SYNC.md', 'requirements-security.txt'):
        assert name in build_package.FILES
    assert not any('identity.json' in name or 'trust.json' in name for name in build_package.FILES)
