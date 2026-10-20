import pytest
from agentmesh_log.oskeystore import initialize,OsKeyStore
from agentmesh_log.broker.keystore import BrokerHome
from agentmesh_log.broker.service import Broker,Session
from agentmesh_log.broker.grants import Grant
from agentmesh_log.broker.handshake import Challenge,hello,authenticate
from agentmesh_log.presence import PresenceManager
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.keyring import initialize_keyring,sync_control,AgeKeyring
from agentmesh_log.pairing import JoinRequest,Approver
from agentmesh_log.identity import AgentIdentity
from agentmesh_log.events import EVENT_DOMAIN,open_event,verify_event
from agentmesh_log.encoding import typed
from agentmesh_log.lifecycle import remove_device
from .test_oskeystore import FakeBackend
from .test_lifecycle import yes


def add_agent(home,store,sid):
    state=store.load(); agent=AgentIdentity(state.device,state.account.account_id)
    profile=next(t.root.scope_id for t in state.trees if sid in t.scopes)
    state.grants[agent.agent_id]=Grant(agent.agent_id,agent.public_bytes.hex(),[sid],[sid],sid,profile)
    store.save(state); PresenceManager(home,store).open('first_access',console=yes())
    return agent


def write(broker,agent,text):
    session=Session(agent.certificate()); result=broker.request(session,dict(op='remember',body=text))
    assert result.get('stage')=='sign'
    assert 'id' in broker.request(session,dict(op='sign',signature=agent._private.sign(EVENT_DOMAIN+typed(result['unsigned'])).hex()))
    return result['unsigned']


def test_p4_4_two_device_end_to_end(tmp_path):
    backend=FakeBackend(); home=BrokerHome(tmp_path/'a'); initialize(home,['work','personal'],backend=backend)
    store=OsKeyStore(home,backend); state=store.load(); scope=state.add('work/client-a'); state.add('work/client-b')
    initialize_keyring(state,FilesystemStorage(home.root/'replica')); store.save(state)
    remote=FilesystemStorage(tmp_path/'remote'); sync_control(home,store,remote)
    join=JoinRequest(remote); registry=store.load().registry(); approver=Approver(remote,state.device,registry,{})
    approval=approver.begin(join.request_id); reveal=join.reveal(approval,registry); approver.confirm(reveal,lambda _:True)
    approver.grant(store.load(),[scope.scope_id]); other=BrokerHome(tmp_path/'b'); other_store=OsKeyStore(other,backend)
    join.finish(registry,other,other_store); sync_control(home,store,remote)
    a=add_agent(home,store,scope.scope_id); b=add_agent(other,other_store,scope.scope_id)
    first=Broker(home,store=store); second=Broker(other,store=other_store)
    write(first,a,'shared history from alpha'); write(second,b,'shared history from beta')
    first.sync_once(remote); second.sync_once(remote); first.sync_once(remote)
    for broker,agent in ((first,a),(second,b)):
        results=broker.request(Session(agent.certificate()),dict(op='recall',query='shared history'))['results']
        assert len(results)==2
    stolen=other_store.load(); old_keys=[k for t in stolen.trees for epochs in t.epochs.values() for k in epochs]
    old_data=[remote.get(p) for p in remote.list('events/')]
    remove_device(home,store,remote,stolen.device.device_id,console=yes())
    first.sync_once(remote); second.sync_once(remote)
    for target in (store.load(),other_store.load()):
        challenge=Challenge(target,hello())
        with pytest.raises(ValueError): challenge.verify(authenticate(b,challenge.hello,challenge.message),target)
    new=write(first,a,'shared history after removal'); first.sync_once(remote)
    future=next(remote.get(p) for p in remote.list('events/') if new['id'] in p)
    for key in old_keys:
        with pytest.raises(ValueError): open_event(future,stolen.registry(),key)
    remaining=store.load(); keys={k.key_id:k for t in remaining.trees for epochs in t.epochs.values() for k in epochs}
    for data in old_data:
        env=verify_event(data,remaining.registry())
        assert open_event(data,remaining.registry(),keys[env['scope_key_id']])['body'].startswith('shared history')
    first.close(); second.close()


def test_p4_9_pending_epoch(tmp_path):
    backend=FakeBackend(); home=BrokerHome(tmp_path/'a'); initialize(home,['work'],backend=backend)
    store=OsKeyStore(home,backend); state=store.load(); sid=state.trees[0].root.scope_id
    agent=add_agent(home,store,sid); b=Broker(home,store=store); session=Session(agent.certificate())
    pending=b.request(session,dict(op='remember',body='pending write'))
    ring=AgeKeyring(b.replica.storage,state.registry(),list(state.trees[0].scopes.values()))
    old=state.trees[0].keys[sid]; new=state.trees[0].rotate(sid)
    ring.epoch(new,state.device,previous=old.key_id,reason='rotate')
    sig=agent._private.sign(EVENT_DOMAIN+typed(pending['unsigned'])).hex()
    assert b.request(session,dict(op='sign',signature=sig))=={'error':'denied'}
    assert b.request(session,dict(op='remember',body='stale write'))=={'error':'denied'}
    b.close()
