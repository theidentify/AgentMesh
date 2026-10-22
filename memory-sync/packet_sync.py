"""Reversible packet transport switch; profile-log sync is independent."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid


def home():
    return Path(os.environ.get('AGENTMESH_HOME', str(Path.home() / '.agentmesh')))


def enabled(root=None):
    root = Path(root) if root is not None else home()
    value = False
    for name in ('settings.json', 'settings.local.json'):
        path = root / name
        if not path.exists(): continue
        settings = json.loads(path.read_text(encoding='utf-8'))
        setting = settings.get('packet_sync', {})
        if type(setting) is not dict or set(setting) - {'enabled'}:
            raise ValueError('invalid packet_sync settings')
        if 'enabled' in setting:
            if type(setting['enabled']) is not bool: raise ValueError('invalid packet_sync.enabled')
            value = setting['enabled']
    return value


def workflow_needed(database, workflow_config=None):
    import workflow
    if workflow_config is not None and not Path(workflow_config).is_file():
        raise ValueError('cannot read configured workflow; worker must be retained')
    settings = workflow.load(database, workflow_config)
    return settings is not None and (settings.get('ingest', True) or settings.get('summarize', False))


def runtime_workflow(runtime):
    try:
        config = json.loads(Path(runtime).read_text(encoding='utf-8'))
        if type(config) is not dict or type(config.get('database')) is not str or not Path(config['database']).is_absolute():
            raise ValueError('invalid runtime database')
        filename = config.get('workflow_config')
        if filename is not None and (type(filename) is not str or not Path(filename).is_absolute()):
            raise ValueError('invalid runtime workflow')
        return workflow_needed(config['database'], filename)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('cannot read runtime/workflow configuration; refusing to unload worker') from exc


def configure(action, *, root=None, system=None, runner=None, uid=None, task_name=None,
              installed_state=None, dry_run=False, legacy_drained=False, unmanaged_drained=False, runtime=None):
    if action not in ('retire', 'enable'): raise ValueError('invalid packet-sync action')
    root = Path(root) if root is not None else home()
    system = system or platform.system()
    runner = runner or subprocess.run
    value = action == 'enable'
    commands = []
    if system == 'Darwin' and not value:
        commands = [['launchctl', 'bootout', 'gui/' + str(os.getuid() if uid is None else uid) + '/org.agentmesh.sync']]
    elif system == 'Windows':
        if installed_state is not None:
            state = json.loads(Path(installed_state).read_text(encoding='utf-8'))
            recorded = state.get('task_name')
            if task_name is not None and task_name != recorded: raise ValueError('task name differs from installed state')
            task_name = recorded
            bound_runtime = state.get('runtime')
            if runtime is not None and Path(runtime).resolve() != Path(bound_runtime).resolve():
                raise ValueError('runtime differs from installed state')
            runtime = bound_runtime
            if type(runtime) is not str or not Path(runtime).is_absolute():
                raise ValueError('invalid installed runtime')
            entry = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).with_name('agentmesh.py'))]
            commands = [[*entry, 'windows-autostart', '--runtime', runtime,
                         '--program-root', str(Path(installed_state).resolve().parent),
                         '--installed-state', str(Path(installed_state).resolve()),
                         '--enable' if value else '--disable', '--yes']]
            if legacy_drained: commands[0].append('--legacy-drained')
            if unmanaged_drained: commands[0].append('--unmanaged-drained')
        if type(task_name) is not str or not task_name.strip() or any(ord(c) < 32 for c in task_name):
            raise ValueError('Windows requires --task-name or --installed-state')
        if installed_state is None:
            commands = [['schtasks', '/Change', '/TN', task_name, '/ENABLE' if value else '/DISABLE']]
    elif system == 'Linux' and not value:
        commands = [['systemctl', '--user', 'disable', '--now', 'agentmesh.service']]
    retained = not value and runtime_workflow(runtime or root / 'memory-sync' / 'data' / 'runtime.json')
    if retained: commands = []
    path = root / 'settings.local.json'
    if any(p.is_symlink() for p in (path, root, *root.parents)): raise ValueError('settings path cannot be a symlink')
    settings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    settings['packet_sync'] = {'enabled': value}
    report = {'packet_sync': 'enabled' if value else 'retired', 'dry_run': dry_run,
              'settings': str(path), 'commands': [], 'unit_files': 'retained', 'exchange': 'untouched'}
    if retained: report['worker'] = 'retained for workflow ingest/summary; packet work disabled'
    if not dry_run:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = root / ('.packet-sync-' + uuid.uuid4().hex)
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(settings, stream, sort_keys=True); stream.write('\n')
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, path)
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump(dict(format='agentmesh-packet-sync-status-v1',packet_sync=report['packet_sync']),stream)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temp,root/'packet-sync-status.json')
        finally:
            temp.unlink(missing_ok=True)
    for command in commands:
        entry = {'argv': command, 'status': 'planned'}
        if not dry_run:
            result = runner(command, capture_output=True, text=True, check=False)
            code = result.returncode
            absent = system == 'Darwin' and code == 3
            entry.update(returncode=code, status='not-loaded' if absent else 'done' if code == 0 else 'failed')
        report['commands'].append(entry)
    if value and system != 'Windows':
        report['next'] = 'Regenerate and load the packet sync unit using platform_service.py generate.'
    return report
