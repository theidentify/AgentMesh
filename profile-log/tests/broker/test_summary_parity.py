"""SQLite retirement R2: the broker summarizer backend against the SQLite staging pipeline, same fixture transcripts.

Both runs use one deterministic extractor, so any difference is the backend's: memory keys, kinds, scopes, statuses
and contents must match, and so must the project summary's current-memory block.
"""
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'memory-sync'))

from agentmesh_log import summarize
from agentmesh_log.migrate.mapping import Mapping
from agentmesh_log.broker.service import Broker
from .test_pages import Local
from .support import setup_home

MAPPING = Mapping(dict(format='agentmesh-migration-mapping-v1', projects={'demo': 'work/a'}, prefixes=[],
                       null_project='work/a', **{'global': 'work/a'}))
FIELDS = ('memory_key', 'kind', 'scope', 'scope_key', 'project', 'content', 'status')


def record(ident, role, text):
    return json.dumps({'type': 'message', 'id': ident, 'timestamp': '2026-11-02T00:00:%02dZ' % int(ident[1:]),
                       'message': {'role': role, 'content': [{'type': 'text', 'text': text}]}}) + '\n'


def transcripts(root):
    folder = root/'omp'/'-Workspaces-demo'; folder.mkdir(parents=True)
    (folder/'one.jsonl').write_text(''.join([
        record('u1', 'user', 'remember: deploy Deployment stays manual'), record('a1', 'assistant', 'Noted.'),
        record('u2', 'user', 'remember: index Use SQLite only for indexes'), record('a2', 'assistant', 'Fine.'),
        record('u3', 'user', 'just chatting'), record('a3', 'assistant', 'Sure.')]), encoding='utf-8')
    (folder/'two.jsonl').write_text(''.join([
        record('u4', 'user', 'remember: deploy Deployment is automated now'), record('a4', 'assistant', 'Updated.'),
        record('u5', 'user', 'remember: tests Run the parity test before merging')]), encoding='utf-8')
    return {'omp': root/'omp'}


def extractor(request, *, model):
    """Each 'remember: <slug> <text>' request becomes project.demo.decision.<slug>, updating a key already related."""
    seen = json.loads(request['input'][0]['content'])
    related = {r['memory_key'] for r in seen['related_existing']}
    items = []
    for event in seen['events']:
        if event['kind'] != 'user_request' or not event['content'].startswith('remember: '): continue
        slug, text = event['content'][len('remember: '):].split(' ', 1)
        key = 'project.demo.decision.' + slug
        item = dict(memory_key=key, content=text, status='active', confidence=0.9,
                    evidence=[dict(event_id=event['id'], quote=text)])
        if key not in related: item.update(kind='decision', scope='project', scope_key='demo', project='demo')
        items.append(item)
    payload = dict(items=items, reviewed_event_ids=[e['id'] for e in seen['events']], conflicts=[])
    return dict(payload=payload, provider='fixture', model=model, api_calls=1, usage={})


class InProcess:
    """The CLI's two steps over an in-process broker, with the JSON round trip the subprocess would do."""
    def __init__(self, client): self.client = client
    def related(self, project, sessions):
        context = summarize.related(self.client, MAPPING, project, sessions)
        return json.loads(json.dumps(dict(related=context, digest=summarize.digest(context))))
    def write(self, request):
        request = json.loads(json.dumps(request))
        assert summarize.valid_request(request, 'write')
        try: return json.loads(json.dumps(summarize.write(self.client, MAPPING, request)))
        except summarize.Stale: return dict(status='stale')


def summarizer(tmp_path):
    home, state, store, agents = setup_home(tmp_path/'broker')
    grant = state.grants[agents[0].agent_id]; grant.allow_summarize = True; store.save(state)
    return Local(Broker(home), agents[0])


def sqlite_run(tmp_path, roots):
    import memory_sync, sqlite_memory, bounded_digest, ingest_sessions
    db = tmp_path/'staging.db'
    sqlite_memory.init_database(db); memory_sync.initialize(db, 'mac', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
    assert ingest_sessions.ingest(db, roots=roots)['errors'] == 0
    for _ in range(20):
        if bounded_digest.run_once(db, extractor=extractor, model='fixture', max_events=2)['status'] == 'idle': break
    import sqlite3
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        items = {r['memory_key']: {k: r[k] for k in FIELDS} for r in c.execute('SELECT * FROM memory_items')}
        summary = c.execute("SELECT content FROM memory_summaries WHERE scope='project' AND scope_key='demo'").fetchone()[0]
    return items, summary


def broker_run(tmp_path, roots, client):
    import broker_digest
    state = broker_digest.State(tmp_path/'jobs')
    sources, errors = broker_digest.discover(roots)
    assert not errors
    results = []
    for _ in range(20):
        results.append(broker_digest.run_once(state, sources, InProcess(client), extractor=extractor, model='fixture', max_events=2))
        if results[-1]['status'] == 'idle': break
    heads = client.request(dict(op='heads', scope='work/a'))['heads']
    items = {k: dict(memory_key=k, kind=h['kind'], scope=h['item_scope'], scope_key=h['scope_key'], project=h['project'],
                     content=h['content'], status=h['status']) for k, h in heads.items() if not k.startswith('summary/')}
    return items, heads['summary/demo']['content'], results, state


def test_backends_agree(tmp_path):
    roots = transcripts(tmp_path)
    client = summarizer(tmp_path)
    old_items, old_summary = sqlite_run(tmp_path, roots)
    new_items, new_summary, results, state = broker_run(tmp_path, roots, client)
    assert set(old_items) == {'project.demo.decision.deploy', 'project.demo.decision.index', 'project.demo.decision.tests'}
    assert new_items == old_items
    assert old_items['project.demo.decision.deploy']['content'] == 'Deployment is automated now'
    current = lambda text: text.split(summarize.MARKER_START)[1]
    assert current(new_summary) == current(old_summary)
    # Five user events in windows of two: the first ends mid-file, the second spans both files.
    assert [r['status'] for r in results] == ['committed', 'committed', 'committed', 'idle']
    assert all(not f['done'] for f in state.cursors['files'].values())
    rerun = broker_run(tmp_path, roots, client)[2]
    assert [r['status'] for r in rerun] == ['idle']


def test_revision_keeps_one_head(tmp_path):
    roots = transcripts(tmp_path)
    client = summarizer(tmp_path)
    broker_run(tmp_path, roots, client)
    found = client.request(dict(op='recall', query='Deployment'))['results']
    assert [r['content'] for r in found if 'Deployment' in r['content'] and r['kind'] == 'decision'] == ['Deployment is automated now']
