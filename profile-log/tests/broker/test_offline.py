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
    remote.put('events/tampered.json',b'{}')
    assert c.sync_once(remote)['invalid']==1
    assert 'events/tampered.json' not in list(c.replica.storage.list('events/'))
