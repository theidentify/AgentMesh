import json
import sqlite3
from unittest.mock import patch

import pytest
import ingest_sessions
import sqlite_memory
import memory_sync
import summarize_memory


def fixture(tmp_path):
    database = tmp_path/'staging.db'
    sqlite_memory.init_database(database)
    root = tmp_path/'transcripts'
    root.mkdir()
    return database, root


def write(path, ident, timestamp):
    value = {'type':'message', 'id':ident, 'timestamp':timestamp,
             'message':{'role':'user', 'content':[{'type':'text', 'text':ident}]}}
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value)+'\n')


def rows(database, table):
    with sqlite3.connect(database) as c:
        return c.execute('SELECT * FROM '+table).fetchall()


def test_p7_5_now_no_old_events_or_summarize(tmp_path):
    database, root = fixture(tmp_path)
    memory_sync.initialize(database, 'windows', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
    write(root/'old.jsonl', 'old', '2020-01-01T00:00:00Z')
    report = ingest_sessions.ingest(database, roots={'omp':root}, start_at='now')
    assert report['inserted'] == report['errors'] == 0
    assert rows(database, 'observation_events') == []
    assert len(rows(database, 'ingestion_cursors')) == 1
    with patch.object(summarize_memory, 'invoke_command') as invoke:
        assert summarize_memory.summarize_once(database, primary_node='windows')['status'] == 'idle'
        invoke.assert_not_called()


def test_p7_5_cutoff_persists_for_append_and_new_files(tmp_path):
    database, root = fixture(tmp_path)
    write(root/'mixed.jsonl', 'old', '2020-01-01T00:00:00Z')
    write(root/'mixed.jsonl', 'new', '2026-10-22T01:00:00Z')
    assert ingest_sessions.ingest(database, roots={'omp':root}, start_at='2026-10-22T00:00:00Z')['inserted'] == 1
    write(root/'mixed.jsonl', 'late-old', '2020-01-01T00:00:00Z')
    write(root/'copied-old.jsonl', 'copied-old', '2020-01-01T00:00:00Z')
    write(root/'new.jsonl', 'latest', '2026-10-22T02:00:00Z')
    assert ingest_sessions.ingest(database, roots={'omp':root})['inserted'] == 1
    with sqlite3.connect(database) as c:
        assert c.execute('SELECT content FROM observation_events ORDER BY id').fetchall() == [('new',), ('latest',)]


def test_p7_5_existing_cursors_refused_without_changes(tmp_path):
    database, root = fixture(tmp_path)
    write(root/'a.jsonl', 'old', '2020-01-01T00:00:00Z')
    ingest_sessions.ingest(database, roots={'omp':root})
    before = rows(database, 'ingestion_cursors')
    with pytest.raises(ValueError, match='empty ingestion cursors'):
        ingest_sessions.ingest(database, roots={'omp':root}, start_at='now')
    assert rows(database, 'ingestion_cursors') == before


def test_p7_5_missing_timestamp_rolls_back_file_and_preserves_retry(tmp_path):
    database, root = fixture(tmp_path)
    write(root/'a.jsonl', 'dated', '2026-10-23T00:00:00Z')
    write(root/'a.jsonl', 'undated', None)
    report = ingest_sessions.ingest(database, roots={'omp':root}, start_at='2026-10-22T00:00:00Z')
    assert report['errors'] == 1
    assert rows(database, 'observation_events') == []
    assert rows(database, 'ingestion_cursors') == []
    path = root/'a.jsonl'
    path.write_text(path.read_text().replace('"timestamp": null', '"timestamp": "2026-10-23T00:00:00Z"'))
    assert ingest_sessions.ingest(database, roots={'omp':root})['inserted'] == 2


def test_p7_staging_identity_stable_and_sync_node_authoritative(tmp_path):
    database, root = fixture(tmp_path)
    ingest_sessions.ingest(database, roots={})
    identity = rows(database, 'agentmesh_staging_identity')
    assert len(identity) == 1 and len(identity[0][0]) == 32
    ingest_sessions.ingest(database, roots={})
    assert rows(database, 'agentmesh_staging_identity') == identity
    other = tmp_path/'sync.db'
    sqlite_memory.init_database(other)
    with sqlite3.connect(other) as c:
        c.execute('CREATE TABLE _sync_config(node TEXT)')
        c.execute("INSERT INTO _sync_config VALUES('windows')")
    ingest_sessions.ingest(other, roots={})
    with sqlite3.connect(other) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='agentmesh_staging_identity'").fetchone()


def test_p7_5_cli_start_at(tmp_path, capsys):
    database, root = fixture(tmp_path)
    write(root/'old.jsonl', 'old', '2020-01-01T00:00:00Z')
    assert ingest_sessions.main([str(database), '--root', 'omp='+str(root), '--start-at', 'now']) == 0
    assert json.loads(capsys.readouterr().out)['inserted'] == 0


def test_p7_5_invalid_start_has_no_side_effects(tmp_path):
    database, root = fixture(tmp_path)
    with pytest.raises(ValueError):
        ingest_sessions.ingest(database, roots={}, start_at='2026-10-22')
    with sqlite3.connect(database) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'agentmesh_%'").fetchone()


@pytest.mark.parametrize('agent', ['codex', 'claude'])
def test_p7_5_other_agents_filter_old_events(tmp_path, agent):
    database, root = fixture(tmp_path)
    records = []
    if agent == 'codex':
        records.append({'type':'session_meta', 'payload':{'id':'test-session'}})
    for ident, stamp in [('old', '2020-01-01T00:00:00Z'), ('new', '2026-10-23T00:00:00Z')]:
        if agent == 'codex':
            records.append({'type':'event_msg', 'timestamp':stamp, 'payload':{'type':'item_completed', 'item':{'type':'UserMessage', 'id':ident, 'content':[{'type':'text', 'text':ident}]}}})
        else:
            records.append({'type':'user', 'uuid':ident, 'timestamp':stamp, 'message':{'role':'user', 'content':ident}})
    (root/'a.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in records))
    report = ingest_sessions.ingest(database, roots={agent:root}, start_at='2026-10-22T00:00:00Z')
    assert report['errors'] == 0
    assert report['inserted'] == 1
    with sqlite3.connect(database) as c:
        assert c.execute('SELECT content FROM observation_events').fetchall() == [('new',)]


def test_p7_5_empty_seed_cannot_be_reset(tmp_path):
    database, root = fixture(tmp_path)
    ingest_sessions.ingest(database, roots={}, start_at='2026-10-22T00:00:00Z')
    with pytest.raises(ValueError, match='existing start time'):
        ingest_sessions.ingest(database, roots={}, start_at='2020-01-01T00:00:00Z')
    assert rows(database, 'agentmesh_ingestion_start') == [('2026-10-22T00:00:00+00:00',)]
