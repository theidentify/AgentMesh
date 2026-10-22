"""Content-free staging retention counters in the existing ingest pane."""
from contextlib import closing
from pathlib import Path
import sqlite3
import adapters


def test_staging_retention_counters_in_ingest_pane(tmp_path):
    db = tmp_path/'staging.db'
    with closing(sqlite3.connect(db)) as c, c:
        c.executescript((Path(__file__).parents[1]/'memory-sync/schema.sql').read_text())
        c.execute('''CREATE TABLE agentmesh_retention_runs(ran_at TEXT, older_than_days INTEGER, deleted INTEGER,
            candidates INTEGER, kept_evidence INTEGER, kept_pending INTEGER, kept_too_new INTEGER,
            kept_active_session INTEGER, applied INTEGER)''')
        c.execute("INSERT INTO agentmesh_retention_runs VALUES('2026-10-22T00:00:00+00:00',30,5,5,1,2,3,4,1)")
        c.execute("INSERT INTO agentmesh_retention_runs VALUES('2026-10-23T00:00:00+00:00',30,2,2,1,2,3,4,1)")
    result = adapters.operations(tmp_path, adapters.MemoryReader(sqlite=db), db, agentmesh_home=tmp_path)['ingest']['retention']
    assert result['retention_enabled'] is True
    assert result['last_run_at'] == '2026-10-23T00:00:00+00:00'
    assert result['rows_deleted_total'] == 7
    assert result['last_run'] == dict(ran_at=result['last_run_at'], older_than_days=30, deleted=2,
                                     candidates=2, kept_evidence=1, kept_pending=2, kept_too_new=3, kept_active_session=4, applied=1)
    html = Path(__file__).with_name('index.html').read_text()
    assert 'id="retention-status"' in html
    for field in ('retention_enabled', 'last_run_at', 'rows_deleted_total', 'kept_evidence', 'kept_pending', 'kept_too_new', 'kept_active_session'):
        assert field in html


def test_staging_retention_absent_before_first_run(tmp_path):
    db = tmp_path/'staging.db'
    with closing(sqlite3.connect(db)): pass
    assert adapters.retention_status(db) == dict(retention_enabled=False, last_run_at=None, rows_deleted_total=0, last_run=None)
