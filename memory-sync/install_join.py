"""Device-bound invitations for unsigned groups created with an empty baseline.

Only the setup-new origin issues reservations. Files are transferred through an
operator-trusted channel, not authenticated by the unsigned packet protocol.
Never copy the origin DB or the recipient's private prepared installation.
"""
import hashlib
import os
from pathlib import Path
import secrets
import stat
import sys
import time
import uuid

import memory_sync
from install_adopt import absolute, security_paths, validate
from install_setup import path_binding, validate_paths
from signed_packets import (canonical_uuid, no_symlinks, parse, private_directory,
                            read_local, windows_private, write_local)

REQUEST = 'agentmesh-join-request-v1'
INVITATION = 'agentmesh-join-invitation-v1'
STATE = 'agentmesh-join-state-v1'
ORIGIN_ERROR = 'empty origin provenance unavailable; use the original bootstrap'
BASELINE_ERROR = 'non-empty baseline group; use the original bootstrap'
SIGNED_ERROR = 'signed groups are not supported by invitations; use the security pairing workflow'
REASONS = frozenset({ORIGIN_ERROR, BASELINE_ERROR, SIGNED_ERROR,
    'slot already used or reserved', 'invitation expired', 'invalid invitation',
    'invalid join request', 'invalid prepared installation', 'invitation already consumed',
    'invitation belongs to another prepared device', 'join is not awaiting replay',
    'join confirmation required', 'use --yes with the matching --confirm word',
    'output already exists; no overwrite permitted', 'transfer files must be outside exchange and installation',
    'transfer file parent must already exist', 'source scope changed during confirmation',
    'join paths changed during confirmation', 'foreign group in exchange',
    'invalid exchange history', 'accepted Syncthing .stfolder directory required',
    'invitation lifetime must be between 60 and 604800 seconds',
    'local database must be owner-only', 'prepared installation scope changed',
})


def confirm(word, *, yes=False, confirm_word=None, message=None):
    if yes:
        if confirm_word != word:
            raise ValueError('use --yes with the matching --confirm word')
        return True
    if confirm_word is not None:
        raise ValueError('use --yes with the matching --confirm word')
    print(f'{message or "Confirm " + word + "."} Type {word}:', file=sys.stderr)
    answer = sys.stdin.readline()
    if not answer:
        raise EOFError('join confirmation required')
    return answer.rstrip('\r\n') == word


def record_origin(db, node, group):
    """Called only by setup-new after its empty baseline has been verified."""
    from install_setup import verify_empty
    verify_empty(db, node, group)
    with memory_sync.connect(db) as c, c:
        c.execute('CREATE TABLE _sync_join_origin(origin TEXT PRIMARY KEY, group_id TEXT NOT NULL, node TEXT NOT NULL)')
        c.execute('INSERT INTO _sync_join_origin VALUES(?,?,?)', (str(uuid.uuid4()), group, node))
        c.execute('''CREATE TABLE _sync_join_reservations(
            node TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
            invitation_id TEXT NOT NULL UNIQUE, invitation TEXT NOT NULL)''')


def sidecar_acl(report):
    """Allow private inheritance for SQLite-created files, never broader grants.

    The caller verifies the protected parent directory. SQLite creates disposable
    WAL/SHM files with inherited ACEs, unlike our explicitly provisioned files.
    Reuse the strict owner/principal/rights validator with only those inheritance
    differences normalized; this does not change the on-disk ACL.
    """
    from windows_acl import validate as validate_acl
    normalized = dict(report, control=report['control'] | 0x1000,
                      rules=[dict(ace, flags=0 if ace['flags'] == 16 else ace['flags'])
                             for ace in report['rules']])
    validate_acl(normalized, directory=False)


def private_file(path, *, sqlite_sidecar=False):
    path = no_symlinks(path)
    if sqlite_sidecar and os.name == 'nt':
        from windows_acl import native_report
        private_directory(path.parent)
        sidecar_acl(native_report(str(path), False))
    else:
        windows_private(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('regular file required')
    if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError('local database must be owner-only')


def transfer_path(value, root, exchange, *, output=False):
    path = absolute(value)
    if any(path == parent or path.is_relative_to(parent) for parent in (root, exchange)):
        raise ValueError('transfer files must be outside exchange and installation')
    if not path.parent.is_dir():
        raise ValueError('transfer file parent must already exist')
    if output and path.exists():
        raise ValueError('output already exists; no overwrite permitted')
    return path


def exchange_path(value):
    path = absolute(value)
    if not no_symlinks(path / '.stfolder').is_dir():
        raise ValueError('accepted Syncthing .stfolder directory required')
    signed = no_symlinks(path / 'signed-changes')
    if signed.exists():
        raise ValueError(SIGNED_ERROR)
    return path


def observed_slots(exchange, group, connection=None):
    """Directories reserve even silent publishers; envelopes must match scope.

This is evidence of occupancy, never evidence of vacancy. The origin's durable
reservations cover invited peers that have not published any packets yet.
"""
    exchange_path(exchange)
    directory = no_symlinks(exchange / 'changes')
    used = set()
    if not directory.exists():
        return used
    if not directory.is_dir():
        raise ValueError('invalid exchange history')
    for slot in directory.iterdir():
        no_symlinks(slot)
        if not slot.is_dir() or slot.name not in memory_sync.RANGES:
            raise ValueError('invalid exchange history')
        used.add(slot.name)
        for path in slot.glob('*.json'):
            p = parse(read_local(path))
            if not isinstance(p, dict) or p.get('group') != group:
                raise ValueError('foreign group in exchange')
            from packet_versions import validate_wire, is_new, _validate_rows
            try:
                validate_wire(p,group=group,folder=slot.name)
                if p['format'] not in (memory_sync.FORMAT,'omp-memory-changes-v2') or path.name!=p['uuid']+'.json':
                    raise ValueError('invalid unsigned history')
                for change in p['body']:
                    parents=change['parents'] if is_new(p) else [change['parent']]
                    if (any(isinstance(parent,str) and parent.startswith('baseline:') for parent in parents)
                            or (is_new(p) and change['origin'] is not None)):
                        raise ValueError(BASELINE_ERROR)
                if connection is not None:
                    if is_new(p): _validate_rows(connection,p)
                    else: memory_sync._validate(connection,p)
            except (ValueError,TypeError,KeyError) as exc:
                if str(exc)==BASELINE_ERROR: raise
                raise ValueError('invalid exchange history') from exc
    return used


def hex_digest(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def request_value(value):
    if (not isinstance(value, dict) or set(value) != {'format', 'id', 'node', 'recipient'}
            or value['format'] != REQUEST or not canonical_uuid(value['id'])
            or type(value['node']) is not str or value['node'] not in memory_sync.RANGES
            or not hex_digest(value['recipient'])):
        raise ValueError('invalid join request')
    return value


def invitation_value(value):
    fields = {'format', 'id', 'group', 'node', 'issuer', 'origin', 'baseline', 'policy',
              'request_id', 'recipient', 'issued_at', 'expires_at'}
    if (not isinstance(value, dict) or set(value) != fields or value['format'] != INVITATION
            or any(not canonical_uuid(value[k]) for k in ('id', 'group', 'origin', 'request_id'))
            or type(value['node']) is not str or type(value['issuer']) is not str
            or value['node'] not in memory_sync.RANGES or value['issuer'] not in memory_sync.RANGES
            or value['node'] == value['issuer'] or not hex_digest(value['recipient'])
            or type(value['issued_at']) is not int or type(value['expires_at']) is not int
            or not 60 <= value['expires_at'] - value['issued_at'] <= 604800
            or value['issued_at'] > int(time.time())):
        raise ValueError('invalid invitation')
    if value['baseline'] != 'empty':
        raise ValueError(BASELINE_ERROR)
    if value['policy'] != 'legacy':
        raise ValueError(SIGNED_ERROR)
    return value


def prepare_join(local_dir, exchange, node, output, *, yes=False, confirm_word=None):
    root, exchange = validate_paths(local_dir, exchange, node)
    exchange_path(exchange)
    output = transfer_path(output, root, exchange, output=True)
    binding = path_binding(root, exchange)
    if not confirm('REQUEST', yes=yes, confirm_word=confirm_word,
                   message=f'Prepare a private request for slot {node}; no worker will start.'):
        return {'status': 'pending'}
    root, exchange = validate_paths(local_dir, exchange, node)
    exchange_path(exchange)
    transfer_path(output, root, exchange, output=True)
    if path_binding(root, exchange) != binding:
        raise ValueError('join paths changed during confirmation')
    secret = secrets.token_hex(32)
    request = {'format': REQUEST, 'id': str(uuid.uuid4()), 'node': node,
               'recipient': hashlib.sha256(bytes.fromhex(secret)).hexdigest()}
    root.mkdir(mode=0o700, exist_ok=False)
    windows_private(root, provision=True)
    private_directory(root)
    # Failures retain a marked, private partial installation for review.
    write_local(root / '.setup-pending.json', {'format': STATE, 'status': 'prepared'}, exclusive=True)
    data = root / 'data'
    data.mkdir(mode=0o700, exist_ok=False)
    windows_private(data, provision=True)
    state = {'format': STATE, 'phase': 'prepared', 'root': str(root), 'exchange': str(exchange),
             'request': request, 'secret': secret}
    write_local(data / 'join-state.json', state, exclusive=True)
    write_local(output, request, exclusive=True)
    return {'status': 'prepared', 'request': str(output), 'node': node}


def source(runtime):
    runtime = absolute(runtime)
    cfg = parse(read_local(runtime, private=True))
    if not isinstance(cfg, dict):
        raise ValueError('invalid runtime')
    db = absolute(cfg['database'])
    private_file(db)
    for suffix in ('-wal', '-shm'):
        sidecar = no_symlinks(str(db) + suffix)
        if sidecar.exists():
            try:
                private_file(sidecar, sqlite_sidecar=True)
            except FileNotFoundError:
                pass  # SQLite may remove a sidecar when another connection closes.
    # Refuse signed policy before attempting to load or provision an identity.
    with memory_sync.connect(db) as c:
        origin = origin_scope(c)
    if any(p.exists() for p in security_paths(cfg, runtime)):
        raise ValueError(SIGNED_ERROR)
    scope = validate(cfg, runtime)
    private_directory(runtime.parent)
    exchange = exchange_path(cfg['exchange'])
    return cfg, db, exchange, origin, scope


def origin_scope(c):
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if '_sync_security' in names:
        raise ValueError(SIGNED_ERROR)
    if not {'_sync_config', '_sync_history'} <= names:
        raise ValueError(ORIGIN_ERROR)
    cfg = memory_sync.config(c)
    if c.execute("SELECT 1 FROM _sync_history WHERE revision LIKE 'baseline:%' LIMIT 1").fetchone():
        raise ValueError(BASELINE_ERROR)
    if not {'_sync_join_origin', '_sync_join_reservations'} <= names:
        raise ValueError(ORIGIN_ERROR)
    origins = c.execute('SELECT origin,group_id,node FROM _sync_join_origin').fetchall()
    if (len(origins) != 1 or not canonical_uuid(origins[0][0])
            or tuple(origins[0])[1:] != (cfg['group_id'], cfg['node'])):
        raise ValueError(ORIGIN_ERROR)
    return tuple(origins[0])


def invite_peer(runtime, request, output, *, expires_in=86400, yes=False, confirm_word=None):
    if type(expires_in) is not int or not 60 <= expires_in <= 604800:
        raise ValueError('invitation lifetime must be between 60 and 604800 seconds')
    initial = source(runtime)
    cfg, db, exchange, origin, scope = initial
    root = absolute(cfg.get('app_root', Path(runtime).parent.parent))
    request_path = transfer_path(request, root, exchange)
    value = request_value(parse(read_local(request_path, private=True)))
    output = transfer_path(output, root, exchange, output=True)
    with memory_sync.connect(db) as c:
        check_slot(c, exchange, value['node'])
    if not confirm('INVITE', yes=yes, confirm_word=confirm_word,
                   message=f'Reserve slot {value["node"]} permanently in group {scope["group"]}. '
                           'Loss or expiry will not release it.'):
        return {'status': 'pending'}
    if source(runtime) != initial or request_value(parse(read_local(request_path, private=True))) != value:
        raise ValueError('source scope changed during confirmation')
    transfer_path(output, root, exchange, output=True)
    # BEGIN IMMEDIATE serializes issuers, including separate CLI processes.
    with memory_sync.connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        if origin_scope(c) != origin:
            raise ValueError('source scope changed during confirmation')
        check_slot(c, exchange, value['node'])
        now = int(time.time())
        invitation = {'format': INVITATION, 'id': str(uuid.uuid4()), 'group': scope['group'],
                      'node': value['node'], 'issuer': scope['node'], 'origin': origin[0],
                      'baseline': 'empty', 'policy': 'legacy', 'request_id': value['id'],
                      'recipient': value['recipient'], 'issued_at': now, 'expires_at': now + expires_in}
        c.execute('INSERT INTO _sync_join_reservations VALUES(?,?,?,?)',
                  (value['node'], value['id'], invitation['id'], memory_sync.canonical(invitation)))
    # Commit the reservation BEFORE exposing its invitation. Failed publication
    # burns the slot rather than allowing a possibly delivered invitation twice.
    write_local(output, invitation, exclusive=True)
    return {'status': 'issued', 'invitation': str(output), 'group': scope['group'],
            'node': value['node'], 'expires_at': invitation['expires_at']}


def check_slot(c, exchange, node):
    cfg = memory_sync.config(c)
    used = observed_slots(exchange, cfg['group_id'], c) | {cfg['node']}
    used.update(r[0] for r in c.execute('SELECT node FROM _sync_join_reservations'))
    if node in used:
        raise ValueError('slot already used or reserved')


def prepared(local_dir, exchange):
    root, exchange = absolute(local_dir), exchange_path(exchange)
    if root == exchange or root.is_relative_to(exchange) or exchange.is_relative_to(root):
        raise ValueError('local root must be separate from exchange')
    private_directory(root)
    private_directory(root / 'data')
    state = parse(read_local(root / 'data/join-state.json', private=True))
    if (not isinstance(state, dict) or state.get('format') != STATE
            or state.get('root') != str(root) or state.get('exchange') != str(exchange)
            or state.get('phase') not in ('prepared', 'replaying', 'complete')):
        raise ValueError('invalid prepared installation')
    request = request_value(state.get('request'))
    if (not hex_digest(state.get('secret')) or
            hashlib.sha256(bytes.fromhex(state['secret'])).hexdigest() != request['recipient']):
        raise ValueError('invalid prepared installation')
    if state['phase'] != 'complete':
        read_local(root / '.setup-pending.json', private=True)
    return root, exchange, state


def setup_join(local_dir, exchange, invitation=None, *, resume=False, yes=False, confirm_word=None):
    from runtime_lock import lock
    root, exchange, initial = prepared(local_dir, exchange)
    if resume:
        finalizing = initial['phase'] == 'complete' and (root / '.setup-pending.json').exists()
        if invitation is not None or (initial['phase'] != 'replaying' and not finalizing):
            raise ValueError('join is not awaiting replay')
        payload = invitation_value(initial['invitation'])
    else:
        if initial['phase'] != 'prepared':
            raise ValueError('invitation already consumed')
        path = transfer_path(invitation, root, exchange)
        payload = invitation_value(parse(read_local(path, private=True)))
        if int(time.time()) >= payload['expires_at']:
            raise ValueError('invitation expired')
        request = initial['request']
        if (payload['request_id'], payload['recipient'], payload['node']) != (request['id'], request['recipient'], request['node']):
            raise ValueError('invitation belongs to another prepared device')
    if payload['node'] in observed_slots(exchange, payload['group']):
        raise ValueError('slot already used or reserved')
    if not confirm('RESUME' if resume else 'JOIN', yes=yes, confirm_word=confirm_word,
                   message=f'Join group {payload["group"]} as {payload["node"]}, issued by '
                           f'{payload["issuer"]}. Accept only invitations from a trusted source.'):
        return {'status': 'pending'}
    # Provision the lock through the private-file helper before opening it:
    # runtime_lock creates POSIX-private files, but cannot provision Windows ACLs.
    lock_path = root / 'data/join.lock'
    if not no_symlinks(lock_path).exists():
        try:
            write_local(lock_path, 0, exclusive=True)
        except FileExistsError:
            pass  # Another local consumer won; verify its file before opening.
    private_file(lock_path)
    # The lock and state commit make repeated/concurrent local consumption fail.
    with lock(lock_path):
        if prepared(root, exchange)[2] != initial:
            raise ValueError('invitation already consumed')
        if not resume:
            if invitation_value(parse(read_local(path, private=True))) != payload:
                raise ValueError('prepared installation scope changed')
            if int(time.time()) >= payload['expires_at']:
                raise ValueError('invitation expired')
        if payload['node'] in observed_slots(exchange, payload['group']):
            raise ValueError('slot already used or reserved')
        state = dict(initial, phase='replaying', invitation=payload)
        write_local(root / 'data/join-state.json', state)
        return replay(root, exchange, state)


def replay(root, exchange, state):
    import sqlite_memory
    payload = state['invitation']
    data, db = root / 'data', root / 'data/memory.db'
    if not db.exists():
        # Resume an interrupted schema initialization under the join lock. The
        # final name is published only after all SQLite handles have closed.
        staging = no_symlinks(data / 'join-install.db')
        if not staging.exists():
            fd = os.open(staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            windows_private(staging, provision=True)
        private_file(staging)
        sqlite_memory.init_database(staging)
        memory_sync.initialize(staging, payload['node'], payload['group'])
        os.link(staging, db)  # Exclusive publication, never overwrite another DB.
        staging.unlink()
    private_file(db)
    with memory_sync.connect(db) as c:
        cfg = memory_sync.config(c)
        if cfg != {'node': payload['node'], 'group_id': payload['group']}:
            raise ValueError('prepared installation scope changed')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone():
            raise ValueError(SIGNED_ERROR)
        observed_slots(exchange, payload['group'], c)
    # receive captures only local DB state and reads exchange; never publish or
    # run a worker here. A failed replay cannot activate the partial installation.
    report = memory_sync.receive(db, exchange)
    result = {'status': 'pending', 'receive': report, 'database': str(db)}
    if report['conflict'] or report['invalid']:
        result.update(status='blocked', failure_kind='permanent')
        return result
    if report['pending'] or report['unavailable']:
        result['failure_kind'] = 'transient' if report['unavailable'] else 'pending-history'
        return result
    # If a signed namespace appeared during replay, retain the pending guard.
    exchange_path(exchange)
    workflow = data / 'workflow.json'
    if not workflow.exists():
        write_local(workflow, {'ingest': False, 'summarize': False}, exclusive=True)
    elif parse(read_local(workflow, private=True)) != {'ingest': False, 'summarize': False}:
        raise ValueError('prepared installation scope changed')
    runtime = data / 'runtime.json'
    expected = {'database': str(db), 'exchange': str(exchange), 'node': payload['node'],
                'security_dir': str(root / 'identity'), 'security_state': str(data / 'security-wizard.json')}
    if runtime.exists():
        if parse(read_local(runtime, private=True)) != expected:
            raise ValueError('prepared installation scope changed')
    else:
        write_local(runtime, expected, exclusive=True)
    # Remove the pending guard last: all required state is durable first.
    write_local(data / 'join-state.json', dict(state, phase='complete'))
    no_symlinks(root / '.setup-pending.json').unlink()
    return dict(result, status='joined', runtime=str(runtime), failure_kind=None)
