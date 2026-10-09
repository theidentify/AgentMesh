"""Explicit first-run creation of an empty, isolated sync group; no activation."""
import hashlib
import os
from pathlib import Path
import sys
import uuid

from signed_packets import no_symlinks, private_directory, read_local, windows_private, write_local


def validate_paths(local_dir, exchange, node):
    if node not in ('mac', 'windows', 'linux'):
        raise ValueError('node must be mac, windows or linux')
    root, exchange = Path(local_dir), Path(exchange)
    if any(not path.is_absolute() or '..' in path.parts for path in (root, exchange)):
        raise ValueError('absolute paths without parent traversal required')
    root, exchange = no_symlinks(root), no_symlinks(exchange)
    marker = no_symlinks(exchange / '.stfolder')
    if not exchange.is_dir() or not marker.is_dir():
        raise ValueError('accepted Syncthing .stfolder directory required')
    if root == exchange or root.is_relative_to(exchange) or exchange.is_relative_to(root):
        raise ValueError('local root must be separate from exchange')
    if root.exists():
        raise ValueError('local root already exists; use existing-install wizard commands')
    if not root.parent.is_dir():
        raise ValueError('local root parent must already exist')
    return root, exchange


def path_binding(root, exchange):
    paths = {root.parent, *root.parents, exchange, *exchange.parents, exchange / '.stfolder'}
    return {str(path): (path.stat().st_dev, path.stat().st_ino) for path in paths}


def fingerprint(path):
    """Identity plus completed file bytes; never adopt unknown sidecars or files."""
    path = no_symlinks(path)
    info = path.stat()
    digest = None
    if path.is_file():
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return info.st_dev, info.st_ino, info.st_uid, info.st_mode, digest


def rollback(root, owned):
    """Conservative ordinary-failure cleanup, not hostile-writer/crash atomicity."""
    try:
        no_symlinks(root)
        if {root, *root.rglob('*')} != set(owned):
            return
        if any(fingerprint(path) != value for path, value in owned.items()):
            return
        # Reverse creation order leaves the pending marker until data is gone.
        for path in reversed(owned):
            if fingerprint(path) != owned[path]:
                return
            if path.is_dir():
                path.rmdir()  # Never recursively delete an unexpected entry.
            else:
                path.unlink()
    except (OSError, ValueError):
        # Uncertain ownership or cleanup failure: retain partial state for review.
        return


def verify_empty(db, node, group):
    import memory_sync
    with memory_sync.connect(db) as connection:
        config = [tuple(row) for row in connection.execute('SELECT node,group_id,importing FROM _sync_config')]
        if config != [(node, group, 0)]:
            raise ValueError('new database scope mismatch')
        tables = memory_sync.TABLES + ('_sync_history', '_sync_shadow', '_sync_outbox', '_sync_receipts', '_sync_diagnostics', 'events_fts')
        if any(connection.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone() for table in tables):
            raise ValueError('new database must be empty')
        expected = {table: memory_sync.RANGES[node][0] for table in memory_sync.TABLES if memory_sync.KEYS[table] == ('id',)}
        if dict(connection.execute('SELECT table_name,next_id FROM _sync_counters')) != expected:
            raise ValueError('new allocation ranges mismatch')


def reject_partial(runtime_path=None):
    """Packaged existing-install commands must not bypass first-run completion."""
    from install_inspect import default_runtime
    runtime = no_symlinks(runtime_path or default_runtime())
    marker = no_symlinks(runtime.parent.parent / '.setup-pending.json')
    if marker.exists():
        raise ValueError('partial first-run installation requires manual review')


def setup_new(local_dir, exchange, node):
    import memory_sync
    import sqlite_memory
    from install_inspect import inspect, wizard_status

    root, exchange = validate_paths(local_dir, exchange, node)
    binding = path_binding(root, exchange)
    print('Create a NEW EMPTY installation and isolated group (not a baseline join)? Type NEW:', file=sys.stderr)
    answer = sys.stdin.readline()
    if not answer:
        raise EOFError('confirmation required')
    if answer.rstrip('\r\n') != 'NEW':
        return {'format': 'agentmesh-setup-new-v1', 'status': 'pending'}
    root, exchange = validate_paths(local_dir, exchange, node)
    if path_binding(root, exchange) != binding:
        raise ValueError('setup paths changed during confirmation')
    # A losing installer must not enter the cleanup path or adopt another root.
    root.mkdir(mode=0o700, exist_ok=False)
    owned = {root: fingerprint(root)}
    try:
        windows_private(root, provision=True)
        private_directory(root)
        pending = root / '.setup-pending.json'
        write_local(pending, {'format': 'agentmesh-setup-partial-v1', 'status': 'partial'}, exclusive=True)
        owned[pending] = fingerprint(pending)
        data = root / 'data'
        data.mkdir(mode=0o700, exist_ok=False)
        owned[data] = fingerprint(data)
        windows_private(data, provision=True)
        private_directory(data)
        db = data / 'memory.db'
        fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        owned[db] = fingerprint(db)
        windows_private(db, provision=True)
        sqlite_memory.init_database(db)
        group = str(uuid.uuid4())
        memory_sync.initialize(db, node, group)
        verify_empty(db, node, group)
        # All SQLite handles are closed; incomplete initialization stays partial.
        owned[db] = fingerprint(db)
        workflow = data / 'workflow.json'
        write_local(workflow, {'ingest': False, 'summarize': False}, exclusive=True)
        owned[workflow] = fingerprint(workflow)
        runtime = data / 'runtime.json'
        write_local(runtime, {'database': str(db), 'exchange': str(exchange), 'node': node,
                              'security_dir': str(root / 'identity'),
                              'security_state': str(data / 'security-wizard.json')}, exclusive=True)
        owned[runtime] = fingerprint(runtime)
        for file in (db, workflow, runtime, pending):
            read_local(file, private=True)
        # Keep a real read/write handle until read-only wizard inspection closes.
        # SQLite then cleans its own empty WAL/SHM on the last writable close;
        # never adopt arbitrary sidecars into our rollback ownership manifest.
        with memory_sync.connect(db):
            inspect(runtime)
            wizard_status(runtime)
        no_symlinks(root)
        no_symlinks(exchange / '.stfolder')
        if path_binding(root, exchange) != binding:
            raise ValueError('setup scope changed before completion')
        if {root, *root.rglob('*')} != set(owned) or any(fingerprint(path) != value for path, value in owned.items()):
            raise ValueError('installation contents changed before completion')
        pending.unlink()
        return {'format': 'agentmesh-setup-new-v1', 'status': 'created',
                'group': group, 'node': node, 'runtime': str(runtime)}
    except Exception:
        rollback(root, owned)
        raise
