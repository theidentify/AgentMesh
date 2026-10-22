"""Device trust and persistent local import authority."""
from test_import import setup_home, request_body, finish
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.broker.keystore import BrokerHome, BrokerState, FileKeyStore
from agentmesh_log.broker.grants import Grant, resolve
from agentmesh_log.identity import DeviceIdentity, AgentIdentity
from agentmesh_log.events import verify_event


def test_p5_7_peer_import(tmp_path):
    home,state,store,agents=setup_home(tmp_path); importer=agents[0]
    state.grants[importer.agent_id].allow_import=True; store.save(state)
    origin=Broker(home,store=store)
    peer=None
    try:
        session=Session(importer.certificate())
        result=finish(origin,session,importer,origin.request(session,request_body(device=state.device.device_id)))
        assert result['outcome']=='created'
        # A peer holds the same granted scope keys and trusted device chain.
        # Agent grants are local, as in Phase 4 pairing; they are not replicated.
        peer_state=BrokerState.from_record(origin.state.record())
        peer_state.control.pop('import_agents',None)
        device=DeviceIdentity()
        peer_state.certificates.append(device.certificate(peer_state.account))
        peer_state.device=device
        agent=AgentIdentity(device,peer_state.account.account_id)
        scope=resolve(peer_state,'work/a')
        peer_state.grants={agent.agent_id:Grant(agent.agent_id,agent.public_bytes.hex(),
            [scope],[scope],scope,peer_state.trees[0].root.scope_id,allow_import=True)}
        peer_home=BrokerHome(tmp_path/'peer'); peer_store=FileKeyStore(peer_home); peer_store.save(peer_state)
        peer=Broker(peer_home,store=peer_store)
        for path in origin.replica.keys():
            if path.startswith('events/'):
                data=origin.replica.storage.get(path)
                envelope=peer.replica.put_event(data)
                peer.ingest(envelope,data)
        assert importer.agent_id not in peer.state.control.get('import_agents',[])
        peer.close(); peer=Broker(peer_home,store=peer_store)
        # Decryption and certificate validation succeed; provenance recognition
        # must not disappear merely because the importer is another device's agent.
        assert len(list(peer.payloads()))==1
        assert len(peer.imports.ledger(peer_state.grants[agent.agent_id]))==1
        from broker.support import recall
        result=recall(peer,Session(agent.certificate()),compat=True)
        assert result['results'][0]['source_ref']==dict(device=state.device.device_id,table='memory_items',id=1)
    finally:
        if peer: peer.close()
        origin.close()


def test_local_revoked(tmp_path):
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); session=Session(agent.certificate())
    finish(broker,session,agent,broker.request(session,request_body(device=state.device.device_id)))
    broker.close()
    state=store.load(); state.revoke(agent.agent_id); store.save(state)
    broker=Broker(home,store=store)
    try:
        assert len(broker.imports.entries)==1
        assert agent.agent_id in broker.state.control['import_agents']
    finally: broker.close()


def test_peer_revoked(tmp_path):
    from agentmesh_log.events import seal_event
    from agentmesh_log.broker.imports import admitted
    from agentmesh_log.identity import Registry
    home,state,store,agents=setup_home(tmp_path)
    peer=DeviceIdentity(); state.certificates.append(peer.certificate(state.account))
    agent=AgentIdentity(peer,state.account.account_id); store.save(state)
    broker=Broker(home,store=store)
    try:
        key=state.trees[0].keys[resolve(state,'work/a')]
        data=seal_event(key.write_key,agent,request_body(device=peer.device_id)['body'])
        envelope=verify_event(data,state.registry())
        registry=state.registry()
        from agentmesh_log.registry_store import revocation
        registry.add_revocation(revocation(peer.device_id,state.device,revoked_at=0))
        broker.state.registry=lambda:registry
        assert admitted(broker,envelope,{'body':request_body(device=peer.device_id)['body']}) is None
        broker.ingest(envelope,data)
        assert not broker.imports.entries
    finally: broker.close()
