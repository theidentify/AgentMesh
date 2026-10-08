"""Opt-in signed worker releases. The installed consumer is a fixed trust base."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
import zipfile

from runtime_lock import lock
from signed_packets import (crypto, hex_bytes, no_symlinks, parse, read_local,
                            typed, wire, write_local)

DOMAIN = b'AgentMesh/worker-release/Ed25519/v1\x00'
PRODUCT = 'agentmesh-worker'
LIMIT = 32 * 1024 * 1024
TOTAL = 64 * 1024 * 1024
# Frozen bootstrap contract: no release-selected commands or dynamic paths.
from build_package import FILES
ALLOWED = frozenset(FILES) | {'runtime_lock.py', 'src/omp_memory/__init__.py',
    'src/omp_memory/parser.py', 'src/omp_memory/claude_parser.py', 'src/omp_memory/codex_parser.py'}
REQUIRED = frozenset(ALLOWED)
FIELDS = {'format', 'product', 'release_id', 'key_id', 'version', 'archive_sha256', 'files', 'signature'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_binary(path, limit):
    path = no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('regular release file required')
        data = stream.read(limit + 1)
    if len(data) > limit: raise ValueError('release file too large')
    return data


def read_json(path):
    return parse(read_local(path, private=True))


def sync_tree(root):
    for path in root.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
    if os.name != 'nt':
        for directory in [p for p in root.rglob('*') if p.is_dir()] + [root, root.parent]:
            fd = os.open(directory, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)


def release_key(directory):
    """Offline publisher role, deliberately unrelated to peer packet identity."""
    Private, _, _ = crypto()
    directory = no_symlinks(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    key = Private.generate()
    public = key.public_key().public_bytes_raw()
    identity = {'product': PRODUCT, 'release_id': str(uuid.uuid4()),
                'key_id': digest(public), 'public_key': public.hex()}
    write_local(directory / 'release-private.json', {**identity, 'private_key': key.private_bytes_raw().hex()}, exclusive=True)
    write_local(directory / 'release-public.json', identity, exclusive=True)
    return identity


def provision_trust(local, public, fingerprint, release_id):
    if (set(public) != {'product', 'release_id', 'key_id', 'public_key'} or
        public['product'] != PRODUCT or public['release_id'] != release_id or
        str(uuid.UUID(release_id)) != release_id or public['key_id'] != fingerprint or
        digest(hex_bytes(public['public_key'], 32)) != fingerprint):
        raise ValueError('explicit release identity approval mismatch')
    local = no_symlinks(local)
    if not (local / 'ota-state.json').exists():
        raise ValueError('install consumer before approving release authority')
    write_local(local / 'release-trust.json', public, exclusive=True)


def build_release(source, output, private, version):
    if type(version) is not int or not 1 <= version < 2**63:
        raise ValueError('positive integer version required')
    source, output = no_symlinks(source), no_symlinks(output)
    output.mkdir(parents=True, exist_ok=False)
    identity = read_json(private)
    Private, _, _ = crypto()
    key = Private.from_private_bytes(hex_bytes(identity['private_key'], 32))
    if digest(key.public_key().public_bytes_raw()) != identity['key_id']:
        raise ValueError('release key mismatch')
    files = {}
    archive = output / 'worker.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name in sorted(REQUIRED):
            data = read_local(no_symlinks(source / name))
            files[name] = digest(data)
            package.writestr(name, data)
    if archive.stat().st_size > LIMIT:
        raise ValueError('archive too large')
    manifest = {'format': 'agentmesh-worker-release-v1', 'product': PRODUCT,
                'release_id': identity['release_id'], 'key_id': identity['key_id'],
                'version': version, 'archive_sha256': digest(archive.read_bytes()), 'files': files}
    manifest['signature'] = key.sign(DOMAIN + typed(manifest)).hex()
    write_local(output / 'release.json', manifest, exclusive=True)
    return manifest


def verify(local, manifest, archive):
    trust = read_json(local / 'release-trust.json')
    if (type(manifest) is not dict or set(manifest) != FIELDS or
        manifest['format'] != 'agentmesh-worker-release-v1' or manifest['product'] != PRODUCT or
        (manifest['product'], manifest['release_id'], manifest['key_id']) !=
        (trust['product'], trust['release_id'], trust['key_id']) or
        digest(hex_bytes(trust['public_key'], 32)) != trust['key_id'] or
        type(manifest['version']) is not int or not 1 <= manifest['version'] < 2**63 or
        type(manifest['files']) is not dict or set(manifest['files']) != REQUIRED):
        raise ValueError('invalid release contract or authority')
    _, Public, _ = crypto()
    Public.from_public_bytes(hex_bytes(trust['public_key'], 32)).verify(
        hex_bytes(manifest['signature'], 64), DOMAIN + typed({k: v for k, v in manifest.items() if k != 'signature'}))
    if digest(archive) != manifest['archive_sha256']:
        raise ValueError('archive authentication failed')
    return manifest


def stage(local, manifest, archive):
    import io
    destination = no_symlinks(local / 'versions' / str(manifest['version']))
    if destination.exists():
        # A version is immutable. Recovery cannot reuse a different payload.
        for name, expected in manifest['files'].items():
            if digest(read_local(destination / name)) != expected:
                raise ValueError('staged version differs')
        return destination
    temp = local / 'versions' / ('.stage-' + str(uuid.uuid4()))
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        members = package.infolist()
        if (len(members) != len(REQUIRED) or {m.filename for m in members} != REQUIRED or
            sum(m.file_size for m in members) > TOTAL):
            raise ValueError('unsafe archive contents')
        for m in members:
            mode = m.external_attr >> 16
            if (m.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or
                m.flag_bits & 1 or m.file_size > 8 * 1024 * 1024 or
                m.file_size > max(1, m.compress_size) * 1000):
                raise ValueError('unsafe archive entry')
        temp.mkdir(mode=0o700)
        try:
            for m in members:
                data = package.read(m)
                if digest(data) != manifest['files'][m.filename]:
                    raise ValueError('release file mismatch')
                target = temp / m.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            sync_tree(temp)
            os.replace(temp, destination)
            sync_tree(destination)
        finally:
            if temp.exists(): shutil.rmtree(temp)
    return destination


def health(app, database):
    app, database = no_symlinks(app), no_symlinks(database)
    for name in REQUIRED: no_symlinks(app / name)
    # No release-provided health command. No DB migrations or writes during health.
    command = ('import sys,sqlite3; from contextlib import closing; '
               'import sync_worker,memory_sync,sqlite_memory,workflow; '
               'from pathlib import Path; '
               'c=sqlite3.connect(Path(sys.argv[1]).as_uri()+"?mode=ro",uri=True); '
               'c.execute("PRAGMA query_only=ON"); '
               'assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; '
               'assert not c.execute("PRAGMA foreign_key_check").fetchall(); '
               'assert c.execute("SELECT node,group_id FROM _sync_config").fetchone(); c.close()')
    result = subprocess.run([sys.executable, '-I', '-c',
        'import sys; sys.path.insert(0,sys.argv.pop(1)); ' + command, str(app), str(database)],
        capture_output=True, timeout=60)
    return result.returncode == 0


def snapshot(local, version):
    destination = no_symlinks(local / 'backups' / ('before-' + str(version) + '-' + str(uuid.uuid4())))
    destination.mkdir(parents=True, mode=0o700)
    # Classify by header before filename: a valid DB can itself end in
    # -wal/-shm. Sidecars are only skipped when attached to an identified DB.
    files = []
    databases = set()
    for source in (local / 'data', local / 'identity'):
        if not source.exists(): continue
        for path in source.rglob('*'):
            no_symlinks(path)
            if not path.is_file(): continue
            with path.open('rb') as stream:
                is_sqlite = stream.read(16) == b'SQLite format 3\x00'
            files.append((path, is_sqlite))
            if is_sqlite: databases.add(path)
    required = Path(runtime_at(local)['database'])
    if required not in databases:
        raise ValueError('configured database was not identified for mandatory backup')
    for path, is_sqlite in files:
        if not is_sqlite:
            if path.name in ('worker.lock', 'supervisor.lock'): continue
            if path.name.endswith(('-wal', '-shm')) and path.with_name(path.name[:-4]) in databases:
                continue
        target = destination / path.relative_to(local)
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_sqlite:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('backup integrity failure')
        else:
            shutil.copyfile(path, target)
        os.chmod(target, 0o600)
    mandatory = destination / required.relative_to(local)
    if not mandatory.is_file(): raise ValueError('mandatory database backup missing')
    with closing(sqlite3.connect(mandatory.as_uri() + '?mode=ro', uri=True)) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('mandatory database backup integrity failure')
    for name in ('release-trust.json', 'ota-state.json'):
        shutil.copyfile(local / name, destination / name)
        os.chmod(destination / name, 0o600)
    sync_tree(destination)
    return destination


def state_at(local):
    state = read_json(local / 'ota-state.json')
    if (set(state) != {'format', 'active', 'highest', 'pending', 'previous'} or
        state['format'] != 'agentmesh-ota-state-v1' or
        any(type(state[k]) is not int or not 0 <= state[k] < 2**63 for k in ('active', 'highest')) or
        state['active'] > state['highest'] or
        any(state[k] is not None and (type(state[k]) is not int or not 0 <= state[k] <= state['highest']) for k in ('pending', 'previous'))):
        raise ValueError('invalid durable updater state')
    return state


def recover(local, database):
    state = state_at(local)
    if state['pending'] is not None:
        # Any interruption during activation rolls back code, never user data.
        state['active'] = state['previous']
        state['pending'] = state['previous'] = None
        write_local(local / 'ota-state.json', state)
    if not health(local / 'versions' / str(state['active']), database):
        raise ValueError('active worker unhealthy; no worker started')
    return state


def _apply(local, release_dir, database):
    local, release_dir, database = map(no_symlinks, (local, release_dir, database))
    runtime = runtime_at(local)
    if database != Path(runtime['database']):
        raise ValueError('database outside configured installation')
    with lock(local / 'data' / 'worker.lock'):
        state = recover(local, database)
        # Untrusted transport is bounded and read once; signature precedes extraction.
        manifest = parse(read_local(release_dir / 'release.json'))
        archive = read_binary(release_dir / 'worker.zip', LIMIT)
        verify(local, manifest, archive)
        version = manifest['version']
        if version <= state['highest']: raise ValueError('replay or downgrade refused')
        # Authentication succeeded: burn the attempt before *any* staging or
        # mandatory backup can fail, with active code unchanged.
        state['highest'] = version
        write_local(local / 'ota-state.json', state)
        app = stage(local, manifest, archive)
        backup = snapshot(local, version)
        # Activation intent is separate from the durable anti-replay high water.
        state.update(pending=version, previous=state['active'])
        write_local(local / 'ota-state.json', state)
        state['active'] = version
        write_local(local / 'ota-state.json', state)  # Single atomic active pointer.
        healthy = health(app, database)
        # Keep pending durable until the new worker completes a real cycle.
    success = healthy and worker_cycle(local, runtime, version) == 0
    with lock(local / 'data' / 'worker.lock'):
        state = state_at(local)
        if not success:
            state['active'] = state['previous']
        state['pending'] = state['previous'] = None
        write_local(local / 'ota-state.json', state)
        if not health(local / 'versions' / str(state['active']), database):
            raise ValueError('worker recovery unhealthy; remains stopped')
    if not success and worker_cycle(local, runtime, state['active']) != 0:
        raise ValueError('rollback worker cycle failed; explicit operator recovery required')
    return {'status': 'activated' if success else 'rolled_back', 'version': version, 'backup': str(backup)}


def apply(local, release_dir, database):
    local = no_symlinks(local)
    with lock(local / 'data' / 'supervisor.lock', timeout=0):
        return _apply(local, release_dir, database)


def worker_cycle(local, runtime, version):
    app = local / 'versions' / str(version)
    args = [sys.executable, str(app / 'sync_worker.py'), runtime['database'], runtime['exchange'],
            '--once', '--managed-cycle', '--ota-version', str(version),
            '--workflow-config', str(local / 'data' / 'workflow.json')]
    if runtime.get('security_dir'): args += ['--security-dir', runtime['security_dir']]
    try:
        result = subprocess.run(args, cwd=app, capture_output=True, timeout=3600)
        return result.returncode
    except subprocess.TimeoutExpired:
        return 1


def runtime_at(local):
    # Legacy bootstrap runtime.json is mode 644 inside private data/; it is
    # configuration, not release authority. Keep its bytes/permissions intact.
    runtime = parse(read_local(local / 'data' / 'runtime.json'))
    db = no_symlinks(runtime['database'])
    if db.parent != local / 'data' or not db.is_file():
        raise ValueError('OTA requires database inside managed data directory')
    if runtime.get('security_dir') and no_symlinks(runtime['security_dir']) != local / 'identity':
        raise ValueError('OTA requires security state inside managed identity directory')
    if not (no_symlinks(runtime['exchange']) / '.stfolder').is_dir():
        raise ValueError('accepted exchange required')
    return runtime


def bootstrap_consumer(local, source):
    """One-time local maintenance only; old workers must already be stopped."""
    local, source = no_symlinks(local), no_symlinks(source)
    if (local / 'ota-state.json').exists(): raise ValueError('consumer already installed')
    runtime = runtime_at(local)
    exchange = no_symlinks(runtime['exchange'])
    if local == exchange or local.is_relative_to(exchange) or exchange.is_relative_to(local):
        raise ValueError('installation must be outside exchange')
    with lock(local / 'data' / 'worker.lock'):
        app = local / 'versions' / '0'
        app.mkdir(parents=True, mode=0o700, exist_ok=True)
        for name in REQUIRED:
            target = no_symlinks(app / name)
            target.parent.mkdir(parents=True, exist_ok=True)
            data = read_local(source / name)
            if target.exists():
                if read_local(target) != data: raise ValueError('partial bootstrap source mismatch')
            else:
                with target.open('xb') as out: out.write(data)
        if not health(app, Path(runtime['database'])):
            raise ValueError('bootstrap worker unhealthy')
        consumer = no_symlinks(local / 'consumer')
        consumer.mkdir(mode=0o700, exist_ok=True)
        for name in ('ota_update.py', 'runtime_lock.py', 'signed_packets.py', 'windows_acl.py', 'build_package.py'):
            target = no_symlinks(consumer / name)
            data = read_local(source / name)
            if target.exists():
                if read_local(target) != data: raise ValueError('partial consumer source mismatch')
            else:
                with target.open('xb') as out: out.write(data)
        sync_tree(app); sync_tree(consumer)
        write_local(local / 'ota-state.json', {'format': 'agentmesh-ota-state-v1',
            'active': 0, 'highest': 0, 'pending': None, 'previous': None}, exclusive=True)
    return {'consumer': str(consumer / 'ota_update.py'), 'status': 'installed'}


def supervise(local, interval=60, once=False):
    local = no_symlinks(local)
    runtime = runtime_at(local)
    database = Path(runtime['database'])
    with lock(local / 'data' / 'supervisor.lock', timeout=0):
        while True:
            with lock(local / 'data' / 'worker.lock'):
                state = recover(local, database)
            release = Path(runtime['exchange']) / 'releases' / 'worker'
            if (release / 'release.json').exists():
                try: _apply(local, release, database)
                except Exception:
                    print('Release not activated; check local authority/version/package.', file=sys.stderr)
            with lock(local / 'data' / 'worker.lock'):
                state = recover(local, database)
            app = local / 'versions' / str(state['active'])
            args = [sys.executable, str(app / 'sync_worker.py'), str(database), runtime['exchange'],
                    '--once', '--managed-cycle', '--ota-version', str(state['active']),
                    '--workflow-config', str(local / 'data' / 'workflow.json')]
            if runtime.get('security_dir'): args += ['--security-dir', runtime['security_dir']]
            result = subprocess.run(args, cwd=app, timeout=3600)
            if once: return result.returncode
            time.sleep(interval)


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    key = sub.add_parser('release-key'); key.add_argument('directory')
    build = sub.add_parser('build'); build.add_argument('--source', required=True); build.add_argument('--output', required=True)
    build.add_argument('--private', required=True); build.add_argument('--version', type=int, required=True)
    boot = sub.add_parser('bootstrap'); boot.add_argument('--local', required=True); boot.add_argument('--source', required=True)
    trust = sub.add_parser('trust'); trust.add_argument('--local', required=True); trust.add_argument('--public', required=True)
    trust.add_argument('--fingerprint', required=True); trust.add_argument('--release-id', required=True)
    for command in ('apply', 'run'):
        cmd = sub.add_parser(command); cmd.add_argument('--local', required=True)
        if command == 'apply': cmd.add_argument('--release', required=True)
        else: cmd.add_argument('--once', action='store_true'); cmd.add_argument('--interval', type=float, default=60)
    args = parser.parse_args(argv)
    try:
        if args.command == 'release-key': result = release_key(args.directory)
        elif args.command == 'build': result = build_release(args.source, args.output, args.private, args.version)
        elif args.command == 'bootstrap': result = bootstrap_consumer(args.local, args.source)
        elif args.command == 'trust':
            provision_trust(args.local, read_json(args.public), args.fingerprint, args.release_id)
            result = {'status': 'approved'}
        elif args.command == 'apply':
            local = no_symlinks(args.local)
            result = apply(local, args.release, runtime_at(local)['database'])
        else:
            import math
            if not math.isfinite(args.interval) or args.interval <= 0: raise ValueError('invalid interval')
            return supervise(args.local, args.interval, args.once)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print('OTA operation failed; no remote commands executed. Check local scope, identity and state.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
