import copy
import hashlib
import os
import uuid
from pathlib import Path

import pytest
import key_protection as kp
import signed_packets as signed
import identity_backup as backup

@pytest.fixture
def material():
    key = signed.crypto()[0].generate()
    pub = key.public_key().public_bytes_raw()
    public = dict(format='agentmesh-peer-key-v1', group=str(uuid.uuid4()), node='mac',
                  sender=str(uuid.uuid4()), key_id=hashlib.sha256(pub).hexdigest(), public_key=pub.hex())
    trust = {'format': 'agentmesh-trust-v1', 'peers': {public['key_id']: {**public, 'revoked': False}}}
    return public, key.private_bytes_raw(), trust

@pytest.fixture
def secret():
    return os.urandom(32).hex()

def document(material, secret):
    public, raw, trust = material
    manifest = {k: v for k, v in public.items() if k != 'format'}
    manifest['created_at'] = '2026-10-13T00:00:00Z'
    return backup.seal(manifest, raw, trust, secret, n=2**14)

def test_format_roundtrip(material, secret):
    raw, trust, captured = backup.open_backup(document(material, secret), secret)
    same = raw == material[1]
    assert same, 'key mismatch'
    assert trust == material[2]
    assert captured.endswith('Z')

@pytest.mark.parametrize('field', ['ciphertext', 'nonce', 'key_id', 'sender', 'group', 'node', 'public_key', 'created_at'])
def test_authenticated_fields(material, secret, field):
    doc = document(material, secret)
    obj = doc if field in ('ciphertext', 'nonce') else doc['manifest']
    if field in ('sender', 'group'): obj[field] = str(uuid.uuid4())
    elif field == 'node': obj[field] = 'windows'
    elif field == 'created_at': obj[field] = '2026-10-12T00:00:00Z'
    else: obj[field] = ('0' if obj[field][0] != '0' else '1') + obj[field][1:]
    with pytest.raises(ValueError, match='^' + backup.OPEN_FAILED + '$'): backup.open_backup(doc, secret)

def test_wrong_passphrase(material, secret):
    with pytest.raises(ValueError, match=backup.OPEN_FAILED):
        backup.open_backup(document(material, secret), os.urandom(32).hex())

@pytest.mark.parametrize('field,value', [('n', 2**30), ('n', 1000), ('r', 1), ('p', 2), ('salt', '00'),
    ('cipher', 'unknown'), ('kdf', 'unknown'), ('format', 'unknown'), ('extra', True),
    ('ciphertext', '00' * ((1 << 20) + 1))])
def test_invalid_before_kdf(material, secret, monkeypatch, field, value):
    doc = document(material, secret)
    doc[field] = value
    monkeypatch.setattr(kp, '_derive', lambda *a: pytest.fail('KDF called'))
    with pytest.raises(ValueError, match='^invalid backup file$'): backup.check(doc)

def test_missing_field_and_bad_fingerprint(material, secret):
    doc = document(material, secret)
    del doc['nonce']
    with pytest.raises(ValueError, match=backup.INVALID): backup.check(doc)
    doc = document(material, secret)
    doc['manifest']['key_id'] = '0' * 64
    with pytest.raises(ValueError, match=backup.INVALID): backup.check(doc)

@pytest.mark.parametrize('kind', ['key', 'missing-self', 'different-self'])
def test_invalid_plaintext(material, secret, kind):
    public, raw, trust = copy.deepcopy(material)
    if kind == 'key': raw = signed.crypto()[0].generate().private_bytes_raw()
    elif kind == 'missing-self': trust['peers'].clear()
    else: trust['peers'][public['key_id']]['sender'] = str(uuid.uuid4())
    manifest = {k: v for k, v in public.items() if k != 'format'}
    manifest['created_at'] = '2026-10-13T00:00:00Z'
    doc = backup.seal(manifest, raw, trust, secret, n=2**14)
    with pytest.raises(ValueError, match=backup.OPEN_FAILED if kind == 'key' else backup.INVALID):
        backup.open_backup(doc, secret)

@pytest.mark.parametrize('marker', ['.stfolder', '.git', '.STFOLDER', '.GIT'])
@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_unsafe_markers(tmp_path, monkeypatch, marker, platform):
    monkeypatch.setattr(backup.sys, 'platform', platform)
    (tmp_path / marker).touch()
    child = tmp_path / 'child'
    child.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(child, target_is_directory=True)
    with pytest.raises(ValueError, match='backup location is inside'):
        backup.unsafe_location(alias / 'file')


def test_cloud_location(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, 'CLOUD_ROOTS', (tmp_path / 'cloud',))
    assert backup.unsafe_location(tmp_path / 'cloud' / 'file') == 'cloud'
    assert backup.unsafe_location(tmp_path / 'file') is None


@pytest.mark.parametrize('fallback', [False, True])
def test_write_new(tmp_path, monkeypatch, fallback):
    import errno
    if fallback:
        def unsupported(*a): raise OSError(errno.EPERM, 'unavailable')
        monkeypatch.setattr(backup.os, 'link', unsupported)
    path = tmp_path / 'backup'
    assert backup.write_new(path, b'encrypted payload') == 'owner-only'
    with pytest.raises(ValueError, match='backup file already exists'):
        backup.write_new(path, b'replacement')
    assert path.read_bytes() == b'encrypted payload'
    assert list(tmp_path.iterdir()) == [path]


def test_permissions_unsupported(tmp_path, monkeypatch):
    import errno
    def unsupported(*a): raise OSError(errno.ENOTSUP, 'unsupported')
    monkeypatch.setattr(backup.os, 'chmod', unsupported)
    assert backup.write_new(tmp_path / 'backup', b'ciphertext') == 'unsupported-by-filesystem'

@pytest.fixture
def live(tmp_path, material, secret):
    root = Path(os.path.realpath(tmp_path)) / 'security'
    root.mkdir(mode=0o700)
    public, raw, trust = material
    signed.write_local(root / 'identity.json', {**public, 'protection': kp.seal(public, raw, secret, n=2**14)})
    signed.write_local(root / 'trust.json', trust)
    return root


def test_evidence(live, material):
    key_id = material[0]['key_id']
    assert backup.status(live)['state'] == 'none'
    digest = hashlib.sha256(b'encrypted').hexdigest()
    backup.record(live, key_id, digest, 'verified_at', backup.now())
    result = backup.status(live)
    assert result['state'] == 'verified'
    assert result['backups'][0]['verified_age_seconds'] >= 0
    backup.record(live, key_id, digest, 'drilled_at', backup.now())
    assert backup.status(live)['state'] == 'drilled'
    assert signed.Security(live).public == material[0]
    assert signed.read_trust(live) == material[2]


def test_stale_and_malformed_status(live):
    signed.write_local(live / 'backup-status.json', {'format': 'agentmesh-backup-status-v1', 'key_id': '0'*64, 'backups': {}})
    assert backup.status(live)['state'] == 'none'
    signed.write_local(live / 'backup-status.json', {})
    with pytest.raises(ValueError, match='invalid backup status'): backup.status(live)


def answers(*values):
    it = iter(values)
    return lambda *a: next(it)


def test_create_verify(live, material, secret, tmp_path):
    phrase = os.urandom(32).hex()
    output = Path(os.path.realpath(tmp_path)) / 'backup.json'
    result = backup.create(live, output, ask=answers(secret, phrase, phrase), confirm=lambda word: word, _test_n=2**14)
    assert result['status'] == 'created'
    assert set(result) == {'status', 'key_id', 'file_sha256', 'verified_at', 'permissions'}
    doc = signed.parse(signed.read_local(output))
    assert backup.public(doc['manifest']) == material[0]
    assert backup.status(live)['state'] == 'verified'
    result = backup.verify(output, live, ask=answers(phrase))
    assert result['status'] == 'verified'
    assert 'warning' in result


@pytest.mark.parametrize('case', ['confirmation', 'short', 'mismatch', 'equal', 'readback', 'cloud'])
def test_create_refusals(live, secret, tmp_path, monkeypatch, case):
    phrase = os.urandom(32).hex()
    output = Path(os.path.realpath(tmp_path)) / 'backup.json'
    confirm = lambda word: word
    values = [secret, phrase, phrase]
    if case == 'confirmation': confirm = lambda word: ''
    if case == 'short': values[1] = ''
    if case == 'mismatch': values[2] = os.urandom(32).hex()
    if case == 'equal': values = [secret, secret, secret]
    if case == 'cloud':
        monkeypatch.setattr(backup, 'CLOUD_ROOTS', (output.parent,))
        confirm = lambda word: ''
    if case == 'readback':
        def fail(*a): raise ValueError(backup.OPEN_FAILED)
        monkeypatch.setattr(backup, 'open_backup', fail)
    with pytest.raises(ValueError):
        backup.create(live, output, ask=answers(*values), confirm=confirm, _test_n=2**14)
    assert not output.exists()
    assert not (live / 'backup-status.json').exists()


def test_raw_create_in_memory(live, material, secret, tmp_path, monkeypatch):
    original = signed.read_local
    def read(path, **kw):
        if Path(path) == live / 'identity.json':
            return signed.wire({**material[0], 'private_key': material[1].hex()})
        return original(path, **kw)
    monkeypatch.setattr(signed, 'read_local', read)
    output = Path(os.path.realpath(tmp_path)) / 'backup'
    assert backup.create(live, output, ask=answers(secret, secret), confirm=lambda w: w, _test_n=2**14)['status'] == 'created'


def test_no_console(live, tmp_path):
    with pytest.raises(ValueError, match='interactive console'):
        backup.create(live, tmp_path / 'backup')
    assert not (tmp_path / 'backup').exists()


def test_verify_mismatch_and_failure(live, material, secret, tmp_path):
    doc = document(material, secret)
    output = Path(os.path.realpath(tmp_path)) / 'backup'
    backup.write_new(output, signed.wire(doc))
    with pytest.raises(ValueError): backup.verify(output, live, ask=answers(os.urandom(32).hex()))
    assert not (live / 'backup-status.json').exists()
    # A different live public identity needs no unlock and gains no evidence.
    other = signed.crypto()[0].generate()
    pub = other.public_key().public_bytes_raw()
    p = {**material[0], 'public_key': pub.hex(), 'key_id': hashlib.sha256(pub).hexdigest()}
    signed.write_local(live / 'identity.json', {**p, 'protection': kp.seal(p, other.private_bytes_raw(), secret, n=2**14)})
    signed.write_local(live / 'trust.json', {'format': 'agentmesh-trust-v1', 'peers': {p['key_id']: {**p, 'revoked': False}}})
    assert backup.verify(output, live, ask=answers(secret))['key_id_mismatch']
    assert not (live / 'backup-status.json').exists()

@pytest.mark.parametrize('fail', [False, True])
def test_drill(live, material, secret, tmp_path, monkeypatch, fail):
    import security_wizard
    output = Path(os.path.realpath(tmp_path)) / 'backup'
    backup.write_new(output, signed.wire(document(material, secret)))
    before = {p.name: p.read_bytes() for p in live.iterdir()}
    original = signed.write_local
    writes = []
    def guarded(path, value, **kw):
        payload = signed.wire(value)
        assert material[1].hex().encode() not in payload, 'plaintext key write'
        assert secret.encode() not in payload, 'passphrase write'
        assert 'private_key' not in value, 'raw identity write'
        writes.append(Path(path))
        return original(path, value, **kw)
    monkeypatch.setattr(signed, 'write_local', guarded)
    if fail:
        def broken(*a): raise ValueError('injected failure')
        monkeypatch.setattr(security_wizard, 'verify_ack', broken)
        with pytest.raises(ValueError, match=backup.OPERATION_FAILED): backup.drill(output, live, ask=answers(secret))
    else:
        assert backup.drill(output, live, ask=answers(secret))['status'] == 'drill-passed'
        assert backup.status(live)['state'] == 'drilled'
    assert all(p.name == 'backup-status.json' or not p.exists() for p in writes)
    for name, data in before.items(): assert (live / name).read_bytes() == data
    assert not list(live.parent.glob('.backup-drill-*'))


def test_drill_symlink_temp(material, secret, tmp_path, monkeypatch):
    real = Path(os.path.realpath(tmp_path))
    alias = real / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(backup.tempfile, 'tempdir', str(alias))
    output = real / 'backup'
    backup.write_new(output, signed.wire(document(material, secret)))
    assert backup.drill(output, ask=answers(secret))['status'] == 'drill-passed'
    assert not list(real.glob('.backup-drill-*'))

class FakeBackend:
    def __init__(self, present=False): self.present, self.calls = present, []
    def load(self, key_id):
        self.calls.append(key_id)
        return b'present' if self.present else None


@pytest.fixture
def restore_case(tmp_path, material, secret):
    root = Path(os.path.realpath(tmp_path))
    file = root / 'backup'
    backup.write_new(file, signed.wire(document(material, secret)))
    exchange = root / 'exchange'
    exchange.mkdir()
    (exchange / '.stfolder').mkdir()
    (exchange / 'status').mkdir()
    signed.write_local(exchange / 'status' / 'windows.json', {'group': material[0]['group']})
    return file, root / 'target', exchange


def restore_call(case, secret, *, backend=None, **kwargs):
    phrase = os.urandom(32).hex()
    result = backup.restore(*case, ask=answers(secret, phrase, phrase), confirm=lambda w: w,
                            backend=backend or FakeBackend(), _test_n=2**14, **kwargs)
    return result, phrase


@pytest.mark.parametrize('present', [False, True])
def test_restore_success(restore_case, material, secret, present):
    file, target, exchange = restore_case
    backend = FakeBackend(present)
    result, phrase = restore_call(restore_case, secret, backend=backend)
    restored = signed.Security(target, unlock=lambda: phrase)
    assert restored.public == material[0]
    same = restored.key.private_bytes_raw() == material[1]
    assert same, 'key mismatch'
    assert result['stray_os_unlock_item'] is present
    assert backend.calls == [material[0]['key_id']]
    assert all(k in result for k in ['pinned', 'skipped', 'kept_revoked', 'imported_revoked', 'next'])
    with pytest.raises(ValueError): signed.Security(target, unlock=lambda: secret)
    assert not list(target.glob('.restore-*'))
    assert not (target / 'trust.json.pre-restore').exists()
    assert 'os_unlock' not in signed.parse(signed.read_local(target / 'identity.json'))


@pytest.mark.parametrize('kind', ['identity', 'git', 'sync', 'inside', 'staging', 'pre-restore',
    'revoked', 'fields', 'newer', 'group', 'no-marker', 'wrong-exchange', 'fenced', 'equal'])
def test_restore_refuses(restore_case, material, secret, kind):
    file, target, exchange = restore_case
    target.mkdir(mode=0o700)
    trust = copy.deepcopy(material[2])
    if kind == 'identity': (target / 'identity.json').touch()
    if kind == 'git': (target / '.git').touch()
    if kind == 'sync': (target / '.stfolder').mkdir()
    if kind == 'inside': target = exchange / 'target'
    if kind == 'staging': (target / '.restore-crash').mkdir()
    if kind == 'pre-restore': (target / 'trust.json.pre-restore').touch()
    if kind == 'revoked': trust['peers'][material[0]['key_id']]['revoked'] = True
    if kind == 'fields': trust['peers'][material[0]['key_id']]['sender'] = str(uuid.uuid4())
    if kind in ('newer', 'group'):
        peer = new_peer(material[0], node='mac' if kind == 'newer' else 'windows')
        if kind == 'group': peer['group'] = str(uuid.uuid4())
        trust['peers'][peer['key_id']] = {**peer, 'revoked': False}
    if kind in ('revoked', 'fields', 'newer', 'group'): signed.write_local(target / 'trust.json', trust)
    if kind == 'no-marker': (exchange / '.stfolder').rmdir()
    if kind == 'wrong-exchange': (exchange / 'status' / 'windows.json').unlink()
    phrase = secret if kind == 'equal' else os.urandom(32).hex()
    with pytest.raises(ValueError):
        backup.restore(file, target, exchange, ask=answers(secret, phrase, phrase),
                       confirm=lambda w: '' if kind == 'fenced' else w, backend=FakeBackend(), _test_n=2**14)
    if kind != 'identity': assert not (target / 'identity.json').exists()


def new_peer(own, node='windows'):
    key = signed.crypto()[0].generate().public_key().public_bytes_raw()
    return {**own, 'node': node, 'sender': str(uuid.uuid4()), 'public_key': key.hex(),
            'key_id': hashlib.sha256(key).hexdigest()}


@pytest.mark.parametrize('folder', ['status', 'signed-changes', 'changes'])
@pytest.mark.parametrize('age', [0, -3600, 3600])
def test_restore_activity(restore_case, material, secret, folder, age):
    import time
    file, target, exchange = restore_case
    p = exchange / folder / ('mac.json' if folder == 'status' else 'mac/packet')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b'{}')
    when = time.time() - age
    os.utime(p, (when, when))
    if age > 900: restore_call(restore_case, secret)
    else:
        with pytest.raises(ValueError, match='old device looks active'): restore_call(restore_case, secret)


@pytest.mark.parametrize('choice', ['pin', 'skip', 'wrong', 'revoked', 'conflict', 'current', 'current-revoked'])
def test_restore_peers(restore_case, material, secret, choice):
    file, target, exchange = restore_case
    peer = new_peer(material[0])
    trust = copy.deepcopy(material[2])
    trust['peers'][peer['key_id']] = {**peer, 'revoked': choice in ('revoked', 'current')}
    file.unlink()
    backup.write_new(file, signed.wire(document((material[0], material[1], trust), secret)))
    if choice in ('conflict', 'current', 'current-revoked'):
        target.mkdir(mode=0o700)
        current = new_peer(material[0]) if choice == 'conflict' else peer
        signed.write_local(target / 'trust.json', {'format': 'agentmesh-trust-v1',
             'peers': {current['key_id']: {**current, 'revoked': choice == 'current-revoked'}}})
    prompts = []
    def approve(p):
        prompts.append(p)
        return peer['key_id'] if choice == 'pin' else ('bad' if choice == 'wrong' else '')
    result, _ = restore_call(restore_case, secret, peer_confirm=approve)
    restored = signed.read_trust(target)['peers']
    if choice in ('revoked', 'current', 'current-revoked'):
        assert restored[peer['key_id']]['revoked']
        assert not prompts
        with pytest.raises(ValueError, match='revoked key'):
            signed.approve(target, peer, peer['key_id'], peer['group'], peer['node'], peer['sender'])
    elif choice == 'pin': assert peer['key_id'] in result['pinned']
    else: assert peer['key_id'] in result['skipped']


@pytest.mark.parametrize('existing', [False, True])
def test_restore_rollback(restore_case, material, secret, monkeypatch, existing):
    file, target, exchange = restore_case
    target.mkdir(mode=0o700)
    if existing: signed.write_local(target / 'trust.json', material[2])
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    original = backup.os.link
    def fail(source, destination, **kw):
        if Path(destination) == target / 'identity.json': raise OSError('injected')
        return original(source, destination, **kw)
    monkeypatch.setattr(backup.os, 'link', fail)
    with pytest.raises(ValueError): restore_call(restore_case, secret)
    assert {p.name: p.read_bytes() for p in target.iterdir()} == before


def test_restore_second_activity_check(restore_case, secret, monkeypatch):
    file, target, exchange = restore_case
    original = backup.exchange_activity
    count = 0
    def activity(*args):
        nonlocal count
        count += 1
        if count == 2: (exchange / 'status' / 'mac.json').write_bytes(b'{}')
        return original(*args)
    monkeypatch.setattr(backup, 'exchange_activity', activity)
    with pytest.raises(ValueError, match='old device looks active'): restore_call(restore_case, secret)
    assert not (target / 'identity.json').exists()
    assert not list(target.glob('.restore-*'))


def test_restore_writes_and_fresh_process(restore_case, material, secret, monkeypatch):
    import subprocess
    import sys
    original = signed.write_local
    writes = []
    def guarded(path, value, **kw):
        payload = signed.wire(value)
        safe = material[1].hex().encode() not in payload and secret.encode() not in payload and 'private_key' not in value
        if not safe: raise AssertionError('secret written')
        writes.append(Path(path))
        return original(path, value, **kw)
    monkeypatch.setattr(signed, 'write_local', guarded)
    _, phrase = restore_call(restore_case, secret)
    target = restore_case[1]
    code = "import sys; import signed_packets as s; p=sys.stdin.buffer.read().decode(); x=s.Security(sys.argv[1], unlock=lambda:p); x.sign_bytes(b'fresh-process'); print('loaded-and-signed')"
    process = subprocess.run([sys.executable, '-c', code, str(target)], input=phrase.encode(), capture_output=True)
    if process.returncode or process.stdout.strip() != b'loaded-and-signed':
        raise AssertionError('fresh-process validation failed')
    assert all(p.parent == target or not p.exists() for p in writes)


def test_restore_missing_os_item(restore_case, secret):
    import os_unlock
    class Missing:
        def load(self, key_id): raise os_unlock.OSUnlockError(os_unlock.MISSING)
    result, _ = restore_call(restore_case, secret, backend=Missing())
    assert result['stray_os_unlock_item'] is False


def test_restore_no_console_changes_nothing(restore_case):
    with pytest.raises(ValueError, match='interactive console'):
        backup.restore(*restore_case, backend=FakeBackend())
    assert not restore_case[1].exists()


def test_restore_readback_rollback(restore_case, material, secret, monkeypatch):
    file, target, _ = restore_case
    target.mkdir(mode=0o700)
    signed.write_local(target / 'trust.json', material[2])
    original = signed.Security
    def broken(directory, *args, **kwargs):
        if Path(directory) == target: raise ValueError('injected failure')
        return original(directory, *args, **kwargs)
    monkeypatch.setattr(signed, 'Security', broken)
    with pytest.raises(ValueError): restore_call(restore_case, secret)
    assert signed.read_trust(target) == material[2]
    assert sorted(p.name for p in target.iterdir()) == ['trust.json']


def test_restore_error_has_no_secret_context(restore_case, secret):
    class Broken:
        def load(self, key_id): raise RuntimeError(secret)
    with pytest.raises(ValueError) as error:
        restore_call(restore_case, secret, backend=Broken())
    assert str(error.value) == backup.OPERATION_FAILED
    assert error.value.__context__ is None
    assert not restore_case[1].exists()
