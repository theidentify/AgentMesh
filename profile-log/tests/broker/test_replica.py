from agentmesh_log.broker.replica import Replica
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.events import seal_event
from .support import setup_home


def test_sync(tmp_path):
    home,state,store,agents=setup_home(tmp_path); r=Replica(home,state)
    key=state.trees[0].keys[state.grants[agents[0].agent_id].default_write]
    data=seal_event(key.write_key,agents[0],'fixture'); r.put_event(data)
    assert r.outbox()>0
    remote=FilesystemStorage(tmp_path/'remote'); result=r.sync_once(remote)
    assert result['errors']==0 and r.outbox()==0
    remote.put('events/bad.json',b'{}')
    assert r.sync_once(remote)['invalid']==1
    assert 'events/bad.json' not in list(r.storage.list('events/'))


def test_object_failures(tmp_path):
    home,state,store,agents=setup_home(tmp_path); r=Replica(home,state)
    key=state.trees[0].keys[state.grants[agents[0].agent_id].default_write]
    r.put_event(seal_event(key.write_key,agents[0],'fixture'))
    before=r.watermark()
    class Failing:
        def list(self,prefix): return ['events/unreadable.json'] if prefix=='events/' else []
        def get(self,key): raise OSError('injected')
        def put(self,key,data): raise OSError('injected')
    result=r.sync_once(Failing())
    assert result['errors']>=2 and r.watermark()==before
    assert r.outbox()>0
