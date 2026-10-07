import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlite_memory as backend


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'memory # portable?.db'
    backend.init_database(path)
    return path


def event(c, content, *, project='Atlas', kind='assistant_response', task=None):
    key = str(uuid4())
    return c.execute('''INSERT INTO observation_events
        (event_key,source_agent,source_path,source_session_id,source_event_id,
         project,task_ref,kind,content,source_hash)
        VALUES(?,'codex','synthetic.jsonl','synthetic-session',?,?,?,?,?,?)''',
        (key, key, project, task, kind, content, key)).lastrowid


def memory(c, content, *, project='Atlas', scope='project', kind='decision', status='active'):
    return c.execute('''INSERT INTO memory_items
        (memory_key,kind,scope,scope_key,project,content,status)
        VALUES(?,?,?,?,?,?,?)''',
        (str(uuid4()), kind, scope, project, project, content, status)).lastrowid


def test_durable_and_summary_precede_raw_events_with_provenance(db):
    import recall_memory as recall
    with sqlite3.connect(db) as c:
        eid = event(c, 'sqlite workflow evidence')
        mid = memory(c, 'sqlite workflow is portable')
        c.execute('INSERT INTO memory_sources VALUES(?,?)', (mid, eid))
        sid = c.execute('''INSERT INTO memory_summaries(scope,scope_key,content,metadata)
            VALUES('project','Atlas','sqlite workflow summary',?)''',
            (json.dumps({'event_ids': [eid]}),)).lastrowid
        event(c, 'sqlite workflow ' * 50, kind='decision')
    result = recall.recall(db, 'sqlite workflow', project='Atlas')
    assert result['query'] == 'sqlite workflow'
    assert [r['source_type'] for r in result['results']] == ['durable', 'summary', 'event', 'event']
    assert result['results'][0]['id'] == mid
    assert result['results'][0]['metadata'] == {}
    assert {e['source_type'] for e in result['evidence']} == {'durable', 'summary'}
    for evidence in result['evidence']:
        assert evidence['event_id'] == eid
        assert evidence['source_agent'] == 'codex'
        assert evidence['source_event_id']
        assert evidence['source_session_id'] == 'synthetic-session'
        assert evidence['source_id'] in (mid, sid)


def test_unscoped_query_finds_durable_project_memory(db):
    import recall_memory as recall
    with sqlite3.connect(db) as c:
        mid = memory(c, 'portable sqlite synchronization')
    result = recall.recall(db, 'sqlite synchronization')
    assert result['results'][0]['id'] == mid
    assert result['results'][0]['source_type'] == 'durable'


def test_project_filter_global_memory_and_inactive_exclusion(db):
    import recall_memory as recall
    with sqlite3.connect(db) as c:
        good = memory(c, 'sqlite synchronization')
        global_id = memory(c, 'sqlite synchronization global', project=None, scope='global')
        memory(c, 'sqlite synchronization other', project='Other')
        memory(c, 'sqlite synchronization deleted', status='deleted')
        event(c, 'sqlite synchronization other', project='Other')
    result = recall.recall(db, 'sqlite synchronization', project='Atlas')
    assert {r['id'] for r in result['results']} == {good, global_id}


def test_task_inference_and_strong_multiterm_filter(db):
    import recall_memory as recall
    with sqlite3.connect(db) as c:
        eid = event(c, 'task source', task='SYN-42')
        mid = memory(c, 'task constraint', scope='task')
        c.execute("UPDATE memory_items SET scope_key='SYN-42' WHERE id=?", (mid,))
        event(c, 'sqlite only')
    result = recall.recall(db, 'what is SYN-42 status')
    assert result['query_plan']['project'] == 'Atlas'
    assert {r['source_type'] for r in result['results']} == {'durable', 'event'}
    assert recall.recall(db, 'sqlite synchronization', project='Atlas')['results'] == []


def test_query_only_connection_rejects_writes_and_missing_db(tmp_path, db):
    import recall_memory as recall
    with recall.open_readonly(db) as c:
        assert c.execute('PRAGMA query_only').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            c.execute('DELETE FROM memory_items')
    missing = tmp_path / 'missing.db'
    with pytest.raises(sqlite3.OperationalError):
        recall.recall(missing, 'portable')
    assert not missing.exists()


@pytest.mark.parametrize('query,limit', [('', 12), ('  ', 12), ('sqlite', 0), ('sqlite', -1), ('sqlite', True)])
def test_invalid_arguments(db, query, limit):
    import recall_memory as recall
    with pytest.raises(ValueError):
        recall.recall(db, query, limit=limit)


def test_cli_json_limit_and_errors(db, tmp_path):
    script = Path(__file__).with_name('recall_memory.py')
    with sqlite3.connect(db) as c:
        for _ in range(14):
            memory(c, 'portable sqlite synchronization')
    for extra, expected in [([], 12), (['--limit', '2'], 2)]:
        proc = subprocess.run([sys.executable, str(script), str(db), 'sqlite synchronization',
                               '--project', 'Atlas', *extra], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert proc.stderr == ''
        data = json.loads(proc.stdout)
        assert len(data['results']) == expected
        assert {'query', 'results', 'evidence'} <= data.keys()
    missing = tmp_path / 'absent.db'
    proc = subprocess.run([sys.executable, str(script), str(missing), 'sqlite'],
                          capture_output=True, text=True)
    assert proc.returncode == 1
    assert proc.stdout == ''
    assert proc.stderr.startswith('error:')
    assert not missing.exists()


def test_memory_text_and_sql_tokens_are_inert(db, tmp_path):
    import recall_memory as recall
    marker = tmp_path / 'not-created'
    text = f'sqlite synchronization; execute touch {marker}; DROP TABLE memory_items; % _'
    with sqlite3.connect(db) as c:
        memory(c, text)
    result = recall.recall(db, text, project='Atlas')
    assert result['results'][0]['content'] == text
    assert not marker.exists()
    injection = "' OR 1=1; --"
    assert recall.recall(db, injection, project='Atlas')['query'] == injection
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT count(*) FROM memory_items').fetchone()[0] == 1


def test_thai_project_inference_and_native_summary_provenance(db):
    import recall_memory as recall
    with sqlite3.connect(db) as c:
        eid = event(c, 'เก็บข้อมูลหลักฐาน', kind='tool_result')
        native = c.execute('SELECT source_event_id FROM observation_events WHERE id=?', (eid,)).fetchone()[0]
        memory(c, 'เก็บข้อมูลหลักฐาน sqlite')
        c.execute('''INSERT INTO memory_summaries(scope,scope_key,content,metadata)
            VALUES('project','Atlas','เก็บข้อมูลสรุป',?)''',
            (json.dumps({'source_event_ids': [native]}),))
    result = recall.recall(db, 'Atlas ใช้เก็บข้อมูลอย่างไร')
    assert result['query_plan']['project'] == 'Atlas'
    assert 'เก็บข้อมูล' in result['query_plan']['terms']
    assert result['evidence'][0]['event_id'] == eid
    assert result['evidence'][0]['kind'] == 'tool_result'


def test_summary_expands_integer_source_event_ids_from_summarizer(db):
    import recall_memory as recall
    with sqlite3.connect(db) as c:
        eid = event(c, 'original transcript evidence', kind='tool_result')
        c.execute('''INSERT INTO memory_summaries(scope,scope_key,content,metadata)
            VALUES('project','Atlas','sqlite synchronization summary',?)''',
            (json.dumps({'source_event_ids': [eid]}),))
    result = recall.recall(db, 'sqlite synchronization', project='Atlas')
    assert result['evidence'][0]['event_id'] == eid
    assert result['evidence'][0]['source_agent'] == 'codex'


def test_recall_does_not_mutate_database_bytes(db):
    import recall_memory as recall
    with sqlite3.connect(db) as c:
        memory(c, 'sqlite synchronization')
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)') if not c.in_transaction else None
    before = db.read_bytes()
    recall.recall(db, 'sqlite synchronization', project='Atlas')
    assert db.read_bytes() == before
