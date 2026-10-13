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
    assert raw == material[1], 'key mismatch'
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
