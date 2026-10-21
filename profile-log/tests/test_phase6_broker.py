from dataclasses import replace
import pytest
from broker.support import setup_home, recall
from agentmesh_log.events import EVENT_DOMAIN
from agentmesh_log.encoding import typed

def remember(b,s,a,body="migrate database",**kwargs):
    r=b.request(s,{"op":"remember","body":body,**kwargs})
    if r.get("stage")=="sign": return b.request(s,{"op":"sign","signature":a._private.sign(EVENT_DOMAIN+typed(r["unsigned"])).hex()})
    return r
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.model import Label
from agentmesh_log.encoding import parse
from agentmesh_log.ulid import generate


def test_p6_4_noninterference(tmp_path):
    home,state,store,agents=setup_home(tmp_path)
    app=agents[0]; state.grants[app.agent_id]=replace(state.grants[app.agent_id],connector_app_id='ab'*16,max_label='internal')
    store.save(state); b=Broker(home,store=store); s=Session(app.certificate())
    remember(b,s,app,'migrate database public')
    before=recall(b,s)
    remember(b,s,app,'migrate database hidden',label='secret')
    assert recall(b,s)==before  # Same store as before secret insertion, including counts/ranks/errors.
    # Even a stale secret-enabled grant expires independently in the broker.
    state=store.load(); state.grants[app.agent_id]=replace(state.grants[app.agent_id],allow_secret=True,secret_until=1)
    store.save(state)
    assert recall(b,s)==before
    # A general-only grant excludes internal data before candidate counting too.
    state=store.load(); state.grants[app.agent_id]=replace(state.grants[app.agent_id],max_label='general')
    store.save(state); remember(b,s,app,'migrate database internal',label='internal')
    assert recall(b,s)==before
    b.close()


def test_p6_3_broker_boundary(tmp_path):
    home,state,store,agents=setup_home(tmp_path)
    state.grants[agents[0].agent_id]=replace(state.grants[agents[0].agent_id],connector_app_id='ab'*16,max_label='internal')
    store.save(state); b=Broker(home,store=store); s=Session(agents[0].certificate())
    assert recall(b,s,scopes=['work/b'])=={'error':'denied'}
    assert recall(b,s,profiles=['personal'])=={'error':'denied'}
    assert b.request(s,{'op':'connector_window','app_id':'ab'*16})=={'error':'denied'}
    lines=[parse(x) for x in (home.root/'audit.jsonl').read_bytes().splitlines()]
    assert all(r['agent_id']==agents[0].agent_id for r in lines)
    b.close()


def test_p6_6_broker_outbox_dedup(tmp_path):
    home,state,store,agents=setup_home(tmp_path)
    state.grants[agents[0].agent_id]=replace(state.grants[agents[0].agent_id],connector_app_id='ab'*16,max_label='internal')
    store.save(state); b=Broker(home,store=store); s=Session(agents[0].certificate()); oid=generate()
    first=remember(b,s,agents[0],outbox_id=oid)
    second=remember(b,s,agents[0],outbox_id=oid)
    assert first['id']==second['id']
    assert len(recall(b,s)['results'])==1
    assert remember(b,Session(agents[1].certificate()),agents[1],outbox_id=generate())=={'error':'denied'}
    b.close()
