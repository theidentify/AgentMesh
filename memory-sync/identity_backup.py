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


import errno
from pathlib import Path
import sys
import tempfile

CLOUD_ROOTS = tuple(Path.home() / p for p in (
    'Library/Mobile Documents', 'Library/CloudStorage', 'Dropbox', 'OneDrive', 'Google Drive'
)) + ((Path(os.environ['OneDrive']),) if os.environ.get('OneDrive') else ())
LOCATION_SYNC = 'backup location is inside a Syncthing folder'
LOCATION_GIT = 'backup location is inside a git work tree'
EXISTS = 'backup file already exists'
WRITE_FAILED = 'backup file could not be written'
REASONS |= {LOCATION_SYNC, LOCATION_GIT, EXISTS, WRITE_FAILED}


def normalized(path):
    value = os.path.realpath(Path(path).expanduser())
    return value.casefold() if sys.platform in ('darwin', 'win32') else value


def within(path, root):
    a, b = normalized(path), normalized(root)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def unsafe_location(path):
    path = Path(os.path.realpath(Path(path).expanduser()))
    cloud = any(within(path, root) for root in CLOUD_ROOTS)
    for parent in (path, *path.parents):
        if not parent.is_dir():
            continue
        names = {p.name.casefold() if sys.platform in ('darwin', 'win32') else p.name
                 for p in parent.iterdir()}
        if '.stfolder' in names:
            raise ValueError(LOCATION_SYNC)
        if '.git' in names:
            raise ValueError(LOCATION_GIT)
        if names & {'.dropbox', 'desktop.ini.icloud', '.icloud'}:
            cloud = True
    return 'cloud' if cloud else None


def file_permissions(path):
    try:
        if os.name == 'nt':
            signed.windows_private(path, provision=True)
        else:
            os.chmod(path, 0o600)
        return 'owner-only'
    except (OSError, ValueError):
        return 'unsupported-by-filesystem'


def write_new(path, data):
    path = Path(path)
    temp, created, reason, permissions = None, False, None, None
    try:
        fd, name = tempfile.mkstemp(prefix='.backup-', dir=path.parent)
        temp = Path(name)
        with os.fdopen(fd, 'wb') as stream:
            permissions = file_permissions(temp)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)
            created = True
        except FileExistsError:
            raise
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.EACCES, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV):
                raise
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created = True
            with os.fdopen(fd, 'wb') as stream:
                permissions = file_permissions(path)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
    except FileExistsError:
        reason = EXISTS
    except (OSError, ValueError):
        reason = WRITE_FAILED
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
        if reason and created:
            path.unlink(missing_ok=True)
    if reason:
        raise ValueError(reason)
    return permissions


STATUS_FORMAT = 'agentmesh-backup-status-v1'
STATUS_INVALID = 'invalid backup status'
EVIDENCE_FIELDS = {'created_at', 'verified_at', 'drilled_at'}
REASONS |= {STATUS_INVALID}


def evidence(directory, key_id):
    path = Path(directory) / 'backup-status.json'
    empty = {'format': STATUS_FORMAT, 'key_id': key_id, 'backups': {}}
    if not path.exists():
        return empty
    result = None
    try:
        doc = signed.parse(signed.read_local(path, private=True))
        if type(doc) is dict and set(doc) == {'format', 'key_id', 'backups'} and doc['format'] == STATUS_FORMAT:
            signed.hex_bytes(doc['key_id'], 32)
            if type(doc['backups']) is not dict:
                raise ValueError(STATUS_INVALID)
            for digest, entry in doc['backups'].items():
                signed.hex_bytes(digest, 32)
                if type(entry) is not dict or set(entry) != EVIDENCE_FIELDS:
                    raise ValueError(STATUS_INVALID)
                for value in entry.values():
                    if value is not None:
                        timestamp(value)
            result = doc if doc['key_id'] == key_id else empty
    except (ValueError, OSError, TypeError):
        pass
    if result is None:
        raise ValueError(STATUS_INVALID)
    return result


def record(security_dir, key_id, file_sha256, field, when):
    if field not in EVIDENCE_FIELDS:
        raise ValueError(STATUS_INVALID)
    signed.hex_bytes(file_sha256, 32)
    timestamp(when)
    if signed.Security(security_dir).public['key_id'] != key_id:
        return
    doc = evidence(security_dir, key_id)
    doc['backups'].setdefault(file_sha256, dict.fromkeys(sorted(EVIDENCE_FIELDS)))[field] = when
    signed.write_local(Path(security_dir) / 'backup-status.json', doc)


def status(security_dir):
    key_id = signed.Security(security_dir).public['key_id']
    doc = evidence(security_dir, key_id)
    rows, state = [], 'none'
    current = timestamp(now())
    for digest, entry in sorted(doc['backups'].items()):
        row = {'file_sha256': digest, **entry}
        for field, value in entry.items():
            row[field.removesuffix('_at') + '_age_seconds'] = max(0, (current - timestamp(value)).total_seconds()) if value else None
        rows.append(row)
        if entry['drilled_at']:
            state = 'drilled'
        elif entry['verified_at'] and state == 'none':
            state = 'verified'
    return {'key_id': key_id, 'backups': rows, 'state': state}
