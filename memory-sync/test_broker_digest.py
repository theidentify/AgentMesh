"""Broker summarizer backend (SQLite retirement R2): cursors, failures and state, on synthetic transcripts only.

The write side (heads, remember/revise, quarantine) is tested in profile-log; here the backend is an in-memory fake.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

import broker_digest
from bounded_protocol import ValidationError

HERE = Path(__file__).resolve().parent


def record(ident, text, stamp='2026-11-02T00:00:01Z'):
    return json.dumps({'type': 'message', 'id': ident, 'timestamp': stamp,
                       'message': {'role': 'user', 'content': [{'type': 'text', 'text': text}]}}) + '\n'


def extractor(request, *, model):
    seen = json.loads(request['input'][0]['content'])
    related = {r['memory_key'] for r in seen['related_existing']}
    items = []
    for event in seen['events']:
        key = 'project.demo.fact.' + event['content'].split()[0]
        item = dict(memory_key=key, content=event['content'], status='active', confidence=0.5,
                    evidence=[dict(event_id=event['id'], quote=event['content'])])
        if key not in related: item.update(kind='fact', scope='project', scope_key='demo', project='demo')
        items.append(item)
    return dict(payload=dict(items=items, reviewed_event_ids=[e['id'] for e in seen['events']], conflicts=[]),
                provider='fixture', model=model, api_calls=1, usage={})


class Fake:
    def __init__(self):
        self.heads, self.writes, self.fail, self.stale = {}, [], False, False

    def related(self, project, sessions):
        rows = [dict(memory_key=k, kind=v['kind'], scope=v['scope'], scope_key=v['scope_key'], project=v['project'],
                     content=v['content'], status=v['status'], confidence=v['confidence']) for k, v in sorted(self.heads.items())]
        return dict(related=rows, digest=str(len(self.writes)))

    def write(self, request):
        if self.fail: raise ValidationError('summarizer write failed: unavailable; cursor unchanged')
        if self.stale: return dict(status='stale')
        self.writes.append(request)
        for item in request['items']: self.heads[item['memory_key']] = item
        return dict(status='committed', written=[dict(memory_key=i['memory_key'], outcome='created', id='x') for i in request['items']],
                    rejected=[], skipped=0, summaries=[])


@pytest.fixture
def setup(tmp_path):
    folder = tmp_path / 'omp' / '-Workspaces-demo'
    folder.mkdir(parents=True)
    return tmp_path, folder, Fake()


def run(tmp_path, backend, **kw):
    state = broker_digest.State(tmp_path / 'jobs')
    sources, _ = broker_digest.discover({'omp': tmp_path / 'omp'})
    return broker_digest.run_once(state, sources, backend, extractor=extractor, model='m', **kw)


def cursors(tmp_path):
    return json.loads((tmp_path / 'jobs' / broker_digest.CURSORS).read_text())['files']


def test_window_ends_mid_file_and_resumes(setup):
    tmp_path, folder, fake = setup
    (folder / 'a.jsonl').write_text(record('u1', 'alpha one') + record('u2', 'beta two') + record('u3', 'gamma three'))
    assert run(tmp_path, fake, max_events=2)['events'] == 2
    entry = next(iter(cursors(tmp_path).values()))
    assert entry['offset'] == 0 and len(entry['done']) == 2
    assert run(tmp_path, fake, max_events=2)['events'] == 1
    entry = next(iter(cursors(tmp_path).values()))
    assert entry['done'] == [] and entry['offset'] == (folder / 'a.jsonl').stat().st_size
    assert [i['content'] for w in fake.writes for i in w['items']] == ['alpha one', 'beta two', 'gamma three']
    assert run(tmp_path, fake)['status'] == 'idle'
    with (folder / 'a.jsonl').open('a') as handle: handle.write(record('u4', 'delta four') + '{"partial')
    assert run(tmp_path, fake)['events'] == 1 and fake.writes[-1]['items'][0]['content'] == 'delta four'
    # Provenance carries transcript event ids, never window-local numbers.
    assert all(type(e) is str for e in fake.writes[-1]['provenance']['batch_events'])
    assert fake.writes[-1]['items'][0]['evidence'][0]['event_id'] == fake.writes[-1]['items'][0]['source_event_ids'][0]


def test_failed_or_stale_write_keeps_cursors(setup):
    tmp_path, folder, fake = setup
    (folder / 'a.jsonl').write_text(record('u1', 'alpha one'))
    fake.fail = True
    with pytest.raises(ValidationError): run(tmp_path, fake)
    assert not (tmp_path / 'jobs' / broker_digest.CURSORS).exists()
    fake.fail, fake.stale = False, True
    assert run(tmp_path, fake)['status'] == 'stale'
    assert not (tmp_path / 'jobs' / broker_digest.CURSORS).exists()
    fake.stale = False
    assert run(tmp_path, fake)['status'] == 'committed' and len(fake.writes) == 1


def test_rotation_restarts_and_quiet_file_moves(setup):
    tmp_path, folder, fake = setup
    (folder / 'a.jsonl').write_text(record('u1', 'alpha one') + record('u2', 'beta two'))
    run(tmp_path, fake, max_events=1)
    # The agent replaces the file (new inode): the old cursor and its done keys no longer apply.
    (folder / 'new.tmp').write_text(record('n1', 'zeta new'))
    os.replace(folder / 'new.tmp', folder / 'a.jsonl')
    while run(tmp_path, fake)['status'] != 'idle': pass
    assert [i['content'] for w in fake.writes for i in w['items']] == ['alpha one', 'zeta new']
    # A transcript with nothing to summarize moves its cursor without a model call.
    (folder / 'quiet.jsonl').write_text(json.dumps({'type': 'session', 'id': 's'}) + '\n')
    assert run(tmp_path, fake)['status'] == 'idle'
    quiet = cursors(tmp_path)[str((folder / 'quiet.jsonl').resolve())]
    assert quiet['offset'] == (folder / 'quiet.jsonl').stat().st_size


def test_ingestion_floor(setup):
    tmp_path, folder, fake = setup
    (folder / 'a.jsonl').write_text(record('u1', 'old news', '2026-01-01T00:00:00Z') + record('u2', 'fresh news'))
    state = broker_digest.State(tmp_path / 'jobs')
    state.summary['start_at'] = '2026-06-01T00:00:00Z'; state.save()
    run(tmp_path, fake)
    assert [i['content'] for w in fake.writes for i in w['items']] == ['fresh news']


def test_state_files(tmp_path):
    state = broker_digest.State(tmp_path / 'jobs')
    state.save()
    if os.name != 'nt':
        assert all((tmp_path / 'jobs' / n).stat().st_mode & 0o777 == 0o600 for n in (broker_digest.CURSORS, broker_digest.STATE))
    (tmp_path / 'jobs' / broker_digest.STATE).write_text(json.dumps(dict(format='other')))
    with pytest.raises(ValidationError): broker_digest.State(tmp_path / 'jobs')


def test_tick_schedule(setup):
    tmp_path, folder, fake = setup
    (folder / 'a.jsonl').write_text(record('u1', 'alpha one') + record('u2', 'beta two'))
    options = dict(roots={'omp': tmp_path / 'omp'}, extractor=extractor, model='m', max_events=1)
    jobs = tmp_path / 'jobs'
    assert broker_digest.tick(jobs, fake, max_batches=1, now='2026-11-02T08:00:00+00:00', **options)['status'] == 'pending'
    done = broker_digest.tick(jobs, fake, max_batches=5, now='2026-11-02T09:00:00+00:00', **options)
    assert (done['status'], done['events']) == ('completed', 1)
    assert broker_digest.tick(jobs, fake, now='2026-11-02T10:00:00+00:00', **options)['status'] == 'scheduled'
    assert broker_digest.tick(jobs, fake, now='2026-11-02T10:00:00+00:00', force=True, **options)['status'] == 'completed'


def test_cli_backend_errors(tmp_path):
    script = tmp_path / 'cli.py'
    script.write_text('import sys,json\nsys.stdin.read()\nprint(json.dumps({"error": "Bad Code /etc"}))\nsys.exit(1)\n')
    backend = broker_digest.CliBackend([sys.executable, str(script)], str(tmp_path / 'k'), str(tmp_path / 'm'))
    with pytest.raises(ValidationError, match='summarizer related failed: unavailable'): backend.related('demo', [])
    script.write_text('import sys,json\nsys.stdin.read()\nprint(json.dumps({"error": "unmapped_project"}))\nsys.exit(1)\n')
    with pytest.raises(ValidationError, match='unmapped_project'): backend.write({})
    script.write_text('import sys,json\nr=json.load(sys.stdin)\nprint(json.dumps({"related": [], "digest": sys.argv[1:4] == ["summary","related","--agent-key"] and r["project"]}))\n')
    assert backend.related('demo', []) == dict(related=[], digest='demo')
    with pytest.raises(ValidationError): broker_digest.CliBackend([sys.executable], 'relative', str(tmp_path))


def test_production_broker_backend(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location('agentmesh_production_broker', HERE / 'jobs/agentmesh_production.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    folder = tmp_path / 'omp' / '-Workspaces-demo'; folder.mkdir(parents=True)
    (folder / 'a.jsonl').write_text(record('u1', 'alpha one'))
    config = tmp_path / 'agentmesh-production.json'
    broker = dict(cli_argv=[sys.executable, '-m', 'agentmesh_log.cli'], agent_key=str(tmp_path / 'k'),
                  mapping=str(tmp_path / 'm'), state_dir=str(tmp_path / 'jobs'))
    value = dict(backend='broker', broker=broker, runtime=str(HERE), status_dir=str(tmp_path / 'status'),
                 roots={'omp': str(tmp_path / 'omp')}, summary=dict(model='m'))
    config.write_text(json.dumps(value))
    monkeypatch.setattr(module, 'CONFIG', config)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    assert module.execute('ingest', []) == 0
    report = json.loads((tmp_path / 'status/last-ingest.json').read_text())
    assert (report['backend'], report['processed'], report['errors']) == ('broker', 1, 0)
    assert not (tmp_path / 'jobs' / broker_digest.CURSORS).exists()
    capsys.readouterr()
    assert module.execute('recall', []) == 1
    assert json.loads(capsys.readouterr().err.strip().splitlines()[-1])['error_class'] == 'ValueError'
    config.write_text(json.dumps({**value, 'broker': {**broker, 'extra': 1}}))
    assert module.execute('ingest', []) == 1
