from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.broker.grants import resolve
from agentmesh_log.events import EVENT_DOMAIN
from agentmesh_log.encoding import typed
from .support import setup_home, recall


def test_m11_cross_profile(tmp_path):
    home,state,store,agents=setup_home(tmp_path); g=state.grants[agents[0].agent_id]
    g.read.append(resolve(state,'personal/notes')); store.save(state)
    b=Broker(home); s=Session(agents[0].certificate())
    assert recall(b,s,profiles=['work','personal'])=={'error':'denied'}
    g.allow_cross_profile=True; store.save(state)
    assert 'results' in recall(b,s,profiles=['work','personal'])


def test_m14_exact_write(tmp_path):
    home,state,store,agents=setup_home(tmp_path); g=state.grants[agents[0].agent_id]
    b=Broker(home); s=Session(agents[0].certificate())
    for granted,denied in [('work','work/a'),('work/a','work')]:
        g.write=[resolve(state,granted)]; g.default_write=g.write[0]; store.save(state)
        assert b.request(s,dict(op='remember',body='fixture',scope=denied))=={'error':'denied'}
        assert b.request(s,dict(op='remember',body='fixture',scope=granted))['stage']=='sign'


def test_m15_pending_grant(tmp_path):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); s=Session(agents[0].certificate())
    g=state.grants[agents[0].agent_id]
    for change in ('digest','scope','revoke'):
        g.write=[resolve(state,'work/a')]; state.grants[g.agent_id]=g; store.save(state)
        unsigned=b.request(s,dict(op='remember',body='fixture'))['unsigned']
        signature=agents[0]._private.sign(EVENT_DOMAIN+typed(unsigned)).hex()
        if change=='digest': g.allow_secret=not g.allow_secret
        elif change=='scope': g.write=[]
        else: state.revoke(g.agent_id)
        store.save(state)
        assert b.request(s,dict(op='sign',signature=signature))=={'error':'denied'}
        assert list(b.replica.storage.list('events/'))==[]
