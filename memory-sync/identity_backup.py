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


import functools
import hashlib
import json

CANCELLED = 'backup or restore confirmation was not supplied'
PASSPHRASE_SHORT = 'passphrase must be at least 12 characters'
PASSPHRASE_MISMATCH = 'passphrases do not match; nothing was changed'
PASSPHRASE_REUSED = 'backup and identity passphrases must be different'
OPERATION_FAILED = 'identity backup or restore failed'
READBACK_FAILED = 'backup readback verification failed'
PARENT_MISSING = 'backup parent directory does not exist'
REASONS |= {CANCELLED, PASSPHRASE_SHORT, PASSPHRASE_MISMATCH, PASSPHRASE_REUSED,
            OPERATION_FAILED, READBACK_FAILED, PARENT_MISSING,
            'passphrase entry requires an interactive console'}


def safe_operation(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        reason = None
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            reason = str(exc) if type(exc) is ValueError and str(exc) in REASONS else OPERATION_FAILED
        # Outside the handler: no underlying exception containing secret data survives as context.
        raise ValueError(reason)
    return wrapped


def show(value):
    print(json.dumps(value, sort_keys=True), file=sys.stderr)


def console_confirm(word):
    import console_prompt
    prompt = console_prompt.ConsolePrompt()
    if not prompt.interactive():
        raise ValueError('passphrase entry requires an interactive console')
    return word if prompt.typed(word, 'Continue') else ''


def require_confirmation(word, confirm):
    if confirm(word) != word:
        raise ValueError(CANCELLED)


def location_confirmation(path, confirm):
    if unsafe_location(path) == 'cloud':
        show({'warning': 'This file will be uploaded by cloud sync and permits offline passphrase guessing.'})
        require_confirmation('CLOUD', confirm)


def new_passphrase(ask, previous, label):
    phrase = ask('New ' + label + ' passphrase (at least 12 characters): ')
    if len(phrase) < kp.MIN_PASSPHRASE:
        raise ValueError(PASSPHRASE_SHORT)
    if phrase == previous:
        raise ValueError(PASSPHRASE_REUSED)
    if ask('Repeat the passphrase: ') != phrase:
        raise ValueError(PASSPHRASE_MISMATCH)
    return phrase


def read_backup(path):
    result = None
    try:
        data = signed.read_local(path, private=False)
        result = signed.parse(data)
    except (OSError, ValueError, TypeError):
        pass
    if result is None:
        raise ValueError(OPEN_FAILED)
    return result, hashlib.sha256(data).hexdigest()


def match_live(security_dir, manifest):
    if security_dir is None:
        return False
    return signed.Security(security_dir).public == public(manifest)


@safe_operation
def create(security_dir, output, *, ask=kp.console_passphrase, confirm=console_confirm, backend=None, _test_n=None):
    output = Path(output).expanduser()
    location_confirmation(output, confirm)
    if os.path.lexists(output):
        raise ValueError(EXISTS)
    if not output.parent.is_dir():
        raise ValueError(PARENT_MISSING)
    current = signed.Security(security_dir)
    identity_phrase = None
    if current.locked:
        identity_phrase = ask('Identity passphrase: ')
        current = signed.Security(security_dir, unlock=lambda: identity_phrase)
    manifest = {k: current.public[k] for k in MANIFEST - {'created_at'}} | {'created_at': now()}
    show(manifest)
    require_confirmation('BACKUP', confirm)
    phrase = new_passphrase(ask, identity_phrase, 'backup')
    raw = current.key.private_bytes_raw()
    doc = seal(manifest, raw, signed.read_trust(security_dir), phrase, n=kp.SCRYPT_N if _test_n is None else _test_n)
    permissions = write_new(output, signed.wire(doc))
    verified = False
    try:
        reread, digest = read_backup(output)
        restored, _, _ = open_backup(reread, phrase)
        verified = restored == raw and public(reread['manifest']) == current.public
    finally:
        if not verified:
            output.unlink(missing_ok=True)
    if not verified:
        raise ValueError(READBACK_FAILED)
    when = now()
    record(security_dir, current.public['key_id'], digest, 'created_at', manifest['created_at'])
    record(security_dir, current.public['key_id'], digest, 'verified_at', when)
    return {'status': 'created', 'key_id': current.public['key_id'], 'file_sha256': digest,
            'verified_at': when, 'permissions': permissions}


@safe_operation
def verify(file, security_dir=None, *, ask=kp.console_passphrase):
    phrase = ask('Backup passphrase: ')
    doc, digest = read_backup(file)
    open_backup(doc, phrase)
    show(doc['manifest'])
    when = now()
    matched = match_live(security_dir, doc['manifest'])
    if matched:
        record(security_dir, doc['manifest']['key_id'], digest, 'verified_at', when)
    result = {'status': 'verified', 'key_id': doc['manifest']['key_id'], 'file_sha256': digest, 'verified_at': when}
    if security_dir is not None and not matched:
        result['key_id_mismatch'] = True
    if doc['n'] < kp.SCRYPT_N:
        result['warning'] = 'backup uses a lower-than-production scrypt work factor'
    return result
