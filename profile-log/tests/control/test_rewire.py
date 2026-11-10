"""control rewire-ingest: the supervised jobs move from memory-sync's scripts to agentmesh_log, nothing else changes."""
import json
import plistlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agentmesh_log.control import rewire
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.store import Store
from .support import Presence

PY = '/opt/am/venv/bin/python'
RT = '/Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10'
APP = '/Users/o/.agentmesh/memory-sync'
JOBS = '/Users/o/.agentmesh/jobs'
WRAP = ['--name', 'NAME', '--state-dir', '/Users/o/.agentmesh/production-status', '--webhook-file', '/Users/o/.config/agentmesh/hook']


def job(name, script):
    options = [a if a != 'NAME' else name for a in WRAP]
    return [RT, APP + '/job_notify.py', *options, '--', RT, JOBS + '/' + script]


def spec(argv, env=None):
    return dict(argv=argv, env=env or {}, cwd=JOBS, stdout='/x.log', stderr='/x.log', umask=63, interval=3600,
                calendar=None, run_at_load=True, keep_alive=False, throttle=10)


@pytest.fixture
def home(tmp_path):
    layout = Layout(tmp_path / 'home').ensure()
    store = Store(layout.state)
    store.set_backend('ingest', 'native', spec(job('ingest', 'agentmesh_ingest.py'), {'PATH': '/usr/bin'}), 1)
    store.set_backend('summary', 'native', spec(job('summary', 'agentmesh_summary.py')), 1)
    store.set_backend('retention', 'native', spec(job('retention', 'retention.py')), 1)
    store.set_backend('broker', 'native', spec([PY, '-m', 'agentmesh_log.cli', 'broker', 'run']), 1)
    layout.units.mkdir(parents=True, exist_ok=True)
    with open(layout.units / 'org.agentmesh.summary.plist', 'wb') as stream:
        plistlib.dump(dict(Label='org.agentmesh.summary', ProgramArguments=job('summary', 'agentmesh_summary.py'),
                           StartInterval=3600, RunAtLoad=True), stream)
    yield layout, store
    store.close()


def run(layout, store, answer=True, **kw):
    kw.setdefault('check', lambda python: True)
    return rewire.rewire(layout, store, console=Presence(answer), windows=False, **kw)


def test_rewired_forms():
    assert rewire.rewired(job('ingest', 'agentmesh_ingest.py')) == [
        RT, '-m', 'agentmesh_log.control.jobrun', '--name', 'ingest', '--state-dir', '/Users/o/.agentmesh/production-status',
        '--webhook-file', '/Users/o/.config/agentmesh/hook', '--', RT, '-m', 'agentmesh_log.ingest', 'scan']
    windows = [r'C:\v\python.exe', r'C:\Users\o\.agentmesh\app\AgentMesh\memory-sync\job_notify.py', '--name', 'summary',
               '--state-dir', r'C:\s', '--', r'C:\v\python.exe', r'C:\Users\o\.agentmesh\jobs\agentmesh_summary.py', '--force']
    assert rewire.rewired(windows)[-5:] == [r'C:\v\python.exe', '-m', 'agentmesh_log.ingest', 'summary', '--force']
    # retention's own job is not memory-sync's: only the wrapper changes.
    assert rewire.rewired(job('retention', 'retention.py'))[-3:] == ['--', RT, JOBS + '/retention.py']
    # Already rewired, or not a wrapper at all.
    assert rewire.rewired(rewire.rewired(job('ingest', 'agentmesh_ingest.py'))) is None
    assert rewire.rewired([PY, '-m', 'agentmesh_log.cli', 'broker', 'run']) is None
    assert rewire.interpreters(rewire.rewired(job('ingest', 'agentmesh_ingest.py'))) == [RT]


def test_dry_run_changes_nothing(home):
    layout, store = home
    before = {n: store.spec(n) for n in rewire.UNITS}
    result = run(layout, store)
    assert result['ok'] and result['applied'] is False and result['imports'] == {RT: True}
    assert [u['action'] for u in result['units']] == ['rewire', 'rewire', 'rewire']
    assert {n: store.spec(n) for n in rewire.UNITS} == before
    assert not (layout.control / rewire.SAVED).exists()


def test_apply_rewrites_specs_and_parked_plist_and_back_restores(home):
    layout, store = home
    before = {n: store.spec(n) for n in rewire.UNITS}
    broker = store.spec('broker')
    result = run(layout, store, apply=True)
    assert result['applied'] and result['parked'] == ['summary']
    ingest = store.spec('ingest')
    assert ingest['argv'][1:3] == ['-m', 'agentmesh_log.control.jobrun'] and ingest['argv'][-3:] == ['-m', 'agentmesh_log.ingest', 'scan']
    assert ingest['env'] == {'PATH': '/usr/bin', 'PYTHONPATH': rewire.PACKAGE_ROOT}
    # Schedule, logs and working directory stay; only the command and PYTHONPATH change.
    assert {k: v for k, v in ingest.items() if k not in ('argv', 'env')} == {k: v for k, v in before['ingest'].items() if k not in ('argv', 'env')}
    assert store.spec('broker') == broker
    with open(layout.units / 'org.agentmesh.summary.plist', 'rb') as stream:
        parked = plistlib.load(stream)
    assert parked['ProgramArguments'] == store.spec('summary')['argv'] and parked['StartInterval'] == 3600
    assert parked['EnvironmentVariables'] == {'PYTHONPATH': rewire.PACKAGE_ROOT}
    # A second run finds nothing to do and keeps the first saved specs.
    again = run(layout, store, apply=True)
    assert again['applied'] is False and {u['reason'] for u in again['units']} == {'not_memory_sync'}
    back = run(layout, store, back=True)
    assert back == {'ok': True, 'restored': ['ingest', 'retention', 'summary']}
    assert {n: store.spec(n) for n in rewire.UNITS} == before
    with open(layout.units / 'org.agentmesh.summary.plist', 'rb') as stream:
        parked = plistlib.load(stream)
    assert parked['ProgramArguments'] == before['summary']['argv'] and 'EnvironmentVariables' not in parked
    assert run(layout, store, back=True) == {'error': 'nothing_saved'}


def test_presence_denied_and_import_failure_change_nothing(home):
    layout, store = home
    before = {n: store.spec(n) for n in rewire.UNITS}
    assert run(layout, store, answer=False, apply=True) == {'error': 'presence_required'}
    failed = run(layout, store, apply=True, check=lambda python: False)
    assert failed['error'] == 'import_failed' and failed['applied'] is False
    assert {n: store.spec(n) for n in rewire.UNITS} == before


def test_units_under_launchd_are_skipped(tmp_path):
    layout = Layout(tmp_path / 'home').ensure()
    store = Store(layout.state)
    try:
        result = run(layout, store, apply=True)
        assert {u['reason'] for u in result['units']} == {'not_native'} and result['applied'] is False
    finally:
        store.close()


def test_importable_runs_the_interpreter_with_the_package_path():
    assert rewire.importable(sys.executable) is True
    seen = {}
    def fake(argv, **kw):
        seen.update(argv=argv, path=kw['env']['PYTHONPATH']); return SimpleNamespace(returncode=1)
    assert rewire.importable('/py', run=fake) is False
    assert seen['argv'][0] == '/py' and seen['path'].startswith(rewire.PACKAGE_ROOT)
    assert rewire.importable('/nonexistent/python') is False


def test_rewired_command_runs_a_real_scan(tmp_path, monkeypatch):
    """End to end: the rewired argv runs the jobs through the wrapper with only PYTHONPATH from the spec."""
    status = tmp_path / 'status'
    config = tmp_path / 'agentmesh-production.json'
    broker = dict(cli_argv=['agentmesh-log'], agent_key=str(tmp_path / 'k'), mapping=str(tmp_path / 'm'), state_dir=str(tmp_path / 'j'))
    config.write_text(json.dumps(dict(backend='broker', broker=broker, status_dir=str(status), roots={'omp': str(tmp_path / 'omp')}, summary={})))
    old = [sys.executable, '/gone/job_notify.py', '--name', 'ingest', '--state-dir', str(status), '--',
           sys.executable, '/gone/agentmesh_ingest.py', '--config', str(config)]
    argv = rewire.rewired(old)
    env = dict(PATH='/usr/bin:/bin', PYTHONPATH=rewire.PACKAGE_ROOT, AGENTMESH_HOME=str(tmp_path / 'home'))
    result = subprocess.run(argv, env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['exit'] == 0
    assert json.loads((status / 'job-ingest.json').read_text())['ok'] is True
    assert json.loads((status / 'last-ingest.json').read_text())['backend'] == 'broker'
