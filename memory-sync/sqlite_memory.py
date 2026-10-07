"""Local SQLite feasibility prototype; no production database access."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3

TABLES = ('ingestion_cursors', 'ingestion_errors', 'observation_events',
          'memory_summaries', 'memory_items', 'source_sessions', 'memory_sources', 'summary_state')


def _private(path):
    for name in (str(path), str(path) + '-wal', str(path) + '-shm'):
        try:
            os.chmod(name, 0o600)
        except OSError:
            pass


@contextmanager
def _connect(path):
    path = Path(path).expanduser()
    # Create with private permissions, rather than waiting until after writes.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    _private(path)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=10000')
        yield c
    finally:
        c.close()
        _private(path)


def init_database(path):
    with _connect(path) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
        c.executescript('''
        CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(content,
            content='observation_events', content_rowid='id', tokenize='unicode61');
        CREATE TRIGGER IF NOT EXISTS events_fts_insert AFTER INSERT ON observation_events BEGIN
            INSERT INTO events_fts(rowid,content) VALUES(new.id,new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS events_fts_delete AFTER DELETE ON observation_events BEGIN
            INSERT INTO events_fts(events_fts,rowid,content) VALUES('delete',old.id,old.content);
        END;
        CREATE TRIGGER IF NOT EXISTS events_fts_update AFTER UPDATE OF content ON observation_events BEGIN
            INSERT INTO events_fts(events_fts,rowid,content) VALUES('delete',old.id,old.content);
            INSERT INTO events_fts(rowid,content) VALUES(new.id,new.content);
        END;
        ''')
        with c:
            c.execute("INSERT INTO events_fts(events_fts) VALUES('rebuild')")


def import_snapshot(path, snapshot_path):
    # Reject existing data before changing its schema; recheck under the import
    # write lock below so two simultaneous imports cannot both populate it.
    if Path(path).expanduser().exists():
        with _connect(path) as c:
            names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for name in names:
                if name.startswith(('sqlite_', 'events_fts')):
                    continue
                quoted = '"' + name.replace('"', '""') + '"'
                if c.execute(f'SELECT 1 FROM {quoted} LIMIT 1').fetchone():
                    raise ValueError('refusing to import into a populated database')
    init_database(path)
    with _connect(path) as c, c:
        c.execute('BEGIN IMMEDIATE')
        if any(c.execute(f'SELECT 1 FROM {t} LIMIT 1').fetchone() for t in TABLES):
            raise ValueError('refusing to import into a populated database')
        c.execute('PRAGMA defer_foreign_keys=ON')
        counts = dict.fromkeys(TABLES, 0)
        columns = {t: {r['name'] for r in c.execute(f'PRAGMA table_info({t})')} for t in TABLES}
        with Path(snapshot_path).open(encoding='utf-8') as handle:
            manifest = json.loads(handle.readline())
            if not isinstance(manifest, dict):
                raise ValueError('invalid snapshot manifest')
            names = manifest.get('tables')
            if (manifest.get('format') != 'omp-sqlite-snapshot-v1'
                    or not isinstance(names, list) or len(names) != len(TABLES)
                    or any(not isinstance(n, str) for n in names) or set(names) != set(TABLES)):
                raise ValueError('invalid snapshot manifest')
            for line in handle:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError('invalid snapshot record')
                table, row = record.get('table'), record.get('row')
                if not isinstance(table, str) or table not in TABLES:
                    raise ValueError('unexpected snapshot table')
                if not isinstance(row, dict) or not row or not set(row) <= columns[table]:
                    raise ValueError('invalid snapshot columns')
                keys = list(row)
                values = [json.dumps(row[k], ensure_ascii=False) if k == 'metadata' and not isinstance(row[k], str) else row[k] for k in keys]
                fields = ','.join('"' + k + '"' for k in keys)
                placeholders = ','.join('?' for _ in keys)
                c.execute(f'INSERT INTO {table} ({fields}) VALUES ({placeholders})', values)
                counts[table] += 1
        if c.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('snapshot foreign key violations')
        return counts


def search(path, query, project=None, limit=10):
    query = query.strip()
    if not query or limit <= 0:
        return []
    # Quote each whitespace token: FTS operators and punctuation are data.
    tokens = ['"' + word.replace('"', '""') + '"' for word in query.split()
              if any(ch.isalnum() for ch in word)]
    expression = ' AND '.join(tokens) if '%' not in query and '_' not in query else ''
    with _connect(path) as c:
        # Keep matching in SQLite: no Python ID list or bind-variable ceiling.
        match = 'id IN (SELECT rowid FROM events_fts WHERE events_fts MATCH ?)'
        params = (project, project, query, expression, limit)
        if not expression:
            match = '0'
            params = (project, project, query, limit)
        # instr avoids LIKE wildcards entirely (literal % and _).
        rows = c.execute(f'''SELECT * FROM observation_events
            WHERE (? IS NULL OR project=?) AND
            (instr(lower(content),lower(?))>0 OR {match})
            ORDER BY occurred_at DESC NULLS LAST, id DESC LIMIT ?''', params).fetchall()
        results = [dict(row) for row in rows]
        for row in results:
            row['metadata'] = json.loads(row['metadata'])
        return results


def ingest_file(path, transcript, project=None, agent='omp'):
    from hashlib import sha256
    from dataclasses import asdict
    import sys
    # Import pure parsers only, never ingest.py/db.py (which open PostgreSQL).
    source = Path(__file__).resolve().parent / 'src'
    if source.is_dir() and str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from omp_memory.parser import parse_omp_lines
    from omp_memory.codex_parser import parse_codex_lines, read_session_meta
    from omp_memory.claude_parser import parse_claude_lines

    transcript = Path(transcript).expanduser().resolve()
    stat = transcript.stat()
    identity = f'{stat.st_dev}:{stat.st_ino}'
    with _connect(path) as c, c:
        # Serialize the cursor read + batch write to avoid concurrent regressions.
        c.execute('BEGIN IMMEDIATE')
        cursor = c.execute('SELECT file_identity,byte_offset,line_number FROM ingestion_cursors WHERE source_path=?', (str(transcript),)).fetchone()
        old_identity, offset, line_number = tuple(cursor) if cursor else (None, 0, 0)
        if old_identity != identity or offset > stat.st_size:
            offset, line_number = 0, 0
        with transcript.open('rb') as handle:
            handle.seek(offset)
            tail = handle.read()
        final_newline = tail.rfind(b'\n')
        if final_newline < 0:
            return {'source_path': str(transcript), 'inserted': 0, 'lines_processed': 0, 'next_offset': offset}
        complete = tail[:final_newline + 1]
        raw_lines = complete.splitlines(keepends=True)
        lines, errors = [], []
        current_offset = offset
        for index, raw in enumerate(raw_lines, 1):
            line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
            lines.append(line)
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append((str(transcript), current_offset, line_number + index,
                               sha256(raw).hexdigest(), 'invalid_json', str(exc)))
            current_offset += len(raw)
        if agent == 'codex':
            meta = read_session_meta(transcript) or {}
            events = parse_codex_lines(lines, str(transcript), project=project, session_id=meta.get('id') or meta.get('session_id'))
        elif agent == 'claude':
            events = parse_claude_lines(lines, str(transcript), project=project)
        else:
            events = parse_omp_lines(lines, str(transcript), project=project)
        inserted = 0
        synced = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_sync_config'").fetchone() is not None
        if synced:
            from memory_sync import allocate_id
        for event in events:
            row = asdict(event)
            if synced:
                row['id'] = allocate_id(c, 'observation_events')
            row['metadata'] = json.dumps(row['metadata'], ensure_ascii=False)
            row['source_hash'] = sha256(f'{event.source_event_id}\0{event.content}'.encode()).hexdigest()
            fields = ','.join(row)
            inserted += c.execute(f'INSERT INTO observation_events ({fields}) VALUES ({",".join("?" for _ in row)}) ON CONFLICT(event_key) DO NOTHING', tuple(row.values())).rowcount
        if synced:
            for error in errors:
                c.execute('''INSERT INTO ingestion_errors(id,source_path,byte_offset,line_number,line_hash,error_type,error_message)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_path,line_hash) DO NOTHING''',
                    (allocate_id(c, 'ingestion_errors'), *error))
        else:
            c.executemany('''INSERT INTO ingestion_errors(source_path,byte_offset,line_number,line_hash,error_type,error_message)
                VALUES(?,?,?,?,?,?) ON CONFLICT(source_path,line_hash) DO NOTHING''', errors)
        next_offset = offset + len(complete)
        c.execute('''INSERT INTO ingestion_cursors(source_path,file_identity,byte_offset,line_number,source_hash)
            VALUES(?,?,?,?,?) ON CONFLICT(source_path) DO UPDATE SET
            file_identity=excluded.file_identity,byte_offset=excluded.byte_offset,
            line_number=excluded.line_number,source_hash=excluded.source_hash,updated_at=CURRENT_TIMESTAMP''',
            (str(transcript), identity, next_offset, line_number + len(lines), sha256(f'{identity}:{next_offset}'.encode()).hexdigest()))
        return {'source_path': str(transcript), 'inserted': inserted,
                'lines_processed': len(lines), 'next_offset': next_offset}


def status(path):
    with _connect(path) as c:
        return {'counts': {t: c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLES},
                'foreign_key_errors': [tuple(r) for r in c.execute('PRAGMA foreign_key_check')],
                'journal_mode': c.execute('PRAGMA journal_mode').fetchone()[0],
                'busy_timeout_ms': c.execute('PRAGMA busy_timeout').fetchone()[0]}


def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'import', 'status', 'search', 'ingest'):
        sub = commands.add_parser(name)
        sub.add_argument('database')
        if name == 'import':
            sub.add_argument('snapshot')
        if name == 'search':
            sub.add_argument('query')
            sub.add_argument('--limit', type=int, default=10)
        if name == 'ingest':
            sub.add_argument('transcript')
            sub.add_argument('--agent', choices=('omp', 'codex', 'claude'), default='omp')
        if name in ('search', 'ingest'):
            sub.add_argument('--project')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            init_database(args.database)
            result = status(args.database)
        elif args.command == 'import':
            result = import_snapshot(args.database, args.snapshot)
        elif args.command == 'status':
            result = status(args.database)
        elif args.command == 'search':
            result = search(args.database, args.query, args.project, args.limit)
        else:
            result = ingest_file(args.database, args.transcript, args.project, args.agent)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, sqlite3.Error, ImportError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
