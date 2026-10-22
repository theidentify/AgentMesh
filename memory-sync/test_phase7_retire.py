import json
from types import SimpleNamespace

import pytest

import packet_sync
import platform_service


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path / 'home'))


def runtime(tmp_path, settings):
    data = tmp_path / 'home' / 'memory-sync' / 'data'
    data.mkdir(parents=True)
    (data / 'runtime.json').write_text(json.dumps({'database': str(data / 'staging.db')}))
    (data / 'workflow.json').write_text(json.dumps(settings))
    return data / 'runtime.json'


@pytest.mark.parametrize('settings', [{}, {'ingest': True}, {'ingest': False, 'summarize': True}])
@pytest.mark.parametrize('system', ['Darwin', 'Linux', 'Windows'])
def test_retire_retains_workflow_unit(tmp_path, settings, system):
    path = runtime(tmp_path, settings)
    before = path.read_bytes()
    def forbidden(*args, **kwargs): raise AssertionError('workflow unit must remain running')
    report = packet_sync.configure('retire', system=system, uid=42, task_name='AgentMesh-fixture', runner=forbidden)
    assert report['worker'] == 'retained for workflow ingest/summary; packet work disabled'
    assert report['commands'] == [] and not packet_sync.enabled()
    assert path.read_bytes() == before


def test_mac_retire_off_workflow_bootout(tmp_path):
    runtime(tmp_path, {'ingest': False, 'summarize': False})
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)
    result = packet_sync.configure('retire', system='Darwin', uid=42, runner=runner)
    assert calls == [['launchctl', 'bootout', 'gui/42/org.agentmesh.sync']]
    assert result['commands'][0]['status'] == 'done'


@pytest.mark.parametrize('damage', ['missing-runtime', 'bad-runtime', 'bad-workflow', 'missing-explicit-workflow'])
def test_retire_unreadable_refuses_unload(tmp_path, damage):
    path = runtime(tmp_path, {'ingest': False, 'summarize': False})
    if damage == 'missing-runtime': path.unlink()
    elif damage == 'bad-runtime': path.write_text('{')
    elif damage == 'bad-workflow': path.with_name('workflow.json').write_text('{')
    else: path.write_text(json.dumps({'database': str(path.with_name('staging.db')),
                                     'workflow_config': str(tmp_path / 'absent.json')}))
    def forbidden(*args, **kwargs): raise AssertionError('unreadable workflow must retain worker')
    with pytest.raises(ValueError, match='refusing to unload worker'):
        packet_sync.configure('retire', system='Darwin', uid=42, runner=forbidden)
    assert not (tmp_path / 'home' / 'settings.local.json').exists()


@pytest.mark.parametrize('system', ['macos', 'linux'])
def test_generate_retains_unit_for_explicit_workflow(tmp_path, system):
    path = runtime(tmp_path, {'ingest': False, 'summarize': False})
    workflow = tmp_path / 'separate-workflow.json'
    workflow.write_text(json.dumps({'ingest': True}))
    output = tmp_path / 'unit'
    assert platform_service.main(['generate', '--platform', system, '--python', str(tmp_path / 'python'),
        '--app-dir', str(tmp_path), '--database', str(path.with_name('staging.db')),
        '--exchange', str(tmp_path / 'exchange'), '--output', str(output), '--workflow-config', str(workflow)]) == 0
    assert '--workflow-config' in output.read_text()
    assert str(workflow) in output.read_text()


def test_windows_change_task_preserves_workflow(tmp_path, monkeypatch):
    import windows_install
    from test_windows_install import FakeTasks, SID
    path = runtime(tmp_path, {'ingest': True, 'summarize': True})
    state = {'task': None, 'sid': SID, 'marker': 'fixture', 'root': str(tmp_path),
             'active': 'version', 'programs': {'version': {'files': {}}},
             'runtime': str(path), 'interval': 60, 'timeout': 60, 'legacy_drained': True,
             'task_name': 'AgentMesh-fixture', 'status': 'ready', 'pending': None}
    monkeypatch.setattr(windows_install, 'save', lambda *args: None)
    adapter = FakeTasks()
    report = windows_install.change_task(adapter, state, tmp_path / 'installed.json', enabled=True)
    assert report['status'] == 'enabled'
    assert adapter.tasks['AgentMesh-fixture']['binding']['enabled']
