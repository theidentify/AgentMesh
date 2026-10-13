"""Explicit encrypted identity backup and operator-fenced restore."""
import datetime as dt
import os

import key_protection as kp
import signed_packets as signed

FORMAT = 'agentmesh-identity-backup-v1'
FIELDS = {'format', 'manifest', 'kdf', 'n', 'r', 'p', 'salt', 'cipher', 'nonce', 'ciphertext'}
MANIFEST = {'key_id', 'public_key', 'sender', 'group', 'node', 'created_at'}
MAX_CIPHERTEXT = 1 << 20
OPEN_FAILED = 'backup could not be opened: wrong passphrase or damaged file'
INVALID = 'invalid backup file'
REASONS = frozenset({OPEN_FAILED, INVALID})


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def timestamp(value):
    if type(value) is not str:
        raise ValueError(INVALID)
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() != dt.timedelta(0):
        raise ValueError(INVALID)
    return parsed


def public(manifest):
    return {'format': 'agentmesh-peer-key-v1', **{k: manifest[k] for k in MANIFEST - {'created_at'}}}


def associated_data(manifest):
    return FORMAT.encode() + b'\0' + signed.typed(manifest)


def check(doc):
    valid = False
    try:
        if (type(doc) is dict and set(doc) == FIELDS and doc['format'] == FORMAT
                and doc['kdf'] == 'scrypt' and doc['cipher'] == 'aes-256-gcm'
                and type(doc['n']) is int and kp.MIN_N <= doc['n'] <= kp.MAX_N
                and not doc['n'] & (doc['n'] - 1)
                and type(doc['r']) is int and doc['r'] == 8
                and type(doc['p']) is int and doc['p'] == 1
                and type(doc['manifest']) is dict and set(doc['manifest']) == MANIFEST):
            signed.check_public(public(doc['manifest']))
            timestamp(doc['manifest']['created_at'])
            signed.hex_bytes(doc['salt'], 16)
            signed.hex_bytes(doc['nonce'], 12)
            ciphertext = doc['ciphertext']
            if type(ciphertext) is str and 32 <= len(ciphertext) <= MAX_CIPHERTEXT * 2 and not len(ciphertext) % 2:
                signed.hex_bytes(ciphertext, len(ciphertext) // 2)
                valid = True
    except (ValueError, TypeError, KeyError, OverflowError):
        pass
    if not valid:
        raise ValueError(INVALID)
    return doc


def seal(manifest, private_bytes, trust, passphrase, *, n=kp.SCRYPT_N):
    AESGCM, _, _ = kp._primitives()
    salt, nonce = os.urandom(16), os.urandom(12)
    doc = dict(format=FORMAT, manifest=manifest, kdf='scrypt', n=n, r=8, p=1,
               salt=salt.hex(), cipher='aes-256-gcm', nonce=nonce.hex(), ciphertext='00' * 16)
    check(doc)
    data = signed.wire({'private_key': private_bytes.hex(), 'trust': trust, 'trust_captured_at': now()})
    key = kp._derive(passphrase, salt, n, 8, 1)
    doc['ciphertext'] = AESGCM(key).encrypt(nonce, data, associated_data(manifest)).hex()
    return check(doc)


def open_backup(doc, passphrase):
    # Keep structural validation separate; opening never reveals which authenticated field failed.
    checked = False
    try:
        check(doc)
        checked = True
    except ValueError:
        pass
    if not checked:
        raise ValueError(OPEN_FAILED)
    AESGCM, _, InvalidTag = kp._primitives()
    result = None
    try:
        key = kp._derive(passphrase, bytes.fromhex(doc['salt']), doc['n'], 8, 1)
        data = AESGCM(key).decrypt(bytes.fromhex(doc['nonce']), bytes.fromhex(doc['ciphertext']),
                                   associated_data(doc['manifest']))
        result = signed.parse(data)
        raw = signed.hex_bytes(result['private_key'], 32)
        if signed.crypto()[0].from_private_bytes(raw).public_key().public_bytes_raw().hex() != doc['manifest']['public_key']:
            result = None
    except (InvalidTag, ValueError, TypeError, KeyError):
        result = None
    if result is None:
        raise ValueError(OPEN_FAILED)
    valid = False
    try:
        if set(result) == {'private_key', 'trust', 'trust_captured_at'}:
            timestamp(result['trust_captured_at'])
            signed.check_trust(result['trust'])
            entry = result['trust']['peers'].get(doc['manifest']['key_id'])
            valid = entry == {**public(doc['manifest']), 'revoked': False}
    except (ValueError, TypeError, KeyError):
        pass
    if not valid:
        raise ValueError(INVALID)
    return raw, result['trust'], result['trust_captured_at']
