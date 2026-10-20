"""Independent security-check regressions: valid surrounding records avoid masking."""
import pytest
from agentmesh_log.agekeys import DeviceAgeIdentity
from agentmesh_log.encoding import wire
from agentmesh_log.identity import DeviceIdentity, AgentIdentity
from agentmesh_log.keys import ScopeReadKey
from agentmesh_log.keyring import epoch_path, grant_path
from agentmesh_log.registry_store import device_record, profile_recipient, revocation, signed
from agentmesh_log.pairing import Approver, JoinRequest, check_reveal, sas, digest
from .test_keyring import setup
from .test_pairing import fixture


def test_k2_no_scope(tmp_path):
    account,a,b,c,ages,tree,ring,rs=setup(tmp_path)
    scope=next(s for s in tree.scopes if s!=tree.root.scope_id)
    key=ScopeReadKey.generate(tree.root.scope_id,scope)
    assert ring.registry.device_public(c.device_id)==c.public_bytes
    record=ring.make_grant(key,c,b.device_id,ages[b.device_id].recipient,issued_at=5)
    ring.storage.put(grant_path(record),wire(record))
    with pytest.raises(ValueError,match='unauthorized key issuer'):
        ring.open(record,b.device_id,ages[b.device_id])


def test_k6_recovery_root(tmp_path):
    account,a,b,c,ages,tree,ring,rs=setup(tmp_path)
    root=tree.root.scope_id; child=next(s for s in tree.scopes if s!=root)
    recipient=DeviceAgeIdentity().recipient
    assert ring.validate(ring.make_grant(tree.keys[root],a,'recovery',recipient,issued_at=5))
    record=ring.make_grant(tree.keys[child],a,'recovery',recipient,issued_at=5)
    with pytest.raises(ValueError,match='recovery requires profile root'): ring.validate(record)


def test_k7_epoch_authority(tmp_path):
    account,a,b,c,ages,tree,ring,rs=setup(tmp_path)
    old=tree.keys[tree.root.scope_id]; ring.epoch(old,a,created_at=3)
    key=ScopeReadKey.generate(old.profile_id,old.scope_id)
    rogue=signed('epoch',c._private,profile=key.profile_id,scope=key.scope_id,key_id=key.key_id,
        previous_key_id=old.key_id,created_at=5,reason='rotate',issuer_device=c.device_id)
    ring.storage.put(epoch_path(rogue),wire(rogue))
    assert ring.current(old.profile_id,old.scope_id)['key_id']==old.key_id
    with pytest.raises(ValueError,match='unauthorized epoch issuer'): ring.validate_epoch(rogue)


def test_k8_sibling_scope(tmp_path):
    account,a,b,c,ages,tree,ring,rs=setup(tmp_path)
    client_a=next(s for s in tree.scopes if s!=tree.root.scope_id)
    sibling=tree.add(tree.root.scope_id,'project','client-b'); ring.scopes=dict(tree.scopes)
    ring.publish(tree.keys[sibling.scope_id],a,c.device_id,ages[c.device_id].recipient,issued_at=4)
    assert ring.authorized(c.device_id,tree.root.scope_id,sibling.scope_id,5)
    key=ScopeReadKey.generate(tree.root.scope_id,client_a)
    record=ring.make_grant(key,c,b.device_id,ages[b.device_id].recipient,issued_at=5)
    ring.storage.put(grant_path(record),wire(record))
    with pytest.raises(ValueError,match='unauthorized key issuer'):
        ring.open(record,b.device_id,ages[b.device_id])


def test_r3_forged_revocation(tmp_path):
    account,a,b,c,ages,tree,ring,rs=setup(tmp_path)
    record=revocation(b.device_id,a,revoked_at=10)
    record['signature']=revocation(b.device_id,c,revoked_at=10)['signature']
    rs.storage.put('registry/revocations/'+b.device_id+'.json',wire(record))
    registry=rs.load()
    assert b.device_id not in registry.revocations
    agent=AgentIdentity(b,account.account_id,issued_at=11)
    assert registry.verify_agent(agent.certificate())==agent.public_bytes
    with pytest.raises(ValueError,match='invalid signature'): registry.add_revocation(record)


@pytest.mark.parametrize('operation',['device','agent','grant'])
def test_r4_issuer_chain(tmp_path,operation):
    account,a,b,c,ages,tree,ring,rs=setup(tmp_path)
    child=DeviceIdentity(); age=DeviceAgeIdentity()
    rs.publish_device(device_record(child,child.certificate(account,issuer=b,issued_at=20),age,issued_at=21))
    rs.publish_profile_recipient(profile_recipient(child,tree.root.scope_id,age,issued_at=21))
    ring.registry=rs.load(); key=tree.keys[tree.root.scope_id]
    ring.publish(key,a,child.device_id,age.recipient,issued_at=22)
    grant=ring.make_grant(key,child,c.device_id,ages[c.device_id].recipient,issued_at=25)
    agent=AgentIdentity(child,account.account_id,issued_at=25)
    # The revocation arrives after the certificate was cached, but predates issuance.
    rs.publish_revocation(revocation(b.device_id,a,revoked_at=10)); ring.registry=rs.load()
    with pytest.raises(ValueError,match='revoked device'):
        if operation=='device': ring.registry.device_public(child.device_id)
        elif operation=='agent': ring.registry.verify_agent(agent.certificate())
        else: ring.open(grant,c.device_id,ages[c.device_id])


def test_p2_nonce_sas(tmp_path):
    device,storage,registry=fixture(tmp_path); join=JoinRequest(storage)
    approval=Approver(storage,device,registry,{}).begin(join.request_id)
    reveal=join.reveal(approval,registry)
    # Same commit/reveal: vary only the legitimate approver's nonce and signature.
    codes=set()
    for i in range(8):
        altered=signed('pair-approver',device._private,request_id=join.request_id,
            approver_device_id=device.device_id,Na=i.to_bytes(32,'big').hex(),commit_hash=digest(join.commit))
        codes.add(sas(join.commit,altered,reveal))
    # Multiple fixed inputs avoid a flaky single 1-in-a-million SAS collision.
    assert len(codes)>1
    assert any(code!=sas(join.commit,approval,reveal) for code in codes)


@pytest.mark.parametrize('evidence',['persisted','approval','reveal'])
def test_p5_reused_begin(tmp_path,evidence):
    account,device,other,c,ages,tree,ring,rs=setup(tmp_path)
    storage=rs.storage; registry=rs.load(); join=JoinRequest(storage)
    used={}; approver=Approver(storage,device,registry,used)
    approval=approver.begin(join.request_id)
    if evidence=='persisted':
        # The relay can remove transcript objects; the persisted local pin suffices.
        storage.root.joinpath(join.prefix+'approver.json').unlink()
        used=dict(used)
    elif evidence=='approval':
        # Another approver/process has no local pin but must respect the transcript.
        used={}; device=other
    else:
        join.reveal(approval,registry)
        storage.root.joinpath(join.prefix+'approver.json').unlink(); used={}
    before={path:storage.get(path) for path in storage.list('')}
    with pytest.raises(ValueError,match='^pairing_reused$'):
        Approver(storage,device,registry,used).begin(join.request_id)
    assert before=={path:storage.get(path) for path in storage.list('')}


def test_p6_commit_replay(tmp_path):
    device,storage,registry=fixture(tmp_path); join=JoinRequest(storage); other=JoinRequest(storage)
    approval=Approver(storage,device,registry,{}).begin(join.request_id)
    reveal=join.reveal(approval,registry)
    replay=Approver(storage,device,registry,{}).begin(other.request_id)
    replay=signed('pair-approver',device._private,**{k:(join.request_id if k=='request_id' else v)
        for k,v in replay.items() if k not in ('format','signature')})
    assert replay['request_id']==join.request_id and replay['commit_hash']!=digest(join.commit)
    with pytest.raises(ValueError,match='^pairing_mismatch$'): check_reveal(join.commit,replay,reveal)


def test_backdated_epoch(tmp_path):
    account,a,b,c,ages,tree,ring,rs=setup(tmp_path)
    old=tree.keys[tree.root.scope_id]; ring.epoch(old,a,created_at=3)
    ring.publish(old,a,b.device_id,ages[b.device_id].recipient,issued_at=4)
    rs.publish_revocation(revocation(b.device_id,a,revoked_at=10)); ring.registry=rs.load()
    current=tree.rotate(old.scope_id); ring.epoch(current,a,previous=old.key_id,created_at=11,reason='revoke')
    # Residual: self-declared time allows redistribution of an already held key.
    backdated=ring.make_grant(old,b,c.device_id,ages[c.device_id].recipient,issued_at=9)
    assert ring.open(backdated,c.device_id,ages[c.device_id]).key_id==old.key_id
    for parent,at in ((old.key_id,9),(current.key_id,9),(current.key_id,12)):
        key=ScopeReadKey.generate(old.profile_id,old.scope_id)
        epoch=signed('epoch',b._private,profile=key.profile_id,scope=key.scope_id,key_id=key.key_id,
            previous_key_id=parent,created_at=at,reason='rotate',issuer_device=b.device_id)
        ring.storage.put(epoch_path(epoch),wire(epoch))
        assert ring.current(old.profile_id,old.scope_id)['key_id']==current.key_id
