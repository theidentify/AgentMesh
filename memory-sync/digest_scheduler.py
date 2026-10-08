"""Opt-in fixed-event-window daily/boot coordinator; no service registration."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

import bounded_digest
from bounded_protocol import ValidationError
from memory_sync import connect, config, canonical, digest
from recall_memory import open_readonly
from shared_memory_context import pending_ids
from sqlite_export import publish
from summarize_memory import DEFAULT_CONSUMER


EMPTY: dict = dict(pending=None, success=None)


def read_state(database, consumer=DEFAULT_CONSUMER):
    with open_readonly(database) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='_agentmesh_digest_windows'").fetchone():
            return dict(EMPTY)
        row = c.execute('SELECT state FROM _agentmesh_digest_windows WHERE consumer=?', (consumer,)).fetchone()
        return json.loads(row[0]) if row else dict(EMPTY)


def save(database, consumer, state):
    with connect(database) as c, c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT INTO _agentmesh_digest_windows(consumer,state) VALUES(?,?) ON CONFLICT(consumer) DO UPDATE SET state=excluded.state',
                  (consumer, canonical(state)))


def tick(database, *, root, consumer=DEFAULT_CONSUMER, primary_node='mac', command=None,
         extractor=None, exporter=publish, model='gpt-6-luna', conflict_model='gpt-6.1-sol',
         metrics_path=None, max_batches=8, max_events=24, event_chars=20000,
         related_chars=28000, context_chars=60000, now=None, boot_id=None, force=False):
    bounded_digest._options(consumer, primary_node, max_events, event_chars)
    if any(type(v) is not int or v < 1 for v in (related_chars, context_chars)):
        raise ValidationError('invalid context bounds')
    if type(max_batches) is not int or not 1 <= max_batches <= 1000:
        raise ValidationError('invalid batch bound')
    instant = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValidationError('timezone required')
    stamp, day = instant.isoformat(), instant.date().isoformat()
    if boot_id is not None and (not isinstance(boot_id, str) or not boot_id or len(boot_id) > 200):
        raise ValidationError('invalid boot identity')
    options: dict = dict(consumer=consumer, primary_node=primary_node, max_events=max_events,
                   event_chars=event_chars, related_chars=related_chars, context_chars=context_chars)
    identity = dict(**options, root=str(Path(root).expanduser().absolute()), model=model, conflict_model=conflict_model,
                    command=command)
    with bounded_digest.run_lock(str(Path(database).resolve()) + '.scheduler'):
        # One transaction freezes the currently eligible event set and its configuration.
        with connect(database) as c, c:
            c.execute('BEGIN IMMEDIATE')
            if config(c)['node'] != primary_node:
                raise ValidationError('scheduler requires designated primary node')
            c.execute('''CREATE TABLE IF NOT EXISTS _agentmesh_digest_windows(
                consumer TEXT PRIMARY KEY,state TEXT NOT NULL CHECK(json_valid(state)))''')
            row = c.execute('SELECT state FROM _agentmesh_digest_windows WHERE consumer=?', (consumer,)).fetchone()
            state = json.loads(row[0]) if row else dict(EMPTY)
            success = state['success'] or {}
            if not state['pending']:
                due = force or success.get('day') != day or (boot_id is not None and success.get('boot_id') != boot_id)
                if not due:
                    return dict(status='scheduled', llm_api_calls=0)
                ids = pending_ids(c, consumer)
                if ids and extractor is None and (not isinstance(command, (list, tuple)) or not command or any(not isinstance(a, str) or not a or '\x00' in a for a in command)):
                    raise ValidationError('explicit authenticated worker argv required before opening a window')
                state['pending'] = dict(window_id=uuid.uuid4().hex, day=day, boot_id=boot_id, started_at=stamp,
                    trigger='force' if force else 'boot' if boot_id is not None and success.get('boot_id') != boot_id else 'daily',
                    phase='digest', eligible_ids=ids, manifest_sha256=digest(ids), highwater=max(ids, default=0), configuration=identity)
                c.execute('INSERT INTO _agentmesh_digest_windows(consumer,state) VALUES(?,?) ON CONFLICT(consumer) DO UPDATE SET state=excluded.state',
                          (consumer, canonical(state)))
        pending = state['pending']
        if pending['configuration'] != identity or pending['manifest_sha256'] != digest(pending['eligible_ids']):
            raise ValidationError('pending window configuration/manifest mismatch')
        manifest = pending['eligible_ids']
        options.update(highwater=pending['highwater'], eligible_ids=manifest)
        batches = 0
        if pending['phase'] == 'digest':
            for _ in range(max_batches):
                task = bounded_digest.prepare(database, **options)
                if not task['events']:
                    break
                result = bounded_digest.run_once(database, extractor=extractor, command=command, model=model,
                    conflict_model=conflict_model, metrics_path=metrics_path, **options)
                if result['status'] != 'committed':
                    raise ValidationError('window batch did not commit')
                batches += 1
            if bounded_digest.prepare(database, **options)['events']:
                return dict(status='pending', window_id=pending['window_id'], batches=batches)
            # A removed/changed-kind eligible event cannot masquerade as drained.
            with open_readonly(database) as c:
                missing = c.execute('''SELECT count(*) FROM json_each(?) j WHERE NOT EXISTS(
                    SELECT 1 FROM observation_events e WHERE e.id=j.value AND e.kind IN ('user_request','assistant_response'))''',
                    (canonical(manifest),)).fetchone()[0]
            if missing:
                raise ValidationError('eligible event removed or kind changed')
            pending['phase'] = 'export'
            save(database, consumer, state)
        # Export errors retain this phase; restart retries export without any model calls.
        exported = exporter(database, identity['root'])
        state['success'] = dict(day=pending['day'], boot_id=pending['boot_id'], window_id=pending['window_id'],
                                completed_at=stamp, manifest_sha256=pending['manifest_sha256'], events=len(manifest))
        state['pending'] = None
        save(database, consumer, state)
        if read_state(database, consumer) != state:
            raise ValidationError('scheduler success read-back mismatch')
        return dict(status='completed', window_id=state['success']['window_id'], batches=batches, exporter=exported)


def main(argv=None):
    import argparse
    import sys
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('database')
    p.add_argument('--root', required=True)
    p.add_argument('--command-json', default=os.environ.get('AGENTMESH_DIGEST_COMMAND_JSON'))
    p.add_argument('--metrics-log')
    p.add_argument('--boot-id', default=os.environ.get('AGENTMESH_BOOT_ID'), help='stable OS boot identity, not a per-process UUID')
    p.add_argument('--force', action='store_true')
    p.add_argument('--model', default='gpt-6-luna')
    p.add_argument('--conflict-model', default='gpt-6.1-sol')
    for key, default in [('max-batches',8), ('max-events',24), ('event-chars',20000), ('related-chars',28000), ('context-chars',60000)]:
        p.add_argument('--' + key, type=int, default=default)
    args = p.parse_args(argv)
    try:
        options = vars(args)
        options['command'] = json.loads(options.pop('command_json')) if args.command_json else None
        options['metrics_path'] = options.pop('metrics_log')
        print(canonical(tick(**options)))
        return 0
    except Exception as exc:
        print(canonical(dict(status='blocked', error_class=type(exc).__name__)), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
