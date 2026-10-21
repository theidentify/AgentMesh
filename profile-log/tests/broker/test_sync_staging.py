import queue
import threading
import pytest
from agentmesh_log.broker.client import BrokerClient
from agentmesh_log.broker.service import Broker, Session, PREFIXES
from agentmesh_log.storage import StorageError
from agentmesh_log.storage.filesystem import FilesystemStorage
from .support import setup_home, remember, recall


class Channel:
    def __init__(self,inbox,outbox): self.inbox,self.outbox=inbox,outbox
    def send(self,value): self.outbox.put(value)
    def receive(self):
        value=self.inbox.get(timeout=5)
        if value is None: raise EOFError()
        return value
    def close(self): self.outbox.put(None)


@pytest.mark.parametrize('operation',['list','put'])
def test_recall_during_network(tmp_path,operation):
    home,state,store,agents=setup_home(tmp_path); rows=[]; b=Broker(home,on_sync=rows.append)
    entered=threading.Event(); release=threading.Event(); done=threading.Event(); replies=[]
    storage=FilesystemStorage(tmp_path/'remote')
    class Remote:
        def list(self,prefix):
            if operation=='list': entered.set(); assert release.wait(5)
            return storage.list(prefix)
        def get(self,key): return storage.get(key)
        def put(self,key,data):
            if operation=='put': entered.set(); assert release.wait(5)
            storage.put(key,data)
    inbox,outbox=queue.Queue(),queue.Queue()
    connection=threading.Thread(target=b.connection,args=(Channel(inbox,outbox),)); connection.start()
    client=BrokerClient(agents[0],state.device.public_bytes,Channel(outbox,inbox))
    assert 'id' in client.write('migrate database')
    sync=threading.Thread(target=b.sync_attempt,args=(Remote(),60)); sync.start()
    def request():
        try: replies.append(client.request({'op':'recall','query':'migrate database'}))
        finally: done.set()
    worker=threading.Thread(target=request)
    try:
        assert entered.wait(2); worker.start()
        assert done.wait(1), 'recall blocked on remote I/O'
        assert replies[0]['results'] and not release.is_set()
    finally:
        release.set(); sync.join(5)
        if worker.ident: worker.join(5)
        client.close(); connection.join(5); b.close()
    assert not sync.is_alive() and rows[-1]['outcome']=='ok'


def event(state,agent):
    from agentmesh_log.events import seal_event,event_path,verify_event
    sid=state.grants[agent.agent_id].default_write
    data=seal_event(state.trees[0].keys[sid].write_key,agent,'migrate database')
    return event_path(verify_event(data,state.registry())),data


def test_network_verified(tmp_path):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); storage=FilesystemStorage(tmp_path/'remote')
    key,data=event(state,agents[0]); storage.put(key,data); storage.put('events/bad.json',b'{}'); calls=[]
    class Remote:
        def check(self,method):
            calls.append((method,b.lock._is_owned()))
            assert not b.lock._is_owned()
        def list(self,prefix): self.check('list'); return storage.list(prefix)
        def get(self,key): self.check('get'); return storage.get(key)
        def put(self,key,data): self.check('put'); storage.put(key,data)
    try:
        result=b.sync_once(Remote())
        assert result['downloaded']==1 and result['invalid']==1 and result['errors']==0
        assert recall(b,Session(agents[0].certificate()))['results']
        assert {name for name,owned in calls}=={'list','get','put'}
        assert not any(owned for name,owned in calls)
        assert sum(name=='list' for name,owned in calls)==len(PREFIXES)
    finally: b.close()


@pytest.mark.parametrize('control',[False,True])
def test_failed_flush_retries(tmp_path,control):
    if control:
        from agentmesh_log.oskeystore import initialize
        from agentmesh_log.broker.keystore import BrokerHome
        home=BrokerHome(tmp_path/'home'); initialize(home,['work'],file_keystore=True)
    else: home,*_=setup_home(tmp_path)
    rows=[]; b=Broker(home,on_sync=rows.append); storage=FilesystemStorage(tmp_path/'remote'); failed=[]
    class Remote:
        def list(self,prefix): return storage.list(prefix)
        def get(self,key): return storage.get(key)
        def put(self,key,data):
            if not failed: failed.append(key); raise StorageError('offline')
            storage.put(key,data)
    try:
        b.sync_attempt(Remote(),60)
        assert rows[-1]['outcome']=='remote_error' and rows[-1]['errors']==1
        assert rows[-1]['outbox']==1 and failed[0] not in b._remote_keys
        assert failed[0] not in b.replica.remote_keys
        b.sync_attempt(Remote(),60)
        assert rows[-1]['outcome']=='ok' and rows[-1]['uploaded']==1 and rows[-1]['outbox']==0
        assert storage.get(failed[0])==b.replica.storage.get(failed[0])
    finally: b.close()


@pytest.mark.parametrize('one_shot',[False,True])
def test_sync_serialized(tmp_path,one_shot):
    home,*_=setup_home(tmp_path); b=Broker(home); storage=FilesystemStorage(tmp_path/'remote')
    entered=threading.Event(); release=threading.Event(); second=threading.Event(); started=threading.Event()
    class Remote:
        def list(self,prefix):
            if threading.current_thread().name=='first': entered.set(); assert release.wait(5)
            else: second.set()
            return storage.list(prefix)
        def get(self,key): return storage.get(key)
        def put(self,key,data): storage.put(key,data)
    def attempt():
        started.set()
        if one_shot: b.sync_once(Remote())
        else: b.sync_attempt(Remote(),60)
    first=threading.Thread(target=b.sync_attempt,args=(Remote(),60),name='first')
    other=threading.Thread(target=attempt,name='second'); first.start()
    try:
        assert entered.wait(2); other.start(); assert started.wait(2)
        assert not second.wait(.2)
    finally:
        release.set(); first.join(5)
        if other.ident: other.join(5)
        b.close()
    assert second.is_set() and not first.is_alive() and not other.is_alive()


@pytest.mark.parametrize('control',[False,True])
def test_failed_get_is_staged(tmp_path,control):
    if control:
        from agentmesh_log.oskeystore import initialize
        from agentmesh_log.broker.keystore import BrokerHome
        home=BrokerHome(tmp_path/'home'); initialize(home,['work'],file_keystore=True)
        key='registry/unavailable.json'
    else: home,*_=setup_home(tmp_path); key='events/unavailable.json'
    rows=[]; b=Broker(home,on_sync=rows.append); fetched=[]
    class Remote:
        def list(self,prefix):
            assert not b.lock._is_owned()
            return iter([key] if key.startswith(prefix) else [])
        def get(self,path):
            fetched.append(path); assert not b.lock._is_owned(); raise StorageError('offline')
        def put(self,path,data): assert not b.lock._is_owned()
    try:
        b.sync_attempt(Remote(),60)
        assert rows[-1]['outcome']=='remote_error' and rows[-1]['errors']==1
        assert rows[-1]['downloaded']==0 and fetched==[key]
        assert key not in b.local_keys()
    finally: b.close()


def test_control_download_unlocked(tmp_path):
    from agentmesh_log.oskeystore import initialize
    from agentmesh_log.broker.keystore import BrokerHome
    home=BrokerHome(tmp_path/'home'); initialize(home,['work'],file_keystore=True)
    rows=[]; b=Broker(home,on_sync=rows.append); storage=FilesystemStorage(tmp_path/'remote'); fetched=[]
    key=next(b.replica.storage.list('registry/')); storage.put(key,b.replica.storage.get(key))
    (b.replica.storage.root/key).unlink()
    class Remote:
        def list(self,prefix): assert not b.lock._is_owned(); return storage.list(prefix)
        def get(self,path):
            assert not b.lock._is_owned(); fetched.append(path); return storage.get(path)
        def put(self,path,data): assert not b.lock._is_owned(); storage.put(path,data)
    try:
        b.sync_attempt(Remote(),60)
        assert rows[-1]['outcome']=='ok' and rows[-1]['downloaded']==1 and fetched==[key]
        assert rows[-1]['outbox']==0
        assert rows[-1]['local_objects']==rows[-1]['remote_objects']==len(list(storage.list('')))
    finally: b.close()


def test_list_abort(tmp_path):
    home,*_=setup_home(tmp_path); rows=[]; b=Broker(home,on_sync=rows.append)
    storage=FilesystemStorage(tmp_path/'remote'); calls=[]
    class Remote:
        def list(self,prefix):
            assert not b.lock._is_owned(); calls.append(prefix)
            if prefix==PREFIXES[-1]: raise StorageError('offline')
            return iter(['events/unavailable.json'] if prefix=='events/' else [])
        def get(self,key): pytest.fail('GET after incomplete listing')
        def put(self,key,data): pytest.fail('PUT after incomplete listing')
    try:
        b.sync_attempt(storage,60); before=set(b._remote_keys)
        result=b.sync_once(Remote())
        assert result==dict(uploaded=0,downloaded=0,invalid=0,errors=1)
        assert b._remote_keys==before and calls==list(PREFIXES)
        b.sync_attempt(Remote(),60)
        assert rows[-1]['outcome']=='remote_error' and rows[-1]['remote_objects']==len(before)
    finally: b.close()
