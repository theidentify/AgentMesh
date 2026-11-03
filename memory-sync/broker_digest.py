"""Bounded digest on the broker backend (SQLite retirement R2).

The same windows, prompt, validation and quarantine rules as bounded_digest, without the staging database:
events are read straight from the agents' JSONL files and memories are written through the broker by the
summarizer agent (`agentmesh-log summary related|write`, JSON on stdin). Nothing from a transcript is stored.

State lives in two JSON files in the jobs state directory, written atomically at 0600 under one lock:
  transcript-cursors.json  per file: identity, byte offset and line of the first record not yet summarized, plus
                           the keys of events already summarized after it (a window can end mid-file).
  summary-state.json       ingestion floor, schedule (day/boot of the last drained run), quarantine records.
A window whose write fails keeps every cursor, so the next tick reads and retries the same events.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from bounded_protocol import ValidationError, ConflictError, collect, validate, build_request, encode, error_detail
import transcripts

KINDS = ('user_request', 'assistant_response')
ROOTS = {'omp': '.omp/agent/sessions', 'codex': '.codex/sessions', 'claude': '.claude/projects'}
CURSORS = 'transcript-cursors.json'
STATE = 'summary-state.json'
CURSORS_FORMAT = 'agentmesh-transcript-cursors-v1'
STATE_FORMAT = 'agentmesh-summary-state-v1'
QUARANTINE_LIMIT = 500
UNMAPPED = 'summary-unmapped.json'  # projects the last tick held back for want of a mapping; the owner maps them
UNMAPPED_FORMAT = 'agentmesh-summary-unmapped-v1'


class Unmapped(ValidationError):
    """The migration mapping has no route for this project. Its events wait; other projects carry on."""
    def __init__(self, project):
        super().__init__('unmapped project; its events wait for a mapping')
        self.project = project


def load(path, fmt, empty):
    path = Path(path)
    if not path.exists():
        return dict(format=fmt, **copy.deepcopy(empty))
    if path.is_symlink() or not path.is_file():
        raise ValidationError('invalid state file')
    value = json.loads(path.read_text(encoding='utf-8'))
    if type(value) is not dict or value.get('format') != fmt or set(value) != {'format', *empty}:
        raise ValidationError('invalid state file')
    return value


def save(path, value):
    """Atomic 0600 replace: a crash leaves the old file or the new one, never a torn one."""
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class State:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cursors = load(self.directory / CURSORS, CURSORS_FORMAT, dict(files={}))
        self.summary = load(self.directory / STATE, STATE_FORMAT, dict(start_at=None, success=None, pending=False, quarantine=[]))

    def save(self):
        save(self.directory / CURSORS, self.cursors)
        save(self.directory / STATE, self.summary)


def discover(roots=None, home=None):
    """(path, agent) for every regular JSONL transcript under the agent roots, the same walk as ingest_sessions."""
    home = Path(home).expanduser() if home is not None else Path.home()
    roots = roots if roots is not None else {a: home / p for a, p in ROOTS.items()}
    if any(agent not in ROOTS for agent in roots):
        raise ValueError('unsupported agent')
    found, errors = [], []
    for agent, directory in roots.items():
        directory = Path(directory).expanduser()
        if directory.is_symlink():
            continue
        root = directory.resolve()
        if (agent == 'claude' and 'subagents' in root.parts) or not root.is_dir():
            continue
        for base, dirs, files in os.walk(root, followlinks=False,
                                         onerror=lambda e: None if isinstance(e, FileNotFoundError) else errors.append(e)):
            dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink() and not (agent == 'claude' and d == 'subagents'))
            for name in sorted(files):
                candidate = Path(base) / name
                if name.endswith('.jsonl') and not candidate.is_symlink() and root in candidate.resolve().parents:
                    found.append((candidate.resolve(), agent))
    return found, errors


def _floor(start_at):
    if start_at is None:
        return None
    from timestamps import parse_utc
    return parse_utc(start_at)


def scan(state, sources, *, start_at=None):
    """Read every transcript after its cursor; returns chunks of not-yet-summarized events and a report.

    A file with nothing left to summarize (no new user or assistant events) moves its cursor at once."""
    report = dict(found=len(sources), processed=0, skipped=0, invalid_json=0, errors=0, error_classes=[])
    files = state.cursors['files']
    floor = _floor(start_at)
    chunks = []
    for path, agent in sources:
        try:
            resolved, project, skipped = transcripts.resolve(path, agent)
            entry = files.get(str(resolved))
            if skipped or (entry and not entry['done'] and (entry['identity'], entry['offset']) == transcripts.identity(resolved)):
                report['skipped'] += 1
                continue
            cursor = (entry['identity'], entry['offset'], entry['line']) if entry else None
            window = transcripts.read(resolved, agent, project, cursor)
            report['processed'] += 1
            if not window['complete']:
                continue
            report['invalid_json'] += len(window['errors'])
            done = set(entry['done']) if entry and window['offset'] == entry['offset'] and cursor[0] == window['cursor'][0] else set()
            events = window['events']
            if floor is not None:
                events = [e for e in events if _after(e, floor)]
            todo = [e for e in events if e.kind in KINDS and e.event_key not in done]
            if not todo:
                identity, offset, line = window['cursor']
                files[str(resolved)] = dict(identity=identity, offset=offset, line=line, done=[])
                continue
            chunks.append(dict(path=str(resolved), agent=agent, window=window, done=done, todo=todo, start=window['offset'],
                               start_line=window['cursor'][2] - window['lines']))
        except FileNotFoundError:
            report['skipped'] += 1
        except Exception as exc:
            report['errors'] += 1
            report['error_classes'].append(type(exc).__name__)
    return chunks, report


def _after(event, floor):
    from timestamps import parse_utc
    try:
        return parse_utc(event.occurred_at) >= floor
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError('start-at requires valid event timestamps; repair the transcript and retry') from exc


def window(chunks, *, max_events=24, event_chars=20000):
    """The next bounded batch: one project, in file order, window-local event ids 1..n for the model."""
    pending = [(chunk, e) for chunk in chunks for e in chunk['todo']]
    if not pending:
        return []
    project = pending[0][1].project
    rows = []
    for number, (chunk, event) in enumerate([p for p in pending if p[1].project == project][:max_events + 1], 1):
        row = asdict(event)
        row.update(id=number, _chunk=chunk)
        rows.append(row)
    batch = collect([{k: v for k, v in r.items() if k != '_chunk'} for r in rows], event_chars, max_events)
    for row, original in zip(batch, rows):
        row['_chunk'] = original['_chunk']
    return batch


def commit_cursors(state, batch):
    """Mark the batch's events summarized; a file whose pending events are all done moves its cursor past them."""
    files = state.cursors['files']
    touched = {}
    for row in batch:
        chunk = row['_chunk']
        chunk['done'].add(row['event_key'])
        touched[chunk['path']] = chunk
    for path, chunk in touched.items():
        if all(e.event_key in chunk['done'] for e in chunk['todo']):
            identity, offset, line = chunk['window']['cursor']
            files[path] = dict(identity=identity, offset=offset, line=line, done=[])
        else:
            identity = chunk['window']['cursor'][0]
            files[path] = dict(identity=identity, offset=chunk['start'], line=chunk['start_line'], done=sorted(chunk['done']))


class CliBackend:
    """The summarizer agent through `agentmesh-log summary`; requests on stdin, fixed error codes only."""
    def __init__(self, cli_argv, agent_key, mapping, timeout=600):
        if not isinstance(cli_argv, list) or not cli_argv or any(type(a) is not str or not a for a in cli_argv):
            raise ValidationError('invalid summarizer cli')
        for value in (agent_key, mapping):
            if type(value) is not str or not Path(value).is_absolute():
                raise ValidationError('invalid summarizer paths')
        self.argv, self.agent_key, self.mapping, self.timeout = cli_argv, agent_key, mapping, timeout

    def call(self, action, request):
        argv = [*self.argv, 'summary', action, '--agent-key', self.agent_key, '--mapping', self.mapping]
        try:
            result = subprocess.run(argv, input=json.dumps(request, ensure_ascii=False).encode('utf-8'),
                                    capture_output=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise ValidationError('summarizer timed out; cursor unchanged') from None
        except OSError:
            raise ValidationError('summarizer unavailable; cursor unchanged') from None
        try:
            value = json.loads(result.stdout.decode('utf-8'))
        except (UnicodeError, ValueError):
            value = None
        if type(value) is not dict or result.returncode or 'error' in value:
            code = value.get('error') if type(value) is dict else None
            code = code if type(code) is str and re.fullmatch(r'[a-z_]{1,40}', code) else 'unavailable'
            if action == 'related' and code == 'unmapped_project':
                raise Unmapped(request.get('project'))
            raise ValidationError('summarizer %s failed: %s; cursor unchanged' % (action, code))
        return value

    def related(self, project, sessions):
        return self.call('related', dict(project=project, sessions=sessions))

    def write(self, request):
        return self.call('write', request)


def prepare(chunks, backend, *, max_events=24, event_chars=20000, related_chars=28000, context_chars=60000):
    batch = window(chunks, max_events=max_events, event_chars=event_chars)
    if not batch:
        return None
    events = [{k: v for k, v in r.items() if k != '_chunk'} for r in batch]
    sessions = sorted({e['source_session_id'] for e in events if e.get('source_session_id')})
    answer = backend.related(events[0]['project'], sessions)
    related = answer['related']
    if len(encode(related)) > related_chars:
        raise ValidationError('related context overflow; coverage unchanged')
    request = build_request(events, related, context_chars)
    task = dict(events=events, related=related, related_digest=answer['digest'], sessions=sessions, request=request)
    task['batch_id'] = hashlib.sha256(encode([e['event_key'] for e in events]).encode('utf-8')).hexdigest()
    return batch, task


def extract(task, *, extractor=None, command=None, model, conflict_model=None, calls=None):
    """Model call with the digest's own retry rules: one same-model retry on a validation error, and the conflict
    model only for an explicit conflict."""
    calls = [] if calls is None else calls

    def call(chosen, request):
        if extractor:
            result = extractor(request, model=chosen)
        else:
            from bounded_digest import extract_command
            result = extract_command(request, model=chosen, command=command)
        calls.append(result)
        return result

    def first_valid(chosen):
        result = call(chosen, task['request'])
        try:
            validate(copy.deepcopy(result['payload']), task['events'], task['related'])
        except ConflictError:
            raise
        except ValidationError as exc:
            retry = dict(task['request'], instructions=task['request']['instructions'] +
                         '\nYour previous answer for this exact batch was rejected by the validator: %s. '
                         'Fix that and return the complete corrected JSON.' % error_detail(exc))
            result = call(chosen, retry)
        return result

    try:
        return first_valid(model)
    except ConflictError:
        if conflict_model is None:
            raise
        return first_valid(conflict_model)


def apply(state, batch, task, payload, backend, *, provider=None, model=None):
    """Validate, write through the broker and only then move cursors; stale related context writes nothing."""
    identity_changes, scope_changes, rejected = {}, set(), []
    payload = validate(copy.deepcopy(payload), task['events'], task['related'], identity_changes=identity_changes,
                       scope_changes=scope_changes, rejected=rejected)
    sources = {e['id']: e['source_event_id'] for e in task['events']}
    items = []
    for item in payload['items']:
        item = dict(item)
        item['evidence'] = [dict(q, event_id=sources[q['event_id']]) for q in item['evidence']]
        item['source_event_ids'] = [sources[i] for i in item['source_event_ids']]
        items.append(item)
    agents = sorted({e['source_agent'] for e in task['events']})
    provenance = dict(batch_id=task['batch_id'], batch_events=[e['source_event_id'] for e in task['events']],
                      provider=provider, model=model, origin=dict(agents=agents, sessions=task['sessions']))
    result = backend.write(dict(project=task['events'][0]['project'], sessions=task['sessions'],
                                related_digest=task['related_digest'], items=items, provenance=provenance))
    if result.get('status') == 'stale':
        return dict(status='stale', events=0, items=0)
    if result.get('status') != 'committed':
        raise ValidationError('invalid summarizer result')
    stamp = datetime.now(timezone.utc).isoformat()
    records = [dict(r, batch_id=task['batch_id'], created_at=stamp) for r in result['rejected']]
    records += [dict(memory_key=r['memory_key'], reason=r['reason'], batch_id=task['batch_id'], created_at=stamp)
                for r in rejected if r.get('memory_key')]
    state.summary['quarantine'] = (state.summary['quarantine'] + records)[-QUARANTINE_LIMIT:]
    commit_cursors(state, batch)
    state.save()
    return dict(status='committed', events=len(batch), items=len(result['written']),
                rejected_items=len(records), summaries=len(result['summaries']), batch_id=task['batch_id'])


def run_once(state, sources, backend, *, extractor=None, command=None, model='gpt-6-luna', conflict_model=None,
             max_events=24, event_chars=20000, related_chars=28000, context_chars=60000, held=None):
    """One window. A project without a mapping is added to `held` and skipped: its cursors stay, so its events are
    summarized once the owner maps it, and one new project never stops the others."""
    held = set() if held is None else held
    chunks, report = scan(state, sources, start_at=state.summary['start_at'])
    while True:
        for chunk in chunks:
            chunk['todo'] = [e for e in chunk['todo'] if e.project not in held]
        try:
            prepared = prepare(chunks, backend, max_events=max_events, event_chars=event_chars,
                               related_chars=related_chars, context_chars=context_chars)
            break
        except Unmapped as exc:
            if exc.project in held:
                raise
            held.add(exc.project)
    if prepared is None:
        state.save()
        return dict(status='idle', events=0, items=0, scan=report)
    batch, task = prepared
    calls = []
    result = extract(task, extractor=extractor, command=command, model=model, conflict_model=conflict_model, calls=calls)
    applied = apply(state, batch, task, result['payload'], backend, provider=result['provider'], model=result['model'])
    return dict(applied, scan=report, llm_api_calls=len(calls))


def tick(state_dir, backend, *, metrics_path=None, **kw):
    """One scheduled tick; with metrics_path, a run row in the summary metrics log (the same log the SQLite digest
    wrote, so Insight's metrics view continues), including failed ticks."""
    import time
    import uuid
    started, clock = datetime.now(timezone.utc), time.monotonic()
    metric = dict(record_type='run', pipeline_version='agentmesh-broker-v1', run_id=uuid.uuid4().hex, start=started.isoformat(),
                  status='running', model=kw.get('model'), events_processed=0, batch_count=0, llm_api_calls=0, errors=[], error_detail=None)
    try:
        report = _tick(state_dir, backend, **kw)
        metric.update(status=report['status'], events_processed=report.get('events', 0), batch_count=report.get('batches', 0),
                      llm_api_calls=report.get('llm_api_calls', 0))
        return report
    except Exception as exc:
        metric.update(status='failed', errors=[type(exc).__name__], error_detail=error_detail(exc))
        raise
    finally:
        if metrics_path is not None and metric['status'] != 'scheduled':
            from bounded_digest import append_metric
            metric.update(end=datetime.now(timezone.utc).isoformat(), duration_seconds=time.monotonic() - clock)
            append_metric(metrics_path, metric)


def _tick(state_dir, backend, *, roots=None, home=None, max_batches=8, now=None, boot_id=None, force=False, **options):
    """Daily/boot schedule like digest_scheduler: once due, run windows until drained, at most max_batches per tick."""
    from bounded_digest import run_lock
    if type(max_batches) is not int or not 1 <= max_batches <= 1000:
        raise ValidationError('invalid batch bound')
    instant = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValidationError('timezone required')
    day = instant.date().isoformat()
    Path(state_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
    with run_lock(str(Path(state_dir) / CURSORS)):
        state = State(state_dir)
        success = state.summary['success'] or {}
        due = state.summary['pending'] or force or success.get('day') != day or (boot_id is not None and success.get('boot_id') != boot_id)
        if not due:
            return dict(status='scheduled', llm_api_calls=0)
        state.summary['pending'] = True
        state.save()
        sources, walk_errors = discover(roots, home)
        batches = events = calls = 0
        held = set()
        try:
            for _ in range(max_batches):
                result = run_once(state, sources, backend, held=held, **options)
                if result['status'] == 'idle':
                    break
                if result['status'] != 'committed':
                    raise ValidationError('window batch did not commit')
                batches += 1
                events += result['events']
                calls += result['llm_api_calls']
            else:
                return dict(status='pending', batches=batches, events=events, llm_api_calls=calls, unmapped=sorted(held, key=str))
        finally:
            save(Path(state_dir) / UNMAPPED, dict(format=UNMAPPED_FORMAT, at=instant.isoformat(),
                                                  projects=sorted((p for p in held if p is not None), key=str)))
        state.summary.update(pending=False, success=dict(day=day, boot_id=boot_id, completed_at=instant.isoformat()))
        state.save()
        return dict(status='completed', batches=batches, events=events, llm_api_calls=calls, walk_errors=len(walk_errors),
                    unmapped=sorted(held, key=str), scan={k: v for k, v in result['scan'].items() if k != 'error_classes'})
