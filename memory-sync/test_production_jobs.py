"""Tracked Mac launcher (jobs/agentmesh_production.py) on disposable paths only."""
import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest


HERE = Path(__file__).resolve().parent


@pytest.fixture
def production(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('agentmesh_production_under_test', HERE / 'jobs/agentmesh_production.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path / 'agentmesh-production.json'
    broker = dict(cli_argv=['agentmesh-log'], agent_key=str(tmp_path / 'summarizer.key'),
                  mapping=str(tmp_path / 'mapping.json'), state_dir=str(tmp_path / 'state'))
    config.write_text(json.dumps(dict(backend='broker', broker=broker, runtime=str(HERE), status_dir=str(tmp_path / 'status'),
                                      roots={'omp': str(tmp_path / 'omp')}, summary={})))
    monkeypatch.setattr(module, 'CONFIG', config)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    return module, tmp_path


def error(module, capsys, action):
    assert module.execute(action, []) == 1
    return json.loads(capsys.readouterr().err.strip().splitlines()[-1])


@pytest.mark.parametrize('config', [
    dict(primary_backend='sqlite', retention='disabled', database='/db', runtime='/rt'),
    dict(backend='sqlite', primary_backend='sqlite', retention='disabled', database='/db', runtime='/rt')], ids=['implicit', 'named'])
def test_sqlite_backend_is_refused(production, capsys, config):
    """SQLite retirement R5b: a config that still selects the staging database never runs."""
    module, tmp_path = production
    module.CONFIG.write_text(json.dumps(config))
    line = error(module, capsys, 'ingest')
    assert line['status'] == 'blocked' and line['error_class'] == 'ValueError'
    assert not (tmp_path / 'status').exists()


@pytest.mark.parametrize('action', ['context', 'recall', 'export', 'retention', 'blocked'])
def test_legacy_actions_removed(production, capsys, action):
    module, _ = production
    assert error(module, capsys, action)['error_class'] == 'ValueError'


@pytest.mark.parametrize('name,action', [
    ('agentmesh_ingest.py', 'ingest'), ('agentmesh_summary.py', 'summary')])
def test_job_shims_dispatch_their_action(name, action, monkeypatch):
    import runpy
    import types
    calls = []
    fake = types.ModuleType('agentmesh_production')
    fake.execute = lambda value: calls.append(value) or 0
    monkeypatch.setitem(sys.modules, 'agentmesh_production', fake)
    with pytest.raises(SystemExit) as exit_:
        runpy.run_path(str(HERE / 'jobs' / name), run_name='__main__')
    assert exit_.value.code == 0 and calls == [action]


def test_config_defaults_under_agentmesh_home_not_hermes(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'hermes'))
    spec = importlib.util.spec_from_file_location('agentmesh_production_config_default', HERE / 'jobs/agentmesh_production.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.CONFIG == tmp_path / 'home/jobs/agentmesh-production.json'


def test_boot_id_windows_kernel_boot_time(production, monkeypatch):
    import ctypes
    from types import SimpleNamespace
    from datetime import datetime, timezone
    module, _ = production
    boot = [1760000052.25]
    def query(cls, buffer, size, returned):
        assert (cls, size) == (3, 48)
        filetime = int((boot[0] + 11644473600) * 10**7)
        ctypes.memmove(buffer, filetime.to_bytes(8, 'little') + bytes(40), 48); return 0
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(ntdll=SimpleNamespace(NtQuerySystemInformation=query)), raising=False)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: pytest.fail('Windows must not call sysctl'))
    first = module.boot_id()
    assert first == datetime.fromtimestamp(1760000052.25, timezone.utc).isoformat()
    # Same boot: identical id regardless of the wall clock; a reboot changes it.
    assert module.boot_id() == first
    boot[0] += 3600
    assert module.boot_id() != first
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(ntdll=SimpleNamespace(NtQuerySystemInformation=lambda *a: 0xC0000004)), raising=False)
    with pytest.raises(OSError): module.boot_id()


def test_boot_id_macos_preserves_sysctl(production, monkeypatch):
    from types import SimpleNamespace
    module, _ = production
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='posix'))
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs)); return SimpleNamespace(stdout=' boot value\n')
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.boot_id() == 'boot value'
    assert calls == [(['/usr/sbin/sysctl', '-n', 'kern.boottime'], dict(capture_output=True, text=True, check=True))]
