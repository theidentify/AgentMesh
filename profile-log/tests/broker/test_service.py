import json
import pytest
from agentmesh_log.broker.semantic import Semantic
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.broker.grants import resolve
from agentmesh_log.events import EVENT_DOMAIN
from agentmesh_log.encoding import typed
from .support import setup_home, remember, recall, FakeEmbedding


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


def hybrid(b, home, **kw):
    b.semantic = Semantic(home.root/'index', b.state.index_master_key,
        provider=FakeEmbedding(**kw), min_score=0.5)
    b.semantic.fetch_in_fuse = False


def test_hybrid_recall(tmp_path):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home); a = Session(agents[0].certificate())
    remember(b,a,agents[0]); remember(b,a,agents[0],body='ย้ายฐานข้อมูลไปเครื่องใหม่')
    baseline = recall(b,a)
    hybrid(b, home); assert b.backfill_once() == 2
    for mode in ({}, {'mode': 'hybrid'}):
        out = b.request(a, dict(op='recall', query='database', **mode))
        assert len(out['results']) == 2 and out['query_plan']['retrieval']['effective'] == 'hybrid'
    rows = [json.loads(line) for line in (home.root/'audit.jsonl').read_text().splitlines()]
    assert rows[-1]['mode'] == 'hybrid'
    assert recall(b,a,mode='keyword') == baseline
    assert 'retrieval' not in recall(b,a,mode='keyword')['query_plan']


def test_hybrid_unconfigured(tmp_path):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home); a = Session(agents[0].certificate())
    remember(b,a,agents[0])
    out = recall(b,a,mode='hybrid')
    assert out['query_plan']['retrieval'] == dict(requested='hybrid', effective='keyword', degraded='not_configured')
    # Audit records what actually ran, not what was requested.
    rows = [json.loads(line) for line in (home.root/'audit.jsonl').read_text().splitlines()]
    assert rows[-1]['op'] == 'recall' and rows[-1]['mode'] == 'keyword'
    assert 'retrieval' not in recall(b,a)['query_plan']


@pytest.mark.parametrize('mode', ['vector', '', 1, [], {}], ids=['vec','empty','int','list','dict'])
def test_bad_mode(tmp_path, mode):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home)
    assert recall(b,Session(agents[0].certificate()),mode=mode) == {'error': 'invalid_request'}


def test_cache_on_close(tmp_path):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home); a = Session(agents[0].certificate())
    remember(b,a,agents[0]); hybrid(b, home); b.backfill_once()
    semantic = b.semantic
    semantic.store.put('obsolete', semantic.provider.embed(['obsolete'])[0])
    b.close()
    assert not semantic.store.has('obsolete')
    [path] = (home.root/'index').glob('vectors-*.snap')
    assert b'migrate' not in path.read_bytes()
    restored = Semantic(home.root/'index', b.state.index_master_key, provider=FakeEmbedding())
    assert restored.store.has('migrate database') and not restored.store.has('obsolete')


def test_prefetch_unlocked(tmp_path, monkeypatch):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home); a = Session(agents[0].certificate())
    remember(b,a,agents[0]); hybrid(b, home)
    embed = b.semantic.provider.embed
    calls = []
    def checked(texts):
        assert not b.lock._is_owned()
        calls.append(texts)
        return embed(texts)
    monkeypatch.setattr(b.semantic.provider, 'embed', checked)
    assert recall(b,a,mode='hybrid')['query_plan']['retrieval']['effective'] == 'hybrid'
    assert calls == [['migrate database']]
    assert 'retrieval' not in recall(b,a,mode='keyword')['query_plan']
    assert calls == [['migrate database']]


def test_backfill_locks(tmp_path, monkeypatch):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home); a = Session(agents[0].certificate())
    remember(b,a,agents[0]); hybrid(b, home)
    calls = []
    def check(obj, name, locked):
        original = getattr(obj, name)
        def checked(*args, **kw):
            assert b.lock._is_owned() == locked
            calls.append(name)
            return original(*args, **kw)
        monkeypatch.setattr(obj, name, checked)
    check(b.semantic, 'ensure', False)
    for index in b.indexes.values(): check(index, 'rows', True)
    check(b.semantic, 'pending', True)
    check(b.semantic.provider, 'embed', False)
    check(b.semantic.store, 'put', True)
    check(b.semantic, 'save', True)
    assert b.backfill_once() == 1
    assert calls.index('pending') < calls.index('embed') < calls.index('put') < calls.index('save')
    assert b.backfill_once() == 0


def test_backfill_failure(tmp_path):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home); a = Session(agents[0].certificate())
    assert b.backfill_once() == 0
    remember(b,a,agents[0]); hybrid(b, home, fail=True)
    assert b.backfill_once() == 0
    assert not b.semantic.store.has('migrate database')
    assert recall(b,a)['query_plan']['retrieval']['degraded'] == 'provider_unavailable'


@pytest.mark.parametrize('where', ['settings', 'semantic'], ids=['settings','semantic'])
def test_init_failure(tmp_path, monkeypatch, where):
    from agentmesh_log import settings
    home,state,store,agents = setup_home(tmp_path)
    def fail(*args, **kwargs): raise RuntimeError('unavailable')
    if where == 'settings': monkeypatch.setattr(settings, 'load', fail)
    else:
        monkeypatch.setattr(settings, 'load', lambda: {'semantic': {'mode': 'hybrid'}})
        monkeypatch.setattr(Semantic, 'from_settings', fail)
    b = Broker(home)
    assert b.semantic is None
    assert 'agent_id' in b.request(Session(agents[0].certificate()), {'op': 'whoami'})


def test_settings_wiring(tmp_path, monkeypatch):
    from agentmesh_log import settings
    home,state,store,agents = setup_home(tmp_path)
    monkeypatch.setattr(settings, 'load', lambda: {'semantic': {'mode': 'hybrid', 'min_score': 0.7}})
    b = Broker(home)
    assert b.semantic.min_score == 0.7 and b.semantic.provider is None


def test_semantic_thread(tmp_path, monkeypatch):
    from agentmesh_log.broker import service
    home,state,store,agents = setup_home(tmp_path); b = Broker(home); hybrid(b, home)
    calls = []
    class Thread:
        def __init__(self, *, target, daemon):
            assert daemon
            self.target = target
        def start(self): self.target()
    def backfill():
        calls.append('backfill')
        return 1 if len(calls) == 1 else 0
    def wait(seconds):
        assert seconds == 300
        b.stopped.set()
    monkeypatch.setattr(service.threading, 'Thread', Thread)
    monkeypatch.setattr(b, 'backfill_once', backfill)
    monkeypatch.setattr(b._semantic_wake, 'wait', wait)
    b.serve(server=object())
    assert calls == ['backfill', 'backfill']


def test_ingest_wakes(tmp_path):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home)
    assert not b._semantic_wake.is_set()
    remember(b,Session(agents[0].certificate()),agents[0])
    assert b._semantic_wake.is_set()


@pytest.mark.parametrize('error', [RuntimeError, ValueError], ids=['runtime','value'])
def test_failed_prefetch(tmp_path, monkeypatch, error):
    from _thread import RLock
    from threading import get_ident
    from agentmesh_log import settings

    class OwnedRLock(RLock):
        def __init__(self):
            super().__init__()
            self.owner = None; self.depth = 0
        def acquire(self, *args, **kwargs):
            acquired = super().acquire(*args, **kwargs)
            if acquired:
                self.owner = get_ident(); self.depth += 1
            return acquired
        def release(self):
            self.depth -= 1
            if not self.depth: self.owner = None
            super().release()
        def __enter__(self):
            self.acquire()
            return self
        def __exit__(self, *args): self.release()

    home,state,store,agents = setup_home(tmp_path)
    semantic = Semantic(home.root/'index', state.index_master_key, provider=FakeEmbedding())
    monkeypatch.setattr(settings, 'load', lambda: {'semantic': {'mode': 'hybrid'}})
    monkeypatch.setattr(Semantic, 'from_settings', lambda *args: semantic)
    b = Broker(home); b.lock = OwnedRLock()
    a = Session(agents[0].certificate())
    remember(b,a,agents[0])
    calls = []
    def embed(texts):
        calls.append(texts)
        assert b.lock.owner != get_ident()
        raise error('offline')
    monkeypatch.setattr(semantic.provider, 'embed', embed)
    out = recall(b,a,mode='hybrid')
    assert out['query_plan']['retrieval'] == dict(
        requested='hybrid', effective='keyword', degraded='provider_unavailable')
    assert calls == [['migrate database']]
    assert semantic.fetch_in_fuse is False


def test_backfill_reattach(tmp_path, monkeypatch):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home)
    remember(b,Session(agents[0].certificate()),agents[0]); hybrid(b, home)
    semantic = b.semantic; old_store = semantic.store
    embed = semantic.embed_texts
    def reattach(texts):
        pairs = embed(texts)
        assert pairs
        semantic._attach(FakeEmbedding(revision='e'*64))
        return pairs
    monkeypatch.setattr(semantic, 'embed_texts', reattach)
    assert b.backfill_once() == 0
    assert semantic.store is not old_store
    assert semantic.store.count() == 0
