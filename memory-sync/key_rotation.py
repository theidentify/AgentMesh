"""Operator-authorized key recovery; manifests never use the packet JSON codec."""
import copy
from contextlib import contextmanager, closing, ExitStack
import functools
import hashlib
import os
from pathlib import Path
import re
import stat
import sqlite3
import uuid

import identity_backup as backup
import key_protection as kp
import memory_sync as sync
import signed_packets as signed

FORMAT = 'agentmesh-key-rotation-v2'
DOMAIN = FORMAT.encode() + b'\0'
ROTATIONS = 'agentmesh-rotations-v2'
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


def _sync_directory(directory):
    if os.name != 'nt':
        fd = os.open(directory, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


def _private_mkdir(directory):
    backup.private_mkdir(directory)
    _sync_directory(directory.parent)


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
        _sync_directory(path.parent)
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
def add_rotation(directory, old_key_id, new_key_id, manifest_path, *, sha256=None, count=None):
    signed.hex_bytes(old_key_id, 32)
    signed.hex_bytes(new_key_id, 32)
    if old_key_id == new_key_id: raise ValueError(INVALID_REGISTRY)
    registry = read_rotations(directory)
    manifest = read_manifest(manifest_path, sha256=sha256, count=count)
    target = Path(directory) / 'rotations'
    if not target.exists(): _private_mkdir(target)
    signed.private_directory(target)
    write_manifest(target / (old_key_id + '.manifest'), manifest['packets'].items())
    registry['rotations'][old_key_id] = dict(new_key_id=new_key_id, approved_at=backup.now(),
        manifest_sha256=manifest['sha256'], manifest_count=manifest['count'])
    signed.write_local(Path(directory) / 'rotations.json', registry)
    return registry

LEFTOVERS = 'unfinished rotation or restore exists; inspect or run identity-rotate --resume'
LEGACY = 'rotation requires a signed (required-policy) database; there is no key to rotate'
BINDING = 'signed database binding mismatch'
OLD_UNKNOWN = 'old signing key cannot be determined; supply --old-key'
UNEXPECTED = 'pending outbox is signed by an unexpected key'
BUSY = 'a worker or cycle is using this database'
CANCELLED = 'rotation confirmation was not supplied'
PENDING_EXCHANGE = 'pending packet is already in the exchange; publish or inspect it before rotating'
PENDING_DIFFERS = 'pending packet file differs from the outbox; see compromise-report'
QUARANTINE_INVALID = 'quarantine-collision requires a journal-bound differing packet on resume'
DROP_INVALID = 'drop-pending requires unreferenced pending packets and reason compromised'
PENDING_INVALID = 'invalid pending outbox packet'
RUNTIME = 'runtime does not match rotation paths'
INCOMPLETE_WAL = 'database WAL sidecars are incomplete; inspect or checkpoint the stopped database before retrying'
REASONS |= {QUARANTINE_INVALID, DROP_INVALID, PENDING_DIFFERS, LEFTOVERS, LEGACY, BINDING, OLD_UNKNOWN, UNEXPECTED, BUSY, CANCELLED, PENDING_EXCHANGE, PENDING_INVALID, RUNTIME, INCOMPLETE_WAL}
UPSTREAM_REASONS = backup.UPSTREAM_REASONS | {backup.ACTIVE, backup.EXCHANGE_GROUP, backup.TARGET_EXCHANGE,
    backup.PASSPHRASE_SHORT, backup.PASSPHRASE_MISMATCH, 'passphrase entry requires an interactive console'}


@contextmanager
def readonly(db, *, snapshot_wal=False):
    path = signed.no_symlinks(db)
    sidecars = [signed.no_symlinks(Path(str(path) + suffix)) for suffix in ('-wal', '-shm')]
    present = [p.exists() for p in sidecars]
    # mode=ro may create a missing WAL index. Never repair partial sidecars as
    # a side effect of inspection, or silently ignore an uncheckpointed WAL.
    if any(present) and not all(present): raise ValueError(INCOMPLETE_WAL)
    if snapshot_wal and all(present):
        # Even mode=ro can update SHM read marks. Inspection of a live WAL
        # therefore uses the existing stable-copy reader, never source SQLite.
        from protocol_activation import snapshot
        with snapshot(path) as c:
            yield c
        return
    uri = path.as_uri() + ('?mode=ro' if all(present) else '?mode=ro&immutable=1')
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


def no_pending_files(exchange, node, pending, *, expected=None):
    for p in pending:
        for namespace in ('signed-changes', 'changes'):
            path = signed.no_symlinks(Path(exchange) / namespace / node / (p['uuid'] + '.json'))
            if os.path.lexists(path):
                if (namespace == 'signed-changes' and expected is not None
                        and expected.get(p['uuid']) == signed.read_local(path)):
                    continue
                raise ValueError(PENDING_DIFFERS if expected is not None else PENDING_EXCHANGE)


def validate_packet(p, group, node):
    signed.validate_envelope(p, group, node)
    if p['format'] == signed.FORMAT and (type(p['body']) is not list or not p['body']
            or hashlib.sha256(signed.typed(p['body'])).hexdigest() != p['checksum']):
        raise ValueError(PENDING_INVALID)


def verify_old(p, own):
    validate_packet(p, own['group'], own['node'])
    if p['sender'] != own['sender'] or p['key_id'] != own['key_id']: raise ValueError(PENDING_INVALID)
    unsigned = {k: v for k, v in p.items() if k != 'signature'}
    signed.crypto()[1].from_public_bytes(signed.hex_bytes(own['public_key'], 32)).verify(
        signed.hex_bytes(p['signature'], 64), signed.packet_domain(p) + signed.typed(unsigned))


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
def fence(db, exchange, own, runtime, confirm, *, rotation_files=None):
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
        backup.exchange_activity(exchange, own, _rotation_files=rotation_files)
        yield


def _read_plan(directory, db, *, old_key=None, journal=None, drop_pending=()):
    dropped = dict(journal['manifest']['dropped_packets']) if journal else {}
    selected = set(dropped if journal else drop_pending)
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
        pending, manifest, texts = [], [], {}
        for row in c.execute('SELECT uuid,packet,published FROM _sync_outbox ORDER BY uuid'):
            p = signed.parse(row['packet'])
            if row['published']:
                if p.get('key_id') == key_id:
                    if p['uuid'] != row['uuid']: raise ValueError(PENDING_INVALID)
                    validate_packet(p, scope['group'], scope['node'])
                    if p['sender'] != scope['sender']: raise ValueError(PENDING_INVALID)
                    manifest.append((p['uuid'], signed.envelope_digest(p)))
                continue
            if p.get('key_id') != key_id: raise ValueError(UNEXPECTED)
            validate_packet(p, scope['group'], scope['node'])
            if p['uuid'] != row['uuid'] or p['sender'] != scope['sender']: raise ValueError(PENDING_INVALID)
            verifier = own
            if verifier is not None:
                try: verify_old(p, verifier)
                except Exception: raise ValueError(PENDING_INVALID) from None
            if p['uuid'] in selected:
                digest = hashlib.sha256(row['packet'].encode('utf-8')).hexdigest()
                if journal and dropped[p['uuid']] != digest: raise ValueError(PENDING_INVALID)
                dropped[p['uuid']] = digest
                continue
            pending.append(p)
            manifest.append((p['uuid'], signed.envelope_digest(p)))
            texts[p['uuid']] = hashlib.sha256(row['packet'].encode('utf-8')).hexdigest()
        if selected != set(dropped): raise ValueError(DROP_INVALID)
        for packet in (signed.parse(row[0]) for row in c.execute('SELECT packet FROM _sync_outbox WHERE published=0')):
            if any(ref['uuid'] in selected for ref in packet.get('covers', []) + packet.get('evidence', [])):
                raise ValueError(DROP_INVALID)
        _check_dropped(c, dropped)
        if journal is not None:
            expected = journal['manifest']['pending_packets']
            # Published rows must still match too, including after a DB commit.
            for uid, digest in expected.items():
                row = c.execute('SELECT packet FROM _sync_outbox WHERE uuid=?', (uid,)).fetchone()
                if row is None or hashlib.sha256(row[0].encode('utf-8')).hexdigest() != digest:
                    raise ValueError(PENDING_INVALID)
            if any(expected.get(uid) != digest for uid, digest in texts.items()):
                raise ValueError(PENDING_INVALID)
    return dict(binding=scope, old_key_id=key_id, old_public=own, trust=trust, pending=pending,
                manifest=manifest, pending_packets=texts, dropped_packets=dropped)


def _check_dropped(c, dropped):
    for uid, digest in dropped.items():
        row = c.execute('SELECT packet,published FROM _sync_outbox WHERE uuid=?', (uid,)).fetchone()
        if row is None:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_rotation_dropped'").fetchone():
                raise ValueError(PENDING_INVALID)
            row = c.execute('SELECT packet,0 FROM _sync_rotation_dropped WHERE uuid=?', (uid,)).fetchone()
        if row is None or row[1] or hashlib.sha256(row[0].encode('utf-8')).hexdigest() != digest:
            raise ValueError(PENDING_INVALID)


def packet_summary(p):
    if any(ch['table'] not in sync.TABLES for ch in p['body']):
        raise ValueError(PENDING_INVALID)
    item = dict(uuid=p['uuid'], format=p['format'], changes=[
        dict(table=ch['table'], op='delete' if ch['row'] is None else 'upsert',
             key_sha256=hashlib.sha256(sync.canonical(ch['key']).encode('utf-8')).hexdigest()[:16])
        for ch in p['body']])
    if p['format'] == signed.RESOLUTION_FORMAT: item['kind'] = p['kind']
    return item


@contextmanager
def prepare(security_dir, db, exchange, *, old_key=None, runtime=None, confirm=backup.console_confirm, _journal=None, drop_pending=(), rotation_reason=None, quarantine_collision=()):
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
        initial = _read_plan(directory, db, old_key=old_key, journal=_journal, drop_pending=drop_pending)
        expected = None if _journal is None else _journal['manifest']['pending_packets']
        rotation_files = None if expected is None else {
            Path(exchange) / 'signed-changes' / initial['binding']['node'] / (uid + '.json'): digest
            for uid, digest in expected.items()}
        collisions = {}
        expected_bytes = None
        if expected is not None:
            with readonly(db) as c:
                expected_bytes = {uid: c.execute('SELECT packet FROM _sync_outbox WHERE uuid=?', (uid,)).fetchone()[0].encode('utf-8') for uid in expected}
            for uid in quarantine_collision:
                if uid not in expected_bytes: raise ValueError(QUARANTINE_INVALID)
                path = signed.no_symlinks(Path(exchange) / 'signed-changes' / initial['binding']['node'] / (uid + '.json'))
                raw = signed.read_local(path)
                if raw == expected_bytes[uid]: raise ValueError(QUARANTINE_INVALID)
                packet = signed.parse(raw)
                # The path is journal-bound; the hostile contents need not claim
                # its UUID or scope. Quarantine preserves bytes, never authorizes them.
                if type(packet) is not dict or packet.get('format') not in (signed.FORMAT, signed.RESOLUTION_FORMAT):
                    raise ValueError(QUARANTINE_INVALID)
                collisions[uid] = (path, raw, signed.envelope_digest(packet),
                    signed.envelope_digest(signed.parse(expected_bytes[uid])))
                rotation_files[path] = hashlib.sha256(raw).hexdigest()
            allowed = dict(expected_bytes)
            allowed.update({uid: value[1] for uid, value in collisions.items()})
            no_pending_files(exchange, initial['binding']['node'], initial['pending'], expected=allowed)
            # A process kill may leave a partial temporary file. Exempt only
            # this journal's exact staging names with a matching outbox prefix;
            # cleanup happens later under both locks, in the shared writer.
            with readonly(db) as c:
                for uid in expected:
                    temp = signed.no_symlinks(Path(exchange) / 'signed-changes' / initial['binding']['node'] /
                        ('.' + uid + '.rotate-' + _journal['new_identity']['key_id'] + '.tmp'))
                    if temp.exists():
                        raw = signed.read_local(temp)
                        text = c.execute('SELECT packet FROM _sync_outbox WHERE uuid=?', (uid,)).fetchone()[0]
                        if text.encode('utf-8').startswith(raw): rotation_files[temp] = hashlib.sha256(raw).hexdigest()
        with fence(db, exchange, initial['binding'], runtime, confirm, rotation_files=rotation_files):
            for uid, (path, raw, digest, own_digest) in collisions.items():
                backup.show(dict(uuid=uid, exchange_envelope_digest=digest, outbox_envelope_digest=own_digest))
                confirm_word('QUARANTINE', confirm)
                if signed.read_local(path) != raw: raise ValueError(PENDING_DIFFERS)
            for uid, (path, raw, digest, own_digest) in collisions.items():
                target = Path(exchange)
                for component in ('pairing', 'rotations', 'quarantine'):
                    target = signed.no_symlinks(target / component)
                    if not target.exists(): _private_mkdir(target)
                destination = signed.no_symlinks(target / (uid + '.' + digest + '.json'))
                if destination.exists():
                    if signed.read_local(destination) != raw: raise ValueError(QUARANTINE_INVALID)
                else:
                    os.link(path, destination)
                    _sync_directory(target)
                if signed.read_local(path) != raw: raise ValueError(PENDING_DIFFERS)
                path.unlink()
                _sync_directory(path.parent)
            plan = _read_plan(directory, db, old_key=old_key, journal=_journal, drop_pending=drop_pending)
            if plan['binding'] != initial['binding'] or plan['old_key_id'] != initial['old_key_id']: raise ValueError(BINDING)
            no_pending_files(exchange, plan['binding']['node'], plan['pending'], expected=expected_bytes)
            if rotation_reason == 'compromised' and _journal is None:
                with readonly(db) as c:
                    backup.show({'pending_review': [packet_summary(signed.parse(row[0])) for row in
                        c.execute('SELECT packet FROM _sync_outbox WHERE published=0 ORDER BY uuid')]})
            if plan['pending'] and _journal is None:
                tables, formats = {}, {}
                for packet in plan['pending']:
                    formats[packet['format']] = formats.get(packet['format'], 0) + 1
                    for change in packet['body']:
                        table = change['table']
                        if table not in sync.TABLES: raise ValueError(PENDING_INVALID)
                        counts = tables.setdefault(table, {'upsert': 0, 'delete': 0})
                        counts['delete' if change['row'] is None else 'upsert'] += 1
                backup.show({'pending_packets': len(plan['pending']), 'packets_by_format': formats, 'operations_by_table': tables})
                confirm_word('PUBLISH', confirm)
            yield plan
        return
    except Exception as exc:
        if type(exc) is ValueError and str(exc) in REASONS | UPSTREAM_REASONS:
            reason = str(exc)
    raise ValueError(reason)


JOURNAL = 'agentmesh-rotation-journal-v2'
JOURNAL_INVALID = 'invalid rotation journal'
IN_PROGRESS = 'rotation in progress; run identity-rotate --resume'
TRUST_CONFLICT = 'rotation conflicts with an active identity or allocation slot'
REASONS |= {JOURNAL_INVALID, IN_PROGRESS, TRUST_CONFLICT}
_AUTO_BACKEND = object()


def rotated_trust(trust, old, new, old_public):
    result = copy.deepcopy(trust or backup.empty_trust())
    if old not in result['peers'] and old_public is not None:
        result['peers'][old] = {**old_public, 'revoked': True}
    if old in result['peers']: result['peers'][old]['revoked'] = True
    existing = result['peers'].get(new['key_id'])
    if existing and (existing['revoked'] or public_fields(existing) != new): raise ValueError(TRUST_CONFLICT)
    result['peers'][new['key_id']] = {**new, 'revoked': False}
    active, slots = set(), set()
    for p in result['peers'].values():
        if p['revoked']: continue
        scope = (p['group'], p['node'], p['sender'])
        slot = (p['group'], p['node'])
        if scope in active or slot in slots: raise ValueError(TRUST_CONFLICT)
        active.add(scope)
        slots.add(slot)
    return signed.check_trust(result)


class _MemorySigner:
    def __init__(self, public, key): self.public, self.key = public, key
    def sign_bytes(self, data): return self.key.sign(data)


class _ReplaySecurity(signed.Security):
    """Internal replay only: require exact journal-bound identity and live trust.

    Ordinary Security has no bypass flag. Replay is created only under both
    fences, and cannot authorize any identity other than the sealed journal key.
    """
    def check_self(self):
        doc = signed.parse(signed.read_local(self.directory / 'rotation-journal.json', private=True))
        current = signed.parse(signed.read_local(self.directory / 'identity.json', private=True))
        if current != doc['new_identity'] or public_fields(current) != self.public:
            raise ValueError(JOURNAL_INVALID)
        self._check_self_authorized()


def _manifest_stage(directory, new_key):
    return directory / ('.rotate-' + new_key + '.manifest')


@fixed(JOURNAL_INVALID)
def read_journal(directory, exchange):
    doc = signed.parse(signed.read_local(directory / 'rotation-journal.json', private=True))
    if type(doc) is not dict or set(doc) != {'format', 'old_key_id', 'old_public', 'new_identity', 'reason', 'manifest', 'proposal'} or doc['format'] != JOURNAL:
        raise ValueError(JOURNAL_INVALID)
    identity = doc['new_identity']
    if set(identity) != signed.PUBLIC_FIELDS | {'protection'}: raise ValueError(JOURNAL_INVALID)
    new = public_fields(identity)
    kp.check(identity['protection'])
    signed.hex_bytes(doc['old_key_id'], 32)
    if doc['old_public'] is not None:
        old = signed.check_public(doc['old_public'])
        if old['key_id'] != doc['old_key_id'] or any(old[k] != new[k] for k in ('group', 'node', 'sender')):
            raise ValueError(JOURNAL_INVALID)
    meta = doc['manifest']
    if type(meta) is not dict or set(meta) != {'sha256', 'count', 'published_pending', 'pending_packets', 'dropped_packets', 'trust_restored'} or type(meta['published_pending']) is not int or meta['published_pending'] < 0 or type(meta['trust_restored']) is not bool:
        raise ValueError(JOURNAL_INVALID)
    pending = meta['pending_packets']
    if type(pending) is not dict or len(pending) != meta['published_pending']: raise ValueError(JOURNAL_INVALID)
    for uid, digest in pending.items():
        if not signed.canonical_uuid(uid): raise ValueError(JOURNAL_INVALID)
        signed.hex_bytes(digest, 32)
    dropped = meta['dropped_packets']
    if type(dropped) is not dict or set(dropped) & set(pending): raise ValueError(JOURNAL_INVALID)
    if dropped and doc['reason'] != 'compromised': raise ValueError(JOURNAL_INVALID)
    for uid, digest in dropped.items():
        if not signed.canonical_uuid(uid): raise ValueError(JOURNAL_INVALID)
        signed.hex_bytes(digest, 32)
    p = doc['proposal']
    if (p['new'] != new or p['old_key_id'] != doc['old_key_id'] or p['reason'] != doc['reason']
            or p['manifest_sha256'] != meta['sha256'] or p['manifest_count'] != meta['count']):
        raise ValueError(JOURNAL_INVALID)
    stage = _manifest_stage(directory, new['key_id'])
    # A crash after staging cleanup but before the final journal unlink can use
    # the already-published, hash-pinned manifest, without trusting exchange data.
    source = stage if stage.exists() else Path(exchange) / 'pairing' / 'rotations' / (new['key_id'] + '.manifest')
    check_proposal(p, source)
    return doc


def _write_equal(path, doc):
    if path.exists() and signed.parse(signed.read_local(path, private=True)) == doc: return
    signed.write_local(path, doc)


def _publish_rotation(directory, exchange, journal):
    p = journal['proposal']
    target = signed.no_symlinks(Path(exchange) / 'pairing')
    if not target.exists(): _private_mkdir(target)
    target = signed.no_symlinks(target / 'rotations')
    if not target.exists(): _private_mkdir(target)
    manifest_path = target / (p['new']['key_id'] + '.manifest')
    stage = _manifest_stage(directory, p['new']['key_id'])
    source = stage if stage.exists() else manifest_path
    manifest = read_manifest(source, sha256=p['manifest_sha256'], count=p['manifest_count'], private=source == stage)
    if manifest_path.exists():
        read_manifest(manifest_path, sha256=p['manifest_sha256'], count=p['manifest_count'])
    else: write_manifest(manifest_path, manifest['packets'].items(), exclusive=True)
    proposal_path = target / (p['new']['key_id'] + '.json')
    if proposal_path.exists():
        if signed.read_local(proposal_path) != signed.wire(p): raise ValueError(INVALID)
    else: signed.write_local(proposal_path, p, exclusive=True)


def _replay(directory, db, exchange, plan, journal, phrase, backend, after_step):
    new = public_fields(journal['new_identity'])
    trust = rotated_trust(plan['trust'], journal['old_key_id'], new, journal['old_public'])
    _write_equal(directory / 'trust.json', trust)
    after_step('trust')
    _write_equal(directory / 'identity.json', journal['new_identity'])
    after_step('identity')
    security = _ReplaySecurity(directory, unlock=lambda: phrase)
    stage = _manifest_stage(directory, new['key_id'])
    source = stage if stage.exists() else Path(exchange) / 'pairing' / 'rotations' / (new['key_id'] + '.manifest')
    manifest = read_manifest(source, sha256=journal['manifest']['sha256'],
                             count=journal['manifest']['count'])['packets']
    if dict(plan['manifest']) != manifest: raise ValueError(PENDING_INVALID)
    expected = journal['manifest']['pending_packets']
    dropped = journal['manifest']['dropped_packets']
    with sync.connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        if binding(c) != plan['binding']: raise ValueError(BINDING)
        # Recheck the outbox under the write lock, then publish each original
        # stored text. A failed file write rolls back all published flags.
        pending = c.execute('SELECT uuid,packet FROM _sync_outbox WHERE published=0').fetchall()
        _check_dropped(c, dropped)
        for row in pending:
            packet = signed.parse(row['packet'])
            if any(ref['uuid'] in dropped for ref in packet.get('covers', []) + packet.get('evidence', [])):
                raise ValueError(DROP_INVALID)
        pending = [row for row in pending if row['uuid'] not in dropped]
        for row in pending:
            if expected.get(row['uuid']) != hashlib.sha256(row['packet'].encode('utf-8')).hexdigest():
                raise ValueError(PENDING_INVALID)
        validated = []
        for uid, digest in expected.items():
            row = c.execute('SELECT packet,published FROM _sync_outbox WHERE uuid=?', (uid,)).fetchone()
            if row is None or hashlib.sha256(row['packet'].encode('utf-8')).hexdigest() != digest:
                raise ValueError(PENDING_INVALID)
            p = signed.parse(row['packet'])
            validate_packet(p, new['group'], new['node'])
            if (p['uuid'] != uid or p['sender'] != new['sender'] or p['key_id'] != journal['old_key_id']
                    or manifest.get(uid) != signed.envelope_digest(p)):
                raise ValueError(PENDING_INVALID)
            if journal['old_public'] is not None: verify_old(p, journal['old_public'])
            no_pending_files(exchange, new['node'], [p], expected={uid: row['packet'].encode('utf-8')})
            validated.append((uid, row))
        if {row['uuid'] for row in pending} != {uid for uid, row in validated if not row['published']}:
            raise ValueError(PENDING_INVALID)
        for uid, row in validated:
            sync.write_packet(Path(exchange) / 'signed-changes' / new['node'], uid, row['packet'],
                              signed_packet=True, staging_id=new['key_id'])
            if not row['published']:
                c.execute('UPDATE _sync_outbox SET published=1 WHERE uuid=?', (uid,))
        if not dropped: security.guard(c, exchange)
    after_step('publish')
    verified = _ReplaySecurity(directory, unlock=lambda: phrase)
    verified.check_self()
    with readonly(db) as c:
        if any(row[0] not in dropped for row in c.execute('SELECT uuid FROM _sync_outbox WHERE published=0')):
            raise ValueError(PENDING_INVALID)
        for uid, digest in expected.items():
            row = c.execute('SELECT packet FROM _sync_outbox WHERE uuid=?', (uid,)).fetchone()
            path = Path(exchange) / 'signed-changes' / new['node'] / (uid + '.json')
            if row is None or signed.read_local(path) != row['packet'].encode('utf-8'):
                raise ValueError(PENDING_INVALID)
    after_step('verify')
    _publish_rotation(directory, exchange, journal)
    after_step('proposal')
    if dropped:
        with sync.connect(db) as c, c:
            c.execute('BEGIN IMMEDIATE')
            _check_dropped(c, dropped)
            c.execute('CREATE TABLE IF NOT EXISTS _sync_rotation_dropped(uuid TEXT PRIMARY KEY, packet TEXT NOT NULL, dropped_at TEXT NOT NULL)')
            dropped_at = backup.now()
            for uid in dropped:
                row = c.execute('SELECT packet FROM _sync_outbox WHERE uuid=?', (uid,)).fetchone()
                if row is not None:
                    c.execute('INSERT INTO _sync_rotation_dropped VALUES(?,?,?)', (uid, row[0], dropped_at))
                    c.execute('DELETE FROM _sync_outbox WHERE uuid=?', (uid,))
            security.guard(c, exchange)
    after_step('drop')
    if backend is _AUTO_BACKEND:
        import os_unlock
        backend = os_unlock.current_backend()
    removed = None
    if backend is not None:
        removed = False
        try:
            backend.delete(journal['old_key_id'])
            removed = True
        except Exception:
            pass
    next_steps = ['Read the new fingerprint to each peer out of band, then run peer-rotate there.']
    if journal['reason'] == 'compromised': next_steps.append('Run compromise-report everywhere, including this device.')
    if not journal['manifest']['trust_restored']: next_steps.append('Re-pin peers using signed_packets trust and out-of-band fingerprints.')
    next_steps += ['Create a new backup and run a drill; delete backups of the revoked key.',
                   'Optionally enable identity-os-unlock for the new key.', 'Restart the worker.']
    if removed is False:
        next_steps.insert(0, 'Old OS unlock item was NOT removed. Remove the old fingerprint item by hand; identity-os-unlock disable targets the currently installed key.')
    _manifest_stage(directory, new['key_id']).unlink(missing_ok=True)
    (directory / 'rotation-journal.json').unlink()  # Last durable step: signing opens only now.
    _sync_directory(directory)
    return dict(status='rotated', old_key_id=journal['old_key_id'], new_key_id=new['key_id'], new_fingerprint=new['key_id'],
        reason=journal['reason'], dropped_pending=sorted(dropped), published_pending=journal['manifest']['published_pending'], manifest_packets=journal['manifest']['count'],
        proposal='pairing/rotations/' + new['key_id'] + '.json', trust_restored=journal['manifest']['trust_restored'],
        os_unlock_item_removed=removed, next=next_steps)


@safe_operation
def identity_rotate(security_dir, db, exchange, *, reason=None, old_key=None, runtime=None, resume=False, drop_pending=(), quarantine_collision=(),
                    ask=kp.console_passphrase, confirm=backup.console_confirm, backend=_AUTO_BACKEND,
                    _test_n=None, _after_step=lambda step: None):
    directory = signed.no_symlinks(security_dir)
    if quarantine_collision and (not resume or any(not signed.canonical_uuid(uid) for uid in quarantine_collision)):
        raise ValueError(QUARANTINE_INVALID)
    if drop_pending and (resume or reason != 'compromised' or any(not signed.canonical_uuid(uid) for uid in drop_pending)):
        raise ValueError(DROP_INVALID)
    journal = read_journal(directory, exchange) if resume else None
    if not resume and reason not in ('lost', 'compromised', 'planned'): raise ValueError(INVALID)
    with prepare(directory, db, exchange, old_key=old_key, runtime=runtime, confirm=confirm, _journal=journal, drop_pending=drop_pending, rotation_reason=reason, quarantine_collision=quarantine_collision) as plan:
        if journal is None:
            phrase = backup.new_passphrase(ask, None, 'identity')
            key = signed.crypto()[0].generate()
            pub = key.public_key().public_bytes_raw()
            new = dict(format='agentmesh-peer-key-v1', **plan['binding'], key_id=hashlib.sha256(pub).hexdigest(), public_key=pub.hex())
            identity = backup.sealed_identity(new, key.private_bytes_raw(), phrase, kp.SCRYPT_N if _test_n is None else _test_n)
            # Validate all trust constraints before writing a journal.
            rotated_trust(plan['trust'], plan['old_key_id'], new, plan['old_public'])
            created_directory = not directory.exists()
            if created_directory: _private_mkdir(directory)
            stage = _manifest_stage(directory, new['key_id'])
            staged = False
            try:
                manifest = write_manifest(stage, plan['manifest'], exclusive=True)
                staged = True
                journal = dict(format=JOURNAL, old_key_id=plan['old_key_id'], old_public=plan['old_public'],
                    new_identity=identity, reason=reason,
                    manifest=dict(sha256=manifest['sha256'], count=manifest['count'], published_pending=len(plan['pending']), pending_packets=plan['pending_packets'], dropped_packets=plan['dropped_packets'], trust_restored=plan['trust'] is not None),
                    proposal=proposal(_MemorySigner(new, key), plan['old_key_id'], reason, stage))
                signed.write_local(directory / 'rotation-journal.json', journal, exclusive=True)
            finally:
                # After journal publication, every failure must retain forward recovery.
                # Before it, remove only staging successfully created by this attempt.
                if not os.path.lexists(directory / 'rotation-journal.json'):
                    if staged: stage.unlink(missing_ok=True)
                    if created_directory and not any(directory.iterdir()): directory.rmdir()
        else:
            if any(journal['new_identity'][k] != plan['binding'][k] for k in plan['binding']): raise ValueError(BINDING)
            phrase = ask('New identity passphrase: ')
            kp.open_sealed(public_fields(journal['new_identity']), journal['new_identity']['protection'], phrase)
        return _replay(directory, db, exchange, plan, journal, phrase, backend, _after_step)


OWN_KEY = "a rotation proposal cannot replace this device's own key"
UNKNOWN_OLD = 'unknown old key'
ALREADY = 'already rotated'
NEW_REVOKED = 'new key is revoked here'
GROUP_MISMATCH = 'rotation group does not match this device'
FINGERPRINT = 'rotation fingerprint does not match'
TRUST_CHANGED = 'local trust changed during rotation approval'
REASONS |= {OWN_KEY, UNKNOWN_OLD, ALREADY, NEW_REVOKED, GROUP_MISMATCH, FINGERPRINT, TRUST_CHANGED}


def console_fingerprint():
    import console_prompt
    prompt = console_prompt.ConsolePrompt()
    if not prompt.interactive(): raise ValueError('passphrase entry requires an interactive console')
    prompt.show('Read the full new fingerprint from the rotating device out of band, then type it here:')
    # Do not display or ghost the expected fingerprint from the exchange.
    return prompt.stdin.readline().strip()


@fixed(INVALID)
def read_proposal(path):
    return check_proposal(signed.parse(signed.read_local(path, private=False)), Path(path).with_suffix('.manifest'))


@safe_operation
def peer_rotate(security_dir, proposal_file, *, exchange=None, confirm=console_fingerprint):
    directory = signed.private_directory(security_dir)
    own = signed.Security(directory).public
    path = signed.no_symlinks(proposal_file)
    doc = read_proposal(path)
    new, old = doc['new'], doc['old_key_id']
    if old == own['key_id'] or new['node'] == own['node']: raise ValueError(OWN_KEY)
    if new['group'] != own['group']: raise ValueError(GROUP_MISMATCH)
    trust = signed.read_trust(directory)
    entry = trust['peers'].get(old)
    if entry is None or any(entry[k] != new[k] for k in ('group', 'node', 'sender')): raise ValueError(UNKNOWN_OLD)
    if new['key_id'] in trust['peers']:
        raise ValueError(NEW_REVOKED if trust['peers'][new['key_id']]['revoked'] else ALREADY)
    updated = rotated_trust(trust, old, new, public_fields(entry))
    folder = signed.no_symlinks(Path(exchange) / 'pairing' / 'rotations') if exchange is not None else path.parent
    competing = 0
    for candidate in folder.glob('*.json'):
        if candidate == path: continue
        try:
            other = signed.parse(signed.read_local(candidate))
            if type(other) is dict and other.get('format') == FORMAT and other.get('old_key_id') == old:
                competing += 1
        except (OSError, ValueError):
            continue
    if competing:
        backup.show({'warning': 'more than one rotation was proposed for this key; verify with the device owner'})
    backup.show({'old_fingerprint': old[:16], 'new_fingerprint': new['key_id'][:16],
                 'reason': doc['reason'], 'created_at': doc['created_at'], 'manifest_packets': doc['manifest_count']})
    if confirm() != new['key_id']: raise ValueError(FINGERPRINT)
    if signed.read_trust(directory) != trust: raise ValueError(TRUST_CHANGED)
    add_rotation(directory, old, new['key_id'], path.with_suffix('.manifest'),
                 sha256=doc['manifest_sha256'], count=doc['manifest_count'])
    signed.write_local(directory / 'trust.json', updated)
    next_steps = ['Re-run wizard pairing to update security_state.peer; update the profile-memory SignedChannel peer fingerprint.']
    if doc['reason'] == 'compromised': next_steps.insert(0, 'Run compromise-report --key ' + old + ' on this device.')
    return dict(status='peer-rotated', old_key_id=old, new_key_id=new['key_id'], reason=doc['reason'],
                manifest_packets=doc['manifest_count'], competing_proposals=competing, next=next_steps)


INVALID_SINCE = 'invalid compromise report timestamp'
REASONS.add(INVALID_SINCE)


@safe_operation
def compromise_report(db, exchange, key, *, security_dir=None, since=None):
    signed.hex_bytes(key, 32)
    cutoff = None
    if since is not None:
        try: cutoff = backup.timestamp(since).timestamp()
        except Exception: raise ValueError(INVALID_SINCE) from None
    manifest = None if security_dir is None else {}
    if security_dir is not None:
        entry = read_rotations(security_dir)['rotations'].get(key)
        if entry is not None:
            manifest = read_manifest(Path(security_dir) / 'rotations' / (key + '.manifest'), private=True,
                                     sha256=entry['manifest_sha256'], count=entry['manifest_count'])['packets']
    result = dict(committed=[], uncommitted=[], forged_candidates=[], receipts_without_file=0,
                  legacy_receipts=0, filtered_by_since=0, unreadable=0)
    root = signed.no_symlinks(Path(exchange) / 'signed-changes')
    present = set()
    with readonly(db, snapshot_wal=True) as c:
        cfg = sync.config(c)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        receipts = dict(c.execute('SELECT uuid,checksum FROM _sync_receipts')) if '_sync_receipts' in tables else {}
        envelopes = dict(c.execute('SELECT uuid,digest FROM _sync_receipt_envelopes')) if '_sync_receipt_envelopes' in tables else {}
        result['legacy_receipts'] = len(set(receipts) - set(envelopes))
        for path in sorted(root.glob('*/*')):
            try:
                if not stat.S_ISREG(path.lstat().st_mode): continue
                raw = signed.read_local(path)
                p = signed.parse(raw)
                validate_packet(p, cfg['group_id'], path.parent.name)
                if path.name != p['uuid'] + '.json': raise ValueError(INVALID)
                present.add(p['uuid'])
                if p['key_id'] != key: continue
                # Validate table/op/key shape before emitting any untrusted label.
                if p['format'] == signed.RESOLUTION_FORMAT:
                    import packet_versions
                    packet_versions.validate_wire(p, group=cfg['group_id'], folder=path.parent.name)
                    packet_versions._validate_rows(c, p)
                else:
                    legacy = {k: p[k] for k in ('group', 'node', 'uuid', 'body')}
                    legacy.update(format=sync.FORMAT, checksum=sync.digest(p['body']))
                    sync._validate(c, legacy)
                when = path.stat().st_mtime
                if cutoff is not None and when < cutoff:
                    result['filtered_by_since'] += 1
                    continue
                digest = signed.receipt_digest(p)
                item = dict(uuid=p['uuid'], node=p['node'], file_mtime=when,
                    changes=[dict(table=ch['table'], op='delete' if ch['row'] is None else 'upsert',
                                  key_sha256=hashlib.sha256(sync.canonical(ch['key']).encode('utf-8')).hexdigest()[:16]) for ch in p['body']],
                    in_manifest=None if manifest is None else manifest.get(p['uuid']) == signed.envelope_digest(p))
                if p['format'] == signed.RESOLUTION_FORMAT:
                    item['kind'] = p['kind']
                    for change in item['changes']: change['kind'] = p['kind']
                complete = signed.envelope_digest(p)
                if '_sync_packet_dispositions' in tables:
                    dispositions = c.execute('SELECT envelope_digest FROM _sync_packet_dispositions WHERE packet_uuid=?',
                                             (p['uuid'],)).fetchall()
                    item['superseded'] = any(row[0] == complete for row in dispositions)
                    item['variant_superseded'] = any(row[0] != complete for row in dispositions)
                committed = receipts.get(p['uuid']) == digest and (p['uuid'] not in envelopes or envelopes[p['uuid']] == complete)
                result['committed' if committed else 'uncommitted'].append(item)
                if p['node'] == cfg['node']:
                    row = c.execute('SELECT packet FROM _sync_outbox WHERE uuid=?', (p['uuid'],)).fetchone()
                    if row is None or row[0].encode('utf-8') != raw: result['forged_candidates'].append(item)
            except (OSError, ValueError, TypeError, KeyError, OverflowError):
                result['unreadable'] += 1
        result['receipts_without_file'] = len(set(envelopes) - present)
    return result
