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


def test_heads_all_paged(tmp_path, monkeypatch):
    b, state, client, _ = broker(tmp_path)
    for key in ('a', 'b'): client.write(keyed(key, 'memory ' + key), scope='work/a')
    monkeypatch.setattr(service, 'HEADS_ALL', 1)
    first = client.request(dict(op='heads', scope='work/a'))
    assert (set(first['heads']), first['next']) == ({'a'}, 'a')
    last = client.request(dict(op='heads', scope='work/a', after='a'))
    assert (set(last['heads']), last['next']) == ({'b'}, None)
    assert client.request(dict(op='heads', scope='work/a', after='b')) == {'heads': {}, 'next': None}
    assert client.request(dict(op='heads', scope='work/a', memory_keys=['a'], after='a')) == {'error': 'invalid_request'}
    assert 'next' not in client.request(dict(op='heads', scope='work/a', memory_keys=['a']))
    # The summarizer reads every page.
    assert sorted(summarize.Heads(client).scope('work/a')) == ['a', 'b']
    monkeypatch.setattr(summarize, 'PAGES', 1)
    with pytest.raises(ValueError, match='too many keyed heads'): summarize.Heads(client).scope('work/a')


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
    assert again['rejected'] == [dict(memory_key='k.two', scope='work/a', reason='deletion requires confirmation')]
    # The owner confirms it: the key is hidden, the event history stays.
    confirmed = summarize.delete(client, 'work/a', 'k.two')
    assert confirmed['ok'] and 'k.two' not in client.request(dict(op='heads', scope='work/a'))['heads']
    with pytest.raises(ValueError, match='not_found'): summarize.delete(client, 'work/a', 'k.two')
    client.write(keyed('k.hidden', 'other session', scope='session', scope_key='s9'), scope='work/a')
    outside = summarize.write(client, MAPPING, request(client, [item('k.hidden', 'rewrite', scope='session', scope_key='s9')]))
    assert outside['rejected'] == [dict(memory_key='k.hidden', scope='work/a', reason='stable key exists outside submitted context')]
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


def test_stats_counts_only(tmp_path):
    """R3: Insight's view. Counts per scope and project, never content; secret rows only for agents that read them."""
    b, state, client, _ = broker(tmp_path)
    client.write(keyed('k.one', 'first text'), scope='work/a')
    client.write(dict(kind='fact', content='plain note', project='demo'), scope='work/a')
    client.write(dict(kind='fact', content='other project', project='mesh'), scope='work/a')
    stats = client.request(dict(op='stats'))
    assert stats['total'] == 3 and stats['more_projects'] == 0
    row, = stats['scopes']
    assert (row['scope'], row['count'], row['keyed'], row['kinds'], row['labels']) == ('work/a', 3, 1, {'decision': 1, 'fact': 2}, {'general': 3})
    assert stats['projects'] == [dict(project='demo', count=2), dict(project='mesh', count=1)]
    assert 'text' not in str(stats) and 'note' not in str(stats)
    assert client.request(dict(op='stats', scope='work/a')) == {'error': 'invalid_request'}


def test_write_through_broker_client(tmp_path):
    """The real client signs what the summarizer writes (the in-process Local skips the client's binding checks)."""
    from .test_isolation import connect
    b, state, _, agent = broker(tmp_path)
    client, _, _ = connect(b, agent)
    request = dict(project='demo', sessions=[], items=[item('k.one', 'first')], provenance=dict(batch_id='b'),
                   related_digest=summarize.digest(summarize.related(client, MAPPING, 'demo', [])))
    result = summarize.write(client, MAPPING, request)
    assert [w['outcome'] for w in result['written']] == ['created'] and result['summaries'][0]['outcome'] == 'created'
    request['related_digest'] = summarize.digest(summarize.related(client, MAPPING, 'demo', []))
    request['items'] = [item('k.one', 'second')]
    assert [w['outcome'] for w in summarize.write(client, MAPPING, request)['written']] == ['revised']
    assert summarize.delete(client, 'work/a', 'k.one')['ok'] is True
