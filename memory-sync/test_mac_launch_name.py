import os
from pathlib import Path
import plistlib
from types import SimpleNamespace

import pytest
import mac_launch_name as launch_name


def venv(root, name='python'):
    root.mkdir()
    (root/'bin').mkdir()
    python = root/'bin'/name
    python.write_text('#!/bin/sh\n')
    python.chmod(0o755)
    (root/'pyvenv.cfg').write_text('home = /opt/python\n')
    return python


def unit(root, name, python, **extra):
    label = 'org.agentmesh.'+name
    path = root/(label+'.plist')
    data = dict(Label=label, ProgramArguments=[str(python), 'job_notify.py', '--', str(python), 'worker.py'],
                RunAtLoad=True, KeepAlive=False, EnvironmentVariables={'EXAMPLE': 'value'}, Umask=0o077)
    data.update(extra)
    path.write_bytes(plistlib.dumps(data))
    return path


class Runner:
    def __init__(self, active=(), fail=False):
        self.active, self.fail, self.calls = set(active), fail, []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        code = 0
        if argv[1] == 'print': code = int(argv[2].rsplit('/', 1)[-1] not in self.active)
        if argv[1] == 'bootstrap': code = int(self.fail)
        return SimpleNamespace(returncode=code)


@pytest.fixture(autouse=True)
def no_real_launchctl(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('unexpected subprocess call')
    monkeypatch.setattr(launch_name.subprocess, 'run', unexpected)


def test_dry_run(tmp_path, capsys):
    python = venv(tmp_path/'venv')
    plist = unit(tmp_path, 'sync', python)
    before = plist.read_bytes()
    assert launch_name.main(['--agents', str(tmp_path)]) == 0
    assert plist.read_bytes() == before
    assert not os.path.lexists(python.parent/'AgentMesh')
    assert capsys.readouterr().out == f'org.agentmesh.sync: {python} -> {python.parent}/AgentMesh\n'


@pytest.mark.parametrize('mode', [0o644, 0o600])
def test_apply_preserves_data_and_mode(tmp_path, mode):
    python = venv(tmp_path/'venv')
    plist = unit(tmp_path, 'sync', python)
    plist.chmod(mode)
    expected = plistlib.loads(plist.read_bytes())
    expected['ProgramArguments'][0] = str(python.parent/'AgentMesh')
    runner = Runner()
    launch_name.apply(launch_name.plan(tmp_path), runner=runner, reload=False)
    assert plistlib.loads(plist.read_bytes()) == expected
    assert plist.stat().st_mode & 0o777 == mode
    assert os.readlink(python.parent/'AgentMesh') == 'python'
    assert launch_name.plan(tmp_path) == []
    assert runner.calls == []


def test_skipped_plists(tmp_path):
    python = venv(tmp_path/'venv')
    unit(tmp_path, 'shell', '/bin/sh')
    unit(tmp_path, 'binary', python.parent/'agentmesh')
    other = venv(tmp_path/'nonvenv')
    (other.parent.parent/'pyvenv.cfg').unlink()
    unit(tmp_path, 'nonvenv', other)
    unit(tmp_path, 'foreign', python).rename(tmp_path/'org.other.sync.plist')
    (tmp_path/'org.agentmesh.broken.plist').write_text('not a plist')
    unit(tmp_path, 'program', python, Program=str(python))
    for i, argv in enumerate(([], ['relative'], [str(python), 1], str(python), [''], [str(python.parent/'python2')])):
        unit(tmp_path, str(i), python, ProgramArguments=argv)
    (tmp_path/'org.agentmesh.array.plist').write_bytes(plistlib.dumps([]))
    assert launch_name.plan(tmp_path) == []


@pytest.mark.parametrize('name', ['python', 'python3', 'python3.10', 'python3.10.1'])
def test_python_names_and_plan_order(tmp_path, name):
    python = venv(tmp_path/'venv', name)
    for label in ('z', 'a'): unit(tmp_path, label, python)
    items = launch_name.plan(tmp_path)
    assert [i['label'] for i in items] == ['org.agentmesh.a', 'org.agentmesh.z']
    assert items[0] == dict(plist=tmp_path/'org.agentmesh.a.plist', label='org.agentmesh.a',
                            old=str(python), new=str(python.parent/'AgentMesh'), link=python.parent/'AgentMesh')
    launch_name.apply(items, reload=False)
    assert os.readlink(python.parent/'AgentMesh') == name


@pytest.mark.parametrize('conflict', ['file', 'elsewhere', 'dangling'])
def test_validate_all_before_rewrite(tmp_path, conflict, capsys):
    first = venv(tmp_path/'first')
    second = venv(tmp_path/'second')
    paths = [unit(tmp_path, 'a', first), unit(tmp_path, 'z', second)]
    before = [p.read_bytes() for p in paths]
    link = second.parent/'AgentMesh'
    if conflict == 'file': link.write_text('keep me')
    else: link.symlink_to(first if conflict == 'elsewhere' else 'missing')
    runner = Runner()
    with pytest.raises(ValueError, match='exists and is not a link to'):
        launch_name.apply(launch_name.plan(tmp_path), runner=runner)
    assert [p.read_bytes() for p in paths] == before
    assert runner.calls == []
    assert launch_name.main(['--agents', str(tmp_path), '--apply']) == 1
    assert 'error:' in capsys.readouterr().err


def test_existing_correct_link(tmp_path):
    python = venv(tmp_path/'venv')
    link = python.parent/'AgentMesh'
    link.symlink_to(python)
    launch_name.ensure_link(link, python)
    assert os.readlink(link) == str(python)


def test_reload_order_and_snapshot(tmp_path):
    python = venv(tmp_path/'venv')
    paths = {name: unit(tmp_path, name, python) for name in ('control', 'sync', 'disabled')}
    runner = Runner(['org.agentmesh.control', 'org.agentmesh.sync'])
    def run(argv):
        if argv[1] == 'print':
            assert all(plistlib.loads(p.read_bytes())['ProgramArguments'][0] == str(python) for p in paths.values())
        else:
            assert all(plistlib.loads(p.read_bytes())['ProgramArguments'][0] == str(python.parent/'AgentMesh') for p in paths.values())
        return runner(argv)
    launch_name.apply(launch_name.plan(tmp_path), runner=run)
    domain = f'gui/{os.getuid()}'
    assert runner.calls == [
        *[['launchctl', 'print', domain+'/org.agentmesh.'+name] for name in ('control', 'disabled', 'sync')],
        ['launchctl', 'bootout', domain+'/org.agentmesh.sync'],
        ['launchctl', 'bootstrap', domain, str(paths['sync'])],
        ['launchctl', 'bootout', domain+'/org.agentmesh.control'],
        ['launchctl', 'bootstrap', domain, str(paths['control'])]]


def test_main_no_reload_and_revert(tmp_path, capsys):
    python = venv(tmp_path/'venv')
    plist = unit(tmp_path, 'sync', python)
    original = plistlib.loads(plist.read_bytes())
    args = ['--agents', str(tmp_path), '--apply', '--no-reload']
    assert launch_name.main(args) == 0
    assert '(file only)' in capsys.readouterr().out
    assert launch_name.main(args) == 0
    assert capsys.readouterr().out == 'nothing to change\n'
    assert launch_name.plan(tmp_path, revert=True)[0]['link'] is None
    assert launch_name.main(args+['--revert']) == 0
    assert plistlib.loads(plist.read_bytes()) == original
    assert (python.parent/'AgentMesh').is_symlink()


def test_revert_requires_python(tmp_path):
    python = venv(tmp_path/'venv')
    unit(tmp_path, 'sync', python.parent/'AgentMesh')
    python.unlink()
    assert launch_name.plan(tmp_path, revert=True) == []


@pytest.mark.parametrize('fail', [False, True])
def test_main_runner_and_bootstrap_failure(tmp_path, monkeypatch, capsys, fail):
    python = venv(tmp_path/'venv')
    unit(tmp_path, 'sync', python)
    runner = Runner(['org.agentmesh.sync'], fail=fail)
    def run(argv, **kwargs):
        assert kwargs == dict(capture_output=True, text=True, timeout=30)
        return runner(argv)
    monkeypatch.setattr(launch_name.subprocess, 'run', run)
    assert launch_name.main(['--agents', str(tmp_path), '--apply']) == int(fail)
    output = capsys.readouterr()
    if fail: assert 'error: bootstrap failed for org.agentmesh.sync' in output.err
    else: assert '(reload)' in output.out
    assert runner.calls[-1][1] == 'bootstrap'


def test_bootstrap_failure_stops_reload(tmp_path):
    python = venv(tmp_path/'venv')
    for name in ('a', 'z'): unit(tmp_path, name, python)
    runner = Runner(['org.agentmesh.a', 'org.agentmesh.z'], fail=True)
    with pytest.raises(RuntimeError, match='org.agentmesh.a'):
        launch_name.apply(launch_name.plan(tmp_path), runner=runner)
    assert [a[1] for a in runner.calls] == ['print', 'print', 'bootout', 'bootstrap']
