"""Read-only PostgreSQL -> SQLite staging mirror. Never writes to PostgreSQL.

A complete source snapshot is compared against the persistent _pg_shadow.
Unchanged source rows do not touch SQLite, preserving independent peer edits.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

TABLES = ('source_sessions', 'observation_events', 'ingestion_cursors',
          'ingestion_errors', 'memory_items', 'memory_sources',
          'memory_summaries', 'summary_state')


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


@contextmanager
def _connect(db_path):
    path = Path(db_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('target must be an existing file-backed SQLite database')
    c = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        c.execute('PRAGMA foreign_keys=ON')
        yield c
    finally:
        c.close()


def _schema(c):
    result = {}
    for table in TABLES:
        info = c.execute(f'PRAGMA table_info({table})').fetchall()
        if not info:
            raise ValueError(f'missing target table: {table}')
        result[table] = (tuple(r['name'] for r in info),
                         tuple(r['name'] for r in sorted(info, key=lambda r: r['pk']) if r['pk']))
    return result


def _row(row, columns, local=False):
    row = dict(row)
    if set(row) != set(columns):
        raise ValueError('source rows must contain exactly the original table columns')
    if local and 'metadata' in row:
        row['metadata'] = json.loads(row['metadata'])
    if 'confidence' in row:
        row['confidence'] = float(row['confidence'])
    for field, value in row.items():
        if value is not None and (field.endswith('_at')):
            # PostgreSQL trims trailing fractional zeros; Python 3.10 accepts
            # only three or six fractional digits. Preserve the instant while
            # padding to microseconds before calling its ISO parser.
            import re
            text = value.replace('Z', '+00:00')
            text = re.sub(r'(?<=:\d{2})\.(\d{1,6})(?=[+-]|$)',
                          lambda match: '.' + match.group(1).ljust(6, '0'), text)
            stamp = datetime.fromisoformat(text)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            row[field] = stamp.astimezone(timezone.utc).isoformat()
    _json(row)
    return row


def _snapshot(rows, schema):
    if set(rows) != set(TABLES):
        raise ValueError('a complete eight-table source snapshot is required')
    result = {}
    for table in TABLES:
        columns, keys = schema[table]
        for raw in rows[table]:
            row = _row(raw, columns)
            for name in keys:
                value = row[name]
                if name in ('id', 'memory_id', 'event_id'):
                    if type(value) is not int or not 0 <= value < 2**40:
                        raise ValueError('source primary key must be a legacy integer below 2**40')
                elif not isinstance(value, str) or not value:
                    raise ValueError('source text primary key must be nonempty')
            pk = _json([row[k] for k in keys])
            key = (table, pk)
            if key in result:
                raise ValueError('duplicate source primary key')
            result[key] = _json(row)
    return result


def _init(c):
    c.execute('CREATE TABLE IF NOT EXISTS _pg_shadow (table_name TEXT NOT NULL, pk_json TEXT NOT NULL, row_json TEXT NOT NULL, PRIMARY KEY(table_name,pk_json))')
    c.execute('CREATE TABLE IF NOT EXISTS _pg_mirror_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), seeded INTEGER NOT NULL)')


def seed(db_path, snapshot_jsonl):
    """Checkpoint a previously imported JSONL snapshot; never imports live rows."""
    with Path(snapshot_jsonl).open(encoding='utf-8') as handle:
        manifest = json.loads(handle.readline())
        if (manifest.get('format') != 'omp-sqlite-snapshot-v1'
                or manifest.get('tables') is None
                or len(manifest['tables']) != len(TABLES)
                or set(manifest['tables']) != set(TABLES)):
            raise ValueError('invalid snapshot manifest')
        rows = {t: [] for t in TABLES}
        for line in handle:
            record = json.loads(line)
            if record['table'] not in TABLES:
                raise ValueError('unexpected snapshot table')
            rows[record['table']].append(record['row'])
    with _connect(db_path) as c:
        try:
            c.execute('BEGIN IMMEDIATE')
            schema = _schema(c)
            desired = _snapshot(rows, schema)
            _init(c)
            previous = {(r['table_name'], r['pk_json']): r['row_json'] for r in c.execute('SELECT * FROM _pg_shadow')}
            if c.execute('SELECT 1 FROM _pg_mirror_state').fetchone():
                if desired != previous:
                    raise ValueError('mirror already seeded with a different or advanced baseline')
            else:
                actual = {}
                for table, (columns, keys) in schema.items():
                    for raw in c.execute(f'SELECT * FROM {table}'):
                        row = _row(raw, columns, local=True)
                        actual[(table, _json([row[k] for k in keys]))] = _json(row)
                if actual != desired:
                    raise ValueError('target does not exactly match imported baseline')
                if c.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('baseline contains foreign key violations')
                c.executemany('INSERT INTO _pg_shadow VALUES (?,?,?)', [(t, pk, row) for (t, pk), row in desired.items()])
                c.execute('INSERT INTO _pg_mirror_state VALUES (1,1)')
            c.commit()
            return {'seeded': len(desired)}
        except Exception:
            c.rollback()
            raise


class MirrorConflict(ValueError):
    """A complete batch was rejected; .conflicts contains safe diagnostics."""
    def __init__(self, conflicts):
        self.conflicts = conflicts
        super().__init__(f'mirror batch refused: {len(conflicts)} conflict(s)')


def _diagnose(c, conflicts):
    # Separate transaction: rejected live changes and shadow remain rolled back.
    c.execute('BEGIN IMMEDIATE')
    c.execute('CREATE TABLE IF NOT EXISTS _pg_mirror_conflicts (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, details_json TEXT NOT NULL)')
    c.execute('INSERT INTO _pg_mirror_conflicts(details_json) VALUES (?)', (_json(conflicts),))
    c.commit()


def _local(c, table, pk, schema):
    columns, keys = schema[table]
    where = ' AND '.join(f'{k}=?' for k in keys)
    raw = c.execute(f'SELECT * FROM {table} WHERE {where}', json.loads(pk)).fetchone()
    return None if raw is None else _json(_row(raw, columns, local=True))


def _require_seed(c):
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'_pg_shadow', '_pg_mirror_state'} <= names:
        raise ValueError('mirror must be seeded before reconciliation')
    if [tuple(r) for r in c.execute('SELECT singleton,seeded FROM _pg_mirror_state')] != [(1, 1)]:
        raise ValueError('mirror has invalid seed state')


def reconcile(db_path, source_rows_by_table):
    """Atomically apply source deltas to an already seeded SQLite database."""
    with _connect(db_path) as c:
        try:
            c.execute('BEGIN IMMEDIATE')
            c.execute('PRAGMA defer_foreign_keys=ON')
            _require_seed(c)
            schema = _schema(c)
            desired = _snapshot(source_rows_by_table, schema)
            previous = {(r['table_name'], r['pk_json']): r['row_json'] for r in c.execute('SELECT * FROM _pg_shadow')}
            counts = dict(inserted=0, updated=0, deleted=0, unchanged=0)
            changes = {key: desired.get(key) for key in previous.keys() | desired.keys()
                       if previous.get(key) != desired.get(key)}
            conflicts = []
            for (table, pk), encoded in changes.items():
                old = previous.get((table, pk))
                local = _local(c, table, pk, schema)
                if local not in (old, encoded):
                    conflicts.append(dict(table=table, pk=json.loads(pk), reason='independent_local_change'))
            # SQLite CASCADE must never silently remove a peer-only link.
            for (table, pk), encoded in changes.items():
                if table == 'memory_items' and encoded is None:
                    for child in c.execute('SELECT memory_id,event_id FROM memory_sources WHERE memory_id=?', json.loads(pk)):
                        child_pk = _json([child['memory_id'], child['event_id']])
                        child_key = ('memory_sources', child_pk)
                        if child_key not in changes or changes[child_key] is not None:
                            conflicts.append(dict(table='memory_sources', pk=json.loads(child_pk), reason='protected_cascade_dependent'))
            if conflicts:
                raise MirrorConflict(conflicts)
            # RESTRICT event references require links to be removed first;
            # self references among memory_items are deferred until commit.
            delete_order = ('memory_sources', 'memory_items', 'observation_events',
                            'summary_state', 'memory_summaries', 'ingestion_errors',
                            'ingestion_cursors', 'source_sessions')
            for table in delete_order:
                keys = schema[table][1]
                where = ' AND '.join(f'{k}=?' for k in keys)
                for (changed_table, pk), encoded in changes.items():
                    if changed_table == table and encoded is None:
                        counts['deleted'] += c.execute(f'DELETE FROM {table} WHERE {where}', json.loads(pk)).rowcount
            for (table, pk), encoded in desired.items():
                if previous.get((table, pk)) == encoded or _local(c, table, pk, schema) == encoded:
                    counts['unchanged'] += 1
                    continue
                row = json.loads(encoded)
                fields = tuple(row)
                values = [_json(row[k]) if k == 'metadata' else row[k] for k in fields]
                keys = schema[table][1]
                where = ' AND '.join(f'{k}=?' for k in keys)
                if (table, pk) in previous:
                    c.execute(f'UPDATE {table} SET {",".join(f"{k}=?" for k in fields)} WHERE {where}', values + json.loads(pk))
                    counts['updated'] += 1
                else:
                    c.execute(f'INSERT INTO {table} ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})', values)
                    counts['inserted'] += 1
            violations = c.execute('PRAGMA foreign_key_check').fetchall()
            if violations:
                raise MirrorConflict([dict(table=r[0], reason='foreign_key', parent=r[2], fk_index=r[3]) for r in violations])
            c.execute('DELETE FROM _pg_shadow')
            c.executemany('INSERT INTO _pg_shadow VALUES (?,?,?)', [(t, pk, row) for (t, pk), row in desired.items()])
            c.commit()
            return counts
        except MirrorConflict as exc:
            c.rollback()
            _diagnose(c, exc.conflicts)
            raise
        except sqlite3.IntegrityError as exc:
            c.rollback()
            conflict = MirrorConflict([dict(reason='sqlite_constraint', constraint=str(exc))])
            _diagnose(c, conflict.conflicts)
            raise conflict from exc
        except Exception:
            c.rollback()
            raise


def refresh(db_path, dsn):
    """Read eight PG tables consistently, close PG, then reconcile SQLite.

    psycopg is optional for seed/reconcile and imported only on this path.
    No source credentials or row contents are returned or printed.
    """
    # Validate target first: a typo must not open PostgreSQL or create a DB.
    with _connect(db_path) as c:
        _schema(c)
        _require_seed(c)
    if not isinstance(dsn, str) or not dsn.strip():
        raise ValueError('a PostgreSQL DSN is required')
    import psycopg
    rows = {t: [] for t in TABLES}
    with psycopg.connect(dsn, autocommit=True) as pg:
        with pg.transaction():
            pg.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            pg.execute("SET LOCAL TIME ZONE 'UTC'")
            for table in TABLES:
                records = pg.execute(f"SELECT to_jsonb(t) - 'search_vector' FROM memory.{table} AS t").fetchall()
                rows[table] = [record[0] for record in records]
    return reconcile(db_path, rows)


def main(argv=None):
    import argparse
    import os
    import sys
    parser = argparse.ArgumentParser(description=__import__('brand').description(__doc__),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    seed_parser = commands.add_parser('seed')
    seed_parser.add_argument('database')
    seed_parser.add_argument('snapshot')
    once_parser = commands.add_parser('once')
    once_parser.add_argument('database')
    once_parser.add_argument('--dsn', default=os.environ.get('OMP_MEMORY_DSN'))
    args = parser.parse_args(argv)
    try:
        if args.command == 'seed':
            result = seed(args.database, args.snapshot)
        else:
            result = refresh(args.database, args.dsn)
        print(_json(result))
        return 0
    except Exception as exc:
        # Driver errors may include credentials or row contents. Never print
        # their messages here; SQLite conflict diagnostics retain safe details.
        result = {'error': type(exc).__name__}
        if isinstance(exc, MirrorConflict):
            result['conflicts'] = len(exc.conflicts)
        print(_json(result), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
