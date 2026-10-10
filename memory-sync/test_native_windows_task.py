"""Explicitly opted-in, disabled disposable task only. Never runs the task."""
import os
from pathlib import Path
import subprocess
import uuid

import pytest
import windows_task


@pytest.mark.skipif(os.name != 'nt' or not os.environ.get('AGENTMESH_NATIVE_TASK_FIXTURE_ROOT'),
                    reason='requires native Windows and explicit disposable fixture root')
def test_native_disabled_task_registration_readback_removal(tmp_path):
    allowed = Path(os.environ['AGENTMESH_NATIVE_TASK_FIXTURE_ROOT']).resolve(strict=True)
    if not tmp_path.resolve().is_relative_to(allowed):
        pytest.fail('native fixture must stay inside explicitly selected disposable root')
    adapter = windows_task.TaskAdapter()
    name = 'AgentMesh-Fixture-' + str(uuid.uuid4())
    before = adapter.read(name)
    assert before['task'] is None
    sid = before['sid']
    binary = tmp_path / 'agentmesh.exe'
    binary.write_bytes(b'disposable never-executed fixture')
    definition = windows_task.binding(sid, 'AgentMesh OWNED disposable fixture ' + name, binary,
                                     tmp_path / 'runtime.json', 60.0, 60.0, False, False)
    task = None
    try:
        task = adapter.register(name, sid, None, definition)
        assert adapter.read(name) == {'sid': sid, 'task': task}
        assert task['binding'] == definition
        changed = {**definition, 'marker': definition['marker'] + ' updated'}
        task = adapter.register(name, sid, task['xml'], changed)
        assert adapter.read(name)['task']['binding'] == changed
        with pytest.raises(ValueError):
            adapter.register(name, sid, 'not-the-owned-snapshot', definition)
        assert adapter.read(name)['task'] == task
    finally:
        # Cleanup only the exact previously observed owned task snapshot.
        current = adapter.read(name)['task']
        if current is not None and current['binding'] in (definition, {**definition, 'marker': definition['marker'] + ' updated'}):
            adapter.remove(name, sid, current['xml'])
        elif current is not None:
            pytest.fail('fixture ownership changed; retained instead of deleting foreign task')
    assert adapter.read(name)['task'] is None


@pytest.mark.skipif(os.name != 'nt' or not os.environ.get('AGENTMESH_NATIVE_TASK_FIXTURE_ROOT'),
                    reason='requires native Windows and explicit disposable fixture root')
def test_native_junction_refuses_program_paths(tmp_path):
    from install_adopt import absolute
    allowed = Path(os.environ['AGENTMESH_NATIVE_TASK_FIXTURE_ROOT']).resolve(strict=True)
    assert tmp_path.resolve().is_relative_to(allowed)
    target = tmp_path / 'target'
    target.mkdir()
    junction = tmp_path / 'junction'
    # Disposable native junction fixture only; not an application adapter.
    subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(target)], check=True, capture_output=True)
    try:
        with pytest.raises(ValueError, match='reparse|symlink'):
            absolute(junction / 'programs')
    finally:
        junction.rmdir()  # Remove the junction itself, never its target.
    assert target.is_dir()
