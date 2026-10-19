import json
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.broker.grants import resolve
from agentmesh_log.events import EVENT_DOMAIN
from agentmesh_log.encoding import typed
from .support import setup_home, remember, recall


def test_dispatch(tmp_path):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home); a,c = [Session(x.certificate()) for x in agents]
    assert 'id' in remember(b,a,agents[0])
    assert len(recall(b,a)['results']) == 1 and recall(b,c)['results'] == []
    assert recall(b,a,scopes=['work/b']) == {'error':'denied'}
    assert recall(b,a,scopes=['work/missing']) == {'error':'denied'}
    state.revoke(agents[0].agent_id); store.save(state)
    assert b.request(a,{'op':'whoami'}) == {'error':'denied'}


def test_p3_7_audit(tmp_path, monkeypatch):
    home,state,store,agents = setup_home(tmp_path); b=Broker(home); s=Session(agents[0].certificate())
    before=0
    for req in ({'op':'recall','query':'unique query marker'}, {'op':'recall','query':'unique query marker'},
                {'op':'recall','query':'x','scopes':['work/b']}, {'op':'bad'}):
        b.request(s,req)
        rows=[json.loads(line) for line in (home.root/'audit.jsonl').read_text(encoding='utf-8').splitlines()]
        assert len(rows) == before+1; before+=1
    assert rows[0]['query_hmac'] == rows[1]['query_hmac']
    assert 'unique query marker' not in (home.root/'audit.jsonl').read_text(encoding='utf-8')
    assert [r['outcome'] for r in rows] == ['ok','ok','denied','invalid']
    def fail(*args,**kwargs): raise OSError('injected')
    monkeypatch.setattr(b.audit,'append',fail)
    assert recall(b,s) == {'error':'unavailable'}


def test_p3_8_policies(tmp_path):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); s=Session(agents[0].certificate())
    response=b.request(s,dict(op='remember',body='migrate database',label='secret'))
    assert response['stage']=='sign'
    assert 'id' in b.request(s,dict(op='sign',signature=agents[0]._private.sign(EVENT_DOMAIN+typed(response['unsigned'])).hex()))
    assert recall(b,s)['results']==[]
    assert recall(b,s,profiles=['work','personal'])=={'error':'denied'}
    grant=state.grants[agents[0].agent_id]; grant.allow_secret=True; grant.allow_cross_profile=True
    grant.read.append(state.trees[1].root.scope_id); store.save(state)
    assert len(recall(b,s,profiles=['work','personal'])['results'])==1
    rows=[json.loads(line) for line in (home.root/'audit.jsonl').read_text(encoding='utf-8').splitlines()]
    assert rows[-1]['cross_profile'] is True
    grant.default_write=resolve(state,'work/common'); grant.write.append(grant.default_write); store.save(state)
    assert remember(b,s,agents[0])=={'error':'denied'}


def test_p3_11_revisions(tmp_path):
    home,state,store,agents=setup_home(tmp_path)
    ga=state.grants[agents[0].agent_id]; gb=state.grants[agents[1].agent_id]
    gb.read=list(ga.read); gb.write=list(ga.write); gb.default_write=ga.default_write; store.save(state)
    b=Broker(home); a,c=[Session(x.certificate()) for x in agents]
    first=remember(b,a,agents[0])['id']
    def revise(s,agent,previous,**kwargs):
        response=b.request(s,dict(op='revise',body='migrate database revision',revises=previous,**kwargs))
        if response.get('stage')!='sign': return response
        return b.request(s,dict(op='sign',signature=agent._private.sign(EVENT_DOMAIN+typed(response['unsigned'])).hex()))
    assert revise(a,agents[0],first,scope='work/b')=={'error':'denied'}
    left=revise(a,agents[0],first)['id']; right=revise(c,agents[1],first)['id']
    rows=recall(b,a)['results']; assert len(rows)==1 and rows[0]['conflict']
    assert set([rows[0]['id'],*rows[0]['alternatives']])=={left,right}
    newest=revise(a,agents[0],left)['id']
    rows=recall(b,a)['results']; assert rows[0]['id']==newest and rows[0]['alternatives']==[right]
    ga.allow_secret=True; store.save(state)
    secret=revise(a,agents[0],newest,label='secret')['id']
    assert revise(a,agents[0],secret,label='general')=={'error':'label_lowering_requires_confirmation'}


def test_p3_13_sessions(tmp_path):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home)
    a,c=[Session(x.certificate()) for x in agents]
    for req in ({'op':'bad'}, {'op':'whoami','extra':1}, {'op':'sign','signature':'0'*128}):
        assert b.request(a,req)=={'error':'invalid_request'}
        assert 'agent_id' in b.request(c,{'op':'whoami'})
    pending=b.request(a,{'op':'remember','body':'migrate database'})
    sig=agents[0]._private.sign(EVENT_DOMAIN+typed(pending['unsigned'])).hex()
    assert b.request(c,{'op':'sign','signature':sig})=={'error':'invalid_request'}
    assert b.request(a,{'op':'sign','signature':'0'*128})=={'error':'invalid_request'}
    assert b.request(a,{'op':'sign','signature':sig})=={'error':'invalid_request'}


def test_discard_pending(tmp_path):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); a=Session(agents[0].certificate())
    for req in ({'op':'unknown'}, [], {'op':'whoami','extra':1}):
        pending=b.request(a,{'op':'remember','body':'migrate database'})
        signature=agents[0]._private.sign(EVENT_DOMAIN+typed(pending['unsigned'])).hex()
        assert b.request(a,req)=={'error':'invalid_request'}
        assert b.request(a,{'op':'sign','signature':signature})=={'error':'invalid_request'}


def test_scopes_filter(tmp_path):
    home,state,store,agents=setup_home(tmp_path)
    grant=state.grants[agents[0].agent_id]; grant.read=[state.trees[0].root.scope_id]
    grant.write=list(state.trees[0].scopes); store.save(state)
    b=Broker(home); s=Session(agents[0].certificate())
    for scope in ('work','work/a','work/b','work/common'):
        pending=b.request(s,dict(op='remember',body={'kind':'fact','content':'migrate database','project':'metadata'},scope=scope))
        assert 'id' in b.request(s,dict(op='sign',signature=agents[0]._private.sign(EVENT_DOMAIN+typed(pending['unsigned'])).hex()))
    assert len(recall(b,s,scopes=['work'])['results'])==4
    assert len(recall(b,s,scopes=['work/a'])['results'])==1
    assert recall(b,s,scopes=[])['results']==[]
    assert recall(b,s,project='metadata')['results']
    assert recall(b,s,project='work/a')['results']==[]
    assert recall(b,s,scopes=['work/a','personal/notes'])=={'error':'denied'}
    assert recall(b,s,profiles=['work'],scopes=['personal'])=={'error':'denied'}


def test_indexes_on_write(tmp_path):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); s=Session(agents[0].certificate())
    remember(b,s,agents[0])
    assert set(b.indexes)==set(state.grants)
    assert len(b.indexes[agents[0].agent_id].rows())==1
    assert b.indexes[agents[1].agent_id].rows()==[]


def test_revision_scope_guard(tmp_path):
    home,state,store,agents=setup_home(tmp_path); grant=state.grants[agents[0].agent_id]
    grant.read=[state.trees[0].root.scope_id]; grant.write=[resolve(state,'work/a'),resolve(state,'work/b')]
    store.save(state); b=Broker(home); s=Session(agents[0].certificate())
    first=remember(b,s,agents[0])['id']
    assert b.request(s,dict(op='revise',body='migrate database',scope='work/b',revises=first))=={'error':'denied'}
    grant.read=[resolve(state,'work/b')]; store.save(state)
    assert b.request(s,dict(op='revise',body='migrate database',scope='work/a',revises=first))=={'error':'denied'}


def test_pending_cross_session(tmp_path):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home)
    a,c=Session(agents[0].certificate()),Session(agents[0].certificate())
    response=b.request(a,dict(op='remember',body='migrate database first'))
    b.request(c,dict(op='remember',body='migrate database second'))
    signature=agents[0]._private.sign(EVENT_DOMAIN+typed(response['unsigned'])).hex()
    assert b.request(c,dict(op='sign',signature=signature))=={'error':'invalid_request'}
    assert 'id' in b.request(a,dict(op='sign',signature=signature))


def test_live_scope_wrap(tmp_path):
    from agentmesh_log.events import wrap_path
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); s=Session(agents[0].certificate())
    tree=state.trees[0]; added=tree.add(tree.root.scope_id,'project','new')
    state.grants[agents[0].agent_id].write.append(added.scope_id); store.save(state)
    assert 'agent_id' in b.request(s,{'op':'whoami'})
    assert wrap_path(tree.wraps[added.scope_id]) in b.replica.keys()
