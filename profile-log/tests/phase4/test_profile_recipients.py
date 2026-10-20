import pytest
from agentmesh_log.agekeys import DeviceAgeIdentity
from agentmesh_log.registry_store import RegistryStore, profile_recipient
from agentmesh_log.keyring import initialize_keyring
from agentmesh_log.broker.keystore import BrokerState
from agentmesh_log.storage.filesystem import FilesystemStorage


def test_p4_1_profile_binding(tmp_path):
    state=BrokerState.create(['work','personal']); storage=FilesystemStorage(tmp_path)
    ring=initialize_keyring(state,storage); profile=state.trees[0].root.scope_id
    assert len(set(state.control['profile_ages'].values()))==2
    identities=[DeviceAgeIdentity.parse(s) for s in state.control['profile_ages'].values()]
    key=state.trees[0].keys[profile]
    record=next(r for r in ring.records() if r['scope']==profile)
    assert ring.open(record,state.device.device_id,identities[0]).key_id==key.key_id
    with pytest.raises(ValueError): ring.open(record,state.device.device_id,identities[1])
    with pytest.raises(ValueError): ring.open(record,state.device.device_id,DeviceAgeIdentity.parse(state.control['age_identity']))
    rs=RegistryStore(storage,state.account.public_record())
    rec=profile_recipient(state.device,profile,identities[0]); rec['age_recipient']=identities[1].recipient
    with pytest.raises(ValueError): rs.publish_profile_recipient(rec)
    binding=next(iter(storage.list('registry/profile-recipients/'+profile+'/')))
    storage.root.joinpath(binding).unlink()
    with pytest.raises(ValueError): ring.open(record,state.device.device_id,identities[0])


def test_p4_3_later_profile(tmp_path):
    from agentmesh_log.oskeystore import initialize,OsKeyStore
    from agentmesh_log.broker.keystore import BrokerHome
    from agentmesh_log.keyring import sync_control
    from agentmesh_log.pairing import JoinRequest,Approver
    from agentmesh_log.cli import execute,parser
    from .test_oskeystore import FakeBackend
    from .test_lifecycle import yes
    backend=FakeBackend(); home=BrokerHome(tmp_path/'a'); initialize(home,['work','personal'],backend=backend)
    store=OsKeyStore(home,backend); storage=FilesystemStorage(tmp_path/'remote'); state=sync_control(home,store,storage)
    work,personal=[t.root.scope_id for t in state.trees]
    join=JoinRequest(storage); registry=state.registry(); approver=Approver(storage,state.device,registry,{})
    approval=approver.begin(join.request_id); reveal=join.reveal(approval,registry); approver.confirm(reveal,lambda _:True)
    result=approver.grant(state,[work]); other=BrokerHome(tmp_path/'b'); target=OsKeyStore(other,backend)
    joined=join.finish(registry,other,target)
    assert join.slots==[] and set(joined.control['profile_ages'])=={work}
    assert len(result['slot_assignments'])==1
    def cli(*args,at=home):
        return execute(parser().parse_args([*args,'--home',str(at.root),'--filesystem',str(storage.root)]),backend=backend,console=yes())
    with pytest.raises(ValueError): cli('pair','regrant',joined.device.device_id,'--scope','personal')
    assert personal not in target.load().control['profile_ages']
    assert cli('pair','recipient',personal,at=other)=={'ok':True}
    assert cli('pair','regrant',joined.device.device_id,'--scope','personal')=={'ok':True}
    restored=sync_control(other,target,storage)
    assert set(restored.control['profile_ages'])=={work,personal}
    assert len(set(restored.control['profile_ages'].values()))==2
    for tree in state.trees:
        imported=next(t for t in restored.trees if t.root.scope_id==tree.root.scope_id)
        assert {k.key_id for k in imported.keys.values()}=={k.key_id for k in tree.keys.values()}


def test_p4_1_recovery_binding(tmp_path):
    from agentmesh_log.oskeystore import initialize,OsKeyStore
    from agentmesh_log.broker.keystore import BrokerHome
    from agentmesh_log.registry_store import read_profile_recovery,recovery_id,signed
    from agentmesh_log.encoding import parse,wire
    from .test_oskeystore import FakeBackend
    backend=FakeBackend(); home=BrokerHome(tmp_path/'a'); initialize(home,['work'],backend=backend)
    state=OsKeyStore(home,backend).load(); storage=FilesystemStorage(home.root/'replica')
    registry=RegistryStore(storage,state.account.public_record()).load(); profile=state.trees[0].root.scope_id
    rid=recovery_id(registry.current_recovery()); record=read_profile_recovery(storage,registry,profile,rid)
    path=storage.root/f'registry/profile-recovery/{profile}/{rid}.json'
    record['age_recipient']=DeviceAgeIdentity().recipient; path.write_bytes(wire(record))
    with pytest.raises(ValueError): read_profile_recovery(storage,registry,profile,rid)
    # A daily device is not allowed to replace the master-signed recovery binding.
    values={k:v for k,v in record.items() if k not in ('format','signature')}
    path.write_bytes(wire(signed('profile-recovery',state.device._private,**values)))
    with pytest.raises(ValueError): read_profile_recovery(storage,registry,profile,rid)
