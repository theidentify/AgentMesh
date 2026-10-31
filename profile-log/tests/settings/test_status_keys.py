"""The broker's content-free `keys` section (M3b): names, ids and times only, never key bytes."""
from dataclasses import replace
import json
from types import SimpleNamespace
import pytest
from test_apply import env
from agentmesh_log import status
from agentmesh_log.admin import create_agent
from agentmesh_log.heartbeats import iso

UNTIL=1_900_000_000_000


@pytest.fixture
def agent(env):
    root,home,store=env; state=store.load()
    _,grant=create_agent(home,state,'legacy',['work/common'],['work/common'],'work/common')
    state.control['agent_names']={'legacy':grant.agent_id}; store.save(state)
    return state,grant


def doc(**sections): return dict(format=status.FORMAT,updated_at=status.timestamp(),**sections)


def test_shape(agent):
    state,grant=agent; state.grants[grant.agent_id]=replace(grant,secret_until=UNTIL)
    value=status.keys(state)
    assert set(value)=={'generated_at','agents','recoveries'}
    assert value['agents']==[dict(name='legacy',key_ref=status.key_ref(grant.agent_id),secret_until=iso(UNTIL))]
    assert value['recoveries']==dict(count=0,latest_issued_at=None)
    assert status.valid(doc(keys=value))


def test_no_secret_window_is_null(agent):
    state,grant=agent
    assert status.keys(state)['agents'][0]['secret_until'] is None


def test_unnamed_grant_kept(agent):
    state,grant=agent; state.control['agent_names']={}
    value=status.keys(state)
    assert value['agents']==[dict(name=None,key_ref=status.key_ref(grant.agent_id),secret_until=None)] and status.valid(doc(keys=value))


def test_latest_recovery(agent,monkeypatch):
    state,_=agent; registry=state.registry()
    registry.recoveries={'a':dict(issued_at=1000),'b':dict(issued_at=5000)}
    monkeypatch.setattr(type(state),'registry',lambda self: registry)
    assert status.keys(state)['recoveries']==dict(count=2,latest_issued_at=iso(5000))


def test_content_free(agent):
    state,grant=agent
    text=json.dumps(status.keys(state))
    secrets=[grant.agent_id,grant.agent_public_key,state.device._private.public_key().public_bytes_raw().hex(),
             state.device._private.private_bytes_raw().hex()]
    assert not any(s in text for s in secrets)


def test_ref_is_stable_and_separated():
    ref=status.key_ref('ab'*16)
    assert ref==status.key_ref('ab'*16)!=status.key_ref('cd'*16) and len(ref)==32 and 'ab'*16 not in ref
    import hashlib
    assert ref!=hashlib.sha256(('ab'*16).encode()).hexdigest()[:32]


def good():
    return dict(generated_at=status.timestamp(),agents=[dict(name='a',key_ref='0'*32,secret_until=None)],
                recoveries=dict(count=1,latest_issued_at=iso(5000)))


def bad(change):
    value=good(); change(value); return value


@pytest.mark.parametrize('value',[
    bad(lambda v: v.update(extra=1)),
    bad(lambda v: v['agents'][0].update(public_key='x')),
    bad(lambda v: v['agents'][0].update(name=7)),
    bad(lambda v: v['agents'][0].update(key_ref='0'*31)),
    bad(lambda v: v['agents'][0].update(key_ref='G'*32)),
    bad(lambda v: v['agents'][0].update(secret_until='tomorrow')),
    bad(lambda v: v['recoveries'].update(count=-1)),
    bad(lambda v: v['recoveries'].update(latest_issued_at=5000)),
    bad(lambda v: v.pop('recoveries')),
],ids=['extra','agent_extra','name_int','short_id','non_hex_id','until_text','negative','raw_ms','missing'])
def test_refused(value):
    assert status.valid(doc(keys=good())) and not status.valid(doc(keys=value))


def test_applied_writes_keys(agent):
    state,_=agent; root=status.settings.root()
    status.applied(root,'ok',[],state)
    value=json.loads((root/'profile-log-status.json').read_text())
    assert value['keys']['agents'][0]['name']=='legacy' and status.valid(value)
