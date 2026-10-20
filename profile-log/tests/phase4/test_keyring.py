import copy
import pytest
from agentmesh_log.keyring import AgeKeyring, grant_path
from agentmesh_log.agekeys import DeviceAgeIdentity, encrypt
from agentmesh_log.registry_store import RegistryStore, device_record, revocation, signed, profile_recipient
from agentmesh_log.identity import DeviceIdentity, AgentIdentity
from agentmesh_log.model import Account, ScopeTree, Profile, Label
from agentmesh_log.keys import ScopeReadKey
from agentmesh_log.encoding import wire
from agentmesh_log.events import seal_event, open_event
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.broker.handshake import b64


def setup(tmp_path):
    account = Account(); a,b,c = DeviceIdentity(),DeviceIdentity(),DeviceIdentity()
    ages = {d.device_id:DeviceAgeIdentity() for d in (a,b,c)}
    storage=FilesystemStorage(tmp_path); registry=RegistryStore(storage,account.public_record())
    for d in (a,b,c): registry.publish_device(device_record(d,d.certificate(account,issuer=None if d is a else a,issued_at=1),ages[d.device_id],issued_at=2))
    tree=ScopeTree(Profile('work')); child=tree.add(tree.root.scope_id,'project','a')
    for d in (a,b,c): registry.publish_profile_recipient(profile_recipient(d,tree.root.scope_id,ages[d.device_id],issued_at=2))
    ring=AgeKeyring(storage,registry.load(),list(tree.scopes.values()))
    for key in tree.keys.values(): ring.publish(key,a,a.device_id,ages[a.device_id].recipient,issued_at=3)
    return account,a,b,c,ages,tree,ring,registry


def test_p4_1_forged_grants(tmp_path):
    account,a,b,c,ages,tree,ring,registry=setup(tmp_path)
    key=tree.keys[tree.root.scope_id]
    valid=ring.make_grant(key,a,b.device_id,ages[b.device_id].recipient,issued_at=4)
    assert ring.open(valid,b.device_id,ages[b.device_id]).key_id==key.key_id
    unknown=DeviceIdentity()
    for issuer in (unknown,c):
        forged=ring.make_grant(key,issuer,b.device_id,ages[b.device_id].recipient,issued_at=5)
        ring.storage.put(grant_path(forged),wire(forged))
        with pytest.raises(ValueError): ring.open(forged,b.device_id,ages[b.device_id])
        # The same immutable object path needs a fresh storage for the next forgery.
        ring.storage=FilesystemStorage(tmp_path/issuer.device_id)
    ring.storage=registry.storage
    tampered=copy.deepcopy(valid); tampered['issued_at']=5
    with pytest.raises(ValueError): ring.open(tampered,b.device_id,ages[b.device_id])
    wrong=ScopeReadKey.generate(key.profile_id,key.scope_id)
    changed={k:v for k,v in valid.items() if k not in ('format','signature')}
    changed['age']=b64(encrypt(wrong.private_bytes(),ages[b.device_id].recipient))
    with pytest.raises(ValueError): ring.open(signed('keyring-grant',a._private,**changed),b.device_id,ages[b.device_id])
    registry.publish_revocation(revocation(a.device_id,b,revoked_at=6)); ring.registry=registry.load()
    after=ring.make_grant(key,a,b.device_id,ages[b.device_id].recipient,issued_at=7)
    with pytest.raises(ValueError): ring.open(after,b.device_id,ages[b.device_id])


def test_p4_9_concurrent_epochs(tmp_path):
    account,a,b,c,ages,tree,ring,registry=setup(tmp_path)
    old=tree.keys[tree.root.scope_id]
    genesis=ring.epoch(old,a,previous=None,created_at=3)
    left=tree.rotate(old.scope_id); right=tree.rotate(old.scope_id)
    e1=ring.epoch(left,a,previous=old.key_id,created_at=5,reason='rotate')
    e2=ring.epoch(right,a,previous=old.key_id,created_at=5,reason='rotate')
    winner=max(left.key_id,right.key_id)
    assert ring.current(old.profile_id,old.scope_id).get('key_id')==winner
    peer=AgeKeyring(ring.storage,ring.registry,list(tree.scopes.values()))
    assert peer.current(old.profile_id,old.scope_id)['key_id']==winner
    agent=AgentIdentity(a,account.account_id)
    for key in (old,left,right):
        data=seal_event(key.write_key,agent,'history',label=Label.general)
        assert open_event(data,ring.registry,key)['body']=='history'
