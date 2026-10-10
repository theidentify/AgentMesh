"""Real disposable filesystem/SQLite preservation; fake only the task OS boundary."""
from contextlib import closing
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import threading

import pytest
import memory_sync
import sqlite_memory
import windows_install as installer
import windows_task
from signed_packets import init_identity, write_local

SID = 'S-1-5-21-1-2-3-1001'


class FakeTasks:
    def __init__(self):
        self.tasks = {}
        self.operations = []

    def read(self, name):
        return {'sid': SID, 'task': copy.deepcopy(self.tasks.get(name))}

    def register(self, name, sid, expected, definition):
        old = self.tasks.get(name)
        assert (old['xml'] if old else None) == expected
        assert sid == SID
        task = {'sid': sid, 'xml': json.dumps(definition, sort_keys=True), 'binding': copy.deepcopy(definition)}
        self.tasks[name] = task
        self.operations.append('register')
        return copy.deepcopy(task)

    def remove(self, name, sid, expected):
        assert self.tasks[name]['xml'] == expected and sid == SID
        del self.tasks[name]
        self.operations.append('remove')


ONEDIR = ('_internal/python311.dll', '_internal/base_library.zip', '_internal/cryptography/hazmat/_rust.pyd')


def package(parent, version='0.2.0-rc.5', sha='a' * 40, launcher=False, onedir=False):
    directory = parent / (version + '-bundle')
    directory.mkdir()
    binary = directory / 'agentmesh.exe'
    binary.write_bytes(b'disposable binary fixture ' + sha.encode())
    checksums = {'agentmesh.exe': installer.digest(binary)}
    if launcher:
        (directory / 'agentmeshw.exe').write_bytes(b'disposable windowless launcher fixture ' + sha.encode())
        checksums['agentmeshw.exe'] = installer.digest(directory / 'agentmeshw.exe')
    for name in ONEDIR if onedir else ():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(b'disposable onedir runtime fixture ' + name.encode() + sha.encode())
        checksums[name] = installer.digest(directory / name)
    metadata = dict(version=version, source_sha=sha, system='windows', architecture='amd64',
                    checksums=checksums, distribution=installer.DEVELOPMENT,
                    verification='fixture only, no native verification claim',
                    scope='program files only; no database, keys, runtime, snapshots or deployment secrets')
    (directory / 'BUILD.json').write_text(json.dumps(metadata))
    return binary


@pytest.fixture
def existing(tmp_path):
    root = tmp_path / 'existing'
    installer.protection(root)
    data = root / 'data'
    installer.protection(data)
    db = data / 'windows.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'windows', '00000000-0000-4000-8000-000000000001')
    # Stable durable-byte fixture. A separate orphan-WAL test covers zero-write
    # informational planning; mutation-time authoritative reads may use sidecars.
    with closing(sqlite3.connect(db)) as c:
        c.execute('PRAGMA journal_mode=DELETE')
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    identity = root / 'identity'
    init_identity(identity, '00000000-0000-4000-8000-000000000001', 'windows')
    write_local(data / 'workflow.json', {'ingest': False, 'summarize': False})
    write_local(data / 'security-state.json', {'pending': 'preserve'})
    config = dict(node='windows', app_root=str(root), database=str(db), exchange=str(exchange),
                  security_dir=str(identity), security_state=str(data / 'security-state.json'),
                  workflow_config=str(data / 'workflow.json'))
    runtime = data / 'runtime.json'
    write_local(runtime, config)
    return dict(runtime=runtime, program_root=tmp_path / 'programs', binary=package(tmp_path), adapter=FakeTasks())


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_dev, p.stat().st_ino, p.read_bytes())
            for p in root.rglob('*') if p.is_file()}


def command(args, monkeypatch, action='install', answer='INSTALL\n', **options):
    monkeypatch.setattr('sys.stdin', io.StringIO(answer))
    return installer.run(action, **{**args, **options})


def test_install_preserves_existing_all_bytes_and_never_starts_or_registers(existing, monkeypatch):
    before = snapshot(existing['runtime'].parent.parent)
    assert command(existing, monkeypatch)['status'] == 'installed'
    assert snapshot(existing['runtime'].parent.parent) == before
    assert existing['adapter'].operations == []
    state = installer.read_state(existing['program_root'], existing['program_root'] / 'installed.json')
    assert state['scope']['policy'] == 'legacy'
    installed = existing['program_root'] / state['active'] / 'agentmesh.exe'
    assert installed.read_bytes() == existing['binary'].read_bytes()
    assert not (existing['runtime'].parent / '.agentmesh-worker').exists()


@pytest.mark.parametrize('answer', ['NO\n', ''])
def test_decline_eof_and_dry_run_zero_writes(existing, monkeypatch, answer):
    root = existing['runtime'].parent.parent.parent
    before = snapshot(root)
    assert installer.run('install', **existing, dry_run=True)['status'] == 'planned'
    monkeypatch.setattr('sys.stdin', io.StringIO(answer))
    if answer:
        assert installer.run('install', **existing)['status'] == 'pending'
    else:
        with pytest.raises(EOFError):
            installer.run('install', **existing)
    assert snapshot(root) == before
    assert not existing['program_root'].exists()


@pytest.mark.parametrize('damage', ['runtime', 'binary', 'scope', 'exchange', 'task'])
def test_prompt_time_revalidation(existing, monkeypatch, damage):
    class Input:
        def readline(self):
            if damage == 'runtime':
                write_local(existing['runtime'], {'secret': 'changed'})
            elif damage == 'binary':
                existing['binary'].write_bytes(b'changed')
            elif damage == 'scope':
                config = json.loads(existing['runtime'].read_bytes())
                with closing(sqlite3.connect(config['database'])) as c, c:
                    c.execute("UPDATE _sync_config SET group_id='00000000-0000-4000-8000-000000000002'")
            elif damage == 'exchange':
                exchange = existing['runtime'].parent.parent.parent / 'exchange'
                exchange.rename(exchange.with_name('old'))
                (exchange / '.stfolder').mkdir(parents=True)
            else:
                name = 'AgentMesh-' + __import__('hashlib').sha256(str(existing['runtime']).encode()).hexdigest()[:24]
                existing['adapter'].tasks[name] = {'foreign': True}
            return 'INSTALL\n'
    monkeypatch.setattr('sys.stdin', Input())
    with pytest.raises((ValueError, KeyError)):
        installer.run('install', **existing)
    assert not existing['program_root'].exists()
    assert existing['adapter'].operations == []


def test_missing_runtime_scope_and_foreign_task_refused(existing, monkeypatch):
    args = {**existing, 'runtime': existing['runtime'].with_name('missing.json')}
    with pytest.raises(FileNotFoundError):
        command(args, monkeypatch)
    config = json.loads(existing['runtime'].read_bytes())
    config['node'] = 'mac'
    write_local(existing['runtime'], config)
    with pytest.raises(ValueError, match='scope mismatch'):
        command(existing, monkeypatch)
    config['node'] = 'windows'
    write_local(existing['runtime'], config)
    name = 'AgentMesh-fixture'
    existing['adapter'].tasks[name] = {'foreign': True}
    with pytest.raises(ValueError, match='foreign'):
        command(existing, monkeypatch, task_name=name)
    assert not existing['program_root'].exists()


@pytest.mark.parametrize('target', ['exchange', 'data', 'identity', 'ancestor'])
def test_overlapping_program_root_refused(existing, monkeypatch, target):
    app = existing['runtime'].parent.parent
    roots = {'exchange': app.parent / 'exchange/programs', 'data': app / 'data/programs',
             'identity': app / 'identity/programs', 'ancestor': app.parent}
    with pytest.raises(ValueError, match='overlaps'):
        command(existing, monkeypatch, program_root=roots[target])


@pytest.mark.skipif(os.name == 'nt', reason='native junction refusal covered separately; POSIX symlink fixture')
def test_symlink_program_root_refused(existing, monkeypatch):
    real = existing['program_root'].with_name('real')
    real.mkdir()
    existing['program_root'].symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        command(existing, monkeypatch)


def test_opt_in_task_upgrade_rollback_uninstall_preserves_db_identity(existing, monkeypatch):
    command(existing, monkeypatch)
    initial = snapshot(existing['runtime'].parent.parent)
    assert command(existing, monkeypatch, 'autostart', 'ENABLE\n', enable=True, legacy_drained=True)['status'] == 'enabled'
    state = installer.read_state(existing['program_root'], existing['program_root'] / 'installed.json')
    binding = state['task']['binding']
    assert binding['logon'] == 3 and binding['level'] == 0 and binding['user'] == SID
    assert not binding['terminate'] and not binding['battery_start'] and not binding['battery_stop']
    assert binding['restart_count'] == 0 and binding['multiple'] == 2 and not binding['demand']
    assert binding['triggers'][0]['user'] == SID
    assert '--legacy-drained' in binding['actions'][0]['arguments']
    old = state['active']
    newer = package(existing['binary'].parent.parent, '0.2.0-rc.6', 'b' * 40)
    assert command(existing, monkeypatch, 'upgrade', 'UPGRADE\nBIND\n', binary=newer, legacy_drained=True)['status'] == 'selected'
    assert command(existing, monkeypatch, 'rollback', 'ROLLBACK\n', legacy_drained=True)['status'] == 'selected'
    assert installer.read_state(existing['program_root'], existing['program_root'] / 'installed.json')['active'] == old
    assert command(existing, monkeypatch, 'autostart', 'DISABLE\n', disable=True)['status'] == 'disabled'
    assert command(existing, monkeypatch, 'uninstall', 'UNINSTALL\n', legacy_drained=True)['status'] == 'uninstalled'
    assert existing['adapter'].tasks == {}
    assert {p.name for p in existing['program_root'].iterdir()} == {'installed.json', 'installer.lock', 'OWNER.json'}
    after = snapshot(existing['runtime'].parent.parent)
    assert all(after[k] == v for k, v in initial.items())
    assert json.loads((existing['program_root'] / 'installed.json').read_bytes())['status'] == 'uninstalled'


def test_upgrade_stages_while_running_without_implicit_replacement(existing, monkeypatch):
    command(existing, monkeypatch)
    monkeypatch.setattr(installer.managed, 'status', lambda runtime: {'state': 'running', 'cycles': 3})
    newer = package(existing['binary'].parent.parent, '0.2.0-rc.6', 'b' * 40)
    before = snapshot(existing['runtime'].parent.parent)
    result = command(existing, monkeypatch, 'upgrade', 'UPGRADE\nNO\n', binary=newer)
    assert result['status'] == 'staged' and not result['worker_changed']
    assert snapshot(existing['runtime'].parent.parent) == before
    with pytest.raises(ValueError, match='proven stopped'):
        command(existing, monkeypatch, 'upgrade', 'UPGRADE\nBIND\n', binary=newer, legacy_drained=True)
    assert not existing['adapter'].operations


@pytest.mark.parametrize('damage', ['extra', 'binary', 'task', 'runtime', 'db_identity'])
def test_uninstall_refuses_uncertain_ownership(existing, monkeypatch, damage):
    command(existing, monkeypatch)
    root = existing['program_root']
    state = installer.read_state(root, root / 'installed.json')
    if damage == 'extra':
        (root / 'user-file.txt').write_text('preserve me')
    elif damage == 'binary':
        (root / state['active'] / 'agentmesh.exe').write_bytes(b'changed')
    elif damage == 'task':
        existing['adapter'].tasks[state['task_name']] = {'foreign': True}
    elif damage == 'runtime':
        write_local(existing['runtime'], {**json.loads(existing['runtime'].read_bytes()), 'new': True})
    else:
        db = Path(json.loads(existing['runtime'].read_bytes())['database'])
        db.rename(db.with_suffix('.old'))
        db.write_bytes(db.with_suffix('.old').read_bytes())
    before = snapshot(root)
    with pytest.raises(ValueError):
        command(existing, monkeypatch, 'uninstall', 'UNINSTALL\n', legacy_drained=True)
    assert snapshot(root) == before


def test_concurrent_operator_lock_refuses_without_program_switch(existing, monkeypatch):
    command(existing, monkeypatch)
    ready = threading.Event(); release = threading.Event()
    def holder():
        with installer.lock(existing['program_root'] / 'installer.lock', timeout=0):
            ready.set(); release.wait(10)
    thread = threading.Thread(target=holder)
    thread.start(); assert ready.wait(5)
    try:
        with pytest.raises(TimeoutError):
            command(existing, monkeypatch, 'autostart', 'ENABLE\n', enable=True, legacy_drained=True)
        assert existing['adapter'].operations == []
    finally:
        release.set(); thread.join(5)


def test_legacy_approval_not_inferred_and_lifetime_lock_blocks_mutation(existing, monkeypatch):
    command(existing, monkeypatch)
    with pytest.raises(ValueError, match='explicit prior legacy'):
        command(existing, monkeypatch, 'autostart', 'ENABLE\n', enable=True)
    config = json.loads(existing['runtime'].read_bytes())
    directory = installer.managed.control(config, create=True)
    with installer.lock(directory / 'lifetime.lock', timeout=0):
        with pytest.raises(TimeoutError):
            command(existing, monkeypatch, 'autostart', 'ENABLE\n', enable=True, legacy_drained=True)
    assert existing['adapter'].operations == []


def test_failed_readback_retains_private_recovery_descriptor(existing, monkeypatch):
    command(existing, monkeypatch)
    class Broken(FakeTasks):
        def register(self, *args, **kwargs):
            super().register(*args, **kwargs)
            raise ValueError('fixture readback failure')
    with pytest.raises(ValueError, match='readback'):
        command(existing, monkeypatch, 'autostart', 'ENABLE\n', enable=True, legacy_drained=True, adapter=Broken())
    state = json.loads((existing['program_root'] / 'installed.json').read_bytes())
    assert state['status'] == 'recovery_required' and state['pending']['operation'] == 'task'
    with pytest.raises(ValueError, match='recovery'):
        command(existing, monkeypatch, 'uninstall', 'UNINSTALL\n', legacy_drained=True)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX verifies native production gate')
def test_cli_cannot_bypass_windows_gate(existing, capsys):
    import agentmesh
    assert agentmesh.main(['windows-install', '--runtime', str(existing['runtime']), '--binary', str(existing['binary']), '--dry-run']) == 1
    assert not existing['program_root'].exists()


def test_orphan_wal_dry_run_decline_and_eof_have_no_side_effects(existing, monkeypatch):
    config = json.loads(existing['runtime'].read_bytes())
    db = Path(config['database'])
    with closing(sqlite3.connect(db)) as c, c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE installer_informational_probe(value TEXT)')
        wal = db.with_name(db.name + '-wal').read_bytes()
    db.with_name(db.name + '-wal').write_bytes(wal)
    assert not db.with_name(db.name + '-shm').exists()
    before = snapshot(existing['runtime'].parent.parent)
    assert installer.run('install', **existing, dry_run=True)['status'] == 'planned'
    assert command(existing, monkeypatch, answer='NO\n')['status'] == 'pending'
    with pytest.raises(EOFError):
        command(existing, monkeypatch, answer='')
    assert snapshot(existing['runtime'].parent.parent) == before
    assert not db.with_name(db.name + '-shm').exists()


def test_strict_scope_preserves_identity_trust_and_policy(existing, monkeypatch):
    from signed_packets import Security
    config = json.loads(existing['runtime'].read_bytes())
    # Explicit fixture setup only; the installer must never call this guard.
    with closing(sqlite3.connect(config['database'])) as c, c:
        c.row_factory = sqlite3.Row
        Security(config['security_dir']).guard(c)
    before = snapshot(existing['runtime'].parent.parent)
    assert command(existing, monkeypatch)['status'] == 'installed'
    assert command(existing, monkeypatch, 'autostart', 'ENABLE\n', enable=True, unmanaged_drained=True)['status'] == 'enabled'
    state = installer.read_state(existing['program_root'], existing['program_root'] / 'installed.json')
    assert state['scope']['policy'] == 'required' and state['scope']['key_id']
    assert '--legacy-drained' not in state['task']['binding']['actions'][0]['arguments']
    after = snapshot(existing['runtime'].parent.parent)
    assert all(after[k] == value for k, value in before.items())


def test_replace_stop_timeout_retains_descriptor_and_never_launches_new_binary(existing, monkeypatch):
    command(existing, monkeypatch)
    next_binary = package(existing['binary'].parent.parent, '0.2.0-rc.6', 'b' * 40)
    def stop(runtime, timeout):
        raise TimeoutError('fixture cooperative acknowledgement timeout')
    monkeypatch.setattr(installer.managed, 'stop', stop)
    def forbidden(*args, **kwargs):
        pytest.fail('replacement launched before proven cooperative stop')
    monkeypatch.setattr(installer, 'start_worker', forbidden)
    with pytest.raises(TimeoutError, match='cooperative'):
        command(existing, monkeypatch, 'upgrade', 'UPGRADE\nREPLACE\n', binary=next_binary, replace=True, legacy_drained=True)
    state = json.loads((existing['program_root'] / 'installed.json').read_bytes())
    assert state['status'] == 'recovery_required'
    assert state['pending']['operation'] == 'replace' and state['pending']['old'] != state['pending']['new']
    assert state['active'] == state['pending']['old']
    assert existing['adapter'].operations == []


def test_program_rollback_never_restores_database_updates(existing, monkeypatch):
    command(existing, monkeypatch)
    next_binary = package(existing['binary'].parent.parent, '0.2.0-rc.6', 'b' * 40)
    command(existing, monkeypatch, 'upgrade', 'UPGRADE\nBIND\n', binary=next_binary, legacy_drained=True)
    config = json.loads(existing['runtime'].read_bytes())
    db = Path(config['database'])
    with closing(sqlite3.connect(db)) as c, c:
        c.execute('CREATE TABLE installer_preservation_probe(id INTEGER PRIMARY KEY, value TEXT)')
        c.execute("INSERT INTO installer_preservation_probe VALUES(1, 'new committed work')")
    before = snapshot(existing['runtime'].parent.parent)
    command(existing, monkeypatch, 'rollback', 'ROLLBACK\n', legacy_drained=True)
    assert snapshot(existing['runtime'].parent.parent) == before
    with closing(sqlite3.connect(db)) as c:
        assert c.execute('SELECT * FROM installer_preservation_probe').fetchall() == [(1, 'new committed work')]
        assert c.execute('PRAGMA integrity_check').fetchone() == ('ok',)


def test_installer_public_errors_are_specific_but_secret_safe():
    from cli_errors import report
    known = report(ValueError('foreign Scheduled Task refused'), 'windows-install')
    assert known['reason'] == 'foreign Scheduled Task refused' and known['code'] == 'GUARD_REFUSED'
    assert 'do not retry blindly' in known['next_action']
    unknown = report(ValueError('secret runtime body or token'), 'windows-install')
    assert 'secret runtime body or token' not in json.dumps(unknown)
    assert report(ValueError('foreign Scheduled Task refused'), 'status') == {'error': 'ValueError'}


def test_same_selected_version_requires_replace_gate_and_cooperative_stop(existing, monkeypatch):
    command(existing, monkeypatch)
    called = []
    def stop(runtime, timeout):
        called.append(runtime)
        raise TimeoutError('fixture stop acknowledged no exit yet')
    monkeypatch.setattr(installer.managed, 'stop', stop)
    assert command(existing, monkeypatch, 'upgrade', 'UPGRADE\nNO\n', replace=True,
                   legacy_drained=True)['status'] == 'staged'
    assert called == []
    with pytest.raises(TimeoutError, match='no exit yet'):
        command(existing, monkeypatch, 'upgrade', 'UPGRADE\nREPLACE\n', replace=True, legacy_drained=True)
    assert called == [existing['runtime']]
    state = json.loads((existing['program_root'] / 'installed.json').read_bytes())
    assert state['pending']['operation'] == 'replace'
    assert state['pending']['old'] == state['pending']['new']


@pytest.mark.parametrize('action,options,answer', [
    ('autostart', {'enable': True}, 'ENABLE\n'),
    ('uninstall', {}, 'UNINSTALL\n'),
])
def test_strict_unmanaged_worker_needs_approval_and_cycle_lock_drain(existing, monkeypatch, action, options, answer):
    from signed_packets import Security
    config = json.loads(existing['runtime'].read_bytes())
    with closing(sqlite3.connect(config['database'])) as c, c:
        c.row_factory = sqlite3.Row
        Security(config['security_dir']).guard(c)
    command(existing, monkeypatch)
    with pytest.raises(ValueError, match='unmanaged-worker drain approval'):
        command(existing, monkeypatch, action, answer, **options)
    with installer.lock(Path(config['database']).parent / 'worker.lock', timeout=0):
        with pytest.raises(TimeoutError, match='cycle did not drain'):
            command(existing, monkeypatch, action, answer, unmanaged_drained=True, **options)
    assert existing['adapter'].operations == []
    state = installer.read_state(existing['program_root'], existing['program_root'] / 'installed.json')
    assert (existing['program_root'] / state['active'] / 'agentmesh.exe').is_file()


@pytest.mark.parametrize('newer', [False, True])
def test_failed_start_keeps_previous_exact_task_and_does_not_self_duplicate_history(existing, monkeypatch, newer):
    command(existing, monkeypatch)
    command(existing, monkeypatch, 'autostart', 'DISABLE\n', disable=True)
    state_path = existing['program_root'] / 'installed.json'
    original = installer.read_state(existing['program_root'], state_path)
    candidate = package(existing['binary'].parent.parent, '0.2.0-rc.6', 'b' * 40) if newer else existing['binary']
    def launch(*args, **kwargs):
        raise OSError('fixture executable launch failed')
    monkeypatch.setattr(installer, 'start_worker', launch)
    with pytest.raises(OSError, match='launch failed'):
        command(existing, monkeypatch, 'upgrade', 'UPGRADE\nREPLACE\n', binary=candidate, replace=True, legacy_drained=True)
    retained = json.loads(state_path.read_bytes())
    assert retained['status'] == 'recovery_required'
    assert retained['pending']['phase'] == 'start'
    assert retained['pending']['task'] == original['task']
    assert retained['history'] == ([original['active']] if newer else [])
    if newer:
        assert retained['task'] != original['task']


def task_path(existing):
    task = existing['adapter'].tasks[installer.read_state(existing['program_root'], existing['program_root'] / 'installed.json')['task_name']]
    return Path(task['binding']['actions'][0]['path']), task['binding']['actions'][0]['arguments']


def test_logon_task_uses_windowless_launcher_when_bundled_and_rollback_keeps_console_entry(existing, monkeypatch):
    command(existing, monkeypatch)
    command(existing, monkeypatch, 'autostart', 'ENABLE\n', enable=True, legacy_drained=True)
    path, arguments = task_path(existing)
    assert path.name == 'agentmesh.exe' and '--legacy-drained' in arguments  # rc.5-shaped bundle has no launcher
    newer = package(existing['binary'].parent.parent, '0.2.0-rc.6', 'b' * 40, launcher=True)
    assert command(existing, monkeypatch, 'upgrade', 'UPGRADE\nBIND\n', binary=newer, legacy_drained=True)['status'] == 'selected'
    state = installer.read_state(existing['program_root'], existing['program_root'] / 'installed.json')
    assert set(state['programs'][state['active']]['files']) == {'agentmesh.exe', 'agentmeshw.exe', 'BUILD.json'}
    path, arguments = task_path(existing)
    assert path == existing['program_root'] / state['active'] / 'agentmeshw.exe'
    assert path.read_bytes() == (newer.parent / 'agentmeshw.exe').read_bytes()
    assert arguments.startswith('worker-start ') and '--legacy-drained' in arguments
    assert command(existing, monkeypatch, 'rollback', 'ROLLBACK\n', legacy_drained=True)['status'] == 'selected'
    assert task_path(existing)[0].name == 'agentmesh.exe'


def test_launcher_checksum_mismatch_refused(existing, monkeypatch):
    command(existing, monkeypatch)
    newer = package(existing['binary'].parent.parent, '0.2.0-rc.6', 'b' * 40, launcher=True)
    (newer.parent / 'agentmeshw.exe').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='program checksum mismatch'):
        command(existing, monkeypatch, 'upgrade', 'UPGRADE\nBIND\n', binary=newer, legacy_drained=True)


def installed_tree(directory):
    return {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}


def test_onedir_bundle_installs_verified_tree_and_rolls_back_to_flat(existing, monkeypatch):
    command(existing, monkeypatch)
    command(existing, monkeypatch, 'autostart', 'ENABLE\n', enable=True, legacy_drained=True)
    newer = package(existing['binary'].parent.parent, '0.2.0-rc.8', 'c' * 40, launcher=True, onedir=True)
    assert command(existing, monkeypatch, 'upgrade', 'UPGRADE\nBIND\n', binary=newer, legacy_drained=True)['status'] == 'selected'
    state = installer.read_state(existing['program_root'], existing['program_root'] / 'installed.json')
    directory = existing['program_root'] / state['active']
    assert installed_tree(directory) == {'agentmesh.exe', 'agentmeshw.exe', 'BUILD.json', *ONEDIR}
    for name in ONEDIR:
        assert (directory / name).read_bytes() == (newer.parent / name).read_bytes()
    assert task_path(existing)[0] == directory / 'agentmeshw.exe'
    assert command(existing, monkeypatch, 'rollback', 'ROLLBACK\n', legacy_drained=True)['status'] == 'selected'
    assert task_path(existing)[0].name == 'agentmesh.exe'


def test_onedir_unlisted_runtime_file_blocks_every_operation(existing, monkeypatch):
    command(existing, monkeypatch)
    newer = package(existing['binary'].parent.parent, '0.2.0-rc.8', 'c' * 40, launcher=True, onedir=True)
    command(existing, monkeypatch, 'upgrade', 'UPGRADE\nBIND\n', binary=newer, legacy_drained=True)
    state_path = existing['program_root'] / 'installed.json'
    active = existing['program_root'] / installer.read_state(existing['program_root'], state_path)['active']
    (active / '_internal' / 'planted.dll').write_bytes(b'not listed in BUILD.json')
    with pytest.raises(ValueError, match='installed program directory changed'):
        installer.read_state(existing['program_root'], state_path)


@pytest.mark.parametrize('name', ['_internal/../agentmesh.exe', '../outside.dll', '_internal\\x.dll', '/abs.dll', 'other/x.dll', '_internal//x.dll'])
def test_bundle_refuses_unsafe_or_foreign_program_paths(existing, monkeypatch, name):
    newer = package(existing['binary'].parent.parent, '0.2.0-rc.8', 'c' * 40, onedir=True)
    manifest = newer.parent / 'BUILD.json'
    metadata = json.loads(manifest.read_text())
    metadata['checksums'][name] = 'e' * 64
    manifest.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='invalid BUILD.json checksum'):
        installer.bundle(newer)


def test_onedir_uninstall_removes_only_owned_tree(existing, monkeypatch):
    command(existing, monkeypatch)
    newer = package(existing['binary'].parent.parent, '0.2.0-rc.8', 'c' * 40, launcher=True, onedir=True)
    command(existing, monkeypatch, 'upgrade', 'UPGRADE\nBIND\n', binary=newer, legacy_drained=True)
    assert command(existing, monkeypatch, 'uninstall', 'UNINSTALL\n', legacy_drained=True)['status'] == 'uninstalled'
    assert sorted(p.name for p in existing['program_root'].iterdir()) == ['OWNER.json', 'installed.json', 'installer.lock']
