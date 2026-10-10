"""Bounded existing-install program lifecycle. Never initializes or restores data."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from install_adopt import absolute, load
from runtime_lock import lock
from terminal_progress import Wait, step
from signed_packets import parse, private_directory, read_local, windows_private, write_local
import worker_lifecycle as managed
import windows_task

FORMAT = 'agentmesh-windows-programs-v1'
LAUNCHER = 'agentmeshw.exe'
# PyInstaller onedir runtime shared by agentmesh.exe and agentmeshw.exe (RC.8+).
# rc.5-rc.7 flat onefile bundles stay verifiable for rollback.
RUNTIME = '_internal'
TOP_LEVEL = {'agentmesh.exe', LAUNCHER, 'BUILD.json'}
DEVELOPMENT = 'unsigned development trial; not a final or trusted-publisher release'


def digest(path):
    path = absolute(path)
    if not path.is_file():
        raise ValueError('regular program file required')
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def program_name(name):
    """A top-level bundle file, or a relative onedir runtime path under _internal/."""
    if not isinstance(name, str):
        return False
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        return True
    parts = name.split('/')
    # Segments cannot be empty, '.', '..', absolute or contain backslashes.
    return (len(parts) > 1 and parts[0] == RUNTIME
            and all(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9._+-]*', part) for part in parts[1:]))


def at(directory, name):
    return Path(directory).joinpath(*name.split('/'))


def valid_files(names):
    names = set(names)
    return ({'agentmesh.exe', 'BUILD.json'} <= names
            and all(n in TOP_LEVEL or (n.startswith(RUNTIME + '/') and program_name(n)) for n in names))


def runtime_directories(names):
    return {'/'.join(n.split('/')[:depth]) for n in names for depth in range(1, n.count('/') + 1)}


def tree(directory):
    """Every file and directory below an owned version; links are never followed."""
    files, directories = set(), set()
    for p in Path(directory).rglob('*'):
        name = p.relative_to(directory).as_posix()
        if p.is_symlink() or not (p.is_file() or p.is_dir()):
            raise ValueError('installed program directory changed')
        (files if p.is_file() else directories).add(name)
    return files, directories


def owned_tree(directory, names):
    return tree(directory) == (set(names), runtime_directories(names))


def identity(path):
    s = absolute(path).stat()
    return [s.st_dev, s.st_ino]


def bundle(binary):
    binary = absolute(binary)
    manifest = absolute(binary.parent / 'BUILD.json')
    metadata = json.loads(manifest.read_bytes())
    required = {'version', 'source_sha', 'system', 'architecture', 'checksums', 'distribution', 'verification', 'scope'}
    if (type(metadata) is not dict or set(metadata) != required
            or not re.fullmatch(r'0\.2\.0-rc\.(?:[5-9]|[1-9][0-9]+)', metadata.get('version', ''))
            or not re.fullmatch(r'[0-9a-f]{40}', metadata.get('source_sha', ''))
            or metadata['system'] != 'windows' or metadata['architecture'] not in ('amd64', 'x86_64', 'arm64')
            or metadata['distribution'] != DEVELOPMENT or not isinstance(metadata['verification'], str)
            or metadata['scope'] != 'program files only; no database, keys, runtime, snapshots or deployment secrets'
            or not isinstance(metadata['checksums'], dict) or binary.name != 'agentmesh.exe'):
        raise ValueError('invalid development BUILD.json schema')
    for name, checksum in metadata['checksums'].items():
        if (not program_name(name)
                or not isinstance(checksum, str) or not re.fullmatch(r'[0-9a-f]{64}', checksum)):
            raise ValueError('invalid BUILD.json checksum')
    files = {'agentmesh.exe': digest(binary), 'BUILD.json': digest(manifest)}
    for name in metadata['checksums']:
        if name == LAUNCHER or name.startswith(RUNTIME + '/'):
            files[name] = digest(at(binary.parent, name))
    if any(metadata['checksums'].get(name) != files[name] for name in files if name != 'BUILD.json'):
        raise ValueError('program checksum mismatch')
    return dict(key=metadata['version'] + '-' + metadata['source_sha'], binary=str(binary), files=files)


def task_entry(state, key):
    """Logon task entry: the windowless launcher when that version ships one."""
    name = LAUNCHER if LAUNCHER in state['programs'][key]['files'] else 'agentmesh.exe'
    return Path(state['root']) / key / name


ACTIONS = {
    'INSTALL': 'copy the program files (the worker is not stopped or started)',
    'UPGRADE': 'copy the new program files (the worker keeps running)',
    'BIND': 'switch to the new version (the worker must already be stopped; it is not started)',
    'REPLACE': 'stop the worker, switch to the new version and start it again',
    'ROLLBACK': 'switch back to the previous version (the worker is not started)',
    'UNINSTALL': 'remove the program files and start-at-login task (your data is kept)',
    'ENABLE': 'start the worker automatically at sign-in (it is not started now)',
    'DISABLE': 'stop starting the worker at sign-in (a running worker keeps running)',
}


ROUTINE = {'install': ('Install now?', 'Install now'), 'upgrade': ('Upgrade now?', 'Upgrade now'),
           'enable': ('Enable start at login?', 'Enable'), 'disable': ('Turn off start at login?', 'Turn off'),
           'recover': ('Finish the interrupted upgrade?', 'Finish upgrade')}


def short(key):
    """'0.2.0-rc.8-<sha>' -> '0.2.0-rc.8 (830536f)' for people; keys stay exact in state."""
    if not key:
        return 'none'
    version, _, sha = key.rpartition('-')
    return f'{version} ({sha[:7]})' if version else key


def describe(plan, *, current=None, previous=None, replace=False, enable=False):
    """The one summary shown before the single decision; stdout JSON is unchanged."""
    action, task = plan['operation'], plan['autostart'] or plan['task_exists']
    version = short(plan['version'])
    if action == 'install':
        title, steps, duration = 'Install ' + version, ['Copy and verify the program files',
            'Nothing else changes: the worker keeps running and start at login stays as it is'], 'about a minute'
    elif action == 'upgrade':
        title = 'Upgrade ' + short(current) + ' -> ' + version
        steps = ['Copy and verify the new program files (the worker keeps running)']
        if replace:
            steps += ['Ask the current worker to stop after its sync cycle (no process is killed)']
        else:
            steps += ['Switch the selected version (the worker must already be stopped; it is not started)']
        if task:
            steps += ['Point start at login to ' + version]
        if replace:
            steps += ['Start the worker from ' + version, 'Confirm it completes two sync cycles']
        duration = 'usually 1-3 minutes (up to about 5)' if replace else 'about a minute'
    elif action == 'autostart':
        title = ('Enable' if enable else 'Turn off') + ' start at login'
        steps = (['Start the worker from ' + version + ' each time you sign in (not now)'] if enable
                 else ['Stop starting the worker at sign-in (a running worker keeps running)'])
        duration = 'a few seconds'
    elif action == 'recover':
        title = 'Finish the interrupted upgrade to ' + version
        steps = ['Check the running worker is ' + version + ' and healthy (nothing is stopped or started)',
                 'Wait for one more sync cycle', 'Mark the upgrade complete']
        duration = 'up to about 2 minutes'
    elif action == 'rollback':
        title = 'Roll back ' + version + ' -> ' + short(previous)
        steps = ['Switch back to ' + short(previous) + ' (the worker must already be stopped; it is not started)']
        steps += ['Point start at login to ' + short(previous)] if task else []
        duration = 'a few seconds'
    else:
        title = 'Uninstall the AgentMesh program'
        steps = ['Remove the start-at-login task' if task else 'No start-at-login task to remove',
                 'Remove the installed program files', 'Keep the database, identity, runtime and settings']
        duration = 'a few seconds'
    instruction = {'install': 'Install AgentMesh ' + version + '?', 'upgrade': 'Upgrade AgentMesh to ' + version + '?',
                   'autostart': ('Start AgentMesh at sign-in?' if enable else 'Stop starting AgentMesh at sign-in?'),
                   'recover': 'Finish the interrupted upgrade to ' + version + '?',
                   'rollback': 'Roll back AgentMesh to ' + short(previous) + '?',
                   'uninstall': 'Uninstall the AgentMesh program?'}[action]
    content = (['From ' + short(current) + ' to ' + version] if action == 'upgrade' else [])
    content += ['\u2022 ' + step for step in steps] + ['', 'Takes ' + duration + '.']
    if action in ('upgrade', 'install') and current:
        content += ['You can roll back to ' + short(current) + '.']
    details = ['Worker now: ' + plan['worker'], 'Start at login: ' + ('on' if plan['autostart'] else 'off'),
               'Task: ' + plan['task_name'], 'Program folder: ' + plan['program_root'], 'Policy: ' + plan['policy'],
               'Publisher: not verified (unsigned trial; checksums only)',
               'Once you confirm, Ctrl+C is ignored until it finishes.']
    button = {'install': 'Install', 'upgrade': 'Upgrade now', 'recover': 'Finish upgrade',
              'autostart': 'Turn on' if enable else 'Turn off'}.get(action)
    info = {'instruction': instruction, 'content': content, 'details': details, 'button': button}
    lines = ['', 'AgentMesh ' + action + ' plan: ' + title]
    lines += [f'  {n}. {step}' for n, step in enumerate(steps, 1)]
    lines += ['  Worker now:     ' + plan['worker'], '  Start at login: ' + ('on' if plan['autostart'] else 'off'),
              '  Expected time:  ' + duration]
    if action in ('upgrade', 'install') and current:
        lines += ['  Rollback:       windows-rollback returns to ' + short(current)]
    lines += ['  Policy:         ' + plan['policy'] + '  |  Publisher: not verified (unsigned trial; checksums only)',
              '  Once you confirm, Ctrl+C is ignored until it finishes.', '']
    return '\n'.join(lines), info


def approve(prompt, action, token, *, yes, confirm_word, enable):
    """Routine actions: one select (or --yes). Destructive: typed word (or --yes --confirm WORD)."""
    routine = action in ('install', 'upgrade', 'autostart', 'recover')
    if yes:
        if not routine and (confirm_word or '').strip().upper() != token:
            raise ValueError('destructive action requires matching --confirm word')
        return True
    if not prompt.interactive():
        raise ValueError('interactive confirmation unavailable; pass --yes')
    if routine:
        return prompt.decide(*ROUTINE['enable' if enable else 'disable'] if action == 'autostart' else ROUTINE[action])
    return prompt.typed(token, ACTIONS[token])


def protection(path):
    path.mkdir(mode=0o700, exist_ok=False)
    windows_private(path, provision=True)
    private_directory(path)


def validate_root(root, config, runtime):
    root = absolute(root)
    # app_root may be the common AgentMesh container; separate siblings are OK.
    private = [Path(config['database']).parent, runtime.parent,
               Path(config.get('security_dir', runtime.parent.parent / 'identity')),
               Path(config.get('security_state', runtime.parent / 'wizard.json')).parent,
               Path(config.get('workflow_config', runtime.parent / 'workflow.json')).parent,
               Path(config['exchange'])]
    if config.get('app'):
        private.append(Path(config['app']))
    for value in private:
        p = absolute(value)
        if root == p or root.is_relative_to(p) or p.is_relative_to(root):
            raise ValueError('program root overlaps existing state or exchange')
    if not root.parent.is_dir():
        raise ValueError('existing program-root parent required')
    return root


def preservation(config, runtime):
    paths = [runtime, Path(config['database']), Path(config['exchange']), Path(config['exchange']) / '.stfolder']
    paths += [absolute(config.get('app_root', runtime.parent.parent))]
    result = {'paths': {str(p): identity(p) for p in paths}, 'files': {str(runtime): digest(runtime)}}
    for key in ('workflow_config', 'security_state'):
        if config.get(key):
            p = absolute(config[key])
            result['files'][str(p)] = digest(p) if p.exists() else None
    security = absolute(config.get('security_dir', runtime.parent.parent / 'identity'))
    if security.exists():
        private_directory(security)
        # Includes identity/trust and any other private files; never dump bodies.
        for p in security.rglob('*'):
            p = absolute(p)
            result['paths'][str(p)] = identity(p)
            if p.is_file():
                result['files'][str(p)] = digest(p)
    return result


def read_state(root, state_path, *, recovering=False):
    private_directory(root)
    marker = parse(read_local(root / 'OWNER.json', private=True))
    state = parse(read_local(state_path, private=True))
    if (not isinstance(state, dict) or state.get('format') != FORMAT or marker != {'format': FORMAT, 'owner': state.get('owner')}
            or state.get('root') != str(root) or state.get('root_identity') != identity(root)
            or state.get('marker') != FORMAT + ':OWNED:' + str(state.get('owner'))
            or state.get('status') != ('recovery_required' if recovering else 'ready')
            or str(uuid.UUID(state['owner'])) != state['owner']):
        raise ValueError('installation ownership or recovery status uncertain')
    if state_path != root / 'installed.json':
        raise ValueError('installed-state must be the owned root installed.json')
    programs = state['programs']
    allowed = {'OWNER.json', 'installed.json', 'installer.lock'} | set(programs)
    if {p.name for p in root.iterdir()} - allowed:
        raise ValueError('unowned program-root entries require review')
    if not isinstance(programs, dict) or state['active'] not in programs or set(state['history']) - set(programs):
        raise ValueError('invalid owned version inventory')
    for key, entry in programs.items():
        if (not re.fullmatch(r'0\.2\.0-rc\.[0-9]+-[0-9a-f]{40}', key)
                or set(entry) != {'identity', 'files'} or not valid_files(entry['files'])
                or any(not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v) for v in entry['files'].values())):
            raise ValueError('invalid owned program manifest')
        directory = absolute(root / key)
        private_directory(directory)
        if identity(directory) != entry['identity'] or not owned_tree(directory, entry['files']):
            raise ValueError('installed program directory changed')
        for name, checksum in entry['files'].items():
            windows_private(absolute(at(directory, name)))
            if digest(at(directory, name)) != checksum:
                raise ValueError('installed program file changed')
        if bundle(directory / 'agentmesh.exe')['key'] != key:
            raise ValueError('installed BUILD.json changed')
    return state


def task_owned(report, state):
    if report['sid'] != state['sid']:
        raise ValueError('task SID changed')
    task = report['task']
    expected = state['task']
    if task != expected:
        raise ValueError('foreign or changed Scheduled Task refused')
    if task is not None:
        b = task['binding']
        selected = state['active']
        definition = windows_task.binding(state['sid'], state['marker'], task_entry(state, selected),
            state['runtime'], state['interval'], state['timeout'], state['legacy_drained'], b['enabled'])
        if b != definition or task['sid'] != state['sid']:
            raise ValueError('Scheduled Task binding is not owned')


@contextmanager
def stopped(config, runtime, scope, legacy_drained, unmanaged_drained=False):
    if scope['policy'] == 'legacy' and not legacy_drained:
        raise ValueError('explicit prior legacy-worker drain approval required')
    record = managed.status(runtime)
    if record['state'] not in ('stopped', 'failed'):
        raise ValueError('worker must be proven stopped; stale or running refused')
    existing_control = managed.control(config)
    if managed.metadata(existing_control, runtime) is None and not (unmanaged_drained or legacy_drained):
        raise ValueError('explicit prior unmanaged-worker drain approval required')
    # Created only after confirmation. Hold the same kernel lock used by workers
    # through the binding/removal; an informational stopped record is not enough.
    directory = managed.control(config, create=True)
    with lock(directory / 'lifetime.lock', timeout=0), lock(Path(config['database']).parent / 'worker.lock', timeout=0):
        if managed.status(runtime)['state'] not in ('stopped', 'failed'):
            raise ValueError('worker changed while draining')
        yield


def save(path, state):
    write_local(path, state)
    if parse(read_local(path, private=True)) != state:
        raise ValueError('installed-state readback failed')


def stage(root, candidate, state, state_path):
    key = candidate['key']
    if key in state['programs']:
        if state['programs'][key]['files'] != candidate['files']:
            raise ValueError('same revision has different program bytes')
        return key
    directory = absolute(root / key)
    if directory.exists():
        raise ValueError('unowned version directory refused')
    with step('Copying and verifying program files', 'Program files copied and verified'):
        # Recovery records stay local and private, never in distributed BUILD.json.
        state['status'] = 'recovery_required'
        state['pending'] = {'operation': 'stage', 'key': key, 'files': candidate['files']}
        save(state_path, state)
        protection(directory)
        state['pending']['directory_identity'] = identity(directory)
        state['pending']['completed_files'] = {}
        save(state_path, state)
        for name, checksum in candidate['files'].items():
            source = at(Path(candidate['binary']).parent, name)
            dest = at(directory, name)
            for parent in reversed(dest.relative_to(directory).parents[:-1]):
                if not (directory / parent).exists():
                    protection(directory / parent)
            with absolute(source).open('rb') as src, dest.open('xb') as target:
                windows_private(dest, provision=True)
                shutil.copyfileobj(src, target)
                target.flush(); os.fsync(target.fileno())
            windows_private(dest)
            if digest(dest) != checksum:
                raise ValueError('installed program readback mismatch')
            state['pending']['completed_files'][name] = {'identity': identity(dest), 'sha256': checksum}
            save(state_path, state)
        if bundle(directory / 'agentmesh.exe') != {**candidate, 'binary': str(directory / 'agentmesh.exe')}:
            raise ValueError('installed bundle verification failed')
    state['programs'][key] = {'identity': identity(directory), 'files': candidate['files']}
    state['pending'] = None
    state['status'] = 'ready'
    save(state_path, state)
    return key


def start_worker(command, timeout):
    # Narrow launch seam: fixtures replace this, never the global subprocess.
    # Its own process group: a console Ctrl+C cannot reach the starter.
    options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
               else {'start_new_session': True})
    subprocess.run(command, check=True, timeout=timeout, **options)


@contextmanager
def uninterruptible():
    """After approval Ctrl+C must not split a transaction (left recovery_required).

    Windows: SetConsoleCtrlHandler(NULL, TRUE), inherited by child processes.
    POSIX: SIGINT ignored in this process; the ignore disposition survives exec.
    """
    if os.name == 'nt':
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.SetConsoleCtrlHandler(None, True)
        try:
            yield
        finally:
            kernel32.SetConsoleCtrlHandler(None, False)
        return
    import signal
    import threading
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def recoverable(state):
    """Only an upgrade interrupted after the new binding, before commit, rolls forward."""
    pending = state.get('pending') or {}
    if not (pending.get('operation') == 'upgrade' and pending.get('phase') == 'start'
            and pending.get('new') == state['active'] and pending.get('new') in state['programs']
            and (pending.get('old') == state['active'] or pending.get('old') in state['history'])
            and any(name.startswith(RUNTIME + '/') for name in state['programs'][state['active']]['files'])):
        raise ValueError('recovery shape not supported; manual review required')


def finish_interrupted_start(state, state_path, root, runtime, timeout):
    """Commit only when the selected onedir version is proven running and cycling."""
    expected = absolute(root / state['active'] / RUNTIME)
    health = managed.status(runtime)
    if health.get('state') != 'running' or health.get('last_error') or health.get('cycles', 0) < 1:
        raise ValueError('recovery requires a healthy running worker')
    if not health.get('bundle_dir') or Path(health['bundle_dir']).resolve() != expected.resolve():
        raise ValueError('running worker is not the selected version')
    nonce, first = health.get('nonce'), health['cycles']
    deadline = time.monotonic() + state['interval'] + timeout
    with Wait('Confirming the running worker completes another sync cycle', state['interval'] + timeout,
              done='Running worker healthy'):
        while True:
            health = managed.status(runtime)
            if health.get('state') != 'running' or health.get('nonce') != nonce or health.get('last_error'):
                raise ValueError('recovery requires a healthy running worker')
            if health['cycles'] > first:
                break
            if time.monotonic() > deadline:
                raise TimeoutError('no further healthy cycle; recovery state retained')
            time.sleep(0.1)
    state.update(status='ready', pending=None)
    save(state_path, state)
    return {'status': 'recovered', 'version': state['active'], 'worker_changed': False}


def run(action, *, runtime, program_root=None, installed_state=None, binary=None,
        dry_run=False, enable=False, disable=False, interval=60, timeout=60,
        legacy_drained=False, unmanaged_drained=False, replace=False, task_name=None, adapter=None,
        yes=False, confirm_word=None, prompt=None):
    # Only an explicitly injected OS boundary permits POSIX focused fixtures.
    adapter = windows_task.TaskAdapter() if adapter is None else adapter
    runtime = absolute(runtime)
    if not runtime.is_file():
        # Checked before the native ACL probe, which cannot describe absence.
        raise FileNotFoundError('existing runtime required')
    config, scope = load(runtime, authoritative=False)
    if scope['node'] != 'windows':
        raise ValueError('Windows runtime node required')
    managed.positive(interval); managed.positive(timeout)
    if program_root is None:
        local = os.environ.get('LOCALAPPDATA')
        if not local:
            raise ValueError('explicit program root required')
        program_root = Path(local) / 'AgentMesh/programs'
    root = validate_root(program_root, config, runtime)
    state_path = absolute(installed_state) if installed_state else root / 'installed.json'
    if state_path != root / 'installed.json':
        raise ValueError('installed-state must be the owned root installed.json')
    fresh = not root.exists()
    if fresh and action != 'install':
        raise ValueError('existing installed-state required')
    if action == 'install' and not fresh:
        raise ValueError('program root exists; use upgrade only for owned intact storage')
    baseline = preservation(config, runtime)
    parent_identity = identity(root.parent)
    candidate = bundle(binary) if action in ('install', 'upgrade') else None
    if candidate and (Path(candidate['binary']).is_relative_to(Path(config['exchange'])) or root.is_relative_to(Path(candidate['binary']).parent)):
        raise ValueError('source bundle must be local and outside target/exchange')
    recovering = action == 'recover'
    state = None if fresh else read_state(root, state_path, recovering=recovering)
    if recovering:
        recoverable(state)
    if state and (state['runtime'] != str(runtime) or state['scope'] != scope
                  or state['runtime_hash'] != digest(runtime) or state['database_identity'] != identity(config['database'])):
        raise ValueError('installed runtime or database scope changed')
    name = task_name or (state['task_name'] if state else 'AgentMesh-' + hashlib.sha256(str(runtime).encode()).hexdigest()[:24])
    if state and name != state['task_name']:
        raise ValueError('task name changed')
    report = adapter.read(name)
    if state:
        task_owned(report, state)
    elif report['task'] is not None:
        raise ValueError('foreign Scheduled Task refused')
    if action == 'autostart' and enable == disable:
        raise ValueError('select exactly one of enable or disable')
    if action == 'rollback' and not state['history']:
        raise ValueError('no previous owned program available')
    plan = {'status': 'planned', 'operation': action, 'program_root': str(root),
            'installed_state': str(state_path), 'version': candidate['key'] if candidate else state['active'],
            'task_name': name, 'autostart': bool(report['task'] and report['task']['binding']['enabled']),
            'worker': managed.status(runtime)['state'], 'policy': scope['policy'],
            'publisher': 'checksum consistency only; not trusted publisher identity'}
    if dry_run:
        return plan
    token = {'install': 'INSTALL', 'upgrade': 'UPGRADE', 'rollback': 'ROLLBACK', 'recover': 'RECOVER',
             'uninstall': 'UNINSTALL', 'autostart': 'ENABLE' if enable else 'DISABLE'}[action]
    if prompt is None:
        import console_prompt
        prompt = console_prompt.ConsolePrompt()
    if not yes:
        text, info = describe({**plan, 'task_exists': report['task'] is not None},
                              current=state['active'] if state else None,
                              previous=state['history'][-1] if state and state['history'] else None,
                              replace=replace, enable=enable)
        prompt.show(text)
        if hasattr(prompt, 'remember'):
            prompt.remember(info)  # the same plan, structured for a native dialog
    if not approve(prompt, action, token, yes=yes, confirm_word=confirm_word, enable=enable):
        prompt.show('Cancelled. Nothing was changed.')
        return {'status': 'cancelled'}

    prompt.show('Working. Ctrl+C is ignored until this finishes.')

    def apply():
        nonlocal state
        def revalidate():
            if (identity(root.parent) != parent_identity or validate_root(root, config, runtime) != root
                    or load(runtime) != (config, scope) or preservation(config, runtime) != baseline
                    or (candidate is not None and bundle(binary) != candidate)):
                raise ValueError('paths, program, configuration or scope changed during confirmation')
            if adapter.read(name) != report:
                raise ValueError('Scheduled Task changed during confirmation')
            if state is not None and read_state(root, state_path, recovering=recovering) != state:
                raise ValueError('installed-state changed during confirmation')

        revalidate()
        if fresh:
            # Exclusive claim, no cleanup of a concurrent installer's root.
            protection(root)
            owner = str(uuid.uuid4())
            state = dict(format=FORMAT, owner=owner, root=str(root), root_identity=identity(root), status='ready',
                         runtime=str(runtime), runtime_hash=digest(runtime), database_identity=identity(config['database']),
                         scope=scope, sid=report['sid'], task_name=name, task=None,
                         marker=FORMAT + ':OWNED:' + owner, programs={}, active=None, history=[],
                         interval=interval, timeout=timeout, legacy_drained=False, pending=None)
            write_local(root / 'OWNER.json', {'format': FORMAT, 'owner': owner}, exclusive=True)
            save(state_path, state)
            fd = os.open(root / 'installer.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            windows_private(root / 'installer.lock', provision=True)
        else:
            windows_private(root / 'installer.lock')
        if state is None:
            raise ValueError('existing installed-state required')
        with lock(root / 'installer.lock', timeout=0):
            if not fresh:
                revalidate()
            if recovering:
                return finish_interrupted_start(state, state_path, root, runtime, timeout)
            if action in ('install', 'upgrade'):
                if candidate is None:
                    raise ValueError('invalid development BUILD.json schema')
                key = stage(root, candidate, state, state_path)
                if action == 'install':
                    state['active'] = key
                    save(state_path, state)
                    return {'status': 'installed', 'version': key, 'installed_state': str(state_path), 'autostart': False, 'worker_changed': False}
                if key == state['active'] and not replace:
                    return {'status': 'unchanged'}
                # Staging ran while a healthy worker continued; the single approval
                # above covered the binding too, so re-check nothing moved meanwhile.
                baseline_after = read_state(root, state_path)
                if (load(runtime) != (config, scope) or preservation(config, runtime) != baseline
                        or bundle(binary) != candidate or adapter.read(name) != report or baseline_after != state):
                    raise ValueError('binding inputs changed during confirmation')
            else:
                key = state['history'][-1] if action == 'rollback' else state['active']
            if action == 'autostart' and disable:
                # Disable only the logon launcher; it does not stop a running worker.
                return change_task(adapter, state, state_path, enabled=False)
            restarting = False
            if replace:
                if action != 'upgrade':
                    raise ValueError('live replacement is upgrade-only')
                if scope['policy'] == 'legacy' and not legacy_drained:
                    raise ValueError('explicit prior legacy-worker drain approval required')
                state['status'] = 'recovery_required'
                state['pending'] = {'operation': 'replace', 'old': state['active'], 'new': key, 'task': state['task']}
                save(state_path, state)
                managed.stop(runtime, timeout=timeout)  # nonce-cooperative, never PID termination
                restarting = True
            with stopped(config, runtime, scope, legacy_drained, unmanaged_drained):
                if (load(runtime) != (config, scope) or preservation(config, runtime) != baseline
                        or identity(root) != state['root_identity'] or adapter.read(name) != report
                        or parse(read_local(state_path, private=True)) != state):
                    raise ValueError('binding inputs changed while draining')
                directory = absolute(root / key)
                for filename, checksum in state['programs'][key]['files'].items():
                    if digest(directory / filename) != checksum:
                        raise ValueError('installed program changed while draining')
                if action == 'autostart':
                    state['interval'], state['timeout'], state['legacy_drained'] = interval, timeout, legacy_drained
                    result = change_task(adapter, state, state_path, enabled=True)
                elif action == 'uninstall':
                    state['status'] = 'recovery_required'
                    state['pending'] = {'operation': 'uninstall', 'task': state['task'], 'programs': state['programs']}
                    save(state_path, state)
                    if state['task'] is not None:
                        adapter.remove(name, state['sid'], state['task']['xml'])
                        if adapter.read(name)['task'] is not None:
                            raise ValueError('task removal not verified')
                    for version, entry in state['programs'].items():
                        directory = absolute(root / version)
                        if identity(directory) != entry['identity'] or not owned_tree(directory, entry['files']):
                            raise ValueError('uninstall ownership changed')
                        for filename, checksum in entry['files'].items():
                            p = absolute(at(directory, filename))
                            if digest(p) != checksum:
                                raise ValueError('uninstall program changed')
                            p.unlink()
                            if p.exists():
                                raise ValueError('uninstall file removal failed')
                        for folder in sorted(runtime_directories(entry['files']), key=lambda n: n.count('/'), reverse=True):
                            at(directory, folder).rmdir()  # Empty by construction; never recursive.
                        directory.rmdir()
                    state.update(status='uninstalled', task=None, pending=None, programs={})
                    save(state_path, state)
                    return {'status': 'uninstalled', 'data_preserved': True, 'recovery': str(state_path)}
                else:
                    old = state['active']
                    transition = {'operation': action, 'old': old, 'new': key, 'task': state['task']}
                    state['status'] = 'recovery_required'
                    state['pending'] = transition
                    save(state_path, state)
                    state['active'] = key
                    if state['task'] is not None:
                        change_task(adapter, state, state_path, enabled=state['task']['binding']['enabled'], finish=False)
                    if action == 'rollback':
                        state['history'].pop()
                    elif old != key:
                        state['history'].append(old)
                    state['status'] = 'recovery_required' if restarting else 'ready'
                    state['pending'] = {**transition, 'phase': 'start'} if restarting else None
                    save(state_path, state)
                    result = {'status': 'selected', 'version': key, 'worker_changed': False}
            if restarting:
                command = [str(root / key / 'agentmesh.exe'), 'worker-start', '--runtime', str(runtime),
                           '--interval', str(state['interval']), '--timeout', str(timeout)]
                if legacy_drained:
                    command.append('--legacy-drained')
                start_worker(command, timeout + 120)
                health = managed.status(runtime)
                if health['state'] != 'running' or health.get('cycles', 0) < 1 or health.get('last_error'):
                    raise ValueError('replacement healthy cycle not verified')
                nonce = health['nonce']
                first_cycles = health['cycles']
                deadline = time.monotonic() + state['interval'] + timeout
                with Wait('Confirming the new worker completes another sync cycle', state['interval'] + timeout,
                          done='New worker healthy'):
                    while time.monotonic() < deadline:
                        health = managed.status(runtime)
                        if health['state'] != 'running' or health.get('nonce') != nonce or health.get('last_error'):
                            raise ValueError('replacement recurring worker health failed')
                        if health['cycles'] > first_cycles:
                            break
                        time.sleep(0.1)
                    else:
                        raise TimeoutError('replacement recurring cycle not verified; recovery retained')
                state.update(status='ready', pending=None)
                save(state_path, state)
                result['worker_changed'] = True
            if digest(runtime) != state['runtime_hash'] or identity(config['database']) != state['database_identity']:
                raise ValueError('runtime/database preservation failed')
            return result

    with uninterruptible():
        return apply()


def change_task(adapter, state, state_path, *, enabled, finish=True):
    previous = state['task']
    state['status'] = 'recovery_required'
    state['pending'] = {'operation': 'task', 'previous': previous, 'transition': state.get('pending')}
    save(state_path, state)
    definition = windows_task.binding(state['sid'], state['marker'], task_entry(state, state['active']),
        state['runtime'], state['interval'], state['timeout'], state['legacy_drained'], enabled)
    with step('Updating the start-at-login task', 'Start-at-login task ' + ('enabled' if enabled else 'disabled')):
        task = adapter.register(state['task_name'], state['sid'], previous['xml'] if previous else None, definition)
        report = adapter.read(state['task_name'])
        if report != {'sid': state['sid'], 'task': task} or task['binding'] != definition or task['sid'] != state['sid']:
            raise ValueError('task binding readback failed')
    state['task'] = task
    if finish:
        state.update(status='ready', pending=None)
    save(state_path, state)
    return {'status': 'enabled' if enabled else 'disabled', 'worker_changed': False}
