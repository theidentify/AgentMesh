"""Synthetic source contracts; never open application databases."""
import hashlib
from pathlib import Path
import sqlite3
import pytest
from agentmesh_log.migrate.source import open_source, rows, body_for, snapshot


@pytest.fixture(params=['DELETE', 'WAL'])
def database(tmp_path, request):
    path = tmp_path / 'source.db'
    c = sqlite3.connect(path)
    c.executescript((Path(__file__).resolve().parents[3] / 'memory-sync/schema.sql').read_text(encoding='utf-8'))
    c.execute('PRAGMA journal_mode=' + request.param)
    c.execute('CREATE TABLE agentmesh_staging_identity(node TEXT NOT NULL)')
    c.execute("INSERT INTO agentmesh_staging_identity VALUES('test-node')")
    c.execute("INSERT INTO memory_items(id,kind,scope,content,metadata,updated_at) VALUES(1,'fact','global','synthetic memory','{\"evidence\":\"excluded\"}','2020-01-01 00:00:00')")
    c.execute("INSERT INTO memory_summaries(scope,scope_key,content,metadata) VALUES('task','AM-1','summary','{\"event_ids\":[1,true],\"source_event_ids\":[2,\"excluded\"]}')")
    c.commit(); c.close()
    return path


def test_p5_1_source(database, tmp_path):
    before = hashlib.sha256(database.read_bytes()).digest()
    with open_source(database) as c:
        schema = list(c.execute('SELECT * FROM sqlite_master'))
        with pytest.raises(sqlite3.OperationalError):
            c.execute("INSERT INTO memory_items(kind,scope,content) VALUES('fact','global','x')")
        # query_only must not mask a mutation removing mode=ro.
        c.execute('PRAGMA query_only=OFF')
        with pytest.raises(sqlite3.OperationalError):
            c.execute("INSERT INTO memory_items(kind,scope,content) VALUES('fact','global','x')")
    snapshot(database, tmp_path / 'copy.db')
    with open_source(database) as c:
        assert schema == list(c.execute('SELECT * FROM sqlite_master'))
    assert before == hashlib.sha256(database.read_bytes()).digest()
    assert not Path(str(database) + '-journal').exists()


def test_bodies(database):
    values = rows(database)
    item, summary = values
    body, timestamp, fallback = body_for(item, 'work/common', now=1)
    assert body['metadata']['evidence_event_ids'] == []
    assert 'excluded' not in str(body)
    assert timestamp == 1577836800000 and not fallback
    assert len(body['metadata']['row_hash']) == 64
    assert body_for(summary, 'work/common')[0]['metadata']['evidence_event_ids'] == [1, 2]
    assert body_for(item, 'work/other')[0]['metadata']['row_hash'] != body['metadata']['row_hash']


def test_every_summary_row(database):
    # Live versions are range-allocated IDs (1 and 2**41+2), not an order; recall returns both.
    with sqlite3.connect(database) as c:
        c.execute("INSERT INTO memory_summaries(scope,scope_key,version,content) VALUES('project','p',1,'current')")
        c.execute("INSERT INTO memory_summaries(scope,scope_key,version,content) VALUES('project','p',?,'other')", (2**41+2,))
        c.execute("INSERT INTO memory_summaries(scope,scope_key,version,content) VALUES('session','s',1,'never recalled')")
    found = {r['content']: r['reason'] for r in rows(database) if r['table'] == 'memory_summaries'}
    assert found['current'] is None and found['other'] is None
    assert found['never recalled'] == 'not_recalled'
