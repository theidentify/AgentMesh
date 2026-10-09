"""Mac-first operator replacement. No launchd action happens during planning."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import subprocess
import sys
import time
import uuid

from install_adopt import absolute, validate
from signed_packets import parse, private_directory, read_local, write_local
from worker_lifecycle import positive


def digest(path):
    with absolute(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def owned(path, *, directory=False):
    path = absolute(path)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022 or (not path.is_dir() if directory else not path.is_file()):
        raise ValueError('user-owned non-writable-by-others path required')
    return path


class Launchd:
    """The real boundary is used only by the operator, never by test fixtures."""
    def __init__(self):
        if sys.platform != 'darwin':
            raise ValueError('macOS operator only')
        self.domain = 'gui/' + str(os.getuid())
    def inspect(self, label):
        result = subprocess.run(['/bin/launchctl', 'print', self.domain + '/' + label],
            capture_output=True, text=True, timeout=15)
        if result.returncode:
            return {'loaded': False, 'pid': None, 'arguments': []}
        text = result.stdout
        pid = re.search(r'^\s*pid = (\d+)\s*$', text, re.M)
        arguments = re.search(r'^[ \t]*arguments = \{[ \t]*\n(.*?)^[ \t]*\}', text, re.M | re.S)
        if not arguments:
            raise ValueError('cannot verify launchd command')
        return {'loaded': True, 'pid': int(pid[1]) if pid else None,
                'arguments': [line.strip() for line in arguments[1].splitlines() if line.strip()]}
    def drain(self, label, timeout):
        state = self.inspect(label)
        processes = {state['pid']} if state['pid'] else set()
        if processes:
            tree = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid='], check=True,
                                  capture_output=True, text=True, timeout=5)
            edges = [tuple(map(int, line.split())) for line in tree.stdout.splitlines() if line.strip()]
            while True:
                descendants = {pid for pid, parent in edges if parent in processes}
                if descendants <= processes:
                    break
                processes.update(descendants)
        subprocess.run(['/bin/launchctl', 'bootout', self.domain + '/' + label],
            check=True, capture_output=True, timeout=timeout)
        deadline = time.monotonic() + timeout
        from worker_lifecycle import alive
        while processes:
            processes = {pid for pid in processes if alive(pid)}
            if not processes:
                break
            if time.monotonic() > deadline:
                raise TimeoutError('old process tree has not exited; replacement refused')
            time.sleep(0.1)
        if self.inspect(label)['loaded']:
            raise ValueError('service not drained')
    def bootstrap(self, plist):
        subprocess.run(['/bin/launchctl', 'bootstrap', self.domain, str(plist)],
            check=True, capture_output=True, timeout=30)


def read_plan(manifest, binary, supervisor):
    manifest = owned(manifest)
    config = parse(read_local(manifest, private=True))
    required = {'runtime', 'app_root', 'database', 'exchange', 'node', 'security_dir',
                'security_state', 'workflow_config', 'plist', 'expected_args', 'install_root', 'backup_root'}
    if not isinstance(config, dict) or not required <= set(config):
        raise ValueError('incomplete replacement manifest')
    scope = validate(config, config['runtime'], authoritative=False)
    if absolute(Path(config['database']).parent.parent / 'ota-state.json').exists():
        raise ValueError('OTA-owned installation requires its existing consumer')
    runtime = absolute(config['runtime'])
    if runtime.exists():
        from install_adopt import load
        current, current_scope = load(runtime, authoritative=False)
        for key in ('database', 'exchange', 'node', 'security_dir', 'security_state'):
            if current.get(key) != config[key]:
                raise ValueError('existing runtime path mismatch')
        if current_scope != scope or current.get('workflow_config', str(absolute(config['database']).parent / 'workflow.json')) != config['workflow_config']:
            raise ValueError('existing runtime scope/workflow mismatch')
    if config['node'] != 'mac':
        raise ValueError('Mac allocation required')
    root = owned(config['app_root'], directory=True)
    for key in ('database', 'workflow_config'):
        owned(config[key])
    plist = owned(config['plist'])
    service = plistlib.loads(read_local(plist))
    label = service.get('Label')
    if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', label) or 'Program' in service:
        raise ValueError('unsupported service descriptor')
    args = config['expected_args']
    if (not isinstance(args, list) or not args or not all(isinstance(a, str) and '\n' not in a and '\x00' not in a for a in args)
            or service.get('ProgramArguments') != args):
        raise ValueError('expected service command mismatch')
    if not Path(args[0]).is_absolute() or '..' in Path(args[0]).parts:
        raise ValueError('absolute existing executable required')
    if not Path(args[0]).is_file() or not os.access(args[0], os.X_OK):
        raise ValueError('existing executable missing')
    # Bind the selected old command to the actual local DB and exchange.
    if len(args) >= 4 and Path(args[1]).name == 'sync_worker.py':
        script = owned(args[1])
        if not script.is_relative_to(root) or args[2:4] != [config['database'], config['exchange']]:
            raise ValueError('expected service runtime mismatch')
        options = args[4:]
        accepted = {'--interval', '--workflow-config', '--security-dir'}
        if len(options) % 2 or any(options[i] not in accepted for i in range(0, len(options), 2)):
            raise ValueError('unsupported legacy service options; separate cutover required')
        parsed = dict(zip(options[::2], options[1::2]))
        interval = positive(float(parsed.get('--interval', 60)))
        if parsed.get('--workflow-config', config['workflow_config']) != config['workflow_config']:
            raise ValueError('workflow path mismatch')
        if '--security-dir' in parsed and (scope['policy'] != 'required' or parsed['--security-dir'] != config['security_dir']):
            raise ValueError('legacy command would silently activate signing')
        if scope['policy'] == 'required' and parsed.get('--security-dir') != config['security_dir']:
            raise ValueError('old command cannot safely resume strict policy')
    elif len(args) >= 4 and args[1:4] == ['worker-run', '--runtime', config['runtime']]:
        owned(args[0])
        options = args[4:]
        if (len(options) not in (2, 3) or options[0] != '--interval'
                or (len(options) == 3 and options[2] != '--legacy-drained')
                or (scope['policy'] == 'legacy') != (len(options) == 3)):
            raise ValueError('unsupported managed service arguments')
        interval = positive(float(options[1]))
    else:
        raise ValueError('expected service is not an AgentMesh worker')
    live = supervisor.inspect(label)
    if not live['loaded'] or live['arguments'] != args:
        raise ValueError('expected service not loaded or live command mismatch')
    binary = owned(binary)
    if not os.access(binary, os.X_OK):
        raise ValueError('bundled executable required')
    exchange = absolute(config['exchange'])
    for key in ('install_root', 'backup_root'):
        path = absolute(config[key])
        if path == exchange or path.is_relative_to(exchange) or exchange.is_relative_to(path) or path in (root, absolute(config['database']).parent):
            raise ValueError('private install and backup roots must be separate from exchange/data')
        if path.exists():
            private_directory(path)
        else:
            owned(path.parent, directory=True)
    install, backups = (absolute(config[k]) for k in ('install_root', 'backup_root'))
    if install.is_relative_to(backups) or backups.is_relative_to(install):
        raise ValueError('separate install and backup roots required; no overlap')
    protected = [absolute(config[k]) for k in ('security_dir', 'security_state', 'workflow_config', 'runtime', 'database')]
    if any(p.is_relative_to(target) or target.is_relative_to(p) for target in (install, backups) for p in protected):
        raise ValueError('install/backup roots must be separate from identity and active state')
    return config, scope, service, binary, interval


def plan(manifest, binary, *, supervisor=None):
    supervisor = supervisor or Launchd()
    config, scope, service, binary, interval = read_plan(manifest, binary, supervisor)
    return {'status': 'planned', 'label': service['Label'], 'policy': scope['policy'],
            'runtime': config['runtime'], 'binary_sha256': digest(binary), 'interval': interval,
            'runtime_action': 'preserve' if absolute(config['runtime']).exists() else 'bind_after_drain',
            'database_restore': 'never_automatic', 'distribution': 'local_development_trial'}


def blob(path, data, *, exclusive=False, expected=None, mode=0o600):
    """Owner-only atomic publication; no recursive or uncertain cleanup."""
    path = absolute(path)
    temp = path.with_name('.' + path.name + '.' + str(uuid.uuid4()) + '.tmp')
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if expected is not None and digest(path) != expected:
            raise ValueError('target changed; refusing overwrite')
        if exclusive:
            os.link(temp, path)
        else:
            os.replace(temp, path)
        if digest(path) != hashlib.sha256(data).hexdigest():
            raise ValueError('published file verification failed')
    finally:
        temp.unlink(missing_ok=True)


def private_root(path):
    path = absolute(path)
    if not path.exists():
        path.mkdir(mode=0o700)
    return private_directory(path)


def sqlite_backup(database, destination):
    database, destination = absolute(database), absolute(destination)
    temp = destination.with_name('.snapshot-' + str(uuid.uuid4()))
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
            source.execute('PRAGMA query_only=ON')
            with closing(sqlite3.connect(temp)) as target:
                source.backup(target)
                target.execute('PRAGMA journal_mode=DELETE')
                if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or target.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('database backup verification failed')
        with temp.open('rb') as stream:
            os.fsync(stream.fileno())
        os.link(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def install_binary(binary, root):
    root = private_root(root)
    checksum = digest(binary)
    version = absolute(root / checksum)
    executable = version / 'agentmesh'
    if version.exists():
        owned(version, directory=True)
        if not executable.is_file() or digest(executable) != checksum or executable.stat().st_mode & 0o222:
            raise ValueError('immutable version collision')
    else:
        version.mkdir(mode=0o700)
        blob(executable, binary.read_bytes(), exclusive=True, mode=0o500)
        version.chmod(0o500)
    return executable


def process_owned_by(pid, launcher_pid):
    if pid == launcher_pid:
        return True
    # PyInstaller onefile has a supervising bootloader and a Python child.
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'ppid='],
                            capture_output=True, text=True, timeout=5)
    return result.returncode == 0 and result.stdout.strip() == str(launcher_pid)


def wait_health(supervisor, service, runtime, *, previous_nonce, timeout):
    from worker_lifecycle import status
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        live = supervisor.inspect(service['Label'])
        record = status(runtime)
        if live['loaded'] and live['arguments'] != service['ProgramArguments']:
            raise ValueError('live replacement command mismatch')
        if (live['loaded'] and live['pid'] and record.get('pid') and process_owned_by(record['pid'], live['pid'])
                and record.get('nonce') != previous_nonce and record['state'] == 'running'
                and record['cycles'] >= 1 and not record['last_error']):
            return
        if record.get('nonce') != previous_nonce and record['state'] in ('failed', 'stale'):
            raise ValueError('replacement service cycle failed')
        time.sleep(0.1)
    raise TimeoutError('replacement service has not completed a healthy cycle')


def code_descriptor(arguments):
    # Existing Python may intentionally be a venv symlink. Resolve only this
    # explicitly selected interpreter, never a mutable data/security path.
    executable = Path(arguments[0]).resolve(strict=True)
    files = [executable]
    if len(arguments) > 1 and Path(arguments[1]).name == 'sync_worker.py':
        files.append(owned(arguments[1]))
    return {'arguments': arguments, 'files': {str(p): digest(p) for p in files}}


def _restore(backup, recovery, supervisor, timeout):
    """Only service/code/config rollback; a SQLite backup is never restored."""
    config = recovery['config']
    plist = owned(config['plist'])
    if digest(plist) not in (recovery['old_plist_sha256'], recovery.get('new_plist_sha256')):
        raise ValueError('service configuration changed; manual recovery required')
    old = plistlib.loads(read_local(backup / 'service.plist', private=True))
    if digest(backup / 'service.plist') != recovery['old_plist_sha256']:
        raise ValueError('recovery descriptor changed')
    if digest(backup / 'old-code.json') != recovery['old_code_sha256'] or parse(read_local(backup / 'old-code.json', private=True)) != code_descriptor(old['ProgramArguments']):
        raise ValueError('old code changed; manual recovery required')
    live = supervisor.inspect(old['Label'])
    allowed = [old['ProgramArguments'], recovery.get('new_args')]
    if live['loaded']:
        if live['arguments'] not in allowed:
            raise ValueError('foreign service command; no stop performed')
        supervisor.drain(old['Label'], timeout)
    if validate({**config, **recovery['scope']}, config['runtime']) != recovery['scope']:
        raise ValueError('database scope changed; old service remains stopped')
    runtime = absolute(config['runtime'])
    if recovery.get('runtime_created') and runtime.exists():
        if digest(runtime) != recovery.get('runtime_sha256'):
            raise ValueError('adopted runtime changed; manual recovery required')
        runtime.unlink()
    blob(plist, read_local(backup / 'service.plist', private=True), expected=digest(plist))
    supervisor.bootstrap(plist)
    live = supervisor.inspect(old['Label'])
    if not live['loaded'] or live['arguments'] != old['ProgramArguments']:
        raise ValueError('old service configuration was not restored')
    recovery['state'] = 'rolled_back'
    write_local(backup / 'recovery.json', recovery)
    return {'status': 'rolled_back', 'backup': str(backup), 'database_restore': 'not_performed'}


def rollback(backup, *, supervisor=None, timeout=300):
    positive(timeout)
    supervisor = supervisor or Launchd()
    backup = private_directory(absolute(backup))
    recovery = parse(read_local(backup / 'recovery.json', private=True))
    if not isinstance(recovery, dict) or recovery.get('format') != 'agentmesh-replacement-v1' or recovery.get('state') == 'rolled_back':
        raise ValueError('invalid or already rolled-back recovery descriptor')
    print('Rollback code/service only. SQLite will NOT be restored. Type ROLLBACK:', file=sys.stderr)
    answer = sys.stdin.readline()
    if not answer:
        raise EOFError('ROLLBACK confirmation required')
    if answer.rstrip('\r\n') != 'ROLLBACK':
        return {'status': 'pending'}
    return _restore(backup, recovery, supervisor, timeout)


def replace(manifest, binary, *, supervisor=None, timeout=300, dry_run=False):
    positive(timeout)
    supervisor = supervisor or Launchd()
    config, scope, service, binary, interval = read_plan(manifest, binary, supervisor)
    report = plan(manifest, binary, supervisor=supervisor)
    if dry_run:
        return report
    import json
    print(json.dumps(report, sort_keys=True), file=sys.stderr)
    print('Drain selected worker, back up, replace and verify. Failure restores code/service, NEVER the DB. Type REPLACE:', file=sys.stderr)
    baseline_manifest = read_local(manifest, private=True)
    old_plist = read_local(config['plist'])
    old_workflow = read_local(config['workflow_config'])
    runtime = absolute(config['runtime'])
    old_runtime = read_local(runtime, private=True) if runtime.exists() else None
    identities = {str(absolute(config[k])): (absolute(config[k]).stat().st_dev, absolute(config[k]).stat().st_ino)
                  for k in ('database', 'app_root', 'exchange', 'workflow_config', 'plist')}
    binary_hash = digest(binary)
    old_code = code_descriptor(service['ProgramArguments'])
    answer = sys.stdin.readline()
    if not answer:
        raise EOFError('REPLACE confirmation required')
    if answer.rstrip('\r\n') != 'REPLACE':
        return {'status': 'pending'}
    if (read_local(manifest, private=True) != baseline_manifest
            or read_local(config['plist']) != old_plist or read_local(config['workflow_config']) != old_workflow
            or (read_local(runtime, private=True) if runtime.exists() else None) != old_runtime
            or digest(binary) != binary_hash or code_descriptor(service['ProgramArguments']) != old_code):
        raise ValueError('replacement inputs changed during confirmation')
    if any((absolute(p).stat().st_dev, absolute(p).stat().st_ino) != binding for p, binding in identities.items()):
        raise ValueError('replacement paths changed during confirmation')
    read_plan(manifest, binary, supervisor)
    if validate(config, runtime) != scope:
        raise ValueError('authoritative database scope changed')
    drained = False
    backup = None
    recovery = None
    try:
        # Old workers do not participate in our kernel locks. launchd + PID exit
        # must finish BEFORE adopting, taking backups, or starting any new cycle.
        supervisor.drain(service['Label'], timeout)
        drained = True
        if supervisor.inspect(service['Label'])['loaded']:
            raise ValueError('old service did not drain')
        if validate(config, runtime) != scope:
            raise ValueError('database scope changed while draining')
        root = private_root(config['backup_root'])
        backup = root / str(uuid.uuid4())
        backup.mkdir(mode=0o700)
        blob(backup / 'service.plist', old_plist, exclusive=True)
        blob(backup / 'workflow.json', old_workflow, exclusive=True)
        if old_runtime is not None:
            blob(backup / 'runtime.json', old_runtime, exclusive=True)
        write_local(backup / 'old-code.json', old_code, exclusive=True)
        sqlite_backup(config['database'], backup / 'database.sqlite')
        recovery = {'format': 'agentmesh-replacement-v1', 'state': 'backed_up',
                    'config': config, 'scope': scope, 'runtime_created': False,
                    'old_code_sha256': digest(backup / 'old-code.json'),
                    'old_plist_sha256': hashlib.sha256(old_plist).hexdigest()}
        write_local(backup / 'recovery.json', recovery, exclusive=True)
        executable = install_binary(binary, config['install_root'])
        if old_runtime is None:
            from install_adopt import adopt
            result = adopt(**{key: config[key] for key in ('runtime', 'app_root', 'database', 'exchange', 'node', 'security_dir', 'security_state', 'workflow_config')})
            if result['status'] != 'bound':
                raise ValueError('binding declined; restoring old service')
            recovery['runtime_created'] = True
            recovery['runtime_sha256'] = digest(runtime)
            write_local(backup / 'recovery.json', recovery)
        from install_adopt import load
        runtime_config, runtime_scope = load(runtime)
        for key in ('database', 'exchange', 'node', 'security_dir', 'security_state'):
            if runtime_config.get(key) != config[key]:
                raise ValueError('runtime differs from selected service scope')
        if runtime_config.get('workflow_config', str(absolute(config['database']).parent / 'workflow.json')) != config['workflow_config'] or runtime_scope != scope:
            raise ValueError('runtime workflow or policy mismatch')
        cycle_args = [str(executable), 'worker-run', '--runtime', str(runtime), '--once']
        if scope['policy'] == 'legacy':
            cycle_args += ['--legacy-drained']
        environment = {**os.environ, **service.get('EnvironmentVariables', {})}
        cycle = subprocess.run(cycle_args, env=environment, stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=timeout)
        if cycle.returncode:
            raise ValueError('bounded replacement cycle failed')
        from worker_lifecycle import status
        previous_nonce = status(runtime)['nonce']
        new_service = dict(service)
        new_service['ProgramArguments'] = [str(executable), 'worker-run', '--runtime', str(runtime), '--interval', str(interval)]
        if scope['policy'] == 'legacy':
            new_service['ProgramArguments'] += ['--legacy-drained']
        new_plist = plistlib.dumps(new_service)
        recovery.update(new_args=new_service['ProgramArguments'], new_plist_sha256=hashlib.sha256(new_plist).hexdigest(), state='activating')
        write_local(backup / 'recovery.json', recovery)
        blob(config['plist'], new_plist, expected=recovery['old_plist_sha256'])
        supervisor.bootstrap(config['plist'])
        wait_health(supervisor, new_service, runtime, previous_nonce=previous_nonce, timeout=timeout)
        if plistlib.loads(read_local(config['plist'])) != new_service or validate(config, runtime) != scope:
            raise ValueError('replacement verification mismatch')
        recovery['state'] = 'active'
        write_local(backup / 'recovery.json', recovery)
        return {'status': 'replaced', 'backup': str(backup), 'binary': str(executable),
                'policy': scope['policy'], 'health': 'completed_cycle', 'database_restore': 'not_performed'}
    except Exception as exc:
        try:
            if recovery is not None:
                result = _restore(backup, recovery, supervisor, timeout)
            elif drained:
                if (read_local(config['plist']) != old_plist
                        or code_descriptor(service['ProgramArguments']) != old_code
                        or validate(config, runtime) != scope):
                    raise ValueError('old code or database scope changed; service remains stopped')
                supervisor.bootstrap(config['plist'])
                live = supervisor.inspect(service['Label'])
                if not live['loaded'] or live['arguments'] != service['ProgramArguments']:
                    raise ValueError('old service not restored')
                result = {'status': 'rolled_back', 'backup': str(backup) if backup else None}
            else:
                raise ValueError('drain incomplete; manual review required')
            return {**result, 'error': type(exc).__name__}
        except Exception as recovery_error:
            return {'status': 'recovery_required', 'backup': str(backup) if backup else None,
                    'error': type(exc).__name__, 'recovery_error': type(recovery_error).__name__,
                    'database_restore': 'not_performed'}
