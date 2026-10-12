import importlib.util
import json
from pathlib import Path

import memory_sync
import sqlite_memory
import pytest
import sync_worker


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


def test_bounded_worker_restart_respects_daily_boot_gate(tmp_path, monkeypatch, capsys):
    import sync_worker
    import digest_scheduler
    import test_bounded_digest as fixtures
    db = tmp_path / 'local.db'
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
    fixture = fixtures.BoundedTests()
    fixture.db = db
    settings: dict = dict(ingest=False, summarize=True, summary_backend='bounded',
                    summary=dict(root=str(tmp_path / 'export'), boot_id='os-boot-1',
                                 now='2026-10-08T10:00:00+00:00'))
    config = tmp_path / 'workflow.json'
    config.write_text(json.dumps(settings))
    calls = []
    def extractor(request, *, model):
        ids = [e['id'] for e in json.loads(request['input'][0]['content'])['events']]
        calls.append(ids)
        return dict(payload=dict(items=[], reviewed_event_ids=ids, conflicts=[]),
                    provider='synthetic-test', model=model, api_calls=1, usage={})
    tick = digest_scheduler.tick
    monkeypatch.setattr(digest_scheduler, 'tick',
                        lambda database, **options: tick(database, extractor=extractor, **options))
    argv = [str(db), str(exchange), '--once', '--workflow-config', str(config)]
    def once(extra=()):
        assert sync_worker.main([*argv, *extra]) == 0
        stdout = json.loads(capsys.readouterr().out)
        assert stdout == json.loads((exchange / 'status/mac.json').read_text())
        return stdout['workflow']['summary']

    fixtures.BoundedTests.event(fixture, 1, 'First window.')
    assert once()['status'] == 'completed'
    prior = digest_scheduler.read_state(db)
    fixtures.BoundedTests.event(fixture, 2, 'Arrival after successful window.')
    assert once() == dict(status='scheduled', llm_api_calls=0)
    assert calls == [[1]]
    assert digest_scheduler.read_state(db) == prior
    import bounded_digest
    assert [e['id'] for e in bounded_digest.prepare(db)['events']] == [2]
    assert once(['--force-summary'])['status'] == 'completed'
    assert calls == [[1], [2]]
    assert bounded_digest.prepare(db)['events'] == []
    fixtures.BoundedTests.event(fixture, 3, 'Arrival before next OS boot.')
    settings['summary']['boot_id'] = 'os-boot-2'
    config.write_text(json.dumps(settings))
    assert once()['status'] == 'completed'
    assert calls == [[1], [2], [3]]
    assert digest_scheduler.read_state(db)['success']['boot_id'] == 'os-boot-2'
    assert bounded_digest.prepare(db)['events'] == []


def test_ingestion_error_count_marks_worker_cycle_failed():
    import sync_worker
    assert sync_worker.failed({'workflow': {'ingestion': {'errors': 1, 'error_classes': ['JSONDecodeError']}}})
    assert not sync_worker.failed({'workflow': {'ingestion': {'errors': 0}}})
    assert sync_worker.failed({'workflow': {'summary': {'status': 'stale'}}})


def test_publish_or_exchange_unavailable_marks_worker_cycle_failed():
    import sync_worker
    healthy = {'publish': {'published': 0, 'unavailable': False},
               'receive': {'applied': 0, 'pending': 0, 'conflict': 0, 'invalid': 0, 'unavailable': False}}
    assert not sync_worker.failed({'cycle': healthy})
    assert sync_worker.failed({'cycle': {**healthy, 'publish': {'published': 0, 'unavailable': True,
                                                               'error': 'Permission denied'}}})
    assert sync_worker.failed({'cycle': {**healthy, 'receive': {**healthy['receive'], 'unavailable': True}}})
    assert sync_worker.failed({'cycle': healthy, 'workflow': {'summary': {'status': 'committed'},
        'summary_sync': {**healthy, 'publish': {'published': 1, 'unavailable': True, 'error': 'disk full'}}}})


@pytest.mark.parametrize('report, permanent', [
    ({}, False),
    ({'cycle': {'receive': {'unavailable': True}}}, False),
    ({'cycle': {'publish': {'error': 'OSError'}}}, False),
    ({'workflow': {'summary_sync': {'receive': {'error': 'OSError'}}}}, False),
    ({'sync': {'conflict': 1}}, True),
    ({'sync': {'invalid': 1}}, True),
    ({'workflow': {'ingestion': {'errors': 1}}}, True),
    ({'workflow': {'summary': {'status': 'blocked'}}}, True),
    ({'error': 'ValueError'}, True),
    ({'sync': {'invalid': 1}, 'cycle': {'receive': {'unavailable': True}}}, True),
])
def test_failure_classification(report, permanent):
    assert sync_worker.permanent_failure(report) is permanent
    assert sync_worker.failed(report) is bool(report)


def test_missing_exchange_reports_transient_and_recovers(tmp_path):
    db, exchange = tmp_path / 'local.db', tmp_path / 'exchange'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
    report = sync_worker.run_once(db, exchange)
    assert report['failure_kind'] == 'transient'
    assert report['warning'] == 'exchange unavailable'
    assert not exchange.exists()
    (exchange / '.stfolder').mkdir(parents=True)
    assert sync_worker.run_once(db, exchange)['failure_kind'] is None


def test_receive_io_error_is_not_invalid_packet(tmp_path, monkeypatch):
    db, exchange = tmp_path / 'local.db', tmp_path / 'exchange'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
    packet = exchange / 'changes/windows/packet.json'
    packet.parent.mkdir(parents=True)
    packet.write_text('{}')
    original = Path.read_text
    def read(path, *a, **kw):
        if path == packet:
            raise OSError('temporarily offline')
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'read_text', read)
    report = memory_sync.receive(db, exchange)
    assert report['unavailable']
    assert report['invalid'] == 0
    assert memory_sync.status(db)['invalid'] == 0


def test_status_write_failure_preserves_permanent_failure(tmp_path, monkeypatch):
    db, exchange = tmp_path / 'local.db', tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
    packet = exchange / 'changes/windows/bad.json'
    packet.parent.mkdir(parents=True)
    packet.write_text('{}')
    original = Path.mkdir
    def mkdir(path, *a, **kw):
        if path == exchange / 'status':
            raise OSError('offline')
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    report = sync_worker.run_once(db, exchange)
    assert report['failure_kind'] == 'permanent'
    assert report['status_write']['unavailable']


def test_foreground_watch_retries_transient_at_normal_interval(monkeypatch):
    calls, waits = [], []
    def cycle(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return {'cycle': {'receive': {'unavailable': True}}}
    monkeypatch.setattr(sync_worker, 'run_once', cycle)
    monkeypatch.setattr('terminal_progress.TerminalProgress.wait', lambda self, interval, message: waits.append(interval))
    assert sync_worker.main(['db', 'exchange', '--interval', '7']) == 0
    assert len(calls) == 2 and waits == [7]


@pytest.mark.parametrize('step', ['publish', 'receive'])
def test_exchange_operation_oserror_is_transient(tmp_path, monkeypatch, step):
    db, exchange = tmp_path / 'local.db', tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
    if step == 'publish':
        original = Path.mkdir
        def mkdir(path, *a, **kw):
            if path == exchange / 'changes/mac':
                raise OSError('offline')
            return original(path, *a, **kw)
        monkeypatch.setattr(Path, 'mkdir', mkdir)
    else:
        original = Path.glob
        def glob(path, *a, **kw):
            if path == exchange / 'changes':
                raise OSError('offline')
            return original(path, *a, **kw)
        monkeypatch.setattr(Path, 'glob', glob)
    report = sync_worker.run_once(db, exchange)
    assert report['cycle'][step]['unavailable']
    assert report['failure_kind'] == 'transient'
    assert report['warning'] == 'exchange unavailable'
    assert not sync_worker.permanent_failure(report)
    assert json.loads((exchange / 'status/mac.json').read_text()) == report
