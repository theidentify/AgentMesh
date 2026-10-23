"""Tracked Mac launcher (jobs/agentmesh_production.py) on disposable paths only."""
import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest

import job_notify
import sqlite_memory

HERE = Path(__file__).resolve().parent


@pytest.fixture
def production(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('agentmesh_production_under_test', HERE / 'jobs/agentmesh_production.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = tmp_path / 'mac.db'
    sqlite_memory.init_database(db)
    config = tmp_path / 'agentmesh-production.json'
    config.write_text(json.dumps(dict(primary_backend='sqlite', retention='disabled', database=str(db),
                                      runtime=str(HERE), export_root=str(tmp_path / 'export'),
                                      status_dir=str(tmp_path / 'status'), roots={'omp': str(tmp_path / 'omp')})))
    monkeypatch.setattr(module, 'CONFIG', config)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    return module, tmp_path


def blocked(module, capsys):
    assert module.execute('ingest', []) == 1
    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    return line


def test_ingest_errors_report_a_notifier_safe_detail(production, capsys, monkeypatch):
    module, tmp_path = production
    import ingest_sessions
    monkeypatch.setattr(ingest_sessions, 'ingest', lambda database, roots: dict(
        found=2, processed=2, skipped=0, inserted=0, errors=2, error_classes=['OperationalError', 'OperationalError']))
    line = blocked(module, capsys)
    assert line['detail'] == 'native ingestion errors: OperationalError=2'
    assert line['error_class'] == 'ValidationError' and line['status'] == 'blocked'
    assert job_notify.summary_fields(json.dumps(line))['detail'] == line['detail']
    saved = json.loads((tmp_path / 'status/last-ingest.json').read_text())
    assert saved['error_classes'] == ['OperationalError', 'OperationalError']


def test_export_failure_reports_its_fixed_message(production, capsys):
    module, tmp_path = production
    root = tmp_path / 'export'
    root.mkdir()
    (root / '_Index.md').write_text('# Manual note with the generated name\n')
    line = blocked(module, capsys)
    assert line['detail'] == 'manual projection note collision'


def test_clean_ingest_still_succeeds(production, capsys):
    module, tmp_path = production
    assert module.execute('ingest', []) == 0
    assert json.loads((tmp_path / 'status/last-ingest.json').read_text())['errors'] == 0


@pytest.mark.parametrize('name,action', [
    ('agentmesh_ingest.py', 'ingest'), ('agentmesh_summary.py', 'summary'),
    ('agentmesh_retention.py', 'retention')])
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
