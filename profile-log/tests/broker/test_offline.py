from agentmesh_log.broker.keystore import BrokerHome, FileKeyStore
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.events import EVENT_DOMAIN
from agentmesh_log.encoding import typed
from .support import setup_home, remember, recall


def test_p3_9_offline(tmp_path):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); s=Session(agents[0].certificate())
    class Offline:
        def list(self,*args): raise OSError('offline')
        def put(self,*args): raise OSError('offline')
        def get(self,*args): raise OSError('offline')
    count=b.replica.outbox(); assert b.sync_once(Offline())['errors']>0
    first=remember(b,s,agents[0])['id']
    response=b.request(s,dict(op='revise',body='migrate database revised',revises=first))
    assert 'id' in b.request(s,dict(op='sign',signature=agents[0]._private.sign(EVENT_DOMAIN+typed(response['unsigned'])).hex()))
    assert recall(b,s)['results'] and 'agent_id' in b.request(s,{'op':'whoami'})
    assert b.replica.outbox()>count
    remote=FilesystemStorage(tmp_path/'remote'); assert b.sync_once(remote)['errors']==0
    assert b.replica.outbox()==0
    second=BrokerHome(tmp_path/'second'); FileKeyStore(second).save(state); c=Broker(second)
    assert c.sync_once(remote)['downloaded']==2
    assert recall(c,s)['results'][0]['content']=='migrate database revised'
    remote.put(f"events/{state.trees[0].root.scope_id}/{state.grants[agents[0].agent_id].default_write}/tampered.json",b'{}')
    assert c.sync_once(remote)['invalid']==1
    assert f"events/{state.trees[0].root.scope_id}/{state.grants[agents[0].agent_id].default_write}/tampered.json" not in list(c.replica.storage.list('events/'))


def test_live_broker_adopts_sync(tmp_path, monkeypatch):
    """A one-shot `broker sync` (another process) downloads into the replica of a running broker; the running
    broker must see those events on its next request, and must not adopt objects that fail verification."""
    import agentmesh_log.broker.service as service
    monkeypatch.setattr(service, 'ADOPT_INTERVAL', 0)
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); s=Session(agents[0].certificate())
    remote=FilesystemStorage(tmp_path/'remote')
    second=BrokerHome(tmp_path/'second'); FileKeyStore(second).save(state)
    running=Broker(second); assert recall(running,s)['results']==[]          # started before anything was synced
    remember(b,s,agents[0]); assert b.sync_once(remote)['errors']==0
    assert Broker(second).sync_once(remote)['downloaded']==1                    # the one-shot sync process
    assert recall(running,s)['results'][0]['content']=='migrate database'
    scope=f"events/{state.trees[0].root.scope_id}/{state.grants[agents[0].agent_id].default_write}/"
    running.replica.storage.put(scope+'tampered.json',b'{}')
    assert running.adopt_local(force=True)==0 and scope+'tampered.json' not in running.replica.keys()
