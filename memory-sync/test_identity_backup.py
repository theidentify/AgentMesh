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
