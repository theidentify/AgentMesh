import json
from pathlib import Path
import subprocess
import sys

import sqlite_memory


def test_worker_errors_show_fixed_acl_reason_without_secret_values(monkeypatch, capsys):
    import agentmesh
    import worker_lifecycle

    def fail(*args, **kwargs):
        raise ValueError('Windows private ACL must be protected')

    monkeypatch.setattr(worker_lifecycle, 'status', fail)
    assert agentmesh.main(['worker-status', '--runtime', '/unused']) == 1
    error = json.loads(capsys.readouterr().err)
    assert error['reason'] == 'Windows private ACL must be protected'
    assert error['stage'] == 'worker-status'

    def secret_failure(*args, **kwargs):
        raise ValueError('secret-key-and-private-runtime-value')

    monkeypatch.setattr(worker_lifecycle, 'status', secret_failure)
    assert agentmesh.main(['worker-status', '--runtime', '/unused']) == 1
    output = capsys.readouterr().err
    assert 'secret-key-and-private-runtime-value' not in output
    assert json.loads(output)['reason'] == 'details withheld; inspect diagnostic stage'


def test_worker_status_private_runtime_error_is_actionable_without_reading_secret(tmp_path):
    runtime = tmp_path / 'runtime.json'
    runtime.write_text('secret-content-must-not-be-parsed-or-disclosed')
    runtime.chmod(0o644)
    process = subprocess.run([sys.executable, str(Path(__file__).with_name('agentmesh.py')),
                              'worker-status', '--runtime', str(runtime)],
                             capture_output=True, text=True, timeout=60)
    assert process.returncode == 1
    error = json.loads(process.stderr)
    assert error['error'] == 'ValueError'
    assert error['reason'] != 'details withheld; inspect diagnostic stage'
    assert error['stage'] in ('signed_packets.read_local', 'windows_acl.validate', 'windows_acl.apply')
    assert 'secret-content-must-not-be-parsed-or-disclosed' not in process.stderr


def test_cli_inspect_install_without_database_is_readonly(tmp_path):
    import memory_sync
    local = tmp_path / 'local'
    data = local / 'data'
    data.mkdir(parents=True)
    db = data / 'mac.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    runtime = data / 'runtime.json'
    runtime.write_text(json.dumps({'database': str(db), 'exchange': str(exchange), 'node': 'mac'}))
    entry = Path(__file__).with_name('agentmesh.py')
    command = [sys.executable, str(entry), 'inspect-install', '--runtime', str(runtime)]
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)['status'] == 'ready'
    assert 'private_key' not in run.stdout


def test_cli_wizard_status_uses_existing_runtime_without_creating_identity(tmp_path):
    import memory_sync
    data = tmp_path / 'local' / 'data'
    data.mkdir(parents=True)
    db = data / 'mac.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    runtime = data / 'runtime.json'
    runtime.write_text(json.dumps({'database': str(db), 'exchange': str(exchange), 'node': 'mac'}))
    before = db.read_bytes(), runtime.read_bytes()
    entry = Path(__file__).with_name('agentmesh.py')
    run = subprocess.run([sys.executable, str(entry), 'wizard-status', '--runtime', str(runtime)],
                         text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report['policy'] == 'legacy'
    assert report['wizard_step'] == 'prerequisites'
    assert (db.read_bytes(), runtime.read_bytes()) == before
    assert not (data.parent / 'identity').exists()
    assert not (data / 'security-wizard.json').exists()


def test_shared_cli_recall_is_readonly_and_rejects_missing_database(tmp_path):
    cli = Path(__file__).with_name('agentmesh.py')
    assert cli.exists(), 'shared CLI is missing'
    db = tmp_path / 'local.db'
    sqlite_memory.init_database(db)
    before = db.read_bytes()
    process = subprocess.run([sys.executable, str(cli), '--database', str(db),
                              'recall', 'memory'], text=True, capture_output=True)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)['query'] == 'memory'
    assert db.read_bytes() == before
    missing = tmp_path / 'missing.db'
    process = subprocess.run([sys.executable, str(cli), '--database', str(missing),
                              'recall', 'memory'], text=True, capture_output=True)
    assert process.returncode != 0
    assert not missing.exists()


def test_shared_cli_reports_blocked_summary_as_failure_but_preserves_sync(tmp_path):
    import memory_sync
    db = tmp_path / 'local.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    config = tmp_path / 'workflow.json'
    config.write_text(json.dumps({'ingest': False, 'summarize': True, 'summary': {'command': []}}))
    process = subprocess.run([sys.executable, str(Path(__file__).with_name('agentmesh.py')),
                              '--database', str(db), '--exchange', str(exchange),
                              '--config', str(config), 'once'], text=True, capture_output=True)
    assert process.returncode == 1
    assert not process.stderr
    assert json.loads(process.stdout)['workflow']['summary']['status'] == 'blocked'
    assert json.loads((exchange / 'status/mac.json').read_text())['sync']['invalid'] == 0


def test_ingest_exit_status_matches_reported_errors(tmp_path, monkeypatch, capsys):
    import agentmesh
    import ingest_sessions
    db = tmp_path / 'local.db'
    sqlite_memory.init_database(db)
    report = {'files': 1, 'errors': 0, 'error_classes': []}
    monkeypatch.setattr(ingest_sessions, 'ingest', lambda *a, **k: dict(report))
    assert agentmesh.main(['--database', str(db), 'ingest']) == 0
    report.update(errors=1, error_classes=['JSONDecodeError'])
    assert agentmesh.main(['--database', str(db), 'ingest']) == 1
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])['errors'] == 1


def test_once_transient_exchange_exits_one(tmp_path, monkeypatch, capsys):
    import sync_worker
    report = {'cycle': {'receive': {'unavailable': True}}, 'failure_kind': 'transient'}
    monkeypatch.setattr(sync_worker, 'run_once', lambda *a, **kw: report)
    import agentmesh
    db = tmp_path / 'local.db'
    sqlite_memory.init_database(db)
    assert agentmesh.main(['--database', str(db), '--exchange', str(tmp_path / 'exchange'), 'once']) == 1
    assert json.loads(capsys.readouterr().out)['failure_kind'] == 'transient'


def test_protocol_capability_and_redacted_conflicts_cli(tmp_path,capsys):
    import agentmesh
    from test_conflict_commands import setup_conflict
    assert agentmesh.main(['protocol-capability'])==0
    import json
    assert json.loads(capsys.readouterr().out)['protocol_capability']==1
    args,p,q=setup_conflict(tmp_path)
    assert agentmesh.main(['conflicts','--runtime',str(args['runtime']),'--json'])==0
    report=json.loads(capsys.readouterr().out)
    assert report['conflicts'] and 'last_event_id' not in json.dumps(report)
    assert agentmesh.main(['conflicts','--runtime',str(args['runtime']),'--json','--show-content'])==1


def test_resolution_errors_are_fixed_and_actionable():
    from cli_errors import report
    value=report(ValueError('stale resolution plan'),'resolve')
    assert value.get('reason')=='stale resolution plan'
    assert 'preview' in value['next_action'].lower()
    secret=report(ValueError('private row secret'),'resolve')
    assert 'private row secret' not in __import__('json').dumps(secret)
