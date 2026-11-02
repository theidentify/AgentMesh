import pytest
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.broker.grants import Grant
from .test_pages import Local
from .support import setup_home


def broker(tmp_path, summarize=True):
    home, state, store, agents = setup_home(tmp_path)
    grant = state.grants[agents[0].agent_id]; grant.allow_summarize = summarize; grant.allow_secret = True; store.save(state)
    b = Broker(home); return b, Local(b, agents[0]), Local(b, agents[1])


def item(key, content, **kw):
    return dict(kind='decision', content=content, metadata=dict(memory_key=key), **kw)


def test_heads_current(tmp_path):
    b, writer, other = broker(tmp_path)
    first = writer.write(item('k1', 'old wording'), scope='work/a')
    second = writer.write(item('k1', 'new wording'), scope='work/a', revises=first['id'])
    writer.write(item('k2', 'second key', project='mesh'), scope='work/a')
    hidden = writer.request(dict(op='remember', body=item('k3', 'token sk-x'), scope='work/a', tags=[], task=None, label='secret'))
    from agentmesh_log.events import EVENT_DOMAIN
    from agentmesh_log.encoding import typed
    signed = writer.request(dict(op='sign', signature=writer.agent._private.sign(EVENT_DOMAIN+typed(hidden['unsigned'])).hex()))
    assert signed['label'] == 'secret'
    answer = writer.request(dict(op='heads', scope='work/a', memory_keys=['k1', 'k2', 'k3', 'missing']))
    assert set(answer['heads']) == {'k1', 'k2'}
    assert answer['heads']['k1']['id'] == second['id'] and answer['heads']['k1']['content'] == 'new wording'
    assert answer['heads']['k2']['project'] == 'mesh'
    recall = writer.request(dict(op='recall', query='wording'))
    assert recall['results'] and all(not k.startswith('_') for r in recall['results'] for k in r)


def test_heads_authorization(tmp_path):
    b, writer, other = broker(tmp_path)
    writer.write(item('k1', 'x'), scope='work/a')
    assert writer.request(dict(op='heads', scope='work/b', memory_keys=['k1'])) == {'error': 'denied'}
    assert other.request(dict(op='heads', scope='work/b', memory_keys=['k1'])) == {'error': 'denied'}
    for bad in (dict(scope='work/a', memory_keys=[]), dict(scope='work/a', memory_keys=['a', 'a']),
                dict(scope='work/a', memory_keys=['x'*513]), dict(scope='work/a', memory_keys='k1'),
                dict(scope=None, memory_keys=['k1']), dict(scope='work/a', memory_keys=['k1'], extra=1)):
        assert writer.request(dict(op='heads', **bad)) == {'error': 'invalid_request'}
    b2, plain, _ = broker(tmp_path/'two', summarize=False)
    assert plain.request(dict(op='heads', scope='work/a', memory_keys=['k1'])) == {'error': 'denied'}


def test_grant_flag_and_status_schema():
    with pytest.raises(ValueError): Grant('a', 'b', [], [], None, None, allow_summarize='yes')
    from agentmesh_log.status import valid
    base = dict(name='n', read=[], write=[], default_write=None, allow_secret=False, allow_cross_profile=False, allow_import=False)
    for row in (base, {**base, 'allow_pages': True}, {**base, 'allow_pages': False, 'allow_summarize': True}):
        assert valid({'format': 'agentmesh-profile-log-status-v1', 'updated_at': '2026-11-02T00:00:00Z',
                      'overview': dict(generated_at='2026-11-02T00:00:00Z', profiles=[], agents=[row], devices=[])})
    assert not valid({'format': 'agentmesh-profile-log-status-v1', 'updated_at': '2026-11-02T00:00:00Z',
                      'overview': dict(generated_at='x', profiles=[], agents=[{**base, 'allow_summarize': 'y'}], devices=[])})
