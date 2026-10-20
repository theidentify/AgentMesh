import hashlib
import itertools
import shutil
import pytest
from agentmesh_log.recovery import create_recovery, test_recovery as check_recovery, use_recovery, combine_shares
from agentmesh_log.broker.keystore import BrokerHome,BrokerState,FileKeyStore
from agentmesh_log.keyring import initialize_keyring,sync_control
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.identity import AgentIdentity
from agentmesh_log.events import seal_event,open_event,event_path,verify_event
from agentmesh_log.model import Label
from .test_lifecycle import yes


def tree_hash(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and p.name!='presence.jsonl'}


@pytest.mark.parametrize('indices',[(0,1),(0,2),(1,2)],ids=['ab','ac','bc'])
def test_p4_6_recovery_all_lost(tmp_path,indices):
    home=BrokerHome(tmp_path/'a'); store=FileKeyStore(home); storage=FilesystemStorage(tmp_path/'remote')
    state=BrokerState.create(['work','personal']); state.add('work/client-a'); initialize_keyring(state,storage); store.save(state)
    shares=create_recovery(home,store,storage,console=yes())
    pinned=state.account.public_record(); old_keys={k.key_id for t in state.trees for k in t.keys.values()}
    agent=AgentIdentity(state.device,state.account.account_id); events=[]
    for tree in state.trees:
        for key in tree.keys.values():
            event=seal_event(key.write_key,agent,'history',label=Label.general)
            storage.put(event_path(verify_event(event,state.registry())),event); events.append(event)
    with pytest.raises(ValueError): combine_shares([shares[0]])
    before=tree_hash(tmp_path); log=home.root/'presence.jsonl'; lines=len(log.read_bytes().splitlines())
    assert check_recovery(home,store,storage,[shares[i] for i in indices],console=yes())
    assert before==tree_hash(tmp_path)
    assert len(log.read_bytes().splitlines())==lines+1
    from agentmesh_log.encoding import parse
    assert parse(log.read_bytes().splitlines()[-1])['window_used'] is False
    survivor=BrokerHome(tmp_path/'survivor'); survivor_store=FileKeyStore(survivor); survivor_store.save(store.load())
    shutil.rmtree(home.root)
    fresh=BrokerHome(tmp_path/'fresh'); fresh_store=FileKeyStore(fresh)
    new_shares=use_recovery(fresh,fresh_store,storage,[shares[i] for i in indices],account=pinned,console=yes())
    recovered=fresh_store.load(); keys={k.key_id:k for t in recovered.trees for epochs in t.epochs.values() for k in epochs}
    for event in events:
        envelope=verify_event(event,recovered.registry())
        assert open_event(event,recovered.registry(),keys[envelope['scope_key_id']])['body']=='history'
    assert not old_keys & {k.key_id for t in recovered.trees for k in t.keys.values()}
    assert not check_recovery(fresh,fresh_store,storage,shares[:2],console=yes())
    assert check_recovery(fresh,fresh_store,storage,new_shares[:2],console=yes())
    sync_control(survivor,survivor_store,storage)
    assert survivor_store.load().control['alerts']
    # A second total loss must retain history using only the newly issued shares.
    second=BrokerHome(tmp_path/'second'); second_store=FileKeyStore(second)
    use_recovery(second,second_store,storage,new_shares[:2],account=pinned,console=yes())
    restored=second_store.load(); keys={k.key_id:k for t in restored.trees for epochs in t.epochs.values() for k in epochs}
    for event in events:
        envelope=verify_event(event,restored.registry())
        assert open_event(event,restored.registry(),keys[envelope['scope_key_id']])['body']=='history'


def test_p4_6_scopes_added_after_init(tmp_path):
    from agentmesh_log.cli import execute,parser
    from agentmesh_log.oskeystore import initialize,OsKeyStore
    from .test_oskeystore import FakeBackend
    backend=FakeBackend(); home=BrokerHome(tmp_path/'a'); shares=initialize(home,['work'],backend=backend)
    store=OsKeyStore(home,backend); pinned=store.load().account.public_record()
    assert execute(parser().parse_args(['broker','scope','add','--home',str(home.root),'work/later']),backend=backend,console=yes())=={'ok':True}
    remote=FilesystemStorage(tmp_path/'remote'); sync_control(home,store,remote)
    fresh=BrokerHome(tmp_path/'fresh'); target=OsKeyStore(fresh,backend)
    use_recovery(fresh,target,remote,shares[:2],account=pinned,console=yes())
    assert any(s.name=='later' for t in target.load().trees for s in t.scopes.values())


def test_p4_6_cli_alert(tmp_path):
    from agentmesh_log.cli import execute,parser
    home=BrokerHome(tmp_path/'a'); store=FileKeyStore(home); storage=FilesystemStorage(tmp_path/'remote')
    state=BrokerState.create(['work']); initialize_keyring(state,storage); store.save(state)
    shares=create_recovery(home,store,storage,console=yes())
    fresh=BrokerHome(tmp_path/'b')
    use_recovery(fresh,FileKeyStore(fresh),storage,shares[:2],account=state.account.public_record(),console=yes())
    result=execute(parser().parse_args(['broker','sync','--home',str(home.root),'--filesystem',str(storage.root)]))
    assert len(result['alerts'])==1


def test_p4_6_forged_alert(tmp_path):
    from agentmesh_log.recovery import collect_alerts
    from agentmesh_log.registry_store import signed,recovery_id
    from agentmesh_log.encoding import wire
    from agentmesh_log import ulid
    home=BrokerHome(tmp_path/'a'); store=FileKeyStore(home); storage=FilesystemStorage(tmp_path/'remote')
    state=BrokerState.create(['work']); initialize_keyring(state,storage); store.save(state)
    create_recovery(home,store,storage,console=yes()); state=store.load(); registry=state.registry()
    record=signed('recovery-alert',state.device._private,account_id=state.account.account_id,id=ulid.generate(),
        recovery_key_id=recovery_id(registry.current_recovery()),device_id=state.device.device_id,issued_at=1)
    storage.put('alerts/recovery/'+record['id']+'.json',wire(record))
    collect_alerts(state,storage,registry)
    assert not state.control['alerts']
