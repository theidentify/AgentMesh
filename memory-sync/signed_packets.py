"""Opt-in Ed25519 packet authentication and operator-pinned local trust."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import uuid

FORMAT = 'agentmesh-signed-changes-v2'
ENCODING = 'agentmesh-typed-v1'
DOMAIN = b'AgentMesh/change-packet/Ed25519/v2\x00'
MAX_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 64
MAX_VALUES = 100000
SLOTS = ('mac', 'windows', 'linux')
FIELDS = {'format', 'encoding', 'group', 'node', 'sender', 'uuid', 'body', 'checksum', 'key_id', 'signature'}
PUBLIC_FIELDS = {'format', 'group', 'node', 'sender', 'key_id', 'public_key'}


def crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:
        raise ValueError('signed sync requires cryptography; install requirements-security.txt') from exc
    return Ed25519PrivateKey, Ed25519PublicKey, InvalidSignature


def canonical_uuid(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def hex_bytes(value, length):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{%d}' % (length * 2), value):
        raise ValueError('invalid hex field')
    return bytes.fromhex(value)


def typed(value):
    """Injective typed binary encoding, NOT RFC 8785 / JCS. See SIGNED-SYNC.md."""
    count = 0
    def encode(v, depth):
        nonlocal count
        count += 1
        if count > MAX_VALUES or depth > MAX_DEPTH:
            raise ValueError('packet structure limit exceeded')
        if v is None: return b'n'
        if type(v) is bool: return b't' if v else b'f'
        if type(v) is int:
            if not -(2**63) <= v < 2**63: raise ValueError('integer outside signed 64-bit range')
            return b'i' + str(v).encode('ascii') + b';'
        if type(v) is float:
            if not math.isfinite(v): raise ValueError('non-finite number')
            return b'd' + struct.pack('>d', v)
        if type(v) is str:
            try: raw = v.encode('utf-8', errors='strict')
            except UnicodeError as exc: raise ValueError('invalid Unicode scalar') from exc
            return b's' + str(len(raw)).encode('ascii') + b':' + raw
        if type(v) is list:
            return b'l' + str(len(v)).encode('ascii') + b':' + b''.join(encode(x, depth + 1) for x in v)
        if type(v) is dict and all(type(k) is str for k in v):
            keys = sorted(v, key=lambda k: k.encode('utf-8', errors='strict'))
            return b'o' + str(len(keys)).encode('ascii') + b':' + b''.join(encode(k, depth + 1) + encode(v[k], depth + 1) for k in keys)
        raise ValueError('unsupported JSON value')
    try: result = encode(value, 0)
    except (UnicodeError, RecursionError) as exc: raise ValueError('invalid or excessive structure') from exc
    if len(result) > MAX_BYTES: raise ValueError('encoded packet too large')
    return result


def wire(value):
    typed(value)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(data) > MAX_BYTES: raise ValueError('packet too large')
    return data


def parse(data):
    if len(data) > MAX_BYTES: raise ValueError('packet too large')
    def pairs(items):
        obj = {}
        for k, v in items:
            if k in obj: raise ValueError('duplicate JSON key')
            obj[k] = v
        return obj
    def invalid(_): raise ValueError('non-finite number')
    try:
        if isinstance(data, bytes): data = data.decode('utf-8', errors='strict')
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
        typed(value)
        return value
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('invalid encoding or excessive structure') from exc


def no_symlinks(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    for part in (path, *path.parents):
        if part.is_symlink(): raise ValueError('symlink path not permitted')
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        # Windows junctions and other reparse points are not necessarily
        # reported as symlinks by Python 3.10/3.11. Reject all of them.
        if getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ValueError('reparse path not permitted')
    return path


def windows_private(path, *, provision=False):
    if os.name == 'nt':
        import windows_acl
        windows_acl.apply(no_symlinks(path), provision=provision)


def read_local(path, *, private=False):
    path = no_symlinks(path)
    if private:
        windows_private(path)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode): raise ValueError('regular file required')
        if private and os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError('local security files must be owner-only (chmod 600)')
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES: raise ValueError('file too large')
    return data


def write_local(path, value, *, exclusive=False):
    path = no_symlinks(path)
    # Security files are local, never under the exchange. Caller creates parent.
    temp = path.with_name('.' + path.name + '.' + str(uuid.uuid4()) + '.tmp')
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            windows_private(temp, provision=True)
            stream.write(wire(value)); stream.flush(); os.fsync(stream.fileno())
        if exclusive:
            os.link(temp, path)  # Atomic no-clobber publication.
        else:
            os.replace(temp, path)
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


def check_public(p):
    if type(p) is not dict or set(p) != PUBLIC_FIELDS or p['format'] != 'agentmesh-peer-key-v1':
        raise ValueError('invalid public identity')
    if not canonical_uuid(p['group']) or not canonical_uuid(p['sender']) or p['node'] not in SLOTS:
        raise ValueError('invalid group/sender/allocation slot')
    key = hex_bytes(p['public_key'], 32)
    if hashlib.sha256(key).hexdigest() != p['key_id']: raise ValueError('public fingerprint mismatch')
    return p


def init_identity(directory, group, node, sender=None):
    Private, _, _ = crypto()
    if not canonical_uuid(group) or node not in SLOTS or (sender is not None and not canonical_uuid(sender)):
        raise ValueError('invalid identity scope')
    directory = no_symlinks(directory)
    # Refuse reuse, including partial initialization; never silently rotate a key.
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    windows_private(directory, provision=True)
    private_directory(directory)
    key = Private.generate()
    public = key.public_key().public_bytes_raw()
    p = {'format': 'agentmesh-peer-key-v1', 'group': group, 'node': node,
         'sender': sender or str(uuid.uuid4()), 'key_id': hashlib.sha256(public).hexdigest(), 'public_key': public.hex()}
    write_local(directory / 'identity.json', {**p, 'private_key': key.private_bytes_raw().hex()}, exclusive=True)
    write_local(directory / 'trust.json', {'format': 'agentmesh-trust-v1', 'peers': {p['key_id']: {**p, 'revoked': False}}}, exclusive=True)
    return p


def private_directory(directory):
    directory = no_symlinks(directory)
    info = directory.stat()
    if not stat.S_ISDIR(info.st_mode): raise ValueError('security directory required')
    windows_private(directory)
    if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError('security directory must be owner-only (chmod 700)')
    return directory

def read_trust(directory):
    private_directory(directory)
    trust = parse(read_local(Path(directory) / 'trust.json', private=True))
    if type(trust) is not dict or set(trust) != {'format', 'peers'} or trust['format'] != 'agentmesh-trust-v1' or type(trust['peers']) is not dict:
        raise ValueError('invalid local trust store')
    for key_id, entry in trust['peers'].items():
        if type(entry) is not dict or set(entry) != PUBLIC_FIELDS | {'revoked'} or type(entry['revoked']) is not bool:
            raise ValueError('invalid pinned trust entry')
        check_public({k: entry[k] for k in PUBLIC_FIELDS})
        if entry['key_id'] != key_id: raise ValueError('trust key mismatch')
    return trust


def approve(directory, public, fingerprint, group, node, sender):
    # Every scope field and full fingerprint is supplied by the operator, not inferred from the packet.
    check_public(public)
    if (public['key_id'], public['group'], public['node'], public['sender']) != (fingerprint, group, node, sender):
        raise ValueError('operator approval mismatch')
    trust = read_trust(directory)
    old = trust['peers'].get(fingerprint)
    if old and old['revoked']: raise ValueError('revoked key cannot be reauthorized; use a new key')
    for entry in trust['peers'].values():
        if not entry['revoked'] and entry['group'] == group and entry['node'] == node and entry['sender'] != sender:
            raise ValueError('allocation slot already bound to another sender; allocation migration required')
    trust['peers'][fingerprint] = {**public, 'revoked': False}
    write_local(Path(directory) / 'trust.json', trust)
    return {'approved': fingerprint}


def revoke(directory, fingerprint):
    hex_bytes(fingerprint, 32)
    trust = read_trust(directory)
    if fingerprint not in trust['peers']: raise ValueError('unknown pinned key')
    trust['peers'][fingerprint]['revoked'] = True
    write_local(Path(directory) / 'trust.json', trust)
    return {'revoked': fingerprint}


class Security:
    def __init__(self, directory):
        Private, _, _ = crypto()
        self.directory = private_directory(directory)
        read_trust(self.directory)  # Reject unsafe trust storage before loading any key material.
        raw = parse(read_local(self.directory / 'identity.json', private=True))
        if type(raw) is not dict or set(raw) != PUBLIC_FIELDS | {'private_key'}: raise ValueError('invalid private identity')
        self.public = check_public({k: raw[k] for k in PUBLIC_FIELDS})
        self.key = Private.from_private_bytes(hex_bytes(raw['private_key'], 32))
        if self.key.public_key().public_bytes_raw().hex() != self.public['public_key']: raise ValueError('private/public identity mismatch')
        self.check_self()

    def check_self(self):
        private_directory(self.directory)
        windows_private(self.directory / 'identity.json')
        entry = read_trust(self.directory)['peers'].get(self.public['key_id'])
        if not entry or entry['revoked'] or {k: entry[k] for k in PUBLIC_FIELDS} != self.public:
            raise ValueError('local signing key is not authorized')

    def guard(self, c, exchange=None):
        self.check_self()
        cfg = dict(c.execute('SELECT node,group_id FROM _sync_config').fetchone())
        if (cfg['node'], cfg['group_id']) != (self.public['node'], self.public['group']):
            raise ValueError('identity does not match database scope')
        if exchange is not None:
            root = no_symlinks(exchange)
            if self.directory == root or self.directory.is_relative_to(root):
                raise ValueError('identity and trust must be outside exchange')
        # Do not rewrite committed legacy packets or silently abandon unpublished backlog.
        for row in c.execute('SELECT packet FROM _sync_outbox WHERE published=0'):
            p = parse(row[0])
            if p.get('format') != FORMAT: raise ValueError('unsigned outbox backlog: drain before signed activation')
            self.verify(p, self.public['group'], self.public['node'])
            if p['sender'] != self.public['sender']: raise ValueError('outbox sender mismatch')
        c.execute('CREATE TABLE IF NOT EXISTS _sync_security(sender TEXT NOT NULL,group_id TEXT NOT NULL,node TEXT NOT NULL)')
        expected = (self.public['sender'], self.public['group'], self.public['node'])
        existing = c.execute('SELECT sender,group_id,node FROM _sync_security').fetchone()
        if existing is not None and tuple(existing) != expected: raise ValueError('signed database binding mismatch')
        if existing is None: c.execute('INSERT INTO _sync_security VALUES(?,?,?)', expected)
        c.execute('CREATE TABLE IF NOT EXISTS _sync_verification(id INTEGER PRIMARY KEY CHECK(id=1),attempts INTEGER NOT NULL,failed_attempts INTEGER NOT NULL,last_success_at TEXT,last_failure_at TEXT)')

    def sign(self, legacy):
        self.check_self()
        if (legacy['group'], legacy['node']) != (self.public['group'], self.public['node']): raise ValueError('signing scope mismatch')
        p = {**legacy, 'format': FORMAT, 'encoding': ENCODING, 'sender': self.public['sender'], 'key_id': self.public['key_id']}
        p['checksum'] = hashlib.sha256(typed(p['body'])).hexdigest()
        p['signature'] = self.key.sign(DOMAIN + typed(p)).hex()
        wire(p)
        return p

    def verify(self, p, group, folder):
        _, Public, InvalidSignature = crypto()
        if type(p) is not dict or set(p) != FIELDS: raise ValueError('signed packet required; invalid envelope')
        if p['format'] != FORMAT or p['encoding'] != ENCODING: raise ValueError('unsupported signed protocol')
        if not canonical_uuid(p['group']) or p['group'] != group or not canonical_uuid(p['sender']) or not canonical_uuid(p['uuid']):
            raise ValueError('invalid signed scope/UUID')
        if type(p['node']) is not str or p['node'] not in SLOTS or p['node'] != folder: raise ValueError('signed sender/folder mismatch')
        hex_bytes(p['key_id'], 32); hex_bytes(p['checksum'], 32)
        signature = hex_bytes(p['signature'], 64)
        trust = read_trust(self.directory)
        entry = trust['peers'].get(p['key_id'])
        if not entry or entry['revoked']: raise ValueError('unknown or revoked signing key')
        if (entry['group'], entry['node'], entry['sender']) != (p['group'], p['node'], p['sender']):
            raise ValueError('key not authorized for packet scope')
        unsigned = {k: v for k, v in p.items() if k != 'signature'}
        try: Public.from_public_bytes(hex_bytes(entry['public_key'], 32)).verify(signature, DOMAIN + typed(unsigned))
        except InvalidSignature as exc: raise ValueError('invalid packet signature') from exc
        if type(p['body']) is not list or not p['body'] or hashlib.sha256(typed(p['body'])).hexdigest() != p['checksum']:
            raise ValueError('signed body checksum/shape mismatch')
        return p


def receipt_digest(p):
    return hashlib.sha256(DOMAIN + typed({k: v for k, v in p.items() if k != 'signature'})).hexdigest()


def require_policy(c, security=None, exchange=None):
    if security is not None:
        security.guard(c, exchange)
    elif c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone():
        raise ValueError('signed database requires explicit --security-dir; refusing unsigned downgrade')


def record_verification(db, verified):
    from datetime import datetime, timezone
    import memory_sync
    when = datetime.now(timezone.utc).isoformat()
    with memory_sync.connect(db) as c, c:
        c.execute('INSERT OR IGNORE INTO _sync_verification VALUES(1,0,0,NULL,NULL)')
        column = 'last_success_at' if verified else 'last_failure_at'
        c.execute('UPDATE _sync_verification SET attempts=attempts+1,failed_attempts=failed_attempts+?, '+column+'=? WHERE id=1', (int(not verified), when))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--security-dir', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--group', required=True); init.add_argument('--node', choices=SLOTS, required=True)
    init.add_argument('--sender', help='existing canonical node UUID for planned rotation only')
    export = sub.add_parser('export-public'); export.add_argument('--output', required=True)
    sub.add_parser('fingerprint')
    trust = sub.add_parser('trust'); trust.add_argument('public_file')
    for flag in ('fingerprint', 'group', 'node', 'sender'): trust.add_argument('--' + flag, required=True)
    rev = sub.add_parser('revoke'); rev.add_argument('--fingerprint', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init': result = init_identity(args.security_dir, args.group, args.node, args.sender)
        elif args.command == 'trust': result = approve(args.security_dir, parse(read_local(args.public_file)), args.fingerprint, args.group, args.node, args.sender)
        elif args.command == 'revoke': result = revoke(args.security_dir, args.fingerprint)
        else:
            public = Security(args.security_dir).public
            if args.command == 'export-public':
                write_local(args.output, public, exclusive=True); result = {'exported': str(args.output), 'fingerprint': public['key_id']}
            else: result = {'fingerprint': public['key_id'], 'sender': public['sender'], 'node': public['node'], 'group': public['group']}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError):
        # Avoid accidentally logging private file contents or packet body data.
        import sys
        print('Security operation failed; check dependency, scope, permissions and explicit approval.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
