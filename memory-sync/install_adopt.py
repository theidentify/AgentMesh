"""Bind an existing installation without changing its database or signing policy."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import sys

from signed_packets import canonical_uuid, no_symlinks, parse, read_local, write_local


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute paths without traversal required')
    return no_symlinks(path)


def validate(config, runtime, *, authoritative=True):
    """Read scope; never call Security.guard (which activates a legacy DB)."""
    from install_setup import reject_partial
    runtime = absolute(runtime)
    reject_partial(runtime)
    db, exchange = (absolute(config[k]) for k in ('database', 'exchange'))
    root = absolute(config.get('app_root', runtime.parent.parent))
    if absolute(root / '.setup-pending.json').exists():
        raise ValueError('partial first-run installation requires manual review')
    if not root.is_dir() or not runtime.parent.is_dir() or not runtime.is_relative_to(root):
        raise ValueError('existing app root and runtime parent required')
    if os.name != 'nt':
        for path in (root, runtime.parent, db):
            info = path.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ValueError('user-owned state, not writable by others, required')
    paths = [runtime, db, root]
    for key in ('security_dir', 'security_state', 'workflow_config'):
        if config.get(key) is not None:
            paths.append(absolute(config[key]))
    if any(p == exchange or p.is_relative_to(exchange) for p in paths) or exchange.is_relative_to(root):
        raise ValueError('local state must remain separate from exchange')
    if not db.is_file() or not no_symlinks(exchange / '.stfolder').is_dir():
        raise ValueError('existing database and accepted exchange required')
    if config.get('workflow_config') and not absolute(config['workflow_config']).is_file():
        raise ValueError('existing workflow configuration required')
    # Informational planning/status must not create a missing WAL index. This
    # snapshot intentionally excludes uncheckpointed WAL; only authoritative
    # checks below the operator/cycle gate may authorize any mutation.
    uri = db.as_uri() + ('?mode=ro' if authoritative else '?mode=ro&immutable=1')
    with closing(sqlite3.connect(uri, uri=True)) as c:
        c.execute('PRAGMA query_only=ON')
        rows = c.execute('SELECT node,group_id FROM _sync_config').fetchall()
        if len(rows) != 1:
            raise ValueError('invalid database scope')
        node, group = rows[0]
        if node not in ('mac', 'windows', 'linux') or not canonical_uuid(group):
            raise ValueError('invalid database scope')
        strict = bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security' AND type='table'").fetchone())
        binding = c.execute('SELECT sender,group_id,node FROM _sync_security').fetchall() if strict else []
    policy = 'required' if strict else 'legacy'
    if node != config['node'] or (config.get('group') is not None and group != config['group']):
        raise ValueError('database scope mismatch')
    if config.get('policy') is not None and policy != config['policy']:
        raise ValueError('database policy changed; explicit review required')
    fingerprint = None
    if strict:
        from signed_packets import Security
        security = Security(absolute(config.get('security_dir', root / 'identity')))
        if binding != [(security.public['sender'], group, node)] or (security.public['node'], security.public['group']) != (node, group):
            raise ValueError('strict identity binding mismatch')
        fingerprint = security.public['key_id']
        if config.get('key_id') is not None and config['key_id'] != fingerprint:
            raise ValueError('persistent signing key changed')
    return {'node': node, 'group': group, 'policy': policy, 'key_id': fingerprint}


def load(runtime, *, authoritative=True):
    runtime = absolute(runtime)
    config = parse(read_local(runtime, private=True))
    if not isinstance(config, dict):
        raise ValueError('invalid runtime')
    return config, validate(config, runtime, authoritative=authoritative)


def adopt(*, runtime, app_root, database, exchange, node, security_dir, security_state, workflow_config):
    runtime = absolute(runtime)
    if runtime.exists():
        raise ValueError('runtime already exists; no overwrite permitted')
    config = {k: str(absolute(v)) for k, v in dict(app_root=app_root, database=database,
              exchange=exchange, security_dir=security_dir, security_state=security_state,
              workflow_config=workflow_config).items()}
    config['node'] = node
    baseline = validate(config, runtime, authoritative=False)
    watched = [absolute(config[k]) for k in ('app_root', 'database', 'exchange', 'workflow_config')]
    watched += [runtime.parent, absolute(exchange) / '.stfolder']
    binding = [(p.stat().st_dev, p.stat().st_ino) for p in watched]
    print('Bind existing paths only (no activation or worker start). Type BIND:', file=sys.stderr)
    answer = sys.stdin.readline()
    if not answer:
        raise EOFError('BIND confirmation required')
    if answer.rstrip('\r\n') != 'BIND':
        return {'status': 'pending'}
    if [(no_symlinks(p).stat().st_dev, p.stat().st_ino) for p in watched] != binding:
        raise ValueError('paths changed during confirmation')
    if validate(config, runtime) != baseline:
        raise ValueError('scope changed during confirmation')
    config.update(baseline)
    write_local(runtime, config, exclusive=True)
    if parse(read_local(runtime, private=True)) != config:
        raise ValueError('runtime verification failed')
    return {'status': 'bound', 'runtime': str(runtime), 'policy': baseline['policy']}
