import pytest
from agentmesh_log.lifecycle import remove_device, profile_state
from agentmesh_log.keyring import initialize_keyring, sync_control
from agentmesh_log.broker.keystore import BrokerHome,BrokerState,FileKeyStore
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.pairing import JoinRequest,Approver
from agentmesh_log.identity import AgentIdentity
from agentmesh_log.events import seal_event,open_event,EVENT_DOMAIN
from agentmesh_log.model import Label
from agentmesh_log.presence import ConsolePresence,PresenceManager
from agentmesh_log.broker.grants import Grant
from agentmesh_log.broker.service import Broker,Session
from agentmesh_log.encoding import typed


def yes(): return ConsolePresence(input_fn=lambda prompt:prompt.split()[1].rstrip(':'),isatty=lambda:True,output=lambda _:None)


def pair(tmp_path,root=False):
    home=BrokerHome(tmp_path/'a'); state=BrokerState.create(['work']); scope=state.add('work/client-a')
    storage=FilesystemStorage(tmp_path/'remote'); initialize_keyring(state,storage); store=FileKeyStore(home); store.save(state)
    sync_control(home,store,storage)
    join=JoinRequest(storage); registry=state.registry(); approver=Approver(storage,state.device,registry,{})
    approval=approver.begin(join.request_id); reveal=join.reveal(approval,registry); approver.confirm(reveal,lambda _:True)
    approver.grant(state,[state.trees[0].root.scope_id if root else scope.scope_id])
    other=BrokerHome(tmp_path/'b'); other_store=FileKeyStore(other); joined=join.finish(registry,other,other_store)
    sync_control(home,store,storage)
    return home,store,other,other_store,storage,scope.scope_id


def test_p4_4_remove_rotates_descendants(tmp_path):
    home,store,other,other_store,storage,sid=pair(tmp_path,root=True)
    before=store.load(); stolen=other_store.load(); agent=AgentIdentity(before.device,before.account.account_id)
    old=before.trees[0].keys[sid]; event=seal_event(old.write_key,agent,'history',label=Label.general)
    remove_device(home,store,storage,stolen.device.device_id,console=yes())
    after=store.load(); new=after.trees[0].keys[sid]
    assert new.key_id!=old.key_id
    future=seal_event(new.write_key,agent,'future',label=Label.general)
    for epochs in stolen.trees[0].epochs.values():
        for key in epochs:
            with pytest.raises(ValueError): open_event(future,stolen.registry(),key)
    assert open_event(event,after.registry(),old)['body']=='history'
    sync_control(other,other_store,storage)
    with pytest.raises(ValueError): other_store.load().registry().verify_agent(AgentIdentity(stolen.device,stolen.account.account_id).certificate())


def test_p4_8_archive_delete(tmp_path):
    home,store,other,other_store,storage,sid=pair(tmp_path)
    from agentmesh_log.recovery import create_recovery
    shares=create_recovery(home,store,storage,console=yes())
    state=store.load(); agent=AgentIdentity(state.device,state.account.account_id)
    state.grants[agent.agent_id]=Grant(agent.agent_id,agent.public_bytes.hex(),[sid],[sid],sid,state.trees[0].root.scope_id)
    store.save(state); p=PresenceManager(home,store); p.open('first_access',console=yes())
    broker=Broker(home); session=Session(agent.certificate())
    result=broker.request(session,dict(op='remember',body='synthetic history'))
    signature=agent._private.sign(EVENT_DOMAIN+typed(result['unsigned'])).hex()
    assert 'id' in broker.request(session,dict(op='sign',signature=signature))
    profile_state(home,store,storage,state.trees[0].root.scope_id,'archived',console=yes())
    assert broker.request(session,dict(op='remember',body='forbidden'))=={'error':'denied'}
    assert broker.request(session,dict(op='recall',query='synthetic history'))['results']
    raw_keys=[k.private_bytes() for t in store.load().trees for epochs in t.epochs.values() for k in epochs]
    profile_state(home,store,storage,state.trees[0].root.scope_id,'deleted',console=yes(),confirmation='work',shares=shares[:2])
    assert not broker.request(session,dict(op='recall',query='synthetic history')).get('results')
    broker.close(); sync_control(other,other_store,storage)
    for target in (home,other):
        for path in target.root.rglob('*'):
            if path.is_file():
                data=path.read_bytes()
                assert all(raw not in data and raw.hex().encode() not in data for raw in raw_keys)
    assert not any(t.epochs for t in other_store.load().trees)


def test_p4_8_retained_age(tmp_path):
    """All post-delete capabilities fail; old paper shares are the explicit exception."""
    import base64
    from agentmesh_log.agekeys import DeviceAgeIdentity
    from agentmesh_log.keys import ScopeReadKey,decode_ciphertext
    from agentmesh_log.encoding import parse,wire
    from agentmesh_log.events import event_path,verify_event
    from agentmesh_log.oskeystore import initialize,OsKeyStore
    from agentmesh_log.recovery import RecoveryIdentity,combine_shares,use_recovery
    from .test_oskeystore import FakeBackend
    backend=FakeBackend(); home=BrokerHome(tmp_path/'a'); old_shares=initialize(home,['work','personal'],backend=backend)
    store=OsKeyStore(home,backend); state=store.load(); pinned=state.account.public_record()
    storage=FilesystemStorage(tmp_path/'remote'); sync_control(home,store,storage)
    profile,keep=[t.root.scope_id for t in state.trees]
    join=JoinRequest(storage); registry=state.registry(); approver=Approver(storage,state.device,registry,{})
    approval=approver.begin(join.request_id); reveal=join.reveal(approval,registry); approver.confirm(reveal,lambda _:True)
    approver.grant(state,[profile,keep]); other=BrokerHome(tmp_path/'b'); other_store=OsKeyStore(other,backend)
    join.finish(registry,other,other_store); sync_control(home,store,storage)
    agent=AgentIdentity(state.device,state.account.account_id); events={}
    for tree in state.trees:
        for key in tree.keys.values():
            event=seal_event(key.write_key,agent,'disposable marker',label=Label.general)
            path=event_path(verify_event(event,state.registry())); storage.put(path,event)
            events[key.scope_id]=event
    deleted_material=[]; deleted_secrets=[]
    for source in (store,other_store):
        snapshot=source.load()
        deleted_secrets.append(snapshot.control['profile_ages'][profile].encode('ascii'))
        deleted_material.append(DeviceAgeIdentity.parse(snapshot.control['profile_ages'][profile]).raw())
        deleted_material.extend(k.private_bytes() for t in snapshot.trees if t.root.scope_id==profile for epochs in t.epochs.values() for k in epochs)
    del state,snapshot
    new_shares=profile_state(home,store,storage,profile,'deleted',console=yes(),confirmation='work',shares=old_shares[:2])
    sync_control(other,other_store,storage)
    age_payloads=[]
    deleted_events=[]
    for path in storage.list(''):
        record=parse(storage.get(path))
        if record.get('profile',record.get('profile_id'))!=profile: continue
        if 'age' in record: age_payloads.append(decode_ciphertext(record['age']))
        if record.get('format')=='agentmesh-event-v1': deleted_events.append(storage.get(path))
    assert age_payloads and deleted_events
    for target,target_store in ((home,store),(other,other_store)):
        current=target_store.load()
        assert profile not in current.control['profile_ages']
        ages=[DeviceAgeIdentity.parse(current.control['age_identity'])]+[DeviceAgeIdentity.parse(v) for v in current.control['profile_ages'].values()]
        keys=[k for t in current.trees for epochs in t.epochs.values() for k in epochs]
        for identity in ages:
            for ciphertext in age_payloads:
                with pytest.raises(ValueError): identity.decrypt(ciphertext)
        for key in keys:
            for event in deleted_events:
                with pytest.raises(ValueError): open_event(event,current.registry(),key)
        keep_key=next(k for k in keys if k.scope_id==keep)
        assert open_event(events[keep],current.registry(),keep_key)['body']=='disposable marker'
        data=[wire(current.record())]+[p.read_bytes() for p in target.root.rglob('*') if p.is_file()]
        assert all(secret not in content for secret in deleted_secrets for content in data)
        for raw in deleted_material:
            for content in data:
                assert all(value not in content for value in (raw,raw.hex().encode(),base64.urlsafe_b64encode(raw).rstrip(b'=')))
    master=RecoveryIdentity(combine_shares(new_shares[:2]))
    for ciphertext in age_payloads:
        with pytest.raises(ValueError): master.age.decrypt(ciphertext)
    fresh=BrokerHome(tmp_path/'fresh'); fresh_store=OsKeyStore(fresh,backend)
    use_recovery(fresh,fresh_store,storage,new_shares[:2],account=pinned,console=yes())
    restored=fresh_store.load(); assert profile not in restored.control['profile_ages']
    keep_key=next(k for t in restored.trees for k in t.epochs.get(keep,[]) if k.key_id==verify_event(events[keep],restored.registry())['scope_key_id'])
    assert open_event(events[keep],restored.registry(),keep_key)['body']=='disposable marker'
    # Old shares intentionally retain access via the immutable old recovery object.
    old_master=RecoveryIdentity(combine_shares(old_shares[:2])); old_profile_age=None
    for path in storage.list('registry/profile-recovery/'+profile+'/'):
        record=parse(storage.get(path))
        try: old_profile_age=DeviceAgeIdentity.from_raw(old_master.age.decrypt(decode_ciphertext(record['age'])))
        except ValueError: continue
    assert old_profile_age is not None
    recovered=False
    for path in storage.list(f'keyring/{profile}/{profile}/'):
        if not path.endswith('/recovery.json'): continue
        record=parse(storage.get(path))
        try: raw=old_profile_age.decrypt(decode_ciphertext(record['age']))
        except ValueError: continue
        key=ScopeReadKey.from_private(profile,profile,raw)
        recovered = open_event(events[profile],store.load().registry(),key)['body']=='disposable marker'
    assert recovered, 'Old-shares exception must remain explicit'


def test_p4_8_bad_delete_shares(tmp_path):
    from agentmesh_log.oskeystore import initialize,OsKeyStore
    from .test_oskeystore import FakeBackend
    from .test_recovery import tree_hash
    backend=FakeBackend(); home=BrokerHome(tmp_path/'a'); shares=initialize(home,['work'],backend=backend)
    store=OsKeyStore(home,backend); storage=FilesystemStorage(home.root/'replica'); profile=store.load().trees[0].root.scope_id
    before=tree_hash(home.root)
    with pytest.raises(ValueError): profile_state(home,store,storage,profile,'deleted',console=yes(),confirmation='work',shares=shares[:1])
    assert tree_hash(home.root)==before
