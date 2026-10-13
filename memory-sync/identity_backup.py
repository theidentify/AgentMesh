"""Explicit encrypted identity backup and operator-fenced restore."""
import copy
import datetime as dt
import errno
import functools
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time
import uuid

import key_protection as kp
import signed_packets as signed

FORMAT = 'agentmesh-identity-backup-v1'
FIELDS = {'format', 'manifest', 'kdf', 'n', 'r', 'p', 'salt', 'cipher', 'nonce', 'ciphertext'}
MANIFEST = {'key_id', 'public_key', 'sender', 'group', 'node', 'created_at'}
MAX_CIPHERTEXT = 1 << 20
OPEN_FAILED = 'backup could not be opened: wrong passphrase or damaged file'
INVALID = 'invalid backup file'
REASONS = frozenset({OPEN_FAILED, INVALID})


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


@safe_operation
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


@safe_operation
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


def supports_private_permissions(path):
    if os.name != 'nt':
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    volume_path = kernel.GetVolumePathNameW
    volume_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    volume_path.restype = wintypes.BOOL
    volume_info = kernel.GetVolumeInformationW
    volume_info.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                           ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    volume_info.restype = wintypes.BOOL
    root, flags = ctypes.create_unicode_buffer(32768), wintypes.DWORD()
    if not volume_path(str(Path(path).absolute()), root, len(root)):
        raise OSError(ctypes.get_last_error(), WRITE_FAILED)
    if not volume_info(root.value, None, 0, None, None, ctypes.byref(flags), None, 0):
        raise OSError(ctypes.get_last_error(), WRITE_FAILED)
    return bool(flags.value & 0x8)  # FILE_PERSISTENT_ACLS; FAT/exFAT cannot enforce private ACLs.


def file_permissions(path):
    if not supports_private_permissions(path):
        return 'unsupported-by-filesystem'
    try:
        if os.name == 'nt':
            signed.windows_private(path, provision=True)
        else:
            os.chmod(path, 0o600)
        return 'owner-only'
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS):
            return 'unsupported-by-filesystem'
        raise


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


def sealed_identity(public_fields, raw, phrase, n):
    return {**public_fields, 'protection': kp.seal(public_fields, raw, phrase, n=n)}


def private_mkdir(path):
    path.mkdir(mode=0o700)
    signed.windows_private(path, provision=True)
    signed.private_directory(path)


@safe_operation
def drill(file, security_dir=None, *, ask=kp.console_passphrase):
    import security_wizard
    phrase = ask('Backup passphrase: ')
    doc, digest = read_backup(file)
    raw, _, _ = open_backup(doc, phrase)
    show(doc['manifest'])
    parent = signed.private_directory(security_dir).parent if security_dir is not None else None
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix='.backup-drill-', dir=parent)))
    try:
        signed.windows_private(root, provision=True)
        signed.private_directory(root)
        restored, peer_dir = root / 'restored', root / 'peer'
        private_mkdir(restored)
        private_mkdir(peer_dir)
        own = public(doc['manifest'])
        peer_key = signed.crypto()[0].generate()
        peer_bytes = peer_key.public_key().public_bytes_raw()
        peer = {'format': 'agentmesh-peer-key-v1', 'group': own['group'],
                'node': next(n for n in signed.SLOTS if n != own['node']), 'sender': str(uuid.uuid4()),
                'key_id': hashlib.sha256(peer_bytes).hexdigest(), 'public_key': peer_bytes.hex()}
        unlock = os.urandom(32).hex()
        for directory, fields, key_bytes in ((restored, own, raw), (peer_dir, peer, peer_key.private_bytes_raw())):
            signed.write_local(directory / 'identity.json', sealed_identity(fields, key_bytes, unlock, kp.MIN_N), exclusive=True)
            signed.write_local(directory / 'trust.json', {'format': 'agentmesh-trust-v1',
                               'peers': {fields['key_id']: {**fields, 'revoked': False}}}, exclusive=True)
        for directory, fields in ((restored, peer), (peer_dir, own)):
            signed.approve(directory, fields, fields['key_id'], fields['group'], fields['node'], fields['sender'])
        origin = signed.Security(restored, unlock=lambda: unlock)
        destination = signed.Security(peer_dir, unlock=lambda: unlock)
        packet = origin.sign({'group': own['group'], 'node': own['node'], 'uuid': str(uuid.uuid4()),
                              'body': [{'probe': 'identity-backup-drill'}]})
        destination.verify(packet, own['group'], own['node'])
        receipt = signed.receipt_digest(packet)
        ack = security_wizard.signed_ack(destination, packet, receipt)
        security_wizard.verify_ack(origin, ack, {'uuid': packet['uuid'], 'digest': receipt}, peer)
    finally:
        shutil.rmtree(root)
    when = now()
    matched = match_live(security_dir, doc['manifest'])
    if matched:
        record(security_dir, own['key_id'], digest, 'drilled_at', when)
    result = {'status': 'drill-passed', 'key_id': own['key_id'], 'file_sha256': digest, 'drilled_at': when}
    if security_dir is not None and not matched:
        result['key_id_mismatch'] = True
    if doc['n'] < kp.SCRYPT_N:
        result['warning'] = 'backup uses a lower-than-production scrypt work factor'
    return result


TARGET_EXISTS = 'restore target already contains an identity'
TARGET_EXCHANGE = 'restore target must be outside the exchange'
RESTORE_LEFTOVER = 'unfinished restore exists; inspect and remove .restore-* and trust.json.pre-restore before retrying'
ROTATION_LEFTOVER = 'unfinished rotation exists; run identity-rotate --resume before restoring'
SELF_REVOKED = 'this key is revoked in the current trust; rotate instead (#9)'
TRUST_CONFLICT = 'current trust conflicts with the backup identity'
EXCHANGE_GROUP = 'exchange does not belong to this group'
ACTIVE = 'the old device looks active: recent exchange activity for this identity'
TRUST_CHANGED = 'current trust changed during restore; retry after inspection'
RESTORE_READBACK = 'restored identity readback failed'
REASONS |= {TARGET_EXISTS, TARGET_EXCHANGE, RESTORE_LEFTOVER, ROTATION_LEFTOVER, SELF_REVOKED, TRUST_CONFLICT,
            EXCHANGE_GROUP, ACTIVE, TRUST_CHANGED, RESTORE_READBACK}
# Fixed, secret-free refusals raised by the modules this one builds on; the operator needs them to act.
UPSTREAM_REASONS = frozenset({
    'protected identity could not be unlocked: wrong passphrase or damaged file',
    'invalid protected identity parameters', 'invalid private identity', 'invalid public identity',
    'invalid local trust store', 'invalid pinned trust entry', 'trust key mismatch',
    'private/public identity mismatch', 'local signing key is not authorized',
    'security directory must be owner-only (chmod 700)', 'security directory required',
    'symlink path not permitted', 'reparse path not permitted',
    'unverifiable Windows ACL', 'unsupported Windows user SID', 'Windows private owner/type mismatch',
    'Windows private ACL must be protected', 'Windows private ACL has no explicit grants',
    'unsupported Windows ACE', 'unapproved Windows ACL principal or ACE type',
    'unsupported or inherited Windows ACE', 'unsupported Windows ACL rights',
    'Windows private ACL requires user full control'})
REASONS |= UPSTREAM_REASONS


def empty_trust():
    return {'format': 'agentmesh-trust-v1', 'peers': {}}


def check_target(target, exchange):
    target = signed.no_symlinks(target)
    if within(target, exchange):
        raise ValueError(TARGET_EXCHANGE)
    if os.path.lexists(target / 'identity.json'):
        raise ValueError(TARGET_EXISTS)
    if target.exists():
        signed.private_directory(target)
        if list(target.glob('.rotate-*')) or os.path.lexists(target / 'rotation-journal.json'):
            raise ValueError(ROTATION_LEFTOVER)
        if list(target.glob('.restore-*')) or os.path.lexists(target / 'trust.json.pre-restore'):
            raise ValueError(RESTORE_LEFTOVER)
    return target


def current_trust(target):
    return signed.read_trust(target) if os.path.lexists(target / 'trust.json') else empty_trust()


def check_current(trust, own):
    entry = trust['peers'].get(own['key_id'])
    if entry and entry['revoked']:
        raise ValueError(SELF_REVOKED)
    if entry and {k: entry[k] for k in signed.PUBLIC_FIELDS} != own:
        raise ValueError(TRUST_CONFLICT)
    for entry in trust['peers'].values():
        if entry['group'] != own['group'] or (not entry['revoked'] and entry['node'] == own['node']
                                             and entry['key_id'] != own['key_id']):
            raise ValueError(TRUST_CONFLICT)


def exchange_activity(exchange, own):
    root = signed.no_symlinks(exchange)
    belongs = False
    try:
        if (root / '.stfolder').exists():
            for path in (root / 'status').glob('*.json'):
                try:
                    value = signed.parse(signed.read_local(path))
                    belongs |= type(value) is dict and value.get('group') == own['group']
                except (OSError, ValueError):
                    continue
    except OSError:
        pass
    if not belongs:
        raise ValueError(EXCHANGE_GROUP)
    newest, active = None, False
    cutoff = time.time() - 15 * 60
    def visit(path):
        nonlocal newest, active
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        except OSError:
            active = True
            return
        try:
            signed.no_symlinks(path)
            if stat.S_ISDIR(info.st_mode):
                for child in path.iterdir():
                    visit(child)
            elif stat.S_ISREG(info.st_mode):
                signed.read_local(path)  # An unreadable entry is activity, even if its mtime is old.
                newest = info.st_mtime if newest is None else max(newest, info.st_mtime)
                active |= info.st_mtime >= cutoff
            else:
                active = True
        except (OSError, ValueError):
            active = True
    visit(root / 'status' / (own['node'] + '.json'))
    for folder in ('signed-changes', 'changes'):
        visit(root / folder / own['node'])
    show({'newest_activity_at': dt.datetime.fromtimestamp(newest, dt.timezone.utc).isoformat() if newest is not None else None})
    if active:
        raise ValueError(ACTIVE)
    return newest


def console_peer(peer):
    if not sys.stdin.isatty():
        raise ValueError('passphrase entry requires an interactive console')
    print('Type the full fingerprint to pin this peer, or Enter to skip: ', end='', file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def reconcile(current, saved, own, peer_confirm):
    trust = copy.deepcopy(current)
    peers = trust['peers']
    report = {k: [] for k in ('pinned', 'skipped', 'kept_revoked', 'imported_revoked')}
    peers[own['key_id']] = {**own, 'revoked': False}
    # Apply every revocation before deciding whether a new active peer occupies a free slot.
    for key_id, entry in saved['peers'].items():
        if key_id == own['key_id']:
            continue
        if key_id in peers:
            peers[key_id]['revoked'] |= entry['revoked']
        elif entry['revoked']:
            peers[key_id] = copy.deepcopy(entry)
            report['imported_revoked'].append(key_id)
    report['kept_revoked'] = sorted(k for k, e in current['peers'].items() if peers[k]['revoked'])
    for key_id, entry in sorted(saved['peers'].items()):
        if key_id in peers:
            continue
        conflict = entry['group'] != own['group'] or any(
            not e['revoked'] and (e['group'], e['node']) == (entry['group'], entry['node'])
            and e['sender'] != entry['sender'] for e in peers.values())
        show({'node': entry['node'], 'sender': entry['sender'], 'fingerprint_prefix': key_id[:16]})
        if conflict or peer_confirm({k: entry[k] for k in signed.PUBLIC_FIELDS}) != key_id:
            report['skipped'].append(key_id)
            show({'skipped': key_id, 'reason': 'slot conflict' if conflict else 'full fingerprint not confirmed'})
        else:
            peers[key_id] = copy.deepcopy(entry)
            report['pinned'].append(key_id)
    return trust, report


def stray_item(key_id, backend):
    import os_unlock
    selected = os_unlock.current_backend() if backend is None else backend
    if selected is None:
        return False
    missing = False
    try:
        return selected.load(key_id) is not None
    except os_unlock.OSUnlockError as exc:
        missing = str(exc) == os_unlock.MISSING
    if missing:
        return False
    raise ValueError(OPERATION_FAILED)


@safe_operation
def restore(file, security_dir, exchange, *, ask=kp.console_passphrase, confirm=console_confirm,
            peer_confirm=console_peer, backend=None, _test_n=None):
    location_confirmation(security_dir, confirm)
    target = check_target(security_dir, exchange)
    phrase = ask('Backup passphrase: ')
    doc, _ = read_backup(file)
    raw, saved, captured = open_backup(doc, phrase)
    own = public(doc['manifest'])
    current = current_trust(target)
    check_current(current, own)
    exchange_activity(exchange, own)
    show({'manifest': doc['manifest'], 'trust_captured_at': captured})
    require_confirmation('FENCED', confirm)
    trust, report = reconcile(current, saved, own, peer_confirm)
    replacement = new_passphrase(ask, phrase, 'identity')
    identity = sealed_identity(own, raw, replacement, kp.SCRYPT_N if _test_n is None else _test_n)
    stray = stray_item(own['key_id'], backend)
    created_target = not target.exists()
    if created_target:
        private_mkdir(target)
    stage = target / ('.restore-' + str(uuid.uuid4()))
    previous = target / 'trust.json.pre-restore'
    had_trust = os.path.lexists(target / 'trust.json')
    replaced, adopted, success, previous_owned = False, False, False, False
    try:
        check_target(target, exchange)
        private_mkdir(stage)
        signed.write_local(stage / 'identity.json', identity, exclusive=True)
        signed.write_local(stage / 'trust.json', trust, exclusive=True)
        exchange_activity(exchange, own)
        if current_trust(target) != current:
            raise ValueError(TRUST_CHANGED)
        if os.path.lexists(target / 'identity.json'):
            raise ValueError(TARGET_EXISTS)
        if had_trust:
            signed.no_symlinks(target / 'trust.json')
            os.link(target / 'trust.json', previous)
            previous_owned = True
        replaced = True
        signed.write_local(target / 'trust.json', trust)
        os.link(stage / 'identity.json', target / 'identity.json')
        adopted = True
        if os.name != 'nt':
            fd = os.open(target, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        reread = signed.Security(target, unlock=lambda: replacement)
        if reread.public != own or reread.key.private_bytes_raw() != raw:
            raise ValueError(RESTORE_READBACK)
        (target / 'backup-status.json').unlink(missing_ok=True)
        previous.unlink(missing_ok=True)
        success = True
    finally:
        if not success:
            if adopted:
                (target / 'identity.json').unlink()
            if replaced:
                if had_trust:
                    signed.replace(previous, target / 'trust.json')
                else:
                    (target / 'trust.json').unlink(missing_ok=True)
            if previous_owned:
                previous.unlink(missing_ok=True)
        if stage.exists():
            shutil.rmtree(stage)
        if not success and created_target:
            target.rmdir()
    next_steps = ['Run a live signed roundtrip with a real peer using the wizard probe before trusting the restore.',
                  'Optional: identity-os-unlock enable', 'worker-start', 'identity-backup create']
    if stray:
        next_steps.insert(0, 'Run identity-os-unlock disable or enable to remove or replace the stray OS item.')
    return {'status': 'restored', 'key_id': own['key_id'], **report, 'stray_os_unlock_item': stray, 'next': next_steps}
