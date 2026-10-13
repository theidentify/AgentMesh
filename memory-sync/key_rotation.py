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
