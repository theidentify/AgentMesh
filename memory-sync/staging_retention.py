"""Explicit local staging retention. Dry-runs read one snapshot and write nothing."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import packet_sync
from recall_memory import open_readonly
from shared_memory_context import pending_ids
from summarize_memory import DEFAULT_CONSUMER

FIELDS = ('event_ids', 'batch_event_ids', 'source_event_ids', 'evidence_event_ids')
COUNTS = ('candidates', 'kept_evidence', 'kept_pending', 'kept_too_new', 'kept_active_session')


def settings(root=None):
    root = Path(root) if root is not None else packet_sync.home()
    days, configured = 30, False
    for name in ('settings.json', 'settings.local.json'):
        path = root / name
        if not path.exists(): continue
        value = json.loads(path.read_text(encoding='utf-8')).get('staging', {})
        if type(value) is not dict or set(value) - {'retention'}:
            raise ValueError('invalid staging settings')
        value = value.get('retention', {})
        if type(value) is not dict or set(value) - {'older_than_days'}:
            raise ValueError('invalid staging.retention settings')
        if 'older_than_days' in value:
            days, configured = value['older_than_days'], True
            validate_days(days)
    return days, configured


def validate_days(days):
    if type(days) is not int or days < 7:
        raise ValueError('older_than_days must be an integer at least 7')


def exists(c, table):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def run_status(c, *, configured=False):
    last, total = None, 0
    if exists(c, 'agentmesh_retention_runs'):
        row = c.execute('SELECT * FROM agentmesh_retention_runs WHERE applied=1 ORDER BY rowid DESC LIMIT 1').fetchone()
        last = dict(row) if row else None
        total = c.execute('SELECT coalesce(sum(deleted),0) FROM agentmesh_retention_runs WHERE applied=1').fetchone()[0]
    return dict(retention_enabled=configured or last is not None, last_run_at=last['ran_at'] if last else None,
                rows_deleted_total=total, last_run=last)


def timestamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # SQLite CURRENT_TIMESTAMP (cursor updates) is UTC without an offset.
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def references(value, ids, native):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FIELDS or key == 'eligible_ids':
                if not isinstance(child, list):
                    raise ValueError('invalid provenance; retention refused')
                for item in child:
                    if type(item) is int: ids.add(item)
                    elif type(item) is str: native.add(item)
                    else: raise ValueError('invalid provenance; retention refused')
            elif key == 'event_id':
                if type(child) is int: ids.add(child)
                elif type(child) is str: native.add(child)
                else: raise ValueError('invalid provenance; retention refused')
            else:
                references(child, ids, native)
    elif isinstance(value, list):
        for child in value: references(child, ids, native)


def classify(c, cutoff, configurations=()):
    consumers = {DEFAULT_CONSUMER}
    for row in c.execute('SELECT consumer FROM summary_state'):
        # hermes is the retired PostgreSQL scalar checkpoint, not a SQLite consumer.
        if row[0].startswith('agentmesh/'):
            consumers.add(row[0].split(':', 1)[0])
    evidence = {r[0] for r in c.execute('SELECT event_id FROM memory_sources')}
    native = set()
    for table in ('memory_items', 'memory_summaries'):
        for row in c.execute('SELECT metadata FROM ' + table):
            meta = json.loads(row[0])
            references(meta, evidence, native)
            if isinstance(meta, dict) and isinstance(meta.get('consumer'), str) and meta['consumer'].startswith('agentmesh/'):
                consumers.add(meta['consumer'])
    for table in ('_agentmesh_digest_windows', '_agentmesh_worker_state'):
        if not exists(c, table): continue
        for row in c.execute('SELECT * FROM ' + table):
            consumers.add(row['consumer'])
            if table == '_agentmesh_digest_windows':
                references(json.loads(row['state']), evidence, native)
    for workflow_config in configurations:
        consumer = workflow_config.get('summary', {}).get('consumer', DEFAULT_CONSUMER)
        if type(consumer) is not str: raise ValueError('invalid summary consumer')
        consumers.add(consumer)
    pending = set()
    for consumer in consumers:
        pending.update(pending_ids(c, consumer))
        # Legacy summarize accepts ALL kinds. Require explicit receipts even for
        # tool/system rows; scalar legacy checkpoints are not deletion authority.
        pending.update(r[0] for r in c.execute('''SELECT e.id FROM observation_events e
            WHERE e.kind NOT IN ('user_request','assistant_response') AND NOT EXISTS(
            SELECT 1 FROM summary_state s WHERE s.consumer=? || ':event:' || CAST(e.id AS TEXT))''', (consumer,)))
    cursors = {r['source_path']: dict(r) for r in c.execute('SELECT * FROM ingestion_cursors')}
    stamps = {path: timestamp(row['updated_at']) for path, row in cursors.items()}
    latest = max((stamp for stamp in stamps.values() if stamp), default=None)
    active, mtimes = set(), {}
    for path, row in cursors.items():
        stamp = stamps[path]
        if stamp is None or stamp >= cutoff or stamp == latest: active.add(path)
        try:
            info = Path(path).stat()
            mtimes[path] = info.st_mtime_ns
            if (f'{info.st_dev}:{info.st_ino}' != row['file_identity'] or info.st_size != row['byte_offset']
                    or info.st_mtime >= cutoff.timestamp()): active.add(path)
        except (OSError, ValueError):
            active.add(path)
    newest = max(mtimes.values(), default=None)
    active.update(path for path, mtime in mtimes.items() if mtime == newest)
    counts = dict.fromkeys(COUNTS, 0)
    candidates = []
    for row in c.execute('SELECT id,event_key,source_path,source_event_id,occurred_at FROM observation_events'):
        stamp = timestamp(row['occurred_at'])
        if row['id'] in evidence or row['source_event_id'] in native: reason = 'kept_evidence'
        elif row['id'] in pending: reason = 'kept_pending'
        elif stamp is None or stamp >= cutoff: reason = 'kept_too_new'
        elif row['source_path'] not in cursors or row['source_path'] in active: reason = 'kept_active_session'
        else:
            reason = 'candidates'
            candidates.append((row['id'], row['event_key']))
        counts[reason] += 1
    return counts, candidates


def retain(database, *, older_than_days=None, apply=False, root=None, now=None, workflow_config=None):
    days, _ = settings(root)
    days = days if older_than_days is None else older_than_days
    validate_days(days)
    if apply and packet_sync.enabled(root):
        raise ValueError('retention apply requires packet_sync.enabled=false')
    instant = now or datetime.now(timezone.utc)
    cutoff = instant - timedelta(days=days)
    import workflow
    if workflow_config is not None and not Path(workflow_config).is_file():
        raise ValueError('configured workflow missing; retention refused')
    configurations = [workflow.load(database, workflow_config) or {}]
    production = (Path(root) if root is not None else packet_sync.home()) / 'jobs/agentmesh-production.json'
    if production.exists():
        config = json.loads(production.read_text(encoding='utf-8'))
        if Path(config['database']).expanduser().resolve() == Path(database).expanduser().resolve():
            configurations.append(config)
    if not apply:
        with open_readonly(database) as c:
            counts, _ = classify(c, cutoff, configurations)
        return dict(older_than_days=days, applied=False, deleted=0, **counts)
    # mode=rw refuses accidental creation; no init, sync capture, or checkpoint.
    uri = Path(database).expanduser().resolve().as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('BEGIN IMMEDIATE')
        if packet_sync.enabled(root): raise ValueError('retention apply requires packet_sync.enabled=false')
        counts, candidates = classify(c, cutoff, configurations)
        # The external-content FTS index must have its deletion trigger intact.
        trigger = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='events_fts_delete'").fetchone()
        if not trigger or "'delete',old.id,old.content" not in ''.join(trigger[0].split()):
            raise ValueError('FTS deletion trigger missing or unsupported')
        c.execute('CREATE TABLE IF NOT EXISTS observation_event_tombstones(event_key TEXT PRIMARY KEY, deleted_at TEXT NOT NULL)')
        c.execute('''CREATE TABLE IF NOT EXISTS agentmesh_retention_runs(
            ran_at TEXT NOT NULL, older_than_days INTEGER NOT NULL, deleted INTEGER NOT NULL,
            candidates INTEGER NOT NULL, kept_evidence INTEGER NOT NULL, kept_pending INTEGER NOT NULL,
            kept_too_new INTEGER NOT NULL, kept_active_session INTEGER NOT NULL, applied INTEGER NOT NULL)''')
        stamp = instant.isoformat()
        c.executemany('INSERT OR IGNORE INTO observation_event_tombstones VALUES(?,?)', [(sha256(key.encode('utf-8')).hexdigest(), stamp) for _, key in candidates])
        c.executemany('DELETE FROM observation_events WHERE id=?', [(ident,) for ident, _ in candidates])
        c.execute('INSERT INTO agentmesh_retention_runs VALUES(?,?,?,?,?,?,?,?,1)',
                  (stamp, days, len(candidates), *(counts[key] for key in COUNTS)))
        return dict(older_than_days=days, applied=True, deleted=len(candidates), **counts)
