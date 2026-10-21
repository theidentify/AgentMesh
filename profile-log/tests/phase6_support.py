"""Disposable device and direct-client adapter; production uses real broker IPC."""
from pathlib import Path
from agentmesh_log.broker.keystore import BrokerHome,BrokerState,FileKeyStore
from agentmesh_log.broker.service import Broker,Session
from agentmesh_log.broker.client import BrokerClient
from agentmesh_log.broker.grants import resolve
from agentmesh_log.keyring import initialize_keyring
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.connector import ConnectorRegistry,policy_record,gateway_record
from agentmesh_log.identity import AgentIdentity
from agentmesh_log.encoding import parse
from agentmesh_log.bridge import Bridge
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class DirectClient:
    write=BrokerClient.write
    def __init__(self,path,broker):
        r=parse(Path(path).read_bytes());self.agent=AgentIdentity.from_private(r['agent_cert'],bytes.fromhex(r['agent_private_key']));self.session=Session(r['agent_cert']);self.broker=broker
    def request(self,req): return self.broker.request(self.session,req)
    def close(self): pass


def device(tmp_path,*,native=False,remote_root=None):
    home=BrokerHome(tmp_path/'home');state=BrokerState.create(['personal','work']);state.add('personal/inbox');state.add('work/other')
    storage=FilesystemStorage(home.root/'replica');initialize_keyring(state,storage);store=FileKeyStore(home);store.save(state)
    controls=ConnectorRegistry(storage,state.account.public_record(),[s for t in state.trees for s in t.scopes.values()])
    p=policy_record(state.device,'ab'*16,'Claude','client',read=[{'profile':state.trees[0].root.scope_id,'scopes':None}],remember={'profile':state.trees[0].root.scope_id,'scope':resolve(state,'personal/inbox')})
    controls.publish(p);key=Ed25519PrivateKey.generate();pin=gateway_record(state.device,key.public_key().public_bytes_raw(),'https://gateway.example');controls.publish(pin)
    if remote_root is not None:
        remote=FilesystemStorage(remote_root)
        for path in storage.list(''):remote.put(path,storage.get(path))
        storage=remote
        controls=ConnectorRegistry(storage,state.account.public_record(),[s for t in state.trees for s in t.scopes.values()])
    broker=Broker(home,store=store)
    bridge=Bridge(home,store,storage,**({} if native else {'client_factory':lambda path:DirectClient(path,broker)}))
    return home,store,storage,controls,p,key,pin,broker,bridge
