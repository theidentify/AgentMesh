"""Opt-in bootstrap pause for source review before local transcript ingestion."""
import json
import os
from pathlib import Path
import sys

import pytest
import build_package
import sync_worker
from test_bootstrap import load_bootstrap


@pytest.fixture
def bootstrap_install(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    snapshot = tmp_path / 'snapshot.jsonl'
    snapshot.write_text(json.dumps({'format': 'omp-sqlite-snapshot-v1', 'tables': build_package.TABLES}) + '\n')
    build_package.build(snapshot, exchange, '00000000-0000-4000-8000-000000000001')
    return load_bootstrap(), exchange, tmp_path / 'local'


def test_no_ingest_first_cycle_then_enable_only_chosen_roots(bootstrap_install, tmp_path, monkeypatch, capsys):
    module, exchange, local = bootstrap_install
    # Fail the cycle if the bootstrap attempts any default-source discovery.
    import ingest_sessions
    original = ingest_sessions.ingest
    def forbidden(*args, **kwargs):
        raise AssertionError('ingestion ran before source review')
    monkeypatch.setattr(ingest_sessions, 'ingest', forbidden)
    assert module.main(['--exchange', str(exchange), '--local-dir', str(local), '--no-ingest', '--once']) == 0
    output = capsys.readouterr().out
    assert 'Ingestion is disabled' in output and 'review' in output and 'roots' in output
    workflow = local / 'data/workflow.json'
    config = json.loads(workflow.read_text())
    assert config['ingest'] is False
    transcript_dir = tmp_path / 'chosen'
    transcript_dir.mkdir()
    transcript = {'type': 'message', 'id': 'chosen-event', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'reviewed source'}]}}
    (transcript_dir / 'session.jsonl').write_text(json.dumps(transcript) + '\n')
    # Exact documented JSON edit; explicit roots replace every default root.
    config.update(ingest=True, roots={'omp': str(transcript_dir)})
    workflow.write_text(json.dumps(config))
    seen = []
    def selected(database, **kwargs):
        seen.append(kwargs['roots'])
        return original(database, **kwargs)
    monkeypatch.setattr(ingest_sessions, 'ingest', selected)
    report = sync_worker.run_once(local / 'data/windows.db', exchange)
    assert not sync_worker.failed(report)
    assert report['workflow']['ingestion']['inserted'] == 1
    assert seen == [{'omp': str(transcript_dir)}]


def test_no_ingest_rerun_preserves_roots_and_other_settings(bootstrap_install):
    module, exchange, local = bootstrap_install
    module.install(exchange, local)
    workflow = local / 'data/workflow.json'
    config = {'ingest': True, 'summarize': False, 'roots': {'codex': '/chosen'}, 'summary_interval_seconds': 123}
    workflow.write_text(json.dumps(config))
    module.install(exchange, local, no_ingest=True)
    assert json.loads(workflow.read_text()) == dict(config, ingest=False)
    module.install(exchange, local)
    assert json.loads(workflow.read_text()) == dict(config, ingest=False)


def test_windows_launcher_forwards_arguments_to_both_python_choices():
    lines = Path(__file__).with_name('START-WINDOWS.cmd').read_text().splitlines()
    launches = [line for line in lines if 'bootstrap_windows.py' in line]
    assert len(launches) == 2
    assert all(line.endswith(' %*') for line in launches)
