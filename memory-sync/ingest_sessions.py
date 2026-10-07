"""Discover local OMP, Codex and Claude JSONL files using existing SQLite parsers."""
from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Any

import sqlite_memory

DEFAULT_ROOTS = {'omp': '.omp/agent/sessions', 'codex': '.codex/sessions',
                 'claude': '.claude/projects'}


def ingest(database, roots=None, home=None, project=None):
    """Ingest transcripts into an initialized SQLite database.

    Explicit roots replace defaults (including an empty mapping). Found counts
    JSONL candidates outside pruned directories; skipped counts unsafe candidates
    and cursor-complete files. Processed counts backend attempts. Errors counts
    failed files/directories, with one class name per error and no paths/messages.
    Only complete newline-terminated records are consumed by the backend.
    """
    home = Path(home).expanduser() if home is not None else Path.home()
    database = Path(database).expanduser().resolve()
    read_uri = database.as_uri() + '?mode=ro'
    with closing(sqlite3.connect(read_uri, uri=True)) as connection:
        connection.execute('SELECT file_identity,byte_offset FROM ingestion_cursors LIMIT 1')
    roots = roots if roots is not None else {a: home / p for a, p in DEFAULT_ROOTS.items()}
    if any(agent not in DEFAULT_ROOTS for agent in roots):
        raise ValueError('unsupported agent')
    report: dict[str, Any] = dict(found=0, processed=0, skipped=0, inserted=0,
                                  errors=0, error_classes=[])

    def error(exc):
        report['errors'] += 1
        report['error_classes'].append(type(exc).__name__)

    for agent, directory in roots.items():
        try:
            directory = Path(directory).expanduser()
            if directory.is_symlink():
                continue
            root = directory.resolve()
            if agent == 'claude' and 'subagents' in root.parts:
                continue
            if not root.is_dir():
                continue
            for base, dirs, files in os.walk(root, followlinks=False, onerror=error):
                dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink()
                                 and not (agent == 'claude' and d == 'subagents'))
                for name in sorted(files):
                    if not name.endswith('.jsonl'):
                        continue
                    report['found'] += 1
                    try:
                        candidate = Path(base) / name
                        transcript = candidate.resolve()
                        if (candidate.is_symlink() or root not in transcript.parents
                                or not transcript.is_file()):
                            report['skipped'] += 1
                            continue
                        stat = transcript.stat()
                        with closing(sqlite3.connect(read_uri, uri=True)) as connection:
                            cursor = connection.execute('SELECT file_identity,byte_offset FROM ingestion_cursors WHERE source_path=?', (str(transcript),)).fetchone()
                        if cursor and cursor == (f'{stat.st_dev}:{stat.st_ino}', stat.st_size):
                            report['skipped'] += 1
                            continue
                        report['processed'] += 1
                        result = sqlite_memory.ingest_file(database, transcript, project=project, agent=agent)
                        report['inserted'] += result['inserted']
                        # The backend tolerates malformed JSON; surface its newly
                        # consumed errors without re-reading/redacting transcripts.
                        start = cursor[1] if cursor and cursor[0] == f'{stat.st_dev}:{stat.st_ino}' and cursor[1] <= stat.st_size else 0
                        with closing(sqlite3.connect(read_uri, uri=True)) as connection:
                            invalid = connection.execute("SELECT 1 FROM ingestion_errors WHERE source_path=? AND byte_offset>=? AND byte_offset<? AND error_type='invalid_json' LIMIT 1", (str(transcript), start, result['next_offset'])).fetchone()
                        if invalid:
                            import json
                            error(json.JSONDecodeError('', '', 0))
                    except Exception as exc:
                        # Parser shape errors and unreadable files are independent;
                        # never expose exception strings or transcript paths.
                        error(exc)
        except Exception as exc:
            error(exc)
    return report


def main(argv=None):
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__import__('brand').description(__doc__),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('database', help='initialized local SQLite database')
    parser.add_argument('--root', action='append', metavar='AGENT=PATH',
                        help='replace default discovery roots; repeat for multiple agents')
    parser.add_argument('--project', help='fixed project for newly ingested events')
    args = parser.parse_args(argv)
    roots = None
    if args.root is not None:
        roots = {}
        for value in args.root:
            agent, separator, path = value.partition('=')
            if not separator or agent not in DEFAULT_ROOTS or not path:
                parser.error('--root requires omp|codex|claude=PATH')
            if agent in roots:
                parser.error('--root accepts one directory per agent')
            roots[agent] = path
    try:
        result = ingest(args.database, roots=roots, project=args.project)
    except Exception as exc:
        result = dict(found=0, processed=0, skipped=0, inserted=0,
                      errors=1, error_classes=[type(exc).__name__])
    print(json.dumps(result))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
