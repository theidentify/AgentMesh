from agentmesh_log.broker import service, index as index_module
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.broker.keystore import BrokerHome, FileKeyStore
from agentmesh_log.broker.query import analyze_query
from agentmesh_log import events
from agentmesh_log.storage.filesystem import FilesystemStorage
from .support import setup_home, remember, recall
from .test_index import row


def counters(monkeypatch, broker):
    counts = dict(open=0, decrypt=0, get=0)
    for owner, attr, name in ((service,'open_event','open'), (events,'decrypt','decrypt'),
                              (broker.replica.storage,'get','get')):
        original = getattr(owner, attr)
        def counted(*args, _original=original, _name=name, **kwargs):
            counts[_name] += 1
            return _original(*args, **kwargs)
        monkeypatch.setattr(owner, attr, counted)
    return counts


def test_one_write(tmp_path, monkeypatch):
    home,state,store,agents=setup_home(tmp_path)
    state.grants[agents[1].agent_id].read=state.grants[agents[0].agent_id].read[:]; store.save(state)
    b=Broker(home); s=Session(agents[0].certificate())
    for _ in range(3): assert 'id' in remember(b,s,agents[0])
    counts=counters(monkeypatch,b)
    assert 'id' in remember(b,s,agents[0])
    assert counts['open']==1 and counts['decrypt']==1
    assert counts['get']==0
    assert all(len(i.rows())==4 for i in b.indexes.values())


def test_recall_no_io(tmp_path, monkeypatch):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); s=Session(agents[0].certificate())
    remember(b,s,agents[0]); counts=counters(monkeypatch,b)
    assert recall(b,s)['results']
    assert counts==dict(open=0,decrypt=0,get=0)


def test_sync_once_each(tmp_path, monkeypatch):
    home,state,store,agents=setup_home(tmp_path); source=Broker(home); s=Session(agents[0].certificate())
    for _ in range(4): remember(source,s,agents[0])
    remote=FilesystemStorage(tmp_path/'remote'); source.sync_once(remote)
    second=BrokerHome(tmp_path/'second'); FileKeyStore(second).save(state); b=Broker(second)
    counts=counters(monkeypatch,b)
    assert b.sync_once(remote)['downloaded']==4
    assert counts['open']==4 and counts['decrypt']==4
    counts.update(open=0,decrypt=0,get=0)
    assert recall(b,s)['results'] and counts==dict(open=0,decrypt=0,get=0)


def test_sql_candidates(monkeypatch):
    idx=index_module.AgentIndex('a',b'a'*32)
    rows=[{**row(str(i)), 'content':'unrelated prose'} for i in range(100)]
    rows += [row('hit'),{**row('scope'), 'scope':'work/b'},
             {**row('profile'),'profile':'personal'}, {**row('project'),'project':'other'}]
    idx.replace(rows); parsed=[]; original=index_module._decode_row
    def track(data):
        value=original(data); parsed.append(value['id']); return value
    monkeypatch.setattr(index_module,'_decode_row',track)
    result=idx.recall(analyze_query('migrate database'),12,{'work/a'},{'work'})
    assert {r['id'] for r in result['results']}=={'hit','project'}
    assert set(parsed)=={'hit','project'}


def test_restart_snapshot(tmp_path, monkeypatch):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); s=Session(agents[0].certificate())
    first=remember(b,s,agents[0])['id']; b.close()
    calls=[]; original=service.open_event
    def track(*a,**kw): calls.append(1); return original(*a,**kw)
    monkeypatch.setattr(service,'open_event',track)
    b=Broker(home)
    assert recall(b,s)['results'][0]['id']==first and calls==[]
    # Graph data survives snapshot load; only the incoming revision is decrypted.
    from agentmesh_log.encoding import typed
    from agentmesh_log.events import EVENT_DOMAIN
    unsigned=b.request(s,dict(op='revise',body='migrate database revised',revises=first))['unsigned']
    signature=agents[0]._private.sign(EVENT_DOMAIN+typed(unsigned)).hex()
    assert calls==[]  # Prior revision label is already in the restored authorized index.
    calls.clear()
    second=b.request(s,dict(op='sign',signature=signature))['id']
    assert len(calls)==1
    assert [r['id'] for r in recall(b,s)['results']]==[second]


def test_revision_components():
    idx=index_module.AgentIndex('a',b'a'*32)
    idx.add({**row('child1'),'revises':'root'})
    idx.add({**row('child2'),'revises':'root'})
    assert len(idx.rows())==2  # Missing root means unrelated components.
    idx.add(row('root'))
    result=idx.rows(); assert len(result)==1 and result[0]['conflict']
    assert set([result[0]['id'],*result[0]['alternatives']])=={'child1','child2'}
    idx.add({**row('other'),'scope':'work/b','revises':'root'})
    assert len(idx.rows())==2


def test_sql_task_and_thai(monkeypatch):
    idx=index_module.AgentIndex('a',b'a'*32)
    idx.replace([{**row('task'),'content':'unrelated prose','task_ref':'AM-42','project':'wanted'},
                 {**row('thai'),'content':'การย้ายฐานข้อมูล','project':'wanted'},
                 {**row('other'),'content':'การย้ายฐานข้อมูล','project':'other'}])
    seen=[]; original=index_module._decode_row
    def track(data):
        result=original(data); seen.append(result['id']); return result
    monkeypatch.setattr(index_module,'_decode_row',track)
    result=idx.recall(analyze_query('AM-42',project='wanted'),12,{'work/a'},{'work'})
    assert [r['id'] for r in result['results']]==['task'] and seen==['task']
    seen.clear()
    result=idx.recall(analyze_query('ฐานข้อมูล',project='wanted'),12,{'work/a'},{'work'})
    assert [r['id'] for r in result['results']]==['thai'] and seen==['thai']


def test_lazy_snapshots(tmp_path, monkeypatch):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); s=Session(agents[0].certificate())
    snapshots=list((home.root/'index').glob('*.snap')); before={p:p.read_bytes() for p in snapshots}
    def forbidden(*a,**kw): raise AssertionError('full index rebuild during append')
    monkeypatch.setattr(b,'payloads',forbidden)
    marker='incrementalSnapshotPrivateMarker'
    assert 'id' in remember(b,s,agents[0],body=marker)
    assert all(p.read_bytes()==data for p,data in before.items())
    b.close()
    assert any(p.read_bytes()!=data for p,data in before.items())
    assert all(marker.encode() not in p.read_bytes() for p in snapshots)
