"""Opt-in SQLite bounded stable-key digest. No production paths or agent loop."""
from __future__ import annotations

import copy
import json

from bounded_protocol import ValidationError, collect, validate, build_request, encode
from memory_sync import connect, config, allocate_id, canonical, digest
from summarize_memory import DEFAULT_CONSUMER, _options


def seed_coverage(db, *, consumer=DEFAULT_CONSUMER, primary_node='mac'):
    """Explicit one-time migration: receipt only PRESENT legacy rows <= hermes.

    Existing consumer receipts/provenance are preserved. No peer range is seeded,
    and no scalar exclusion is used on future arrivals, even low legacy IDs.
    """
    from memory_sync import LIMIT
    _options(consumer, primary_node, 24, 20000)
    with connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        if config(c)['node'] != primary_node:
            raise ValidationError('coverage migration requires primary node')
        marker = consumer + ':bounded-coverage-seeded'
        row = c.execute('SELECT last_event_id FROM summary_state WHERE consumer=?', (marker,)).fetchone()
        if row:
            return dict(status='already-seeded', legacy_events=0, cutoff=row[0])
        legacy = c.execute("SELECT last_event_id FROM summary_state WHERE consumer='hermes'").fetchone()
        cutoff = legacy[0] if legacy else 0
        if type(cutoff) is not int or not 0 <= cutoff < LIMIT:
            raise ValidationError('invalid legacy checkpoint')
        inserted = c.execute('''INSERT OR IGNORE INTO summary_state(consumer,last_event_id)
            SELECT ? || ':event:' || CAST(id AS TEXT),id FROM observation_events WHERE id<=? AND id<?''',
                             (consumer, cutoff, LIMIT)).rowcount
        c.execute('INSERT INTO summary_state(consumer,last_event_id) VALUES(?,?)', (marker, cutoff))
        return dict(status='seeded', legacy_events=inserted, cutoff=cutoff)


def _prepare(c, consumer, primary_node, max_events, event_chars, related_chars, context_chars, highwater, eligible_ids=None):
    if config(c)['node'] != primary_node:
        raise ValidationError('digest requires designated primary node')
    # Per-event coverage, not scalar id > cursor; late peers may have smaller IDs.
    rows = [dict(r) for r in c.execute('''SELECT * FROM observation_events e
        WHERE e.id<=? AND (? IS NULL OR e.id IN (SELECT value FROM json_each(?))) AND e.kind IN ('user_request','assistant_response')
        AND NOT EXISTS(SELECT 1 FROM summary_state s WHERE s.consumer=? || ':event:' || CAST(e.id AS TEXT))
        AND NOT EXISTS(SELECT 1 FROM memory_items m,json_each(m.metadata,'$.batch_event_ids') j
                       WHERE json_extract(m.metadata,'$.consumer')=? AND j.value=e.id)
        AND NOT EXISTS(SELECT 1 FROM memory_summaries m,json_each(m.metadata,'$.batch_event_ids') j
                       WHERE json_extract(m.metadata,'$.consumer')=? AND j.value=e.id)
        ORDER BY e.id LIMIT ?''', (highwater, None if eligible_ids is None else canonical(eligible_ids), canonical(eligible_ids), consumer, consumer, consumer, max_events + 1))]
    for row in rows:
        row['metadata'] = json.loads(row['metadata'])
    events = collect(rows, event_chars, max_events)
    related = [dict(r) for r in c.execute('''SELECT memory_key,kind,scope,scope_key,project,content,status,confidence
        FROM memory_items WHERE memory_key IS NOT NULL AND (project IS ? OR scope='global') ORDER BY memory_key''',
        (events[0]['project'],))] if events else []
    if len(encode(related)) > related_chars:
        raise ValidationError('related context overflow; coverage unchanged')
    request = build_request(events, related, context_chars) if events else None
    task: dict = dict(consumer=consumer, primary_node=primary_node, group_id=config(c)['group_id'],
                highwater=highwater, eligible_ids=eligible_ids, limits=dict(max_events=max_events, event_chars=event_chars,
                related_chars=related_chars, context_chars=context_chars), events=events, related=related, request=request)
    task['batch_id'] = digest(task)
    return task


def prepare(db, *, consumer=DEFAULT_CONSUMER, primary_node='mac', max_events=24,
            event_chars=20000, related_chars=28000, context_chars=60000, highwater=None, eligible_ids=None):
    _options(consumer, primary_node, max_events, event_chars)
    if any(type(v) is not int or v < 1 for v in (related_chars, context_chars)):
        raise ValidationError('invalid context bounds')
    if highwater is not None and (type(highwater) is not int or highwater < 0):
        raise ValidationError('invalid highwater')
    if eligible_ids is not None and (not isinstance(eligible_ids, list) or any(type(v) is not int or v < 1 for v in eligible_ids) or len(set(eligible_ids)) != len(eligible_ids)):
        raise ValidationError('invalid eligible event manifest')
    with connect(db) as c:
        c.execute('BEGIN')
        ceiling = c.execute('SELECT coalesce(max(id),0) FROM observation_events').fetchone()[0]
        return _prepare(c, consumer, primary_node, max_events, event_chars, related_chars, context_chars,
                        ceiling if highwater is None else highwater, eligible_ids)


def apply(db, task, payload, *, provider=None, model=None):
    payload = validate(copy.deepcopy(payload), task['events'], task['related'])
    with connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        current = _prepare(c, task['consumer'], task['primary_node'], highwater=task['highwater'], eligible_ids=task.get('eligible_ids'), **task['limits'])
        if current != task:
            return dict(status='stale', events=0, items=0)
        if not task['events']:
            return dict(status='idle', events=0, items=0)
        ids = [e['id'] for e in task['events']]
        after = max(ids)
        provenance = dict(generator='agentmesh-bounded-v1', consumer=task['consumer'], batch_id=task['batch_id'],
                          batch_event_ids=ids, provider=provider, model=model)
        for item in payload['items']:
            old = c.execute('SELECT * FROM memory_items WHERE memory_key=?', (item['memory_key'],)).fetchone()
            if old and item['memory_key'] not in {r['memory_key'] for r in task['related']}:
                raise ValidationError('stable key exists outside submitted context')
            md = json.loads(old['metadata']) if old else {}
            # Preserve previous provenance/quotes and full source links on corrections.
            history = md.get('evidence_history', [])
            if md.get('evidence'):
                history = history + [dict(evidence=md['evidence'], batch_id=md.get('batch_id'))]
            prior_ids = md.get('batch_event_ids', []) if md.get('consumer') == task['consumer'] else []
            md.update(provenance, evidence=item['evidence'], evidence_history=history, through_event_id=after)
            md['batch_event_ids'] = sorted(set(prior_ids) | set(ids))
            ident = old['id'] if old else allocate_id(c, 'memory_items')
            c.execute('''INSERT INTO memory_items
                (id,memory_key,kind,scope,scope_key,project,content,status,confidence,metadata)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(memory_key) WHERE memory_key IS NOT NULL DO UPDATE SET
                content=excluded.content,status=excluded.status,confidence=excluded.confidence,
                metadata=excluded.metadata,updated_at=CURRENT_TIMESTAMP''',
                (ident, *(item[f] for f in ('memory_key','kind','scope','scope_key','project','content','status','confidence')), canonical(md)))
            c.executemany('INSERT OR IGNORE INTO memory_sources(memory_id,event_id) VALUES(?,?)',
                          [(ident, eid) for eid in item['source_event_ids']])
        project = task['events'][0]['project']
        scopes = {project} | {i['scope_key'] for i in payload['items'] if i['scope'] == 'project'} if project and payload['items'] else set()
        for scope_key in sorted(scopes):
            old = c.execute("SELECT * FROM memory_summaries WHERE scope='project' AND scope_key=? AND version=1", (scope_key,)).fetchone()
            marker = '<!-- BOUNDED-CURRENT:START -->'
            base = old['content'].split(marker)[0].rstrip() if old else ''
            lines = [f"- [{r['kind']}] {r['memory_key']}: {r['content']}" for r in c.execute('''SELECT kind,memory_key,content FROM memory_items
                WHERE project=? AND (scope_key=? OR ?=?) AND status='active' ORDER BY memory_key''', (project, scope_key, scope_key, project))]
            content = '\n'.join([base, '', marker, '## Current durable memory', *lines, '<!-- BOUNDED-CURRENT:END -->'])
            md = json.loads(old['metadata']) if old else {}
            prior_ids = md.get('batch_event_ids', []) if md.get('consumer') == task['consumer'] else []
            md.update(provenance, through_event_id=after)
            md['batch_event_ids'] = sorted(set(prior_ids) | set(ids))
            ident = old['id'] if old else allocate_id(c, 'memory_summaries')
            c.execute('''INSERT INTO memory_summaries(id,scope,scope_key,version,content,metadata) VALUES(?,'project',?,1,?,?)
                ON CONFLICT(scope,scope_key,version) DO UPDATE SET content=excluded.content,metadata=excluded.metadata''',
                      (ident, scope_key, content, canonical(md)))
        c.executemany('INSERT INTO summary_state(consumer,last_event_id) VALUES(?,?)',
                      [(task['consumer'] + ':event:' + str(eid), eid) for eid in ids])
        c.execute('''INSERT INTO summary_state(consumer,last_event_id) VALUES(?,?) ON CONFLICT(consumer) DO UPDATE SET
            last_event_id=max(last_event_id,excluded.last_event_id),updated_at=CURRENT_TIMESTAMP''', (task['consumer'], after))
        c.commit()
        return dict(status='committed', events=len(ids), items=len(payload['items']), batch_id=task['batch_id'])


from contextlib import contextmanager


@contextmanager
def run_lock(db):
    """OS lock is held across model calls, no long database write transaction."""
    import os
    from pathlib import Path
    path = str(Path(db).resolve()) + '.bounded.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    acquired = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b'0')
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise ValidationError('overlap: bounded digest already running') from None
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        # Never unlink a lock inode while another process may be waiting on it.


def append_metric(path, row):
    import os
    from pathlib import Path
    path = Path(path)
    with run_lock(path):
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as handle:
            handle.write(canonical(row) + '\n')
            handle.flush()
            os.fsync(handle.fileno())


def extract_command(request, *, model='gpt-6-luna', command=None, timeout=120):
    import subprocess
    from memory_sync import load_packet
    if not isinstance(command, (list, tuple)) or not command or any(not isinstance(a, str) or not a or '\x00' in a for a in command):
        raise ValidationError('explicit authenticated worker argv required')
    try:
        result = subprocess.run([*command, '--model', model], input=canonical(request),
                                text=True, encoding='utf-8', capture_output=True, timeout=timeout)
        if result.returncode or len(result.stdout) > 2000000:
            raise ValidationError('model worker failed; coverage unchanged')
        output = load_packet(result.stdout)
        if not isinstance(output, dict) or set(output) != {'payload','provider','model','api_calls','usage'} or output['model'] != model:
            raise ValidationError('invalid model result envelope')
        return output
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
        raise ValidationError('model worker unavailable or invalid; coverage unchanged') from None


def run_once(db, *, extractor=None, command=None, model='gpt-6-luna', conflict_model=None,
             metrics_path=None, apply_result=True, **options):
    """One fixed bounded batch; caller persists a window ceiling across ticks."""
    import time
    import uuid
    from datetime import datetime, timezone
    from bounded_protocol import ConflictError
    start = time.monotonic()
    usage_fields = ('input_total_tokens','input_uncached_tokens','cache_read_tokens','output_tokens','reasoning_tokens')
    metric = dict(record_type='run', pipeline_version='agentmesh-bounded-v1', run_id=uuid.uuid4().hex,
                  start=datetime.now(timezone.utc).isoformat(), status='running', provider=None, model=model,
                  events_processed=0, llm_api_calls=0, fallbacks=0, retries=0, actual_cost_usd=None,
                  context_max_input_tokens=None, context_max_chars=0, submitted_chars=0, raw_chars=0,
                  accepted_items=0, upserted_items=0, source_coverage=None, errors=[])
    metric.update({f: None for f in usage_fields})
    results = []
    try:
        with run_lock(db):
            task = prepare(db, **options)
            metric['highwater'] = task['highwater']
            if not task['events']:
                metric['status'] = 'idle'
                return dict(status='idle', events=0, items=0, metrics=metric)
            metric['raw_chars'] = sum(len(e['content']) for e in task['events'])
            metric['submitted_chars'] = len(task['request']['instructions']) + len(task['request']['input'][0]['content']) + len(encode(task['request']['text']))
            metric['context_max_chars'] = metric['submitted_chars']
            metric['llm_api_calls'] = None  # unknown until the boundary reports
            def call(chosen):
                result = extractor(task['request'], model=chosen) if extractor else extract_command(task['request'], model=chosen, command=command)
                results.append(result)
                metric['provider'], metric['model'] = result['provider'], result['model']
                metric['llm_api_calls'] = sum(r['api_calls'] for r in results) if all(type(r.get('api_calls')) is int for r in results) else None
                for field in usage_fields:
                    values = [(r.get('usage') or {}).get(field) for r in results]
                    metric[field] = sum(values) if all(type(v) is int and v >= 0 for v in values) else None
                totals = [(r.get('usage') or {}).get('input_total_tokens') for r in results]
                metric['context_max_input_tokens'] = max(totals) if all(type(v) is int for v in totals) else None
                return result
            result = call(model)
            try:
                validate(copy.deepcopy(result['payload']), task['events'], task['related'])
            except ConflictError:
                if conflict_model is None:
                    raise
                metric['fallbacks'] = 1
                metric['llm_api_calls'] = None
                for field in usage_fields:
                    metric[field] = None
                metric['context_max_input_tokens'] = None
                result = call(conflict_model)
                validate(copy.deepcopy(result['payload']), task['events'], task['related'])
            metric['accepted_items'] = len(result['payload']['items'])
            report = apply(db, task, result['payload'], provider=result['provider'], model=result['model']) if apply_result else dict(status='validated', events=len(task['events']), items=len(result['payload']['items']))
            metric['status'] = report['status']
            metric['events_processed'] = report['events']
            metric['source_coverage'] = 1.0 if report['status'] in ('committed','validated') else 0.0
            metric['upserted_items'] = report['items'] if apply_result else 0
            return {**report, 'metrics': metric, 'payload': result['payload'], 'task': task}
    except Exception as exc:
        metric['status'] = 'failed'
        metric['errors'] = [type(exc).__name__]
        raise
    finally:
        metric['end'] = datetime.now(timezone.utc).isoformat()
        metric['duration_seconds'] = time.monotonic() - start
        if metrics_path is not None:
            append_metric(metrics_path, metric)


def main(argv=None):
    import argparse
    import sys
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('database')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--seed-coverage', action='store_true', help='explicit one-time legacy receipt migration')
    p.add_argument('--command-json', help='worker argv; model flag is appended')
    p.add_argument('--model', default='gpt-6-luna')
    p.add_argument('--conflict-model')
    p.add_argument('--metrics-log')
    p.add_argument('--consumer', default=DEFAULT_CONSUMER)
    p.add_argument('--primary-node', default='mac')
    p.add_argument('--highwater', type=int)
    for key, default in [('max-events',24),('event-chars',20000),('related-chars',28000),('context-chars',60000)]:
        p.add_argument('--' + key, type=int, default=default)
    args = p.parse_args(argv)
    try:
        options = {k: getattr(args,k) for k in ('consumer','primary_node','highwater','max_events','event_chars','related_chars','context_chars')}
        if args.seed_coverage:
            if not args.apply or args.command_json:
                p.error('--seed-coverage requires --apply and no worker')
            result = seed_coverage(args.database, consumer=args.consumer, primary_node=args.primary_node)
        elif args.command_json:
            result = run_once(args.database, command=json.loads(args.command_json), model=args.model,
                              conflict_model=args.conflict_model, metrics_path=args.metrics_log,
                              apply_result=args.apply, **options)
            result = {k:v for k,v in result.items() if k not in ('task','payload')}
        else:
            if args.apply:
                p.error('--apply requires explicit worker argv')
            task = prepare(args.database, **options)
            result = dict(status='prepared', batch_id=task['batch_id'], events=len(task['events']), highwater=task['highwater'])
        print(canonical(result))
        return 0 if result['status'] != 'stale' else 2
    except Exception as exc:
        print(canonical(dict(status='blocked', error_class=type(exc).__name__)), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
