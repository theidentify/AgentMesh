"""Explicit test fixtures; never connect to production PostgreSQL."""
import importlib
import json
from pathlib import Path
import sqlite3
import pytest

TABLES = ('source_sessions', 'observation_events', 'ingestion_cursors',
          'ingestion_errors', 'memory_items', 'memory_sources',
          'memory_summaries', 'summary_state')


def mirror():
    try:
        return importlib.import_module('pg_mirror')
    except ModuleNotFoundError:
        pytest.fail('pg_mirror module not implemented')


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'local.db'
    with sqlite3.connect(path) as c:
        c.executescript(Path(__file__).with_name('schema.sql').read_text())
    return path


def source():
    return {table: [] for table in TABLES}


def session(i=1, **changes):
    return dict(id=i, source_agent='omp', source_session_id=f'session-{i}',
                source_path=f'/fixture/{i}', workspace=None, project=None,
                started_at=None, last_seen_at=None, metadata={'nested': [True, 1, None]},
                created_at='2026-01-01T00:00:00+00:00',
                updated_at='2026-01-01T00:00:00+00:00', **changes)


def put(db, table, row):
    values = {k: json.dumps(v) if k == 'metadata' else v for k, v in row.items()}
    with sqlite3.connect(db) as c:
        c.execute(f'INSERT INTO {table} ({",".join(values)}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))


def snapshot(tmp_path, rows):
    path = tmp_path / 'snapshot.jsonl'
    records = [dict(format='omp-sqlite-snapshot-v1', tables=list(TABLES))]
    records += [dict(table=t, row=r) for t in TABLES for r in rows[t]]
    path.write_text(''.join(json.dumps(r) + '\n' for r in records))
    return path


def read(db, sql, params=()):
    with sqlite3.connect(db) as c:
        return c.execute(sql, params).fetchall()


@pytest.mark.parametrize('fraction', ['4', '42', '4234', '42345'])
def test_seed_handles_postgres_trimmed_fractional_timestamps(db, tmp_path, fraction):
    rows = source()
    row = session()
    row['created_at'] = f'2026-09-21T15:00:28.{fraction}+00:00'
    rows['source_sessions'] = [row]
    put(db, 'source_sessions', row)
    assert mirror().seed(db, snapshot(tmp_path, rows))['seeded'] == 1


def test_seed_persists_typed_previous_snapshot(db, tmp_path):
    rows = source()
    rows['source_sessions'] = [session()]
    put(db, 'source_sessions', rows['source_sessions'][0])
    result = mirror().seed(db, snapshot(tmp_path, rows))
    assert result['seeded'] == 1
    table, pk, row = read(db, 'SELECT table_name,pk_json,row_json FROM _pg_shadow')[0]
    assert table == 'source_sessions'
    assert json.loads(pk) == [1]
    assert json.loads(row)['metadata'] == {'nested': [True, 1, None]}


def test_new_source_row_applies_preserving_id_and_metadata(db, tmp_path):
    rows = source()
    mirror().seed(db, snapshot(tmp_path, rows))
    rows['source_sessions'] = [session()]
    result = mirror().reconcile(db, rows)
    assert result['inserted'] == 1
    assert read(db, 'SELECT id,metadata FROM source_sessions') == [(1, json.dumps(session()['metadata'], sort_keys=True, separators=(',', ':')))]
    assert len(read(db, 'SELECT * FROM _pg_shadow')) == 1


def test_source_update_when_sqlite_equals_prior_snapshot(db, tmp_path):
    rows = source()
    rows['source_sessions'] = [session()]
    put(db, 'source_sessions', session())
    mirror().seed(db, snapshot(tmp_path, rows))
    rows['source_sessions'][0]['project'] = 'new-pg-project'
    assert mirror().reconcile(db, rows)['updated'] == 1
    assert read(db, 'SELECT project FROM source_sessions') == [('new-pg-project',)]


def test_concurrent_peer_edit_refuses_entire_batch_and_keeps_checkpoint(db, tmp_path):
    rows = source()
    rows['source_sessions'] = [session()]
    put(db, 'source_sessions', session())
    mirror().seed(db, snapshot(tmp_path, rows))
    before = read(db, 'SELECT * FROM _pg_shadow')
    with sqlite3.connect(db) as c:
        c.execute("UPDATE source_sessions SET project='windows' WHERE id=1")
    rows['source_sessions'] = [session(2), session()]
    rows['source_sessions'][1]['project'] = 'postgres'
    with pytest.raises(mirror().MirrorConflict):
        mirror().reconcile(db, rows)
    assert read(db, 'SELECT id,project FROM source_sessions') == [(1, 'windows')]
    assert read(db, 'SELECT * FROM _pg_shadow') == before
    diagnostic = read(db, 'SELECT details_json FROM _pg_mirror_conflicts')
    assert len(diagnostic) == 1
    assert json.loads(diagnostic[0][0])[0]['table'] == 'source_sessions'


def test_new_source_row_already_present_identically_is_safe(db, tmp_path):
    rows = source()
    mirror().seed(db, snapshot(tmp_path, rows))
    put(db, 'source_sessions', session())
    rows['source_sessions'] = [session()]
    result = mirror().reconcile(db, rows)
    assert result['inserted'] == 0
    assert len(read(db, 'SELECT * FROM _pg_shadow')) == 1


def test_unchanged_source_does_not_touch_peer_edits_or_peer_only_rows(db, tmp_path):
    rows = source()
    rows['source_sessions'] = [session()]
    put(db, 'source_sessions', session())
    mirror().seed(db, snapshot(tmp_path, rows))
    with sqlite3.connect(db) as c:
        c.execute("UPDATE source_sessions SET project='windows' WHERE id=1")
        c.execute('CREATE TABLE test_write_log(id INTEGER)')
        c.execute('CREATE TRIGGER no_mirror_update AFTER UPDATE ON source_sessions BEGIN INSERT INTO test_write_log VALUES(new.id); END')
    put(db, 'source_sessions', session(2**40 + 7))
    before = read(db, 'SELECT * FROM source_sessions ORDER BY id')
    assert mirror().reconcile(db, rows)['unchanged'] == 1
    assert read(db, 'SELECT * FROM source_sessions ORDER BY id') == before
    assert read(db, 'SELECT * FROM test_write_log') == []


def event(i=1):
    return dict(id=i, event_key=f'fixture-{i}', source_agent='omp',
                source_path='/fixture', source_session_id=None, source_event_id=None,
                occurred_at=None, project=None, task_ref=None, role='user',
                kind='message', content='fixture event', metadata={}, source_hash='fixture-hash',
                created_at='2026-01-01T00:00:00Z')


def item(i=1, supersedes=None):
    return dict(id=i, kind='fact', scope='global', scope_key=None, project=None,
                content='fixture item', status='active', confidence=1,
                supersedes_id=supersedes, metadata={}, created_at='2026-01-01T00:00:00Z',
                updated_at='2026-01-01T00:00:00Z', memory_key=f'fixture-{i}')


def linked_baseline(db, tmp_path):
    rows = source()
    rows['observation_events'] = [event()]
    rows['memory_items'] = [item(), item(2, supersedes=1)]
    rows['memory_sources'] = [dict(memory_id=1, event_id=1)]
    for t in TABLES:
        for r in rows[t]:
            put(db, t, r)
    mirror().seed(db, snapshot(tmp_path, rows))
    return rows


def test_source_deletes_children_before_parents_and_keeps_peer_rows(db, tmp_path):
    linked_baseline(db, tmp_path)
    put(db, 'memory_items', item(2**40 + 9))
    result = mirror().reconcile(db, source())
    assert result['deleted'] == 4
    assert read(db, 'SELECT id FROM memory_items') == [(2**40 + 9,)]
    assert read(db, 'SELECT * FROM memory_sources') == []
    assert read(db, 'SELECT * FROM observation_events') == []
    assert read(db, 'PRAGMA foreign_key_check') == []
    assert read(db, 'SELECT * FROM _pg_shadow') == []


def test_parent_delete_cannot_cascade_delete_windows_only_link(db, tmp_path):
    rows = linked_baseline(db, tmp_path)
    put(db, 'observation_events', event(2**40 + 8))
    put(db, 'memory_sources', dict(memory_id=1, event_id=2**40 + 8))
    before = read(db, 'SELECT * FROM _pg_shadow')
    rows['memory_items'] = [item(2)]
    rows['memory_sources'] = []
    with pytest.raises(mirror().MirrorConflict):
        mirror().reconcile(db, rows)
    assert len(read(db, 'SELECT * FROM memory_sources')) == 2
    assert read(db, 'SELECT * FROM _pg_shadow') == before


@pytest.mark.parametrize('table,row', [
    ('source_sessions', dict(session(2), source_path='/fixture/1')),
    ('memory_items', dict(item(3), memory_key='fixture-1')),
    ('memory_summaries', dict(id=2, scope='project', scope_key='fixture', version=1,
                            content='duplicate', metadata={}, created_at='2026-01-01T00:00:00Z')),
])
def test_natural_unique_constraint_failure_is_atomic_and_diagnosed(db, tmp_path, table, row):
    rows = linked_baseline(db, tmp_path)
    rows['source_sessions'] = [session()]
    put(db, 'source_sessions', session())
    original_summary = dict(id=1, scope='project', scope_key='fixture', version=1,
                            content='original', metadata={}, created_at='2026-01-01T00:00:00Z')
    put(db, 'memory_summaries', original_summary)
    rows['memory_summaries'] = [original_summary]
    # Seed predates these extra fixture rows; checkpoint them via reconcile.
    mirror().reconcile(db, rows)
    before = read(db, 'SELECT * FROM _pg_shadow')
    rows['source_sessions'].append(session(8))
    rows[table].append(row)
    with pytest.raises(mirror().MirrorConflict):
        mirror().reconcile(db, rows)
    assert read(db, 'SELECT id FROM source_sessions WHERE id=8') == []
    assert read(db, 'SELECT * FROM _pg_shadow') == before
    assert len(read(db, 'SELECT * FROM _pg_mirror_conflicts')) == 1


@pytest.mark.parametrize('invalid_id', [2**40, -1, True, '1'])
def test_source_pk_must_be_legacy_integer(db, tmp_path, invalid_id):
    rows = source()
    mirror().seed(db, snapshot(tmp_path, rows))
    rows['source_sessions'] = [session(invalid_id)]
    with pytest.raises(ValueError, match='legacy'):
        mirror().reconcile(db, rows)
    assert read(db, 'SELECT * FROM _pg_shadow') == []
    assert read(db, 'SELECT * FROM source_sessions') == []


def test_reconcile_requires_seed_even_if_shadow_table_exists(db):
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE _pg_shadow (table_name TEXT,pk_json TEXT,row_json TEXT)')
    with pytest.raises(ValueError, match='seed'):
        mirror().reconcile(db, source())


def test_seed_exact_idempotent_rejects_different_or_used_baseline(db, tmp_path):
    rows = source()
    rows['source_sessions'] = [session()]
    put(db, 'source_sessions', session())
    path = snapshot(tmp_path, rows)
    mirror().seed(db, path)
    mirror().seed(db, path)
    rows['source_sessions'].append(session(2))
    with pytest.raises(ValueError):
        mirror().seed(db, snapshot(tmp_path, rows))
    mirror().reconcile(db, rows)
    rows['source_sessions'] = [session()]
    with pytest.raises(ValueError):
        mirror().seed(db, snapshot(tmp_path, rows))


def test_seed_rejects_target_already_edited_or_populated_elsewhere(db, tmp_path):
    put(db, 'source_sessions', session(2**40 + 1))
    with pytest.raises(ValueError):
        mirror().seed(db, snapshot(tmp_path, source()))
    assert read(db, "SELECT name FROM sqlite_master WHERE name='_pg_shadow'") == []


def test_wrong_path_is_not_created(tmp_path):
    path = tmp_path / 'wrong.db'
    with pytest.raises((ValueError, OSError)):
        mirror().reconcile(path, source())
    assert not path.exists()


def test_fk_violation_rolls_back_and_persists_conflict(db, tmp_path):
    mirror().seed(db, snapshot(tmp_path, source()))
    rows = source()
    rows['source_sessions'] = [session()]
    rows['memory_sources'] = [dict(memory_id=1, event_id=1)]
    with pytest.raises(mirror().MirrorConflict):
        mirror().reconcile(db, rows)
    assert read(db, 'SELECT * FROM source_sessions') == []
    assert read(db, 'SELECT * FROM _pg_shadow') == []
    assert len(read(db, 'SELECT * FROM _pg_mirror_conflicts')) == 1


def test_refresh_uses_readonly_repeatable_snapshot_and_reconciles_real_sqlite(db, tmp_path, monkeypatch):
    # Explicit protocol fixture, not a claim about any external PostgreSQL state.
    from contextlib import contextmanager
    from types import SimpleNamespace
    import sys
    mirror().seed(db, snapshot(tmp_path, source()))
    rows = source()
    rows['source_sessions'] = [session()]
    calls = []
    active = []

    class FixtureConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            assert not active

        @contextmanager
        def transaction(self):
            active.append(True)
            try:
                yield
            finally:
                active.pop()

        def execute(self, sql):
            assert active
            calls.append(sql)
            if sql.startswith('SELECT '):
                table = next(t for t in TABLES if f'memory.{t} AS t' in sql)
                assert calls[:2] == ['SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY', "SET LOCAL TIME ZONE 'UTC'"]
                assert "to_jsonb(t) - 'search_vector'" in sql
                return SimpleNamespace(fetchall=lambda: [(r,) for r in rows[table]])
            return SimpleNamespace(fetchall=lambda: [])

    def connect(dsn, **kwargs):
        assert dsn == 'explicit-test-fixture-dsn'
        assert kwargs['autocommit'] is True
        return FixtureConnection()

    monkeypatch.setitem(sys.modules, 'psycopg', SimpleNamespace(connect=connect))
    assert mirror().refresh(db, 'explicit-test-fixture-dsn')['inserted'] == 1
    assert len(calls) == 10
    assert read(db, 'SELECT id FROM source_sessions') == [(1,)]


def test_cli_seed_prints_counts_only(db, tmp_path):
    import subprocess
    import sys
    rows = source()
    rows['source_sessions'] = [session()]
    put(db, 'source_sessions', session())
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('pg_mirror.py')),
                             'seed', str(db), str(snapshot(tmp_path, rows))],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'seeded': 1}
    assert result.stderr == ''
    assert '/fixture' not in result.stdout


def test_cli_once_reads_environment_dsn_and_redacts_errors(db, tmp_path, monkeypatch, capsys):
    mirror().seed(db, snapshot(tmp_path, source()))
    monkeypatch.setenv('OMP_MEMORY_DSN', 'explicit-test-secret')
    seen = []

    def fail_refresh(path, dsn):
        seen.append((path, dsn))
        raise RuntimeError(f'credential failure {dsn}')

    monkeypatch.setattr(mirror(), 'refresh', fail_refresh)
    assert mirror().main(['once', str(db)]) == 1
    output = capsys.readouterr()
    assert seen == [(str(db), 'explicit-test-secret')]
    assert 'explicit-test-secret' not in output.err + output.out
    assert json.loads(output.err) == {'error': 'RuntimeError'}


def test_seed_rejects_invalid_fk_baseline(db, tmp_path):
    rows = source()
    rows['memory_sources'] = [dict(memory_id=1, event_id=1)]
    put(db, 'memory_sources', rows['memory_sources'][0])
    with pytest.raises(ValueError, match='foreign key'):
        mirror().seed(db, snapshot(tmp_path, rows))
    assert read(db, "SELECT name FROM sqlite_master WHERE name='_pg_shadow'") == []


def test_core_module_does_not_require_psycopg(monkeypatch):
    import builtins
    original = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in ('psycopg', 'memory_sync'):
            raise ImportError(f'deliberately unavailable test dependency: {name}')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded_import)
    assert importlib.reload(mirror()).TABLES == TABLES


def test_partial_snapshot_rejected_without_interpreting_missing_tables_as_deletes(db, tmp_path):
    mirror().seed(db, snapshot(tmp_path, source()))
    with pytest.raises(ValueError, match='complete'):
        mirror().reconcile(db, {'source_sessions': []})
