"""Read-only source, whitelisted bodies and private backup snapshots."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from ..encoding import typed

COLUMNS = {
    'memory_items': {'id','kind','scope','scope_key','project','content','status','confidence','memory_key','created_at','updated_at'},
    'memory_summaries': {'id','scope','scope_key','version','content','metadata','created_at'},
    'memory_sources': {'memory_id','event_id'},
}


@contextmanager
def open_source(database):
    c = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN')
        for table, required in COLUMNS.items():
            if not required <= {r['name'] for r in c.execute('PRAGMA table_info(' + table + ')')}:
                raise ValueError('invalid source schema')
        yield c
    finally:
        c.close()


def rows(database, *, expected_node=None):
    with open_source(database) as c:
        if expected_node is not None and node_from_connection(c)!=expected_node:
            raise ValueError('staging node differs from binding')
        evidence = {}
        for r in c.execute('SELECT memory_id,event_id FROM memory_sources ORDER BY event_id'):
            if type(r['event_id']) is int:
                evidence.setdefault(r['memory_id'], []).append(r['event_id'])
        items = [dict(r) for r in c.execute('SELECT ' + ','.join(sorted(COLUMNS['memory_items'])) + ' FROM memory_items ORDER BY id')]
        summaries = [dict(r) for r in c.execute('SELECT * FROM memory_summaries ORDER BY id')]
    for r in items:
        r.update(table='memory_items', evidence_event_ids=evidence.get(r['id'], []),
                 reason=None if r['status'] == 'active' else r['status'])
    for r in summaries:
        try: meta = json.loads(r.pop('metadata'))
        except (ValueError, TypeError): meta = {}
        meta = meta if type(meta) is dict else {}
        ids = set()
        for name in ('event_ids','batch_event_ids','source_event_ids'):
            values = meta.get(name)
            if type(values) is list: ids.update(v for v in values if type(v) is int)
        r.update(table='memory_summaries', kind='summary', confidence=1.0, status='active',
                 project=r['scope_key'] if r['scope'] == 'project' else None,
                 updated_at=r['created_at'], evidence_event_ids=sorted(ids),
                 # Every non-session row migrates: versions are range-allocated IDs, not an
                 # order, and recall_memory returns every matching summary row.
                 reason='not_recalled' if r['scope'] == 'session' else None)
    return items + summaries


def body_for(row, target_scope, *, now=None):
    source = {k: row.get(k) for k in ('table','id','scope','scope_key','memory_key','version','created_at','updated_at')}
    digest = {k: row.get(k) for k in ('table','id','kind','scope','scope_key','project','content','status',
                                    'confidence','memory_key','version','updated_at','evidence_event_ids')}
    digest['target_scope'] = target_scope
    body = dict(kind=row['kind'], content=row['content'], project=row.get('project'),
                task_ref=row['scope_key'] if row['scope'] == 'task' else None, confidence=row['confidence'],
                metadata=dict(source=source, row_hash=hashlib.sha256(typed(digest)).hexdigest(),
                              evidence_event_ids=row['evidence_event_ids'], retired=False))
    fallback = False
    try:
        instant = datetime.fromisoformat(row['updated_at'].replace('Z', '+00:00'))
        if instant.tzinfo is None: instant = instant.replace(tzinfo=timezone.utc)
        stamp = int(instant.timestamp() * 1000)
        if not 0 <= stamp < 2**63: raise ValueError('invalid timestamp')
    except (ValueError, TypeError, AttributeError, OverflowError):
        stamp = int(time.time()*1000) if now is None else now
        fallback = True
    return body, stamp, fallback


def snapshot(database, destination):
    destination = Path(destination)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with open_source(database) as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                target.execute('PRAGMA journal_mode=DELETE')
            finally: target.close()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def node_from_connection(c):
    table='_sync_config' if c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_config' AND type='table'").fetchone() else 'agentmesh_staging_identity'
    try: values=c.execute('SELECT node FROM '+table).fetchall()
    except sqlite3.Error: raise ValueError('staging identity missing; initialize ingest before apply') from None
    if len(values)!=1 or type(values[0][0]) is not str or not values[0][0] or any(ord(ch)<32 for ch in values[0][0]):
        raise ValueError('invalid staging identity')
    return values[0][0]


def staging_node(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as c:
        c.execute('PRAGMA query_only=ON')
        return node_from_connection(c)


def binding_for(database):
    from ..encoding import parse
    from ..settings import safe
    path=Path(os.environ.get('AGENTMESH_PROFILE_LOG_BRIDGE',str(database)+'.profile-log.json'))
    try:
        safe(path)
        value=parse(path.read_bytes())
        if value.get('format')!='agentmesh-profile-log-bridge-v1': raise ValueError()
        return {k:value[k] for k in ('device_id','staging_node')}
    except Exception: raise ValueError('staging binding missing or invalid; run apply') from None


def check_binding(database,binding,device):
    import re
    if type(binding) is not dict or type(binding.get('device_id')) is not str or not re.fullmatch('[0-9a-f]{32}',binding['device_id']) or type(binding.get('staging_node')) is not str or not binding['staging_node']:
        raise ValueError('staging binding missing or invalid; run apply')
    if binding['device_id']!=device: raise ValueError('staging binding device differs from broker')
    if staging_node(database)!=binding['staging_node']: raise ValueError('staging node differs from binding')
