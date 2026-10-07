import importlib.util
from pathlib import Path
import zipfile

import pytest


def load_bootstrap():
    path = Path(__file__).with_name('bootstrap_windows.py')
    assert path.exists(), 'Windows bootstrap implementation missing'
    spec = importlib.util.spec_from_file_location('omp_windows_bootstrap', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_rejects_traversal_without_writing_files(tmp_path):
    archive = tmp_path / 'package.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('good.py', 'print(1)')
        z.writestr('../escaped.py', 'bad')
    with pytest.raises(ValueError, match='unsafe'):
        load_bootstrap().extract_package(archive, tmp_path / 'app')
    assert not (tmp_path / 'escaped.py').exists()
    assert not (tmp_path / 'app/good.py').exists()


def test_prepare_baseline_verifies_hash_and_keeps_data_outside_exchange(tmp_path):
    import gzip
    import hashlib
    import json
    app = tmp_path / 'app'
    app.mkdir()
    baseline = b'{"format":"omp-sqlite-snapshot-v1","tables":[]}\n'
    (app / 'bootstrap-manifest.json').write_text(json.dumps({'snapshot_sha256': hashlib.sha256(baseline).hexdigest()}))
    (app / 'baseline.jsonl.gz').write_bytes(gzip.compress(baseline))
    data = tmp_path / 'private-data'
    m = load_bootstrap()
    result = m.prepare_baseline(app, data)
    assert result.read_bytes() == baseline
    (app / 'baseline.jsonl.gz').write_bytes(gzip.compress(b'corrupt'))
    with pytest.raises(ValueError, match='checksum'):
        m.prepare_baseline(app, tmp_path / 'second-data')
    assert not (tmp_path / 'second-data/baseline.jsonl').exists()


def test_portable_ingest_works_without_installed_project(tmp_path):
    import json
    import subprocess
    import sys
    root = Path(__file__).parent
    db = tmp_path / 'local.db'
    transcript = tmp_path / 'session.jsonl'
    transcript.write_text(json.dumps({'type': 'message', 'id': 'one', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'portable ingestion'}]}}) + '\n')
    base = [sys.executable, '-S', str(root / 'sqlite_memory.py')]
    first = subprocess.run(base + ['init', str(db)], capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    ingested = subprocess.run(base + ['ingest', str(db), str(transcript)], capture_output=True, text=True)
    assert ingested.returncode == 0, ingested.stderr
    assert json.loads(ingested.stdout)['inserted'] == 1


def test_ingest_allocates_local_ids_after_sync_initialization(tmp_path):
    import json
    import sqlite3
    import sqlite_memory
    import memory_sync
    db = tmp_path / 'synced.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'windows', '00000000-0000-4000-8000-000000000001')
    transcript = tmp_path / 'session.jsonl'
    transcript.write_text(json.dumps({'type': 'message', 'id': 'one', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'local id ingestion'}]}}) + '\ninvalid json\n')
    assert sqlite_memory.ingest_file(db, transcript)['inserted'] == 1
    assert sqlite_memory.ingest_file(db, transcript)['inserted'] == 0
    with sqlite3.connect(db) as c:
        for table in ['observation_events', 'ingestion_errors']:
            ident = c.execute('SELECT id FROM ' + table).fetchone()[0]
            assert 2**40 <= ident < 2**41


@pytest.mark.parametrize('node', ['mac', 'windows', 'linux'])
def test_installer_places_database_outside_exchange_and_is_idempotent(tmp_path, node, capsys):
    import gzip
    import hashlib
    import json
    import shutil
    root = Path(__file__).parent
    exchange = tmp_path / 'exchange'
    exchange.mkdir()
    (exchange / '.stfolder').mkdir()
    tables = ['source_sessions', 'observation_events', 'ingestion_cursors', 'ingestion_errors', 'memory_items', 'memory_sources', 'memory_summaries', 'summary_state']
    snapshot_bytes = (json.dumps({'format': 'omp-sqlite-snapshot-v1', 'tables': tables}) + '\n').encode()
    manifest = {'group_id': '00000000-0000-4000-8000-000000000001', 'snapshot_sha256': hashlib.sha256(snapshot_bytes).hexdigest(), 'table_counts': dict.fromkeys(tables, 0)}
    with zipfile.ZipFile(exchange / 'AgentMesh-bootstrap-v1.zip', 'w') as package:
        for name in ['sqlite_memory.py', 'memory_sync.py', 'schema.sql', 'sync_worker.py']:
            package.write(root / name, name)
        package.writestr('bootstrap-manifest.json', json.dumps(manifest))
        package.writestr('baseline.jsonl.gz', gzip.compress(snapshot_bytes))
    checksum = hashlib.sha256((exchange / 'AgentMesh-bootstrap-v1.zip').read_bytes()).hexdigest()
    (exchange / 'agentmesh-package.json').write_text(json.dumps({'archive_sha256': checksum}))
    module = load_bootstrap()
    local = tmp_path / 'local'
    messages = []
    first = module.install(exchange, local, node=node, progress=messages.append)
    assert any('Verifying package' in message for message in messages)
    assert any('Importing' in message for message in messages)
    assert any('Initializing sync' in message for message in messages)
    assert any('Ready' in message for message in messages)
    assert Path(first['database']).is_file()
    config_path = Path(first['database']).parent / 'workflow.json'
    config = json.loads(config_path.read_text())
    assert config['ingest'] is True and config['summarize'] is False
    assert 'command' not in config
    config['ingest'] = False
    config_path.write_text(json.dumps(config))
    module.install(exchange, local, node=node)
    assert json.loads(config_path.read_text())['ingest'] is False
    assert not Path(first['database']).is_relative_to(exchange)
    assert first['node'] == node
    second = module.install(exchange, local, node=node)
    assert second['database'] == first['database']
    assert module.main(['--exchange', str(exchange), '--local-dir', str(local), '--node', node, '--once']) == 0
    acknowledgment = json.loads((exchange / ('status/' + node + '.json')).read_text())
    assert acknowledgment['node'] == node
    assert acknowledgment['counts'] == dict.fromkeys(tables, 0)
    with pytest.raises(ValueError, match='outside'):
        module.install(exchange, exchange / 'bad-local')


def test_cli_announces_start_before_package_validation(tmp_path, capsys):
    result = load_bootstrap().main(['--exchange', str(tmp_path / 'missing'), '--once'])
    assert result == 1
    assert capsys.readouterr().out.startswith('AgentMesh bootstrap starting')


def test_progress_heartbeat_displays_current_stage(capsys):
    import time
    with load_bootstrap().ConsoleProgress(interval=0.02) as progress:
        progress('[5/6] Initializing sync revisions')
        time.sleep(0.07)
    output = capsys.readouterr().out
    assert 'elapsed' in output
    assert '[5/6] Initializing sync revisions' in output
