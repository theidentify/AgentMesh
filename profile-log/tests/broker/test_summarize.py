"""SQLite retirement R2: heads listing, hidden deletions, imported heads and the summarizer's write rules."""
import pytest
from agentmesh_log import summarize
from agentmesh_log.broker import service
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.migrate.mapping import Mapping
from .test_pages import Local
from .support import setup_home
from agentmesh_log.encoding import typed
from agentmesh_log.events import EVENT_DOMAIN

MAPPING = Mapping(dict(format='agentmesh-migration-mapping-v1', projects={'demo': 'work/a'}, prefixes=[],
                       null_project='work/a', **{'global': 'work/a'}))


def broker(tmp_path, summarize_flag=True, imports=False):
    home, state, store, agents = setup_home(tmp_path)
    grant = state.grants[agents[0].agent_id]; grant.allow_summarize = summarize_flag; grant.allow_import = imports
    store.save(state)
    b = Broker(home, store=store)
    return b, state, Local(b, agents[0]), agents[0]


def item(key, content, scope='project', scope_key='demo', status='active', project='demo'):
    return dict(memory_key=key, kind='decision', scope=scope, scope_key=scope_key, project=project, content=content,
                status=status, confidence=0.9)


def keyed(key, content, **kw):
    return summarize.body(item(key, content, **kw), dict(batch_id='b'))


def imported(b, state, agent, key, content='imported memory'):
    """One staging row through the importer, as `migrate rows` sends it."""
    source = dict(table='memory_items', id=1, scope='project', scope_key='demo', memory_key=key, version=None,
                  created_at=None, updated_at=None, device=state.device.device_id)
    req = dict(op='import', source=dict(table='memory_items', id=1, device=state.device.device_id), row_hash='a'*64,
               created_at=1577836800000, scope='work/a', body=dict(kind='fact', content=content, project='demo', task_ref=None,
               confidence=1.0, metadata=dict(source=source, row_hash='a'*64, evidence_event_ids=[], retired=False)))
    session = Session(agent.certificate())
    response = b.request(session, req)
    while response.get('stage') == 'sign':
        response = b.request(session, dict(op='sign', signature=agent._private.sign(EVENT_DOMAIN+typed(response['unsigned'])).hex()))
    return response


def test_heads_all_hides_deleted(tmp_path):
    b, state, client, _ = broker(tmp_path)
    first = client.write(keyed('k.one', 'first'), scope='work/a')
    client.write(keyed('k.two', 'second', scope='session', scope_key='s1'), scope='work/a')
    client.write(keyed('k.gone', 'to delete'), scope='work/a')
    heads = client.request(dict(op='heads', scope='work/a'))['heads']
    assert sorted(heads) == ['k.gone', 'k.one', 'k.two']
    assert (heads['k.two']['item_scope'], heads['k.two']['scope_key'], heads['k.two']['status']) == ('session', 's1', 'active')
    gone = heads['k.gone']['id']
    client.write(keyed('k.gone', 'to delete', status='deleted'), scope='work/a', revises=gone)
    assert sorted(client.request(dict(op='heads', scope='work/a'))['heads']) == ['k.one', 'k.two']
    assert not [r for r in client.request(dict(op='recall', query='delete'))['results'] if r['content'] == 'to delete']
    assert heads['k.one']['id'] == first['id']
    # Related context: the window's own sessions only.
    assert [r['memory_key'] for r in summarize.related(client, MAPPING, 'demo', [])] == ['k.one']
    assert [r['memory_key'] for r in summarize.related(client, MAPPING, 'demo', ['s1'])] == ['k.one', 'k.two']


def test_heads_all_bounded(tmp_path, monkeypatch):
    b, state, client, _ = broker(tmp_path)
    for key in ('a', 'b'): client.write(keyed(key, 'memory ' + key), scope='work/a')
    monkeypatch.setattr(service, 'HEADS_ALL', 1)
    assert client.request(dict(op='heads', scope='work/a')) == {'error': 'truncated'}
    assert set(client.request(dict(op='heads', scope='work/a', memory_keys=['a']))['heads']) == {'a'}


def test_revise_imported_head(tmp_path):
    b, state, client, agent = broker(tmp_path, imports=True)
    head = imported(b, state, agent, 'k.old')
    assert client.request(dict(op='heads', scope='work/a'))['heads']['k.old']['id'] == head['id']
    assert client.write(keyed('k.other', 'new text'), scope='work/a', revises=head['id']) == {'error': 'denied'}
    revised = client.write(keyed('k.old', 'new text'), scope='work/a', revises=head['id'])
    heads = client.request(dict(op='heads', scope='work/a'))['heads']
    assert heads['k.old']['id'] == revised['id'] and heads['k.old']['content'] == 'new text'


def test_imported_needs_flag(tmp_path):
    b, state, client, agent = broker(tmp_path, summarize_flag=False, imports=True)
    head = imported(b, state, agent, 'k.old')
    assert client.write(keyed('k.old', 'new text'), scope='work/a', revises=head['id']) == {'error': 'denied'}


def request(client, items, sessions=(), project='demo'):
    context = summarize.related(client, MAPPING, project, list(sessions))
    return dict(project=project, sessions=list(sessions), related_digest=summarize.digest(context), items=items,
                provenance=dict(batch_id='b1'))


def test_write_rules(tmp_path):
    b, state, client, _ = broker(tmp_path)
    created = summarize.write(client, MAPPING, request(client, [item('k.one', 'first'), item('k.two', 'second')]))
    assert [w['outcome'] for w in created['written']] == ['created', 'created']
    assert created['summaries'][0]['memory_key'] == 'summary/demo'
    summary = client.request(dict(op='heads', scope='work/a', memory_keys=['summary/demo']))['heads']['summary/demo']
    assert '- [decision] k.one: first\n- [decision] k.two: second' in summary['content']
    again = summarize.write(client, MAPPING, request(client, [item('k.one', 'first'), item('k.two', 'second', status='deleted')]))
    assert again['written'] == [dict(memory_key='k.one', outcome='unchanged', id=created['written'][0]['id'])]
    assert again['rejected'] == [dict(memory_key='k.two', reason='deletion requires confirmation')]
    client.write(keyed('k.hidden', 'other session', scope='session', scope_key='s9'), scope='work/a')
    outside = summarize.write(client, MAPPING, request(client, [item('k.hidden', 'rewrite', scope='session', scope_key='s9')]))
    assert outside['rejected'] == [dict(memory_key='k.hidden', reason='stable key exists outside submitted context')]
    stale = request(client, [item('k.three', 'third')])
    client.write(keyed('k.race', 'written meanwhile'), scope='work/a')
    with pytest.raises(summarize.Stale): summarize.write(client, MAPPING, stale)
    with pytest.raises(ValueError, match='unmapped project'): summarize.related(client, MAPPING, 'elsewhere', [])


def test_valid_request():
    good = dict(project='demo', sessions=['s'], related_digest='d', items=[item('k', 'x')], provenance={})
    assert summarize.valid_request(good, 'write') and summarize.valid_request(dict(project=None, sessions=[]), 'related')
    for bad in ({**good, 'items': [item('summary/demo', 'x')]}, {**good, 'items': [item('k', 'x'), item('k', 'y')]},
                {**good, 'items': [item('k', 'x', status='gone')]}, {**good, 'items': [{**item('k', 'x'), 'extra': 1}]},
                {**good, 'project': ''}, {**good, 'sessions': 's'}, {k: v for k, v in good.items() if k != 'provenance'}):
        assert not summarize.valid_request(bad, 'write')
