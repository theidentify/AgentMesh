import importlib.util
import json
from pathlib import Path

import memory_sync
import sqlite_memory


def test_cycle_publishes_operator_status_without_database_paths(tmp_path):
    module_path = Path(__file__).with_name('sync_worker.py')
    assert module_path.exists(), 'sync worker missing'
    spec = importlib.util.spec_from_file_location('agentmesh_worker', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = tmp_path / 'local.db'
    exchange = tmp_path / 'exchange'
    exchange.mkdir()
    (exchange / '.stfolder').mkdir()
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
    result = module.run_once(db, exchange)
    report = json.loads((exchange / 'status/mac.json').read_text())
    assert report['node'] == 'mac'
    assert report['counts']['observation_events'] == 0
    assert report['sync']['invalid'] == 0
    assert 'database' not in report and str(db) not in json.dumps(report)
    assert result['sync']['conflict'] == 0


def test_worker_console_is_english_and_stdout_remains_json(tmp_path, capsys):
    import sync_worker
    db = tmp_path / 'local.db'
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'windows', '00000000-0000-4000-8000-000000000001')
    assert sync_worker.main([str(db), str(exchange), '--once']) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['node'] == 'windows'
    assert 'Sync completed | Pending 0 | Conflicts 0 | Invalid 0' in output.err
    assert output.err.isascii()
