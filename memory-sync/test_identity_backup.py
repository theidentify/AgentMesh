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
