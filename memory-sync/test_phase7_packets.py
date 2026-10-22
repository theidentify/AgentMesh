import json
from types import SimpleNamespace

import pytest

import packet_sync
import platform_service
import sync_worker


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path / 'home'))
    (tmp_path / 'runtime.json').write_text(json.dumps({'database': str(tmp_path / 'staging.db')}))


def test_p7_2_full_unit_cycle_is_retired(tmp_path, monkeypatch, capsys):
    database = tmp_path / 'staging.db'
    exchange = tmp_path / 'exchange'
    (exchange / 'changes' / 'mac').mkdir(parents=True)
    def forbidden(*args, **kwargs): raise AssertionError('packet cycle entered')
    monkeypatch.setattr(sync_worker, '_run_once', forbidden)
    assert sync_worker.run_once(database, exchange)['packet_sync'] == 'retired'
    assert sync_worker.main([str(database), str(exchange), '--once']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])['packet_sync'] == 'retired'
    assert list((exchange / 'changes' / 'mac').iterdir()) == []
    assert not database.exists()


@pytest.mark.parametrize('system', ['macos', 'linux'])
def test_p7_2_generate_emits_no_sync_unit(tmp_path, system):
    output = tmp_path / 'service'
    assert platform_service.main(['generate', '--platform', system, '--python', str(tmp_path / 'python'),
        '--app-dir', str(tmp_path), '--database', str(tmp_path / 'staging.db'),
        '--exchange', str(tmp_path / 'exchange'), '--output', str(output)]) == 0
    assert not output.exists()


def test_p7_2_retire_fake_bootout_preserves_exchange(tmp_path):
    root = tmp_path / 'home'
    exchange = root / 'exchange'
    exchange.mkdir(parents=True)
    marker = exchange / 'retained'
    marker.write_bytes(b'unchanged')
    (root / 'settings.json').write_text(json.dumps({'packet_sync': {'enabled': True}}))
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=3)
    report = packet_sync.configure('retire', system='Darwin', uid=42, runner=runner, runtime=tmp_path / 'runtime.json')
    assert calls == [['launchctl', 'bootout', 'gui/42/org.agentmesh.sync']]
    assert report['commands'][0]['status'] == 'not-loaded'
    assert not packet_sync.enabled()
    assert marker.read_bytes() == b'unchanged'
    assert report['unit_files'] == 'retained'


def test_p7_2_windows_recorded_task_only(tmp_path):
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)
    for action in ('retire', 'enable'):
        packet_sync.configure(action, system='Windows', task_name='AgentMesh-recorded', runner=runner, runtime=tmp_path / 'runtime.json')
    assert calls == [['schtasks', '/Change', '/TN', 'AgentMesh-recorded', '/DISABLE'],
                     ['schtasks', '/Change', '/TN', 'AgentMesh-recorded', '/ENABLE']]
    with pytest.raises(ValueError): packet_sync.configure('retire', system='Windows', runner=runner)
    assert len(calls) == 2


def test_p7_2_dry_run_writes_nothing(tmp_path):
    def forbidden(*args, **kwargs): raise AssertionError('real runner')
    report = packet_sync.configure('retire', system='Darwin', uid=42, runner=forbidden, dry_run=True, runtime=tmp_path / 'runtime.json')
    assert report['commands'][0]['status'] == 'planned'
    assert not (tmp_path / 'home').exists()


def test_p7_6_packet_rollback_restores_cycle_and_generation(tmp_path):
    import memory_sync
    import sqlite_memory
    root = tmp_path / 'home'
    root.mkdir()
    (root / 'settings.local.json').write_text(json.dumps({'bridge': {'recall_fallback': True}}))
    packet_sync.configure('enable', system='Darwin')
    assert json.loads((root / 'settings.local.json').read_text())['bridge']['recall_fallback']
    database = tmp_path / 'staging.db'
    sqlite_memory.init_database(database)
    memory_sync.initialize(database, 'mac', '00000000-0000-4000-8000-000000000001')
    with memory_sync.connect(database) as c, c:
        c.execute('BEGIN IMMEDIATE')
        c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES (?,'fact','global','rollback row')", (memory_sync.allocate_id(c, 'memory_items'),))
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    report = sync_worker.run_once(database, exchange)
    assert report['packet_sync'] == 'enabled'
    assert not sync_worker.failed(report)
    assert list((exchange / 'changes' / 'mac').iterdir())
    output = tmp_path / 'sync.plist'
    assert platform_service.main(['generate', '--platform', 'macos', '--python', str(tmp_path / 'python'),
        '--app-dir', str(tmp_path), '--database', str(database), '--exchange', str(exchange),
        '--output', str(output)]) == 0
    assert output.exists()


def test_p7_2_cli_retire_reports_runner_failure(tmp_path, monkeypatch, capsys):
    import agentmesh
    monkeypatch.setattr(packet_sync.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(packet_sync.os, 'getuid', lambda: 42, raising=False)
    monkeypatch.setattr(packet_sync.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=5))
    assert agentmesh.main(['packet-sync', 'retire', '--runtime', str(tmp_path / 'runtime.json')]) == 1
    assert json.loads(capsys.readouterr().out)['commands'][0]['status'] == 'failed'


def test_p7_5_agentmesh_forwards_start_at(tmp_path, monkeypatch):
    import agentmesh
    import ingest_sessions
    import sqlite_memory
    database = tmp_path / 'staging.db'
    sqlite_memory.init_database(database)
    seen = []
    def ingest(db, **kwargs):
        seen.append(kwargs)
        return {'errors': []}
    monkeypatch.setattr(ingest_sessions, 'ingest', ingest)
    assert agentmesh.main(['--database', str(database), 'ingest', '--start-at', 'now']) == 0
    assert seen == [{'roots': None, 'project': None, 'start_at': 'now'}]


def test_p7_1_agentmesh_recall_error_exits_nonzero(tmp_path, monkeypatch, capsys):
    import agentmesh
    import recall_memory
    import sqlite_memory
    database = tmp_path / 'staging.db'
    sqlite_memory.init_database(database)
    monkeypatch.setattr(recall_memory, 'recall', lambda *a, **k: {'error': 'profile_log_unavailable'})
    assert agentmesh.main(['--database', str(database), 'recall', 'query']) == 1
    assert json.loads(capsys.readouterr().out) == {'error': 'profile_log_unavailable'}


def test_p7_2_windows_fresh_registration_retired_then_rollback(tmp_path, monkeypatch):
    import copy
    import windows_install
    from test_windows_install import FakeTasks, SID
    state = {'task': None, 'sid': SID, 'marker': 'fixture', 'root': str(tmp_path),
             'active': 'version', 'programs': {'version': {'files': {}}},
             'runtime': str(tmp_path / 'runtime.json'), 'interval': 60, 'timeout': 60,
             'legacy_drained': True, 'task_name': 'AgentMesh-test', 'status': 'ready', 'pending': None}
    adapter = FakeTasks()
    adapter.tasks['unrelated'] = {'unrelated': True}
    before = copy.deepcopy(state)
    writes = []
    monkeypatch.setattr(windows_install, 'save', lambda *args: writes.append(copy.deepcopy(args[1])))
    report = windows_install.change_task(adapter, state, tmp_path / 'installed.json', enabled=True)
    assert report == {'status': 'retired', 'worker_changed': False}
    assert adapter.operations == [] and writes == [] and state == before
    packet_sync.configure('enable', system='Darwin')
    report = windows_install.change_task(adapter, state, tmp_path / 'installed.json', enabled=True)
    assert report['status'] == 'enabled'
    assert adapter.operations == ['register']
    assert adapter.tasks['unrelated'] == {'unrelated': True}
    packet_sync.configure('retire', system='Darwin', uid=42, runtime=tmp_path / 'runtime.json',
                          runner=lambda *a, **k: SimpleNamespace(returncode=0))
    report = windows_install.change_task(adapter, state, tmp_path / 'installed.json', enabled=True)
    assert report['status'] == 'disabled'
    assert not adapter.tasks['AgentMesh-test']['binding']['enabled']
    assert adapter.tasks['unrelated'] == {'unrelated': True}


from test_windows_install import existing


def test_p7_2_owned_windows_retire_enable_preserves_binding(existing):
    import windows_install
    windows_install.run('install', **existing, yes=True)
    state_path = existing['program_root'] / 'installed.json'
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        assert 'windows-autostart' in argv
        enable = '--enable' in argv
        windows_install.run('autostart', runtime=existing['runtime'],
            program_root=existing['program_root'], installed_state=state_path,
            adapter=existing['adapter'], enable=enable, disable=not enable, yes=True,
            legacy_drained='--legacy-drained' in argv)
        return SimpleNamespace(returncode=0)
    for action in ('enable', 'retire', 'enable'):
        result = packet_sync.configure(action, system='Windows', installed_state=state_path,
            runner=runner, legacy_drained=True)
        assert result['commands'][0]['status'] == 'done'
        state = windows_install.read_state(existing['program_root'], state_path)
        windows_install.task_owned(existing['adapter'].read(state['task_name']), state)
        assert state['task']['binding']['enabled'] == (action == 'enable')
    assert len(calls) == 3


def test_p7_1_agentmesh_recall_without_staging(tmp_path, monkeypatch, capsys):
    import agentmesh
    import recall_memory
    database = tmp_path / 'absent.db'
    seen = []
    def recall(db, query, **kwargs):
        seen.append((db, query))
        return {'error': 'profile_log_unavailable'}
    monkeypatch.setattr(recall_memory, 'recall', recall)
    assert agentmesh.main(['--database', str(database), 'recall', 'query']) == 1
    assert json.loads(capsys.readouterr().out) == {'error': 'profile_log_unavailable'}
    assert seen == [(database.resolve(), 'query')]
    assert not database.exists()
    assert agentmesh.main(['--database', str(database), 'ingest']) == 1
    output = capsys.readouterr()
    assert output.out == '' and json.loads(output.err)['error'] == 'FileNotFoundError'
