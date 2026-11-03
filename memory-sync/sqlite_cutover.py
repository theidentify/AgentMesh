"""Seed the broker summarizer's JSON state from the staging database (SQLite retirement R4 cutover).

Read-only on the database. For every transcript the ingest followed:
  - no event still waiting for the summary: the broker cursor starts where the ingest cursor stopped;
  - some events waiting: the broker cursor starts at the top of the file and every event the ingest stored, except
    the waiting ones, is marked done. The broker then summarizes exactly the waiting events and whatever
    was written after the ingest cursor, so nothing is summarized twice and nothing is skipped.
The ingestion floor, the last finished schedule and the open quarantine rows carry over. Deleted with the rest of the
SQLite code in R5 (after the Windows cutover).
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import broker_digest
import transcripts

CONSUMER = 'agentmesh/sqlite-summary-v1'


def connect(database):
    c = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return closing(c)


def table(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def events_until(path, agent, project, offset):
    """The events of the complete records in the first `offset` bytes, the part the ingest already read."""
    omp, codex, claude = transcripts.parsers()
    with Path(path).open('rb') as handle:
        head = handle.read(offset)
    lines = [raw.decode('utf-8', errors='replace').rstrip('\r\n') for raw in head[:head.rfind(b'\n') + 1].splitlines(keepends=True)]
    if agent == 'codex':
        meta = codex.read_session_meta(path) or {}
        return codex.parse_codex_lines(lines, str(path), project=project, session_id=meta.get('id') or meta.get('session_id'))
    if agent == 'claude':
        return claude.parse_claude_lines(lines, str(path), project=project)
    return omp.parse_omp_lines(lines, str(path), project=project)


def plan(database, *, consumer=CONSUMER):
    """Everything the seed would write, plus counts for the dry run. Reads the database only."""
    from shared_memory_context import pending_ids
    with connect(database) as c:
        cursors = {r['source_path']: r for r in c.execute('SELECT source_path,file_identity,byte_offset,line_number FROM ingestion_cursors')}
        waiting = list(pending_ids(c, consumer))
        pending = {}
        for start in range(0, len(waiting), 500):
            chunk = waiting[start:start + 500]
            for r in c.execute('SELECT event_key,source_path,source_agent,project FROM observation_events WHERE id IN (%s)' % ','.join('?' * len(chunk)), chunk):
                pending.setdefault(r['source_path'], dict(agent=r['source_agent'], project=r['project'], keys=set()))['keys'].add(r['event_key'])
        start_at = c.execute('SELECT start_at FROM agentmesh_ingestion_start LIMIT 1').fetchone() if table(c, 'agentmesh_ingestion_start') else None
        success = None
        if table(c, '_agentmesh_digest_windows'):
            row = c.execute('SELECT state FROM _agentmesh_digest_windows WHERE consumer=?', (consumer,)).fetchone()
            success = (json.loads(row[0]).get('success') or None) if row else None
        quarantine = [dict(memory_key=r['memory_key'], reason=r['reason'], batch_id=r['batch_id'], created_at=r['created_at'])
                      for r in c.execute('SELECT memory_key,reason,batch_id,created_at FROM memory_quarantine WHERE resolved_at IS NULL ORDER BY id')] \
            if table(c, 'memory_quarantine') else []
    with connect(database) as c:
        known = {path: {r[0] for r in c.execute('SELECT event_key FROM observation_events WHERE source_path=?', (path,))}
                 for path in pending}
    files, missing, rekeyed = {}, [], 0
    for path, row in sorted(cursors.items()):
        waiting_here = pending.get(path)
        current = transcripts.identity(path)[0] if Path(path).is_file() else None
        # The seed stores today's identity for a file that is still the same one: the database's device number may
        # predate a reboot (macOS renumbers APFS st_dev), and a stale one would only be refreshed by reading again.
        same = current is not None and transcripts.same_file(row['file_identity'], current)
        if not waiting_here:
            files[path] = dict(identity=current if same else row['file_identity'], offset=row['byte_offset'],
                               line=row['line_number'], done=[])
            continue
        if not same:
            missing.append(path)  # gone or replaced: its waiting events can no longer be read again
            continue
        taken = {e.event_key for e in events_until(path, waiting_here['agent'], waiting_here['project'], row['byte_offset'])}
        # Done only when the ingest stored this exact key and it is not waiting. A key the database does not know
        # (the Claude parser can key a whole-file read differently from the ingest's pieces) is summarized again:
        # a repeat is an unchanged upsert, a skip would lose a memory.
        done = (taken & known.get(path, set())) - waiting_here['keys']
        rekeyed += len(taken - known.get(path, set()))
        files[path] = dict(identity=current, offset=0, line=0, done=sorted(done))
    stray = sorted(set(pending) - set(cursors))
    summary = dict(start_at=start_at[0] if start_at else None,
                   success=dict(day=success.get('day'), boot_id=success.get('boot_id'), completed_at=success.get('completed_at'))
                   if isinstance(success, dict) else None,
                   pending=False, quarantine=quarantine[-broker_digest.QUARANTINE_LIMIT:])
    counts = dict(transcripts=len(files), reread=sum(1 for f in files.values() if f['offset'] == 0 and f['done']),
                  waiting_events=len(waiting), rekeyed_events=rekeyed,
                  unreadable=len(missing) + len(stray), quarantine=len(summary['quarantine']), start_at=summary['start_at'])
    return dict(files=files, summary=summary, counts=counts, unreadable=missing + stray)


def seed(database, state_dir, *, consumer=CONSUMER):
    """Write the two state files; refuses to overwrite either, and refuses waiting events it could not place."""
    result = plan(database, consumer=consumer)
    if result['unreadable']:
        raise ValueError('waiting events in transcripts that are gone or replaced: %d' % len(result['unreadable']))
    state_dir = Path(state_dir)
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in (broker_digest.CURSORS, broker_digest.STATE):
        if (state_dir / name).exists() or (state_dir / name).is_symlink():
            raise ValueError('summarizer state already exists')
    broker_digest.save(state_dir / broker_digest.CURSORS, dict(format=broker_digest.CURSORS_FORMAT, files=result['files']))
    broker_digest.save(state_dir / broker_digest.STATE, dict(format=broker_digest.STATE_FORMAT, **result['summary']))
    return result['counts']


def checkpoint(database):
    """Fold the WAL into the main file before it is archived, so the archive is one self-contained file."""
    with closing(sqlite3.connect(Path(database).resolve().as_uri(), uri=True, timeout=30)) as c:
        busy, _, _ = c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    if busy:
        raise ValueError('staging database busy; checkpoint incomplete')
