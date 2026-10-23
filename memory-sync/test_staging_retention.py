"""Retention gates on disposable staging files only."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest
import agentmesh
import bounded_digest
import digest_scheduler
import memory_sync
import ingest_sessions
import retention_audit
import sqlite_memory
import staging_retention as retention
import summarize_memory
from sqlite_export import publish, check_links, name

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
OLD = '2020-01-01T00:00:00+00:00'
CONSUMER = summarize_memory.DEFAULT_CONSUMER


@pytest.fixture
def staging(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    root = tmp_path / 'home'; root.mkdir()
    monkeypatch.setenv('AGENTMESH_HOME', str(root))
    db = tmp_path / 'staging.db'
    sqlite_memory.init_database(db)
    for filename, stamp in [('old.jsonl', OLD), ('newest.jsonl', '2020-02-01T00:00:00+00:00')]:
        path = tmp_path / filename; path.write_text('')
        seconds = datetime.fromisoformat(stamp).timestamp(); os.utime(path, (seconds, seconds))
        info = path.stat()
        with closing(sqlite3.connect(db)) as c, c:
            c.execute('INSERT INTO ingestion_cursors(source_path,file_identity,byte_offset,updated_at) VALUES(?,?,?,?)',
                      (str(path), f'{info.st_dev}:{info.st_ino}', 0, stamp))
    add(db, 1)
    return db


def add(db, ident, *, kind='user_request', stamp=OLD, filename='old.jsonl', covered=True):
    with closing(sqlite3.connect(db)) as c, c:
        c.execute('''INSERT INTO observation_events(id,event_key,source_path,source_event_id,occurred_at,project,kind,content,source_hash)
            VALUES(?,?,?,?,?,'demo',?,?,?)''',
            (ident, sha256(str(ident).encode()).hexdigest(), str(db.parent/filename), 'native-'+str(ident), stamp, kind,
             'retentionuniquetoken'+str(ident), 'hash'))
        if covered: c.execute('INSERT INTO summary_state(consumer,last_event_id) VALUES(?,?)', (CONSUMER+':event:'+str(ident), ident))


def run(db, **kwargs):
    return retention.retain(db, now=NOW, **kwargs)


def ids(db):
    with closing(sqlite3.connect(db)) as c, c: return [r[0] for r in c.execute('SELECT id FROM observation_events ORDER BY id')]


def snapshot(db):
    with closing(sqlite3.connect(db)) as c, c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {table: c.execute('SELECT * FROM "'+table+'"').fetchall() for table in tables}


@pytest.mark.parametrize('status', ['active', 'resolved', 'superseded', 'deleted'])
def test_keeps_memory_sources_for_every_status(staging, status):
    add(staging, 2)
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute("INSERT INTO memory_items(id,kind,scope,content,status) VALUES(1,'fact','global','memory',?)", (status,))
        c.execute('INSERT INTO memory_sources VALUES(1,2)')
    result = run(staging, apply=True)
    assert ids(staging) == [2]
    assert result['kept_evidence'] == result['deleted'] == 1


@pytest.mark.parametrize('field', ['event_ids', 'batch_event_ids', 'source_event_ids'])
@pytest.mark.parametrize('table', ['memory_items', 'memory_summaries'])
def test_keeps_metadata_evidence(staging, field, table):
    add(staging, 2)
    with closing(sqlite3.connect(staging)) as c, c:
        meta = json.dumps({field: [2]})
        if table == 'memory_items':
            c.execute("INSERT INTO memory_items(kind,scope,content,metadata) VALUES('fact','global','memory',?)", (meta,))
        else:
            c.execute("INSERT INTO memory_summaries(scope,scope_key,content,metadata) VALUES('project','demo','memory',?)", (meta,))
    assert run(staging, apply=True)['kept_evidence'] == 1
    assert ids(staging) == [2]


def test_keeps_native_summary_evidence_and_evidence_history(staging):
    add(staging, 2); add(staging, 3)
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute("INSERT INTO memory_summaries(scope,scope_key,content,metadata) VALUES('project','demo','memory',?)",
                  (json.dumps({'source_event_ids': ['native-2'], 'evidence_history': [{'evidence': [{'event_id': 3}]}]}),))
    assert run(staging, apply=True)['kept_evidence'] == 2
    assert ids(staging) == [2, 3]


@pytest.mark.parametrize('kind', ['user_request', 'assistant_response'])
def test_keeps_pending_conversation_rows(staging, kind):
    add(staging, 2, covered=False, kind=kind)
    result = run(staging, apply=True)
    assert 2 in ids(staging)
    assert result['kept_pending'] == (2 if kind == 'assistant_response' else 1)


def test_requires_every_known_consumer(staging):
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute("INSERT INTO summary_state VALUES('agentmesh/other',0,CURRENT_TIMESTAMP)")
    assert run(staging, apply=True)['kept_pending'] == 1
    assert ids(staging) == [1]


def test_requires_configured_consumer_before_first_receipt(staging):
    (staging.parent/'workflow.json').write_text(json.dumps({'summary': {'consumer': 'agentmesh/other'}}))
    assert run(staging, apply=True)['kept_pending'] == 1
    assert ids(staging) == [1]


@pytest.mark.parametrize('stamp', ['2026-10-01T00:00:00Z', '2026-09-22T00:00:00Z', 'invalid', None])
def test_keeps_new_or_invalid_timestamp(staging, stamp):
    add(staging, 2, stamp=stamp)
    result = run(staging, apply=True)
    if stamp in ('invalid', None):
        assert result['kept_pending'] == 2
        assert ids(staging) == [1, 2]
    else:
        assert result['kept_too_new'] == 1
        assert ids(staging) == [2]


@pytest.mark.parametrize('mode', ['latest', 'recent_cursor', 'bad_cursor', 'missing_cursor', 'advanced_file', 'recent_mtime', 'newest_mtime', 'recent_growing'])
def test_keeps_active_or_unverifiable_session(staging, mode):
    path = staging.parent/'old.jsonl'
    with closing(sqlite3.connect(staging)) as c, c:
        if mode == 'latest': c.execute("UPDATE ingestion_cursors SET updated_at='2020-03-01T00:00:00Z' WHERE source_path=?", (str(path),))
        elif mode == 'recent_cursor': c.execute('UPDATE ingestion_cursors SET updated_at=? WHERE source_path=?', (NOW.isoformat(),str(path)))
        elif mode == 'bad_cursor': c.execute("UPDATE ingestion_cursors SET updated_at='bad' WHERE source_path=?", (str(path),))
        elif mode == 'missing_cursor': c.execute('DELETE FROM ingestion_cursors WHERE source_path=?', (str(path),))
    if mode == 'advanced_file':
        path.write_text('new line\n'); os.utime(path, (1577836800, 1577836800))
    elif mode == 'newest_mtime': os.utime(path, (1583020800,1583020800))
    elif mode == 'recent_growing': path.write_text('new line\n')
    elif mode == 'recent_mtime': os.utime(path, (NOW.timestamp(),NOW.timestamp()))
    assert run(staging, apply=True)['kept_active_session'] == 1
    assert ids(staging) == [1]


def test_keeps_pending_digest_window_provenance(staging):
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute('CREATE TABLE _agentmesh_digest_windows(consumer TEXT,state TEXT)')
        c.execute('INSERT INTO _agentmesh_digest_windows VALUES(?,?)', (CONSUMER,json.dumps({'pending': {'eligible_ids': [1]}})))
    assert run(staging, apply=True)['kept_evidence'] == 1
    assert ids(staging) == [1]


def test_dry_run_is_byte_identical_and_counts_only(staging, capsys):
    before = staging.read_bytes(); data = snapshot(staging)
    assert agentmesh.main(['--database', str(staging), 'staging', 'retention', '--json']) == 0
    output = capsys.readouterr().out
    assert json.loads(output)['candidates'] == 1
    assert 'retentionuniquetoken' not in output and str(staging) not in output
    assert staging.read_bytes() == before
    assert snapshot(staging) == data


def test_fts_and_protected_tables_are_preserved_correctly(staging):
    memory_sync.initialize(staging, 'mac', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
    before = snapshot(staging)
    with closing(sqlite3.connect(staging)) as c, c:
        assert c.execute("SELECT rowid FROM events_fts WHERE events_fts MATCH 'retentionuniquetoken1'").fetchall() == [(1,)]
    assert run(staging, apply=True)['deleted'] == 1
    with closing(sqlite3.connect(staging)) as c, c:
        assert c.execute("SELECT rowid FROM events_fts WHERE events_fts MATCH 'retentionuniquetoken1'").fetchall() == []
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    assert sqlite_memory.search(staging, 'retentionuniquetoken1') == []
    after = snapshot(staging)
    for table in before:
        if table != 'observation_events' and not table.startswith('events_fts'):
            assert after[table] == before[table], table


def test_missing_fts_trigger_refuses_atomically(staging):
    with closing(sqlite3.connect(staging)) as c, c: c.execute('DROP TRIGGER events_fts_delete')
    before = snapshot(staging)
    with pytest.raises(ValueError, match='FTS'): run(staging, apply=True)
    assert snapshot(staging) == before


def test_tombstones_prevent_reingest_after_cursor_reset(staging):
    path = staging.parent/'old.jsonl'
    path.write_text(json.dumps({'type':'message','id':'old','timestamp':OLD,
                              'message':{'role':'user','content':[{'type':'text','text':'old transcript'}]}})+'\n')
    assert sqlite_memory.ingest_file(staging, path)['inserted'] == 1
    os.utime(path, (1577836800,1577836800))
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute('UPDATE ingestion_cursors SET updated_at=? WHERE source_path=?', (OLD,str(path)))
        ident, key = c.execute('SELECT id,event_key FROM observation_events WHERE content=?', ('old transcript',)).fetchone()
        c.execute('INSERT INTO summary_state(consumer,last_event_id) VALUES(?,?)', (CONSUMER+':event:'+str(ident),ident))
    assert run(staging, apply=True)['deleted'] == 2
    with closing(sqlite3.connect(staging)) as c, c:
        assert c.execute('SELECT event_key FROM observation_event_tombstones WHERE event_key=?', (sha256(key.encode()).hexdigest(),)).fetchone()
        c.execute("UPDATE ingestion_cursors SET file_identity='reset',byte_offset=0,line_number=0")
    assert sqlite_memory.ingest_file(staging, path)['inserted'] == 0
    assert ids(staging) == []


def test_no_resummarize_or_export_regeneration_after_retention(staging):
    memory_sync.initialize(staging, 'mac', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
    root = staging.parent/'export'
    calls = []
    def extractor(request, *, model):
        calls.append(request)
        batch = json.loads(request['input'][0]['content'])['events']
        return dict(payload=dict(items=[],reviewed_event_ids=[e['id'] for e in batch],conflicts=[]),
                    provider='fixture',model=model,api_calls=1,usage={})
    with closing(sqlite3.connect(staging)) as c, c: c.execute('DELETE FROM summary_state')
    assert digest_scheduler.tick(staging, root=root, extractor=extractor, force=True)['batches'] == 1
    assert len(calls) == 1
    assert run(staging, apply=True)['deleted'] == 1
    before = {p: p.read_bytes() for p in root.rglob('*.md') if p.name != '_Index.md'}
    assert digest_scheduler.tick(staging, root=root, extractor=extractor, force=True)['batches'] == 0
    assert len(calls) == 1
    with patch.object(summarize_memory, 'invoke_command') as invoke:
        assert summarize_memory.summarize_once(staging)['status'] == 'idle'
        invoke.assert_not_called()
    assert all(p.read_bytes() == value for p, value in before.items())
    assert publish(staging, root)['events'] == 0


def test_partial_session_export_keeps_retired_anchors_without_content(staging):
    add(staging, 2, stamp='2026-10-01T00:00:00Z')
    with closing(sqlite3.connect(staging)) as c, c: c.execute("UPDATE observation_events SET task_ref='DEMO-42' WHERE id=1")
    root = staging.parent/'export'; publish(staging, root)
    assert run(staging, apply=True)['deleted'] == 1
    publish(staging, root)
    assert check_links(root)['broken_links'] == 0
    session = root/'Sessions'/(name(str(staging.parent/'old.jsonl'))+'.md')
    assert 'retentionuniquetoken1' not in session.read_text()
    assert '^event-1' in session.read_text()


def test_apply_refuses_enabled_packet_sync(staging, capsys):
    (Path(os.environ['AGENTMESH_HOME'])/'settings.local.json').write_text('{"packet_sync":{"enabled":true}}')
    before = snapshot(staging)
    assert agentmesh.main(['--database',str(staging),'staging','retention','--apply']) == 1
    assert 'packet_sync.enabled=false' in capsys.readouterr().err
    assert snapshot(staging) == before
    assert run(staging)['candidates'] == 1


@pytest.mark.parametrize('days', [-1, 0, 6])
def test_refuses_days_below_seven(staging, days):
    before = snapshot(staging)
    with pytest.raises(ValueError, match='at least 7'): run(staging, older_than_days=days, apply=True)
    assert snapshot(staging) == before


def test_settings_override_and_audit_last_run(staging):
    root = Path(os.environ['AGENTMESH_HOME'])
    (root/'settings.json').write_text('{"staging":{"retention":{"older_than_days":60}}}')
    (root/'settings.local.json').write_text('{"staging":{"retention":{"older_than_days":45}}}')
    assert run(staging)['older_than_days'] == 45
    assert run(staging, older_than_days=7)['older_than_days'] == 7
    assert retention_audit.audit(staging)['retention_enabled']
    assert retention_audit.audit(staging)['last_run_at'] is None
    report = run(staging, apply=True)
    result = retention_audit.audit(staging)
    assert result['last_run_at'] == NOW.isoformat()
    assert result['rows_deleted_total'] == result['last_run']['deleted'] == 1
    assert all(result['last_run'][key] == report[key] for key in retention.COUNTS)
    run(staging, apply=True)
    assert retention_audit.audit(staging)['rows_deleted_total'] == 1


def test_requires_production_consumer(staging):
    jobs = Path(os.environ['AGENTMESH_HOME'])/'jobs'; jobs.mkdir()
    (jobs/'agentmesh-production.json').write_text(json.dumps({'database': str(staging), 'summary': {'consumer': 'agentmesh/production'}}))
    assert run(staging, apply=True)['kept_pending'] == 1
    assert ids(staging) == [1]


def test_tombstones_never_store_legacy_key_content(staging):
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute("UPDATE observation_events SET event_key='legacy transcript text'")
    assert run(staging, apply=True)['deleted'] == 1
    with closing(sqlite3.connect(staging)) as c:
        assert c.execute('SELECT event_key FROM observation_event_tombstones').fetchone()[0] == sha256(b'legacy transcript text').hexdigest()


def test_apply_rolls_back_fts_tombstones_and_run_on_delete_failure(staging):
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute("CREATE TRIGGER reject_retention BEFORE DELETE ON observation_events BEGIN SELECT RAISE(ABORT,'fixture'); END")
    before = snapshot(staging)
    with pytest.raises(sqlite3.IntegrityError): run(staging, apply=True)
    assert snapshot(staging) == before
    assert sqlite_memory.search(staging, 'retentionuniquetoken1')


@pytest.mark.parametrize('agent', ['codex', 'claude'])
def test_tombstones_prevent_other_parser_reingest(staging, agent):
    path = staging.parent/'old.jsonl'
    if agent == 'codex':
        records = [{'type':'session_meta', 'payload':{'id':'test-session'}},
                   {'type':'event_msg', 'timestamp':OLD, 'payload':{'type':'item_completed', 'item':{'type':'UserMessage', 'id':'old', 'content':[{'type':'text', 'text':'old transcript'}]}}}]
    else:
        records = [{'type':'user', 'uuid':'old', 'timestamp':OLD, 'message':{'role':'user', 'content':'old transcript'}}]
    path.write_text(''.join(json.dumps(row)+'\n' for row in records))
    assert sqlite_memory.ingest_file(staging, path, agent=agent)['inserted'] == 1
    os.utime(path, (1577836800,1577836800))
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute('UPDATE ingestion_cursors SET updated_at=? WHERE source_path=?', (OLD,str(path)))
        ident = c.execute("SELECT id FROM observation_events WHERE content='old transcript'").fetchone()[0]
        c.execute('INSERT INTO summary_state(consumer,last_event_id) VALUES(?,?)', (CONSUMER+':event:'+str(ident),ident))
    assert run(staging, apply=True)['deleted'] == 2
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute("UPDATE ingestion_cursors SET file_identity='reset',byte_offset=0,line_number=0")
    assert sqlite_memory.ingest_file(staging, path, agent=agent)['inserted'] == 0
    assert ids(staging) == []


def test_missing_explicit_workflow_refuses_without_changes(staging):
    before = snapshot(staging)
    with pytest.raises(ValueError, match='configured workflow missing'):
        run(staging, apply=True, workflow_config=staging.parent/'missing.json')
    assert snapshot(staging) == before


def test_cli_defaults_database_from_runtime(staging, capsys):
    runtime = Path(os.environ['AGENTMESH_HOME'])/'memory-sync/data/runtime.json'
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({'database': str(staging)}))
    assert agentmesh.main(['staging', 'retention', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['candidates'] == 1


def test_cli_apply_and_minimum_age(staging, capsys):
    argv = ['--database',str(staging),'staging','retention','--apply','--json']
    assert agentmesh.main([*argv, '--older-than-days', '6']) == 1
    assert 'at least 7' in capsys.readouterr().err
    assert ids(staging) == [1]
    assert agentmesh.main(argv) == 0
    assert json.loads(capsys.readouterr().out)['deleted'] == 1
    assert ids(staging) == []


@pytest.mark.parametrize('kind', ['tool_call', 'tool_result', 'system'])
def test_covered_turn_releases_tools_without_receipts(staging, kind):
    add(staging, 2, kind=kind, covered=False)
    add(staging, 3, kind='assistant_response')
    with closing(sqlite3.connect(staging)) as c, c:
        for ident in (1, 3):
            c.execute('INSERT INTO summary_state(consumer,last_event_id) VALUES(?,?)',
                      ('agentmesh/other:event:'+str(ident), ident))
    assert run(staging, apply=True)['deleted'] == 3
    assert ids(staging) == []


@pytest.mark.parametrize('uncovered', ['anchor', 'assistant', 'second_consumer'])
def test_uncovered_turn_keeps_anchor_and_tools(staging, uncovered):
    add(staging, 2, kind='tool_call', covered=False)
    add(staging, 3, kind='assistant_response', covered=uncovered != 'assistant')
    with closing(sqlite3.connect(staging)) as c, c:
        if uncovered == 'anchor':
            c.execute('DELETE FROM summary_state WHERE last_event_id=1')
        elif uncovered == 'second_consumer':
            c.execute("INSERT INTO summary_state(consumer,last_event_id) VALUES('agentmesh/other:event:1',1)")
    result = run(staging, apply=True)
    assert result['kept_pending'] == (2 if uncovered == 'anchor' else 3)
    assert ids(staging) == ([1, 2] if uncovered == 'anchor' else [1, 2, 3])


@pytest.mark.parametrize('anchor', [False, True])
def test_tools_before_first_anchor_stay_pending(staging, anchor):
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute('DELETE FROM observation_events')
    add(staging, 2, kind='tool_result')
    if anchor: add(staging, 3)
    assert run(staging, apply=True)['kept_pending'] == 1
    assert ids(staging) == [2]


@pytest.mark.parametrize('kind', ['user_request', 'assistant_response', 'tool_call'])
@pytest.mark.parametrize('stamp', [None, 'bad'])
def test_unorderable_rows_keep_turns_pending(staging, kind, stamp):
    add(staging, 2, kind=kind, stamp=stamp)
    add(staging, 3, kind='tool_result', covered=False)
    assert run(staging, apply=True)['kept_pending'] == 3
    assert ids(staging) == [1, 2, 3]


def test_turn_order_uses_timestamp_then_id(staging):
    # Higher ID precedes the anchor in time; tied rows use ID order.
    add(staging, 20, kind='tool_call', stamp='2019-12-31T23:59:59Z')
    add(staging, 2, kind='tool_result', covered=False)
    add(staging, 3, covered=False)
    add(staging, 4, kind='tool_call')
    add(staging, 5)
    assert run(staging, apply=True)['deleted'] == 3
    assert ids(staging) == [3, 4, 20]


def test_deleted_anchor_leftover_attaches_to_uncovered_turn(staging):
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute('DELETE FROM summary_state')
    add(staging, 2, stamp='2020-01-02T00:00:00Z')
    add(staging, 3, kind='tool_call', covered=False, stamp='2026-10-01T00:00:00Z')
    assert run(staging, apply=True)['deleted'] == 1
    assert ids(staging) == [1, 3]
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)
    result = retention.retain(staging, now=later, apply=True)
    assert result['kept_pending'] == 2
    assert result['deleted'] == 0
    assert ids(staging) == [1, 3]


@pytest.mark.parametrize('mode', ['quiet', 'gone', 'moved', 'replaced'])
def test_inactive_transcript_is_eligible(staging, mode):
    path = staging.parent/'old.jsonl'
    if mode == 'gone': path.unlink()
    elif mode == 'moved': path.rename(staging.parent/'moved.jsonl')
    elif mode == 'replaced':
        replacement = staging.parent/'replacement'
        replacement.write_text('replacement content')
        os.utime(replacement, (1577836800,1577836800))
        replacement.replace(path)
    assert run(staging, apply=True)['deleted'] == 1
    assert ids(staging) == []


def test_recent_replaced_transcript_stays_active(staging):
    # Not the newest file, cursor quiet, identity changed, but touched inside the window.
    path = staging.parent/'old.jsonl'
    replacement = staging.parent/'replacement'; replacement.write_text('replacement content')
    recent = (NOW - timedelta(days=1)).timestamp()
    os.utime(replacement, (recent, recent)); replacement.replace(path)
    os.utime(staging.parent/'newest.jsonl', (NOW.timestamp(), NOW.timestamp()))
    result = run(staging, apply=True)
    assert result['deleted'] == 0 and result['kept_active_session'] == 1
    assert ids(staging) == [1]


@pytest.mark.parametrize('mode', ['gone', 'rotation', 'moved'])
def test_retired_transcript_ingest_after_file_change(staging, mode):
    memory_sync.initialize(staging, 'mac', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
    path = staging.parent/'old.jsonl'
    content = json.dumps({'type':'message','id':'old','timestamp':OLD,
                         'message':{'role':'user','content':[{'type':'text','text':'old transcript'}]}})+'\n'
    path.write_text(content)
    assert sqlite_memory.ingest_file(staging, path)['inserted'] == 1
    os.utime(path, (1577836800,1577836800))
    with closing(sqlite3.connect(staging)) as c, c:
        c.execute('UPDATE ingestion_cursors SET updated_at=? WHERE source_path=?', (OLD,str(path)))
        ident, key = c.execute("SELECT id,event_key FROM observation_events WHERE content='old transcript'").fetchone()
        c.execute('INSERT INTO summary_state(consumer,last_event_id) VALUES(?,?)', (CONSUMER+':event:'+str(ident),ident))
    if mode == 'gone': path.unlink()
    assert run(staging, apply=True)['deleted'] == 2
    if mode == 'rotation':
        replacement = staging.parent/'replacement'
        replacement.write_text(content)
        replacement.replace(path)
    elif mode == 'moved': path.rename(staging.parent/'moved.jsonl')
    report = ingest_sessions.ingest(staging, roots={'omp': staging.parent})
    assert report['errors'] == 0
    assert report['inserted'] == (1 if mode == 'moved' else 0)
    with closing(sqlite3.connect(staging)) as c:
        assert c.execute('SELECT 1 FROM observation_events WHERE event_key=?', (key,)).fetchone() is None
    if mode == 'moved':
        # Pre-existing path-dependent keys: a move creates a new pending event.
        with sqlite_memory._connect(staging) as c:
            assert len(retention.pending_ids(c)) == 1
    else:
        assert ids(staging) == []
        with patch.object(summarize_memory, 'invoke_command') as invoke:
            assert summarize_memory.summarize_once(staging)['status'] == 'idle'
            invoke.assert_not_called()
        def unexpected(*args, **kwargs):
            pytest.fail('retired event reached summarizer')
        assert digest_scheduler.tick(staging, root=staging.parent/'export', extractor=unexpected, force=True)['batches'] == 0
