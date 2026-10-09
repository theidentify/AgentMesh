"""Read-only discovery of an existing AgentMesh runtime configuration."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3

from signed_packets import no_symlinks, parse, read_local


def default_runtime():
    if os.name == 'nt':
        base = Path(os.environ['LOCALAPPDATA']) / 'AgentMesh'
    elif __import__('sys').platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support' / 'AgentMesh'
    else:
        base = Path.home() / '.local' / 'share' / 'AgentMesh'
    return base / 'data' / 'runtime.json'


def inspect(runtime_path=None):
    runtime = no_symlinks(runtime_path or default_runtime())
    if not runtime.exists():
        return {'format': 'agentmesh-install-inspect-v1', 'status': 'not_found'}
    if not runtime.is_file():
        raise ValueError('runtime must be a regular file')
    config = parse(read_local(runtime))
    if not isinstance(config, dict) or not all(isinstance(config.get(k), str) for k in ('database', 'exchange', 'node')):
        raise ValueError('invalid runtime configuration')
    if config['node'] not in ('mac', 'windows', 'linux'):
        raise ValueError('invalid node')
    for name in ('database', 'exchange'):
        if not Path(config[name]).is_absolute():
            raise ValueError('runtime paths must be absolute')
    db, exchange = (no_symlinks(config[k]) for k in ('database', 'exchange'))
    if not db.is_file():
        raise ValueError('database missing; no new database was created')
    if not (exchange / '.stfolder').is_dir():
        raise ValueError('exchange folder not accepted')
    if db == exchange or db.is_relative_to(exchange) or runtime.is_relative_to(exchange):
        raise ValueError('database and runtime must be outside exchange')
    # A cold WAL database opened in ordinary read-only mode may create -wal/-shm.
    # Immutable mode avoids that filesystem write when neither sidecar exists.
    # This is informational only: never authorize activation from this snapshot.
    sidecars = (db.with_name(db.name + suffix) for suffix in ('-wal', '-shm'))
    uri = db.as_uri() + ('?mode=ro' if any(path.exists() for path in sidecars) else '?mode=ro&immutable=1')
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        node, group = connection.execute('SELECT node,group_id FROM _sync_config').fetchone()
        strict = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_sync_security'").fetchone()
    if node != config['node']:
        raise ValueError('runtime/database node mismatch')
    identity = config.get('security_dir') or str(runtime.parent.parent / 'identity')
    if not Path(identity).is_absolute():
        raise ValueError('identity path must be absolute')
    identity_path = no_symlinks(identity)
    identity_state = 'present_unverified' if identity_path.is_dir() else 'absent'
    if strict and identity_state == 'absent':
        raise ValueError('strict database missing identity; explicit recovery required')
    return {'format': 'agentmesh-install-inspect-v1', 'status': 'ready',
            'node': node, 'group': group, 'policy': 'required' if strict else 'legacy',
            'identity': identity_state, 'database': str(db), 'exchange': str(exchange)}
