"""The scheduled ingest and summary jobs (agentmesh_log.ingest.production) on disposable paths only."""
import json
import os
from pathlib import Path
import sys

import pytest

from agentmesh_log.ingest import personal, production
from agentmesh_log.ingest.protocol import ValidationError


@pytest.fixture
def config(tmp_path):
    broker = dict(cli_argv=['agentmesh-log'], agent_key=str(tmp_path / 'summarizer.key'),
                  mapping=str(tmp_path / 'mapping.json'), state_dir=str(tmp_path / 'state'))
    path = tmp_path / 'agentmesh-production.json'
    path.write_text(json.dumps(dict(backend='broker', broker=broker, status_dir=str(tmp_path / 'status'),
                                    roots={'omp': str(tmp_path / 'omp')}, summary={})))
    return path


def error(capsys, action, config):
    assert production.execute(action, config=config) == 1
    return json.loads(capsys.readouterr().err.strip().splitlines()[-1])


@pytest.mark.parametrize('value', [
    dict(primary_backend='sqlite', retention='disabled', database='/db', runtime='/rt'),
    dict(backend='sqlite', primary_backend='sqlite', retention='disabled', database='/db', runtime='/rt')], ids=['implicit', 'named'])
def test_sqlite_backend_is_refused(config, capsys, value):
    """SQLite retirement R5b: a config that still selects the staging database never runs."""
    config.write_text(json.dumps(value))
    line = error(capsys, 'ingest', config)
    assert line['status'] == 'blocked' and line['error_class'] == 'ValueError'
    assert not (config.parent / 'status').exists()


@pytest.mark.parametrize('action', ['context', 'recall', 'export', 'retention', 'blocked'])
def test_legacy_actions_removed(config, capsys, action):
    assert error(capsys, action, config)['error_class'] == 'ValueError'


def test_scan_runs_and_memory_sync_runtime_is_ignored(config, capsys):
    """An unmigrated config still names memory-sync's runtime directory: accepted, never put on sys.path."""
    value = json.loads(config.read_text())
    config.write_text(json.dumps(dict(value, runtime='/nonexistent/memory-sync')))
    before = list(sys.path)
    assert production.execute('ingest', config=config) == 0
    assert sys.path == before
    report = json.loads((config.parent / 'status/last-ingest.json').read_text())
    assert (report['backend'], report['processed'], report['errors']) == ('broker', 0, 0)
    config.write_text(json.dumps(dict(value, runtime='relative')))
    assert error(capsys, 'ingest', config)['error_class'] == 'ValueError'


def test_config_defaults_under_agentmesh_home_not_hermes(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'hermes'))
    assert production.default_config() == tmp_path / 'home/jobs/agentmesh-production.json'


def test_child_path_makes_the_package_importable(monkeypatch):
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join(['/a', '/b']))
    production.child_path()
    production.child_path()
    assert os.environ['PYTHONPATH'].split(os.pathsep) == [production.PACKAGE_ROOT, '/a', '/b']
    assert (Path(production.PACKAGE_ROOT) / 'agentmesh_log' / 'cli.py').is_file()


# Personal-only: transcripts never reach a workspace home (one-click join, ~/.agentmesh/ws/<id8>/).

def test_workspace_paths_are_refused_before_any_transcript_is_read(config, capsys, tmp_path):
    home = Path(os.environ['AGENTMESH_HOME'])
    value = json.loads(config.read_text())
    for key in ('agent_key', 'mapping', 'state_dir'):
        broken = dict(value, broker=dict(value['broker'], **{key: str(home / 'ws' / 'abcd1234' / key)}))
        config.write_text(json.dumps(broken))
        line = error(capsys, 'ingest', config)
        assert line['error_class'] == 'ValidationError' and 'personal-only' in line['detail']
    config.write_text(json.dumps(dict(value, status_dir=str(home / 'ws' / 'abcd1234' / 'status'))))
    assert 'personal-only' in error(capsys, 'summary', config)['detail']
    assert not (tmp_path / 'status').exists()


def test_a_workspace_home_as_agentmesh_home_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path / 'personal' / 'ws' / 'abcd1234'))
    with pytest.raises(ValidationError, match='personal-only'):
        personal.check((str(tmp_path / 'state'),))


def test_a_key_that_reaches_a_workspace_broker_is_refused(tmp_path):
    root = tmp_path / 'home'
    workspace_key = root / 'ws' / 'abcd1234' / 'agents' / 'workspace.key'
    workspace_key.parent.mkdir(parents=True)
    workspace_key.write_text(json.dumps(dict(format='agentmesh-agent-key-v1', address=r'\\.\pipe\agentmesh-broker-ws0000')))
    key = root / 'agents' / 'summarizer.key'
    key.parent.mkdir(parents=True)
    # Windows: a pipe a workspace key names.
    key.write_text(json.dumps(dict(format='agentmesh-agent-key-v1', address=r'\\.\pipe\agentmesh-broker-ws0000')))
    with pytest.raises(ValidationError, match='personal-only'): personal.check(agent_key=str(key), root=root)
    # Unix: a socket inside a workspace home.
    key.write_text(json.dumps(dict(format='agentmesh-agent-key-v1', address=str(root / 'ws' / 'abcd1234' / 'broker.sock'))))
    with pytest.raises(ValidationError, match='personal-only'): personal.check(agent_key=str(key), root=root)
    # The personal broker passes.
    key.write_text(json.dumps(dict(format='agentmesh-agent-key-v1', address=str(root / 'profile-log' / 'broker.sock'))))
    personal.check(agent_key=str(key), root=root)
    key.write_text(json.dumps(dict(format='agentmesh-agent-key-v1', address=r'\\.\pipe\agentmesh-broker-me000000')))
    personal.check(agent_key=str(key), root=root)


def test_summary_cli_refuses_a_workspace_key(tmp_path, monkeypatch, capsys):
    from agentmesh_log import cli
    home = Path(os.environ['AGENTMESH_HOME'])
    key = home / 'ws' / 'abcd1234' / 'agents' / 'summarizer.key'
    for action in ('related', 'write'):
        assert cli.main(['summary', action, '--agent-key', str(key), '--mapping', str(tmp_path / 'm')]) == 1
        assert json.loads(capsys.readouterr().out) == {'error': 'workspace_refused'}


# migrate-config

def legacy(tmp_path, command):
    path = tmp_path / 'agentmesh-production.json'
    path.write_text(json.dumps(dict(backend='broker', runtime='/Users/o/.agentmesh/memory-sync', status_dir='/s',
                                    broker=dict(cli_argv=['/v/python', '-m', 'agentmesh_log.cli'], agent_key='/k', mapping='/m', state_dir='/j'),
                                    summary=dict(model='claude-haiku-5-5', command=command))))
    if os.name != 'nt': os.chmod(path, 0o600)
    return path


def test_migrate_config_is_a_dry_run_by_default(tmp_path):
    path = legacy(tmp_path, ['/v/python', '/Users/o/.agentmesh/memory-sync/claude_digest_worker.py', '--effort', 'low'])
    before = path.read_bytes()
    result = production.migrate_config(path)
    assert result['changes'] == ['runtime', 'summary'] and result['applied'] is False
    assert result['command'] == ['/v/python', '-m', 'agentmesh_log.ingest.claude_worker', '--effort', 'low']
    assert path.read_bytes() == before


def test_migrate_config_apply_keeps_a_backup_and_is_idempotent(tmp_path):
    path = legacy(tmp_path, ['C:\\v\\python.exe', 'C:\\Users\\o\\.agentmesh\\app\\AgentMesh\\memory-sync\\claude_digest_worker.py'])
    before = path.read_bytes()
    result = production.migrate_config(path, apply=True)
    assert result['applied'] is True and Path(result['backup']).read_bytes() == before
    value = json.loads(path.read_text())
    assert 'runtime' not in value and value['summary']['command'] == ['C:\\v\\python.exe', '-m', 'agentmesh_log.ingest.claude_worker']
    assert value['broker'] == json.loads(before)['broker'] and value['summary']['model'] == 'claude-haiku-5-5'
    if os.name != 'nt': assert path.stat().st_mode & 0o777 == 0o600
    again = production.migrate_config(path, apply=True)
    assert again['changes'] == [] and again['applied'] is False
    assert Path(result['backup']).read_bytes() == before


def test_migrate_config_through_the_cli(tmp_path, capsys):
    from agentmesh_log import cli
    path = legacy(tmp_path, ['/v/python', '/rt/claude_digest_worker.py'])
    assert cli.main(['ingest', 'migrate-config', '--config', str(path)]) == 0
    assert json.loads(capsys.readouterr().out)['changes'] == ['runtime', 'summary']


def test_ingest_cli_returns_the_job_exit_code_without_a_result_line(config, capsys):
    from agentmesh_log import cli
    assert cli.main(['ingest', 'scan', '--config', str(config)]) == 0
    assert capsys.readouterr().out == ''
    assert cli.main(['ingest', 'summary', '--config', str(config.parent / 'missing.json')]) == 1
    captured = capsys.readouterr()
    assert captured.out == '' and json.loads(captured.err)['action'] == 'summary'


def test_job_run_through_the_cli(tmp_path, capsys):
    from agentmesh_log import cli
    code = cli.main(['job', 'run', '--name', 'ingest', '--state-dir', str(tmp_path / 'status'), '--',
                     sys.executable, '-c', 'raise SystemExit(3)'])
    assert code == 3
    assert json.loads(capsys.readouterr().out)['exit'] == 3
    assert json.loads((tmp_path / 'status' / 'job-ingest.json').read_text())['ok'] is False


def test_boot_id_windows_kernel_boot_time(monkeypatch):
    import ctypes
    from types import SimpleNamespace
    from datetime import datetime, timezone
    boot = [1760000052.25]
    def query(cls, buffer, size, returned):
        assert (cls, size) == (3, 48)
        filetime = int((boot[0] + 11644473600) * 10**7)
        ctypes.memmove(buffer, filetime.to_bytes(8, 'little') + bytes(40), 48); return 0
    monkeypatch.setattr(production, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(ntdll=SimpleNamespace(NtQuerySystemInformation=query)), raising=False)
    monkeypatch.setattr(production.subprocess, 'run', lambda *a, **k: pytest.fail('Windows must not call sysctl'))
    first = production.boot_id()
    assert first == datetime.fromtimestamp(1760000052.25, timezone.utc).isoformat()
    # Same boot: identical id regardless of the wall clock; a reboot changes it.
    assert production.boot_id() == first
    boot[0] += 3600
    assert production.boot_id() != first
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(ntdll=SimpleNamespace(NtQuerySystemInformation=lambda *a: 0xC0000004)), raising=False)
    with pytest.raises(OSError): production.boot_id()


def test_boot_id_macos_preserves_sysctl(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(production, 'os', SimpleNamespace(name='posix'))
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs)); return SimpleNamespace(stdout=' boot value\n')
    monkeypatch.setattr(production.subprocess, 'run', run)
    assert production.boot_id() == 'boot value'
    assert calls == [(['/usr/sbin/sysctl', '-n', 'kern.boottime'], dict(capture_output=True, text=True, check=True))]
