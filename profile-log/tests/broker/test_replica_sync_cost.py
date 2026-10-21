import copy
import hashlib
import math
import pytest
from agentmesh_log import ulid
from agentmesh_log.broker.keystore import BrokerHome
from agentmesh_log.broker.replica import Replica, floor_ulid
from agentmesh_log.events import seal_event, event_path
from agentmesh_log.encoding import parse
from agentmesh_log.storage.filesystem import FilesystemStorage
from .support import setup_home


class CountingStorage(FilesystemStorage):
    def __init__(self, root): super().__init__(root); self.reset()
    def reset(self): self.lists = []; self.gets = 0; self.puts = 0
    def list(self, prefix, *, start_after=None):
        keys = list(super().list(prefix,start_after=start_after))
        self.lists.append((prefix,start_after,len(keys))); return iter(keys)
    def get(self, key): self.gets += 1; return super().get(key)
    def put(self, key, data): self.puts += 1; return super().put(key,data)
    def pages(self): return sum(max(1,math.ceil(n/1000)) for _,_,n in self.lists)


def fixture(tmp_path):
    home,state,store,agents = setup_home(tmp_path)
    key = state.trees[0].keys[state.grants[agents[0].agent_id].default_write]
    clock = [(ulid.timestamp(ulid.generate())+1000)/1000]; writer = Replica(home,state,clock=lambda:clock[0])
    reader = Replica(BrokerHome(tmp_path/'reader'),state,clock=lambda:clock[0])
    remote = CountingStorage(tmp_path/'remote')
    return state,agents[0],key,clock,writer,reader,remote


def sealed(monkeypatch, key, agent, ms, body='fixture'):
    monkeypatch.setattr(ulid,'generate',lambda:floor_ulid(ms))
    return seal_event(key.write_key,agent,body)


@pytest.mark.parametrize('count', [50,500], ids=['50','500'])
def test_steady_state(tmp_path, monkeypatch, count):
    state,agent,key,clock,writer,reader,remote = fixture(tmp_path)
    base = int(clock[0]*1000)
    for i in range(count):
        clock[0] = (base+i*601000)/1000
        writer.put_event(sealed(monkeypatch,key,agent,base+i*601000,f'n{i}'))
    assert writer.sync_once(remote)['errors']==0
    assert reader.sync_once(remote)['downloaded']==count
    prefixes = sum(len(t.scopes) for t in state.trees)
    for i in range(count,count+20):
        clock[0] = (base+i*601000)/1000
        writer.put_event(sealed(monkeypatch,key,agent,base+i*601000,f'n{i}'))
        assert writer.sync_once(remote)['uploaded']==1
        remote.reset(); result = reader.sync_once(remote)
        assert result==dict(uploaded=0,downloaded=1,invalid=0,errors=0,skipped=0)
        assert remote.gets==1 and len(remote.lists)==1+prefixes
        assert sum(n for p,_,n in remote.lists if p.startswith('events/'))==2
        assert all(p!='events/' for p,_,_ in remote.lists)


@pytest.mark.parametrize('forced', [False,True], ids=['scheduled','forced'])
def test_reconcile(tmp_path, monkeypatch, forced):
    state,agent,key,clock,writer,reader,remote = fixture(tmp_path)
    base = int(clock[0]*1000)
    clock[0] = (base+2000000)/1000
    writer.put_event(sealed(monkeypatch,key,agent,base+2000000))
    writer.sync_once(remote); reader.sync_once(remote)
    old = sealed(monkeypatch,key,agent,base); path = event_path(parse(old)); remote.put(path,old)
    assert reader.sync_once(remote)['downloaded']==0 and path not in reader.keys()
    if not forced: clock[0] += 86401
    assert reader.sync_once(remote,full=forced)['downloaded']==1 and path in reader.keys()


@pytest.mark.parametrize('change', ['registry','epoch','wrap','account','full'],
    ids=['registry','epoch','wrap','account','full'])
def test_invalid_cache(tmp_path, monkeypatch, change):
    state,agent,key,clock,writer,reader,remote = fixture(tmp_path)
    base = int(clock[0]*1000)
    data = sealed(monkeypatch,key,agent,base); writer.put_event(data)
    writer.sync_once(remote); reader.sync_once(remote)
    clock[0] = (base+1)/1000
    path = event_path(parse(data)).replace(floor_ulid(base),floor_ulid(base+1)); remote.put(path,b'{}')
    remote.reset(); assert reader.sync_once(remote)['invalid']==1 and remote.gets==1
    assert reader._invalid[path]==hashlib.sha256(b'{}').hexdigest()
    for _ in range(3):
        remote.reset(); result = reader.sync_once(remote)
        assert result['skipped']==1 and result['invalid']==0 and remote.gets==0
    new_state = copy.deepcopy(state)
    if change=='registry': new_state.control.setdefault('registry_records',{})['record'] = {'changed':True}
    elif change=='epoch': new_state.trees[0].rotate(key.scope_id)
    elif change=='wrap': reader._put('keys/new/scope/wrap.json',b'{}')
    elif change=='account':
        from agentmesh_log.model import Account
        new_state.account = Account()
    reader.update_state(new_state)
    remote.reset(); result = reader.sync_once(remote,full=change=='full')
    assert result['invalid']==1 and remote.gets==1


def test_listing_failure(tmp_path, monkeypatch):
    state,agent,key,clock,writer,reader,remote = fixture(tmp_path)
    original = remote.list
    def broken(prefix, **kwargs):
        if prefix=='keys/': raise OSError('listing failed')
        return original(prefix,**kwargs)
    monkeypatch.setattr(remote,'list',broken)
    assert reader.sync_once(remote)['errors']==1
    assert reader.last_full is None and reader.remote_keys==set() and remote.puts==remote.gets==0
    monkeypatch.setattr(remote,'list',original); reader.sync_once(remote)
    before = reader.remote_keys.copy(); last_full = reader.last_full; clock[0] += 1; remote.reset()
    def partial(prefix, **kwargs):
        if prefix.startswith('events/'): raise OSError('listing failed')
        return iter(['keys/new/scope/wrap.json'])
    monkeypatch.setattr(remote,'list',partial)
    assert reader.sync_once(remote)['errors']==1
    assert reader.remote_keys==before and remote.puts==remote.gets==0 and reader.last_full==last_full


def test_prefixes(tmp_path):
    state,agent,key,clock,writer,reader,remote = fixture(tmp_path)
    reader.sync_once(remote)
    remote.put('keys/new/scope/wrap.json',b'{}')
    remote.put('events/new/scope/bad.json',b'{}')
    remote.reset(); assert reader.sync_once(remote)['invalid']==2
    assert ('events/new/scope/',None,1) in remote.lists
    prefixes = reader._prefixes({'events/p/s/not-ulid.json','events/p/s/'+floor_ulid(10)+'.json',
        'events/p/s/'+('Z'*26)+'.json','keys/other/scope/wrap.json'})
    assert prefixes['events/p/s/']==10 and prefixes['events/other/scope/'] is None
    assert floor_ulid(-1)=='0'*26


def test_get_errors_retry(tmp_path, monkeypatch):
    state,agent,key,clock,writer,reader,remote = fixture(tmp_path)
    path = f'events/{key.profile_id}/{key.scope_id}/{ulid.generate()}.json'
    remote.put(path,b'{}'); original = remote.get
    def broken(key): raise OSError('get failed')
    monkeypatch.setattr(remote,'get',broken)
    assert reader.sync_once(remote)['errors']==1 and not reader._invalid
    monkeypatch.setattr(remote,'get',original)
    assert reader.sync_once(remote)['invalid']==1


def test_fast_clock(tmp_path, monkeypatch):
    from agentmesh_log.identity import AgentIdentity
    state,agent,key,clock,writer,reader,remote = fixture(tmp_path)
    base = int(clock[0]*1000)
    writer.put_event(sealed(monkeypatch,key,agent,base+3600000))
    writer.sync_once(remote); assert reader.sync_once(remote)['downloaded']==1
    normal = AgentIdentity(state.device,state.account.account_id,issued_at=base)
    other = Replica(BrokerHome(tmp_path/'other'),state,clock=lambda:clock[0])
    data = sealed(monkeypatch,key,normal,base); other.put_event(data); other.sync_once(remote)
    remote.reset(); result = reader.sync_once(remote)
    assert result==dict(uploaded=0,downloaded=1,invalid=0,errors=0,skipped=0)
    assert remote.gets==1 and event_path(parse(data)) in reader.keys()
    prefix = f'events/{key.profile_id}/{key.scope_id}/'
    assert (prefix,prefix+floor_ulid(base-600000),2) in remote.lists
