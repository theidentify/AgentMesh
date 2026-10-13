"""Operator-authorized key recovery; manifests never use the packet JSON codec."""
import functools
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

import identity_backup as backup
import signed_packets as signed

FORMAT = 'agentmesh-key-rotation-v1'
DOMAIN = FORMAT.encode() + b'\0'
ROTATIONS = 'agentmesh-rotations-v1'
MAX_MANIFEST_BYTES = 200 * 1024 * 1024
INVALID = 'invalid rotation proposal'
INVALID_MANIFEST = 'invalid rotation manifest'
INVALID_REGISTRY = 'invalid rotation registry'
FAILED = 'key rotation operation failed'
FIELDS = {'format', 'old_key_id', 'new', 'reason', 'created_at', 'manifest_sha256', 'manifest_count', 'signature'}
REASONS = {INVALID, INVALID_MANIFEST, INVALID_REGISTRY, FAILED}
UPSTREAM_REASONS = frozenset()


def safe_operation(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        reason = FAILED
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            if type(exc) is ValueError and str(exc) in REASONS | UPSTREAM_REASONS:
                reason = str(exc)
        raise ValueError(reason)
    return wrapped


def fixed(reason):
    """Discard exception chains and data at untrusted format boundaries."""
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except Exception:
                pass
            raise ValueError(reason)
        return wrapped
    return decorate


def _line(pair):
    uid, digest = pair
    if not signed.canonical_uuid(uid): raise ValueError(INVALID_MANIFEST)
    signed.hex_bytes(digest, 32)
    return (uid + ' ' + digest + '\n').encode('ascii')


@fixed(INVALID_MANIFEST)
def read_manifest(path, *, sha256=None, count=None, private=False, _max_bytes=None):
    path = signed.no_symlinks(path)
    if private: signed.windows_private(path)
    maximum = MAX_MANIFEST_BYTES if _max_bytes is None else min(_max_bytes, MAX_MANIFEST_BYTES)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    packets, digest, size, previous = {}, hashlib.sha256(), 0, ''
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum: raise ValueError(INVALID_MANIFEST)
        if private and os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError(INVALID_MANIFEST)
        while True:
            line = stream.readline(103)
            if not line: break
            size += len(line)
            if size > maximum or not re.fullmatch(rb'[0-9a-f-]{36} [0-9a-f]{64}\n', line):
                raise ValueError(INVALID_MANIFEST)
            uid, value = line[:-1].decode('ascii').split(' ')
            if not signed.canonical_uuid(uid) or uid <= previous: raise ValueError(INVALID_MANIFEST)
            previous = uid
            packets[uid] = value
            digest.update(line)
    result = {'sha256': digest.hexdigest(), 'count': len(packets), 'packets': packets}
    if sha256 is not None and result['sha256'] != sha256: raise ValueError(INVALID_MANIFEST)
    if count is not None and (type(count) is not int or count != result['count']): raise ValueError(INVALID_MANIFEST)
    return result


@fixed(INVALID_MANIFEST)
def write_manifest(path, pairs, *, exclusive=False):
    path = signed.no_symlinks(path)
    temp = path.with_name('.' + path.name + '.' + str(uuid.uuid4()) + '.tmp')
    previous, size = '', 0
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            signed.windows_private(temp, provision=True)
            for pair in pairs:
                line = _line(pair)
                if pair[0] <= previous: raise ValueError(INVALID_MANIFEST)
                previous = pair[0]
                size += len(line)
                if size > MAX_MANIFEST_BYTES: raise ValueError(INVALID_MANIFEST)
                stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive: os.link(temp, path)
        else: signed.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return read_manifest(path)


@fixed(INVALID)
def proposal(security, old_key_id, reason, manifest_path):
    manifest = read_manifest(manifest_path)
    doc = dict(format=FORMAT, old_key_id=old_key_id, new=dict(security.public), reason=reason,
               created_at=backup.now(), manifest_sha256=manifest['sha256'], manifest_count=manifest['count'])
    doc['signature'] = security.sign_bytes(DOMAIN + signed.typed(doc)).hex()
    return check_proposal(doc, manifest_path)


@fixed(INVALID)
def check_proposal(doc, manifest_path):
    if type(doc) is not dict or set(doc) != FIELDS or doc['format'] != FORMAT: raise ValueError(INVALID)
    signed.check_public(doc['new'])
    signed.hex_bytes(doc['old_key_id'], 32)
    signed.hex_bytes(doc['manifest_sha256'], 32)
    if doc['old_key_id'] == doc['new']['key_id']: raise ValueError(INVALID)
    if doc['reason'] not in ('lost', 'compromised', 'planned'): raise ValueError(INVALID)
    backup.timestamp(doc['created_at'])
    if type(doc['manifest_count']) is not int or not 0 <= doc['manifest_count'] <= MAX_MANIFEST_BYTES // 102:
        raise ValueError(INVALID)
    signed.crypto()[1].from_public_bytes(signed.hex_bytes(doc['new']['public_key'], 32)).verify(
        signed.hex_bytes(doc['signature'], 64), DOMAIN + signed.typed({k: v for k, v in doc.items() if k != 'signature'}))
    read_manifest(manifest_path, sha256=doc['manifest_sha256'], count=doc['manifest_count'])
    return doc


@fixed(INVALID_REGISTRY)
def read_rotations(directory):
    directory = signed.private_directory(directory)
    path = directory / 'rotations.json'
    if not path.exists(): return {'format': ROTATIONS, 'rotations': {}}
    doc = signed.parse(signed.read_local(path, private=True))
    if type(doc) is not dict or set(doc) != {'format', 'rotations'} or doc['format'] != ROTATIONS or type(doc['rotations']) is not dict:
        raise ValueError(INVALID_REGISTRY)
    for old, entry in doc['rotations'].items():
        signed.hex_bytes(old, 32)
        if type(entry) is not dict or set(entry) != {'new_key_id', 'approved_at', 'manifest_sha256', 'manifest_count'}:
            raise ValueError(INVALID_REGISTRY)
        signed.hex_bytes(entry['new_key_id'], 32)
        signed.hex_bytes(entry['manifest_sha256'], 32)
        backup.timestamp(entry['approved_at'])
        if entry['new_key_id'] == old or type(entry['manifest_count']) is not int or not 0 <= entry['manifest_count'] <= MAX_MANIFEST_BYTES // 102:
            raise ValueError(INVALID_REGISTRY)
    return doc


@safe_operation
def add_rotation(directory, old_key_id, new_key_id, manifest_path):
    signed.hex_bytes(old_key_id, 32)
    signed.hex_bytes(new_key_id, 32)
    if old_key_id == new_key_id: raise ValueError(INVALID_REGISTRY)
    registry = read_rotations(directory)
    manifest = read_manifest(manifest_path)
    target = Path(directory) / 'rotations'
    if not target.exists(): backup.private_mkdir(target)
    signed.private_directory(target)
    write_manifest(target / (old_key_id + '.manifest'), manifest['packets'].items())
    registry['rotations'][old_key_id] = dict(new_key_id=new_key_id, approved_at=backup.now(),
        manifest_sha256=manifest['sha256'], manifest_count=manifest['count'])
    signed.write_local(Path(directory) / 'rotations.json', registry)
    return registry


from contextlib import contextmanager, closing, ExitStack
import sqlite3
import key_protection as kp
import memory_sync as sync

LEFTOVERS = 'unfinished rotation or restore exists; inspect or run identity-rotate --resume'
LEGACY = 'rotation requires a signed (required-policy) database; there is no key to rotate'
BINDING = 'signed database binding mismatch'
OLD_UNKNOWN = 'old signing key cannot be determined; supply --old-key'
UNEXPECTED = 'pending outbox is signed by an unexpected key'
BUSY = 'a worker or cycle is using this database'
CANCELLED = 'rotation confirmation was not supplied'
PENDING_EXCHANGE = 'pending packet is already in the exchange; publish or inspect it before rotating'
PENDING_INVALID = 'invalid pending outbox packet'
RUNTIME = 'runtime does not match rotation paths'
REASONS |= {LEFTOVERS, LEGACY, BINDING, OLD_UNKNOWN, UNEXPECTED, BUSY, CANCELLED, PENDING_EXCHANGE, PENDING_INVALID, RUNTIME}
UPSTREAM_REASONS = backup.UPSTREAM_REASONS | {backup.ACTIVE, backup.EXCHANGE_GROUP,
    backup.PASSPHRASE_SHORT, backup.PASSPHRASE_MISMATCH, 'passphrase entry requires an interactive console'}


@contextmanager
def readonly(db):
    path = signed.no_symlinks(db)
    sidecars = [Path(str(path) + suffix) for suffix in ('-wal', '-shm')]
    uri = path.as_uri() + ('?mode=ro' if any(p.exists() for p in sidecars) else '?mode=ro&immutable=1')
    with closing(sqlite3.connect(uri, uri=True)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        yield c


def binding(c):
    cfg = c.execute('SELECT node,group_id FROM _sync_config').fetchall()
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_sync_security'").fetchone():
        raise ValueError(LEGACY)
    rows = c.execute('SELECT sender,group_id,node FROM _sync_security').fetchall()
    if len(cfg) != 1 or len(rows) != 1: raise ValueError(BINDING)
    sender, group, node = rows[0]
    if (node, group) != tuple(cfg[0]) or not signed.canonical_uuid(sender) or not signed.canonical_uuid(group) or node not in signed.SLOTS:
        raise ValueError(BINDING)
    return {'sender': sender, 'group': group, 'node': node}


def public_fields(doc):
    return signed.check_public({k: doc[k] for k in signed.PUBLIC_FIELDS})


def confirm_word(word, confirm):
    if confirm(word) != word: raise ValueError(CANCELLED)


def no_pending_files(exchange, node, pending):
    for p in pending:
        for namespace in ('signed-changes', 'changes'):
            path = signed.no_symlinks(Path(exchange) / namespace / node / (p['uuid'] + '.json'))
            if os.path.lexists(path): raise ValueError(PENDING_EXCHANGE)


def verify_old(p, own):
    signed.validate_envelope(p, own['group'], own['node'])
    if p['sender'] != own['sender'] or p['key_id'] != own['key_id']: raise ValueError(PENDING_INVALID)
    unsigned = {k: v for k, v in p.items() if k != 'signature'}
    signed.crypto()[1].from_public_bytes(signed.hex_bytes(own['public_key'], 32)).verify(
        signed.hex_bytes(p['signature'], 64), signed.DOMAIN + signed.typed(unsigned))
    if type(p['body']) is not list or not p['body'] or hashlib.sha256(signed.typed(p['body'])).hexdigest() != p['checksum']:
        raise ValueError(PENDING_INVALID)


def _runtime_config(runtime, db, exchange):
    if runtime is None: return None
    config = signed.parse(signed.read_local(runtime, private=True))
    if (signed.no_symlinks(config['database']) != signed.no_symlinks(db)
            or signed.no_symlinks(config['exchange']) != signed.no_symlinks(exchange)):
        raise ValueError(RUNTIME)
    return config


def _worker_running(runtime, config):
    import worker_lifecycle as worker
    try:
        return worker.status(runtime)['state'] == 'running'
    except ValueError:
        # status loads Security, which cannot load a lost/revoked identity or an
        # in-progress journal. Inspect the same owned process metadata directly.
        record = worker.metadata(worker.control(config), runtime)
        return record is not None and record['state'] == 'running' and worker.alive(record['pid'])


@contextmanager
def fence(db, exchange, own, runtime, confirm):
    import runtime_lock
    import worker_lifecycle as worker
    config = _runtime_config(runtime, db, exchange)
    if config is not None and _worker_running(runtime, config): raise ValueError(BUSY)
    confirm_word('STOPPED', confirm)
    with ExitStack() as stack:
        busy = False
        try:
            stack.enter_context(runtime_lock.lock(Path(db).absolute().parent / 'worker.lock', timeout=0))
            if config is not None:
                stack.enter_context(runtime_lock.lock(worker.control(config, create=True) / 'lifetime.lock', timeout=0))
        except TimeoutError:
            busy = True
        if busy: raise ValueError(BUSY)
        backup.exchange_activity(exchange, own)
        yield


def _read_plan(directory, db, *, old_key=None, journal=None):
    trust = signed.read_trust(directory) if (directory / 'trust.json').exists() else None
    own = None
    with readonly(db) as c:
        scope = binding(c)
        if journal is not None:
            key_id, own = journal['old_key_id'], journal['old_public']
        else:
            if (directory / 'identity.json').exists():
                own = public_fields(signed.parse(signed.read_local(directory / 'identity.json', private=True)))
                if any(own[k] != scope[k] for k in scope): raise ValueError(BINDING)
            active = [p for p in (trust or backup.empty_trust())['peers'].values()
                      if not p['revoked'] and all(p[k] == scope[k] for k in scope)]
            key_id = own['key_id'] if own else old_key or (active[0]['key_id'] if len(active) == 1 else None)
            if key_id is None or (trust is None and old_key is None): raise ValueError(OLD_UNKNOWN)
            signed.hex_bytes(key_id, 32)
            if own is None and trust and key_id in trust['peers']:
                own = public_fields(trust['peers'][key_id])
                if any(own[k] != scope[k] for k in scope): raise ValueError(BINDING)
        pending, manifest = [], []
        for row in c.execute('SELECT uuid,packet,published FROM _sync_outbox ORDER BY uuid'):
            p = signed.parse(row['packet'])
            if row['published']:
                if p.get('key_id') == key_id:
                    if p['uuid'] != row['uuid']: raise ValueError(PENDING_INVALID)
                    signed.validate_envelope(p, scope['group'], scope['node'])
                    if p['sender'] != scope['sender']: raise ValueError(PENDING_INVALID)
                    manifest.append((p['uuid'], signed.receipt_digest(p)))
                continue
            permitted = {key_id} if journal is None else {key_id, journal['new_identity']['key_id']}
            if p.get('key_id') not in permitted: raise ValueError(UNEXPECTED)
            signed.validate_envelope(p, scope['group'], scope['node'])
            if p['uuid'] != row['uuid'] or p['sender'] != scope['sender']: raise ValueError(PENDING_INVALID)
            verifier = own if p['key_id'] == key_id else public_fields(journal['new_identity'])
            if verifier is not None:
                try: verify_old(p, verifier)
                except Exception: raise ValueError(PENDING_INVALID) from None
            pending.append(p)
    return dict(binding=scope, old_key_id=key_id, old_public=own, trust=trust, pending=pending, manifest=manifest)


@contextmanager
def prepare(security_dir, db, exchange, *, old_key=None, runtime=None, confirm=backup.console_confirm, _journal=None):
    reason = FAILED
    try:
        directory = signed.no_symlinks(security_dir)
        root = signed.no_symlinks(exchange)
        if directory == root or directory.is_relative_to(root): raise ValueError(backup.TARGET_EXCHANGE)
        if directory.exists():
            signed.private_directory(directory)
            if _journal is None and ((directory / 'rotation-journal.json').exists() or list(directory.glob('.rotate-*'))
                    or list(directory.glob('.restore-*')) or (directory / 'trust.json.pre-restore').exists()):
                raise ValueError(LEFTOVERS)
        initial = _read_plan(directory, db, old_key=old_key, journal=_journal)
        with fence(db, exchange, initial['binding'], runtime, confirm):
            plan = _read_plan(directory, db, old_key=old_key, journal=_journal)
            if plan['binding'] != initial['binding'] or plan['old_key_id'] != initial['old_key_id']: raise ValueError(BINDING)
            no_pending_files(exchange, plan['binding']['node'], plan['pending'])
            if plan['pending'] and _journal is None:
                tables = {}
                for packet in plan['pending']:
                    for change in packet['body']:
                        table = change['table']
                        if table not in sync.TABLES: raise ValueError(PENDING_INVALID)
                        tables[table] = tables.get(table, 0) + 1
                backup.show({'pending_packets': len(plan['pending']), 'operations_by_table': tables})
                confirm_word('RESIGN', confirm)
            yield plan
        return
    except Exception as exc:
        if type(exc) is ValueError and str(exc) in REASONS | UPSTREAM_REASONS:
            reason = str(exc)
    raise ValueError(reason)
