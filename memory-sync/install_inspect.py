"""Existing runtime discovery and bounded security-wizard entry points."""
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


def resolve_wizard(runtime_path=None):
    """Resolve existing installation paths without inventing a new installation."""
    runtime = no_symlinks(runtime_path or default_runtime())
    installation = inspect(runtime)
    if installation['status'] != 'ready':
        raise ValueError('existing installation required')
    config = parse(read_local(runtime))
    if config['database'] != installation['database'] or config['exchange'] != installation['exchange']:
        raise ValueError('runtime configuration changed during inspection')
    local = runtime.parent.parent
    identity = config.get('security_dir', str(local / 'identity'))
    state = config.get('security_state', str(runtime.parent / 'security-wizard.json'))
    if not isinstance(identity, str) or not isinstance(state, str):
        raise ValueError('invalid security paths')
    if not Path(identity).is_absolute() or not Path(state).is_absolute():
        raise ValueError('security paths must be absolute')
    identity, state = no_symlinks(identity), no_symlinks(state)
    exchange = Path(installation['exchange'])
    if any(path == exchange or path.is_relative_to(exchange) for path in (identity, state)):
        raise ValueError('security state must remain outside exchange')
    return {'database': installation['database'], 'exchange': str(exchange),
            'security_dir': str(identity), 'state': str(state)}


def wizard_status(runtime_path=None):
    """Project status without entering setup."""
    from security_wizard import resume
    paths = resolve_wizard(runtime_path)
    return resume(paths['database'], paths['exchange'], paths['security_dir'], paths['state'], dry_run=True)


def wizard_resume(runtime_path=None):
    """Interactive existing-install setup; no bootstrap, dependency or worker path."""
    from argparse import Namespace
    from contextlib import redirect_stdout
    import sys
    from security_wizard import interactive, load_state
    from signed_packets import Security
    runtime = no_symlinks(runtime_path or default_runtime())
    config_bytes = read_local(runtime)
    paths = resolve_wizard(runtime)
    if read_local(runtime) != config_bytes:
        raise ValueError('runtime configuration changed during resolution')
    baseline = None
    fingerprint = None

    def validate_scope():
        nonlocal baseline, fingerprint
        if read_local(runtime) != config_bytes or resolve_wizard(runtime) != paths:
            raise ValueError('runtime configuration changed; resume again after review')
        # Current SQLite state authorizes mutation, never immutable inspection.
        with closing(sqlite3.connect(Path(paths['database']).as_uri() + '?mode=ro', uri=True)) as connection:
            node, group = connection.execute('SELECT node,group_id FROM _sync_config').fetchone()
            strict = connection.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone()
            binding = connection.execute('SELECT sender,group_id,node FROM _sync_security').fetchone() if strict else None
        scope = node, group
        if baseline is not None and scope != baseline:
            raise ValueError('database scope changed; explicit recovery required')
        baseline = scope
        state = load_state(paths['state'])
        bindings = {'database': paths['database'], 'exchange': paths['exchange'],
                    'security_dir': paths['security_dir'], 'node': node, 'group': group}
        if state and any(state.get(k) != v for k, v in bindings.items()):
            raise ValueError('wizard scope changed; explicit recovery required')
        if strict or Path(paths['security_dir']).exists() or (state and state.get('sender')) or fingerprint:
            if not Path(paths['security_dir']).is_dir():
                raise ValueError('persistent identity missing; explicit recovery required')
            security = Security(paths['security_dir'])
            if (security.public['node'], security.public['group']) != scope:
                raise ValueError('identity/database mismatch')
            if strict and binding != (security.public['sender'], group, node):
                raise ValueError('strict identity mismatch; explicit recovery required')
            if state and state.get('sender') and state['sender'] != security.public['sender']:
                raise ValueError('persistent identity changed; explicit recovery required')
            if fingerprint is not None and fingerprint != security.public['key_id']:
                raise ValueError('identity changed while resuming')
            fingerprint = security.public['key_id']

    validate_scope()
    with redirect_stdout(sys.stderr):
        return interactive(Namespace(**paths, confirm_quarantine_receipts=False), validate_scope=validate_scope)
