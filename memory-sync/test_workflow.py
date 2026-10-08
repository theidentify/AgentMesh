import json
import os
import sqlite3
import sys
from pathlib import Path

import memory_sync
import sqlite_memory
import sync_worker

GROUP = '00000000-0000-4000-8000-000000000001'


def test_worker_ingests_native_peer_and_syncs_recall_evidence(tmp_path):
    db, mac = tmp_path / 'windows' / 'local.db', tmp_path / 'mac' / 'local.db'
    db.parent.mkdir()
    mac.parent.mkdir()
    for path, node in [(db, 'windows'), (mac, 'mac')]:
        sqlite_memory.init_database(path)
        memory_sync.initialize(path, node, GROUP)
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    transcript = tmp_path / 'transcripts' / 'session.jsonl'
    transcript.parent.mkdir()
    transcript.write_text(json.dumps({'id': 'session', 'type': 'session', 'cwd': '/demo'}) + '\n' +
        json.dumps({'id': 'e1', 'type': 'message', 'message': {'role': 'user',
        'content': [{'type': 'text', 'text': 'AgentMesh constraint: Keep the database outside exchange.'}]}}) + '\n')
    config = db.parent / 'workflow.json'
    config.write_text(json.dumps({'ingest': True, 'summarize': False,
                                  'roots': {'omp': str(transcript.parent)}}))
    report = sync_worker.run_once(db, exchange, workflow_config=config)
    assert report['workflow']['ingestion']['inserted'] == 1
    assert report['workflow']['summary']['status'] == 'disabled'
    sync_worker.run_once(mac, exchange)
    from recall_memory import recall
    result = recall(mac, 'AgentMesh database exchange')
    assert result['results'][0]['source_agent'] == 'omp'
    assert sync_worker.run_once(mac, exchange)['sync']['conflict'] == 0
    assert result['results'][0]['source_event_id'] == 'e1:text:0'
    second = sync_worker.run_once(db, exchange, workflow_config=config)
    assert second['workflow']['ingestion']['inserted'] == 0
    assert str(transcript) not in json.dumps(report)


def test_failed_provider_retries_without_advancing_success_clock_or_memory(tmp_path):
    import pytest
    import workflow
    from summarize_memory import SummaryError
    db = tmp_path / 'local.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', GROUP)
    source = tmp_path / 'source'
    source.mkdir()
    path = source / 's.jsonl'
    path.write_text(json.dumps({'id': 'e1', 'type': 'message', 'message': {
        'role': 'user', 'content': [{'type': 'text', 'text': 'Do not synchronize a live database.'}]}}) + '\n')
    from ingest_sessions import ingest
    ingest(db, roots={'omp': source})
    settings = {'summarize': True, 'summary': {'command': [str(tmp_path / 'missing-provider')]}}
    with pytest.raises(SummaryError):
        workflow.summarize(db, settings)
    with pytest.raises(SummaryError):
        workflow.summarize(db, settings)
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT last_run FROM _agentmesh_worker_state').fetchone()[0] == 0
        assert c.execute('SELECT lease_until FROM _agentmesh_worker_state').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM memory_items').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM summary_state').fetchone()[0] == 0


def test_primary_configuration_failure_keeps_sync_running_without_fake_summary(tmp_path):
    db = tmp_path / 'mac.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', GROUP)
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    config = tmp_path / 'workflow.json'
    config.write_text(json.dumps({'ingest': False, 'summarize': True,
                                  'summary': {'command': []}}))
    report = sync_worker.run_once(db, exchange, workflow_config=config)
    assert report['workflow']['summary']['status'] == 'blocked'
    assert report['sync']['invalid'] == 0
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT count(*) FROM memory_summaries').fetchone()[0] == 0
