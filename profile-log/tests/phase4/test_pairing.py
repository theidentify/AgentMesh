import pytest
from agentmesh_log.pairing import JoinRequest, Approver, sas, check_reveal
from agentmesh_log.registry_store import RegistryStore, device_record, signed
from agentmesh_log.agekeys import DeviceAgeIdentity
from agentmesh_log.identity import DeviceIdentity
from agentmesh_log.model import Account
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.encoding import wire


def fixture(tmp_path):
    account=Account(); device=DeviceIdentity(); age=DeviceAgeIdentity(); storage=FilesystemStorage(tmp_path)
    registry=RegistryStore(storage,account.public_record()); registry.publish_device(device_record(device,device.certificate(account),age))
    return device,storage,registry.load()


def test_p4_2_commit_reveal_mitm(tmp_path):
    device,storage,registry=fixture(tmp_path)
    join=JoinRequest(storage); attacker=JoinRequest(FilesystemStorage(tmp_path/'attacker'),request_id=join.request_id)
    approver=Approver(storage,device,registry,{})
    approval=approver.begin(join.request_id)
    reveal=join.reveal(approval,registry)
    assert len(sas(join.commit,approval,reveal))==6
    altered=Approver(attacker.storage,device,registry,{})
    other=altered.begin(attacker.request_id)
    other_reveal=attacker.reveal(other,registry)
    assert sas(join.commit,approval,reveal)!=sas(attacker.commit,other,other_reveal)
    with pytest.raises(ValueError,match='pairing_denied'): altered.confirm(other_reveal,lambda code:False)
    for i in range(10000):
        bad=signed('pair-reveal',join.device._private,request_id=join.request_id,Nj=i.to_bytes(32,'big').hex())
        with pytest.raises(ValueError): check_reveal(join.commit,approval,bad)


def test_p4_2_order_expiry_and_replay(tmp_path):
    device,storage,registry=fixture(tmp_path)
    join=JoinRequest(storage)
    with pytest.raises(ValueError): join.reveal(None,registry)
    a=Approver(storage,device,registry,{})
    approval=a.begin(join.request_id)
    with pytest.raises(ValueError): Approver(storage,device,registry,{}).begin(join.request_id)
    another=JoinRequest(storage)
    with pytest.raises(ValueError): another.reveal(approval,registry)
    with pytest.raises(ValueError): Approver(storage,device,registry,{},clock=lambda:another.commit['issued_at']+600001).begin(another.request_id)
    expired=JoinRequest(storage,clock=lambda:1)
    with pytest.raises(ValueError): Approver(storage,device,registry,{}).begin(expired.request_id)


def test_p4_3_scoped_bootstrap(tmp_path):
    from agentmesh_log.broker.keystore import BrokerHome, BrokerState, FileKeyStore
    from agentmesh_log.keyring import initialize_keyring
    from agentmesh_log.events import seal_event, open_event
    from agentmesh_log.identity import AgentIdentity
    from agentmesh_log.model import Label
    home=BrokerHome(tmp_path/'a'); state=BrokerState.create(['work','personal'])
    a=state.add('work/client-a'); state.add('work/client-b')
    storage=FilesystemStorage(tmp_path/'remote'); initialize_keyring(state,storage)
    join=JoinRequest(storage); registry=state.registry()
    approver=Approver(storage,state.device,registry,{})
    approval=approver.begin(join.request_id); reveal=join.reveal(approval,registry)
    approver.confirm(reveal,lambda code:code==sas(join.commit,approval,reveal))
    approver.grant(state,[a.scope_id])
    target=BrokerHome(tmp_path/'b'); store=FileKeyStore(target)
    joined=join.finish(registry,target,store)
    keys=[k for tree in joined.trees for epochs in tree.epochs.values() for k in epochs]
    assert {k.scope_id for k in keys}=={a.scope_id}
    agent=AgentIdentity(state.device,state.account.account_id)
    for tree in state.trees:
        for sid,key in tree.keys.items():
            event=seal_event(key.write_key,agent,'synthetic',label=Label.general)
            if sid==a.scope_id: assert open_event(event,joined.registry(),keys[0])['body']=='synthetic'
            else:
                with pytest.raises(ValueError): open_event(event,joined.registry(),keys[0])


def test_p4_2_slot_binding(tmp_path):
    from agentmesh_log.pairing import check_commit
    device,storage,registry=fixture(tmp_path); join=JoinRequest(storage)
    assert len(set(join.commit['slot_recipients']))==8
    altered={**join.commit,'slot_recipients':list(join.commit['slot_recipients'])}
    altered['slot_recipients'][0]=DeviceAgeIdentity().recipient
    with pytest.raises(ValueError): check_commit(altered)
    values={k:v for k,v in altered.items() if k not in ('format','signature')}
    # Even a freshly self-signed commit cannot reuse one identity across profile slots.
    values['slot_recipients']=[values['slot_recipients'][0]]*8
    with pytest.raises(ValueError): check_commit(signed('pair-commit',join.device._private,**values))
