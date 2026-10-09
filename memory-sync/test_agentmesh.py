import json
from pathlib import Path
import subprocess
import sys

import sqlite_memory


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
