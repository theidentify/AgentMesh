"""Optional private per-machine ingestion and bounded primary summarization."""
import json
import math
from pathlib import Path
import time
import uuid

import memory_sync


def load(database, filename=None):
    path = Path(filename) if filename else Path(database).parent / 'workflow.json'
    if not path.exists():
        return None
    settings = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(settings, dict):
        raise ValueError('workflow config must be an object')
    for key in ('ingest', 'summarize', 'seed_legacy'):
        if key in settings and type(settings[key]) is not bool:
            raise ValueError('workflow switches must be booleans')
    return settings


def ingest(database, settings, *, postgres_mirror=False):
    if postgres_mirror:
        # Do not let two ingesters race on identical event keys/cursor paths.
        return {'status': 'postgres-mirror'}
    if not settings.get('ingest', True):
        return {'status': 'disabled'}
    from ingest_sessions import ingest as ingest_sessions
    return dict(status='completed', **ingest_sessions(database, roots=settings.get('roots')))


def summarize(database, settings, *, force=False):
    if not settings.get('summarize', False):
        return {'status': 'disabled'}
    backend = settings.get('summary_backend', 'legacy')
    if backend not in ('legacy', 'bounded'):
        raise ValueError('unknown summary backend')
    if backend == 'bounded':
        from digest_scheduler import tick
        from bounded_digest import seed_coverage
        options = dict(settings.get('summary', {}))
        if not options.get('root'):
            raise ValueError('bounded scheduler needs an explicit export root')
        if settings.get('seed_legacy', False):
            seed_coverage(database, consumer=options.get('consumer', 'agentmesh/sqlite-summary-v1'),
                          primary_node=options.get('primary_node', 'mac'))
        return tick(database, force=force, **options)
    from summarize_memory import DEFAULT_CONSUMER, seed_legacy_coverage, summarize_once
    options = dict(settings.get('summary', {}))
    if not options.get('command'):
        raise ValueError('primary summarization needs a configured provider command')
    interval = settings.get('summary_interval_seconds', 86400)
    if type(interval) not in (int, float) or not math.isfinite(interval) or interval < 1:
        raise ValueError('summary interval must be a positive number')
    consumer = options.get('consumer', DEFAULT_CONSUMER)
    primary = options.get('primary_node', 'mac')
    if memory_sync.status(database)['node'] != primary:
        raise ValueError('this machine is not the designated summary primary')
    if settings.get('seed_legacy', False):
        seed_legacy_coverage(database, consumer=consumer, primary_node=primary)
    now, owner = time.time(), str(uuid.uuid4())
    with memory_sync.connect(database) as c, c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('''CREATE TABLE IF NOT EXISTS _agentmesh_worker_state(
            consumer TEXT PRIMARY KEY, last_run REAL NOT NULL DEFAULT 0,
            lease_until REAL NOT NULL DEFAULT 0, owner TEXT)''')
        c.execute('INSERT OR IGNORE INTO _agentmesh_worker_state(consumer) VALUES(?)', (consumer,))
        state = c.execute('SELECT last_run,lease_until FROM _agentmesh_worker_state WHERE consumer=?',
                          (consumer,)).fetchone()
        if state[1] > now:
            return {'status': 'running-on-primary'}
        if not force and state[0] and now - state[0] < interval:
            return {'status': 'scheduled'}
        lease = now + options.get('timeout', 300) + 60
        c.execute('UPDATE _agentmesh_worker_state SET lease_until=?,owner=? WHERE consumer=?',
                  (lease, owner, consumer))
    success = False
    try:
        result = summarize_once(database, **options)
        success = result.get('status') in ('committed', 'idle')
        return result
    finally:
        with memory_sync.connect(database) as c, c:
            c.execute('''UPDATE _agentmesh_worker_state SET last_run=CASE WHEN ? THEN ? ELSE last_run END,
                lease_until=0,owner=NULL WHERE consumer=? AND owner=?''',
                (success, time.time(), consumer, owner))
