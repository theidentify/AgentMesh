import copy
import pytest
from phase4.test_keyring import setup
from agentmesh_log.connector import ConnectorRegistry, policy_record, secret_record, gateway_record, outbox_key_record, gateway_id
from agentmesh_log.registry_store import signed, revocation
from agentmesh_log.encoding import wire
from agentmesh_log.ulid import generate
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


def fixture(tmp_path):
    account,a,b,c,ages,tree,ring,registry=setup(tmp_path)
    child=next(s for s in tree.scopes.values() if s.parent)
    ring.publish(tree.keys[child.scope_id],a,b.device_id,ages[b.device_id].recipient,issued_at=4)
    cr=ConnectorRegistry(ring.storage,account.public_record(),list(tree.scopes.values()))
    p=policy_record(a,'ab'*16,'Claude','client',read=[{'profile':tree.root.scope_id,'scopes':None}],remember={'profile':tree.root.scope_id,'scope':child.scope_id},issued_at=5)
    cr.publish(p)
    return cr,p,a,b,c,tree,registry


def test_p6_3_widening_ignored(tmp_path):
    cr,p,a,b,c,tree,registry=fixture(tmp_path)
    narrow=policy_record(a,p['app_id'],'Claude','client',read=[{'profile':tree.root.scope_id,'scopes':[p['remember']['scope']]}],remember=p['remember'],issued_at=6)
    cr.publish(narrow)
    widened=signed('connector-policy',b._private,**{k:v for k,v in p.items() if k not in ('signature','format','issuer_device','issued_at')},issuer_device=b.device_id,issued_at=7)
    cr.publish(widened)
    assert cr.policy(p['app_id'],at=8)==narrow
    assert cr.policy(p['app_id'],at=5)==p


def test_p6_3_revoke_honoured(tmp_path):
    cr,p,a,b,c,tree,registry=fixture(tmp_path)
    revoked=signed('connector-policy',b._private,**{k:v for k,v in p.items() if k not in ('signature','format','issuer_device','issued_at','state')},issuer_device=b.device_id,issued_at=6,state='revoked')
    cr.publish(revoked)
    assert cr.policy(p['app_id'],at=7)['state']=='revoked'
    # A write-only device cannot revoke, even with a valid device signature.
    cr.publish(signed('connector-policy',c._private,**{k:v for k,v in p.items() if k not in ('signature','format','issuer_device','issued_at')},issuer_device=c.device_id,issued_at=8))
    assert cr.policy(p['app_id'],at=9)==revoked


def test_p6_4_window_validation(tmp_path):
    cr,p,a,b,c,tree,registry=fixture(tmp_path)
    cr.publish(secret_record(b,p['app_id'],10,100))
    assert cr.window(p['app_id'],at=11) is None
    valid=secret_record(a,p['app_id'],10,100); cr.publish(valid)
    assert cr.window(p['app_id'],at=11)==valid
    assert cr.window(p['app_id'],at=100) is None
    bad=copy.deepcopy(valid); bad['not_after']=200; cr.publish(bad)
    assert cr.window(p['app_id'],at=150) is None
    too_long=secret_record(a,p['app_id'],200,3600201)
    with pytest.raises(ValueError): cr.publish(too_long)
    registry.publish_revocation(revocation(a.device_id,b,revoked_at=50))
    assert cr.window(p['app_id'],at=51) is None


def test_p6_pins(tmp_path):
    cr,p,a,b,c,tree,registry=fixture(tmp_path)
    key=Ed25519PrivateKey.generate(); record=gateway_record(a,key.public_key().public_bytes_raw(),'https://example.workers.dev',issued_at=5)
    cr.publish(record)
    assert cr.gateway(gateway_id(record),at=6)==record
    out=X25519PrivateKey.generate(); record=outbox_key_record(a,out.public_key().public_bytes_raw(),issued_at=5); cr.publish(record)
    assert cr.outbox_keys(at=6)==[record]
    registry.publish_revocation(revocation(a.device_id,b,revoked_at=7))
    assert cr.outbox_keys(at=8)==[]


def test_p6_5_gateway_rotation(tmp_path):
    cr,p,a,b,c,tree,registry=fixture(tmp_path)
    old=gateway_record(a,Ed25519PrivateKey.generate().public_key().public_bytes_raw(),'https://example.workers.dev',issued_at=6);cr.publish(old)
    new=gateway_record(a,Ed25519PrivateKey.generate().public_key().public_bytes_raw(),'https://example.workers.dev',issued_at=8);cr.publish(new)
    assert cr.gateway(gateway_id(old),at=7)==old
    with pytest.raises(ValueError,match='denied'):cr.gateway(gateway_id(old),at=9)
    assert cr.gateway(gateway_id(new),at=9)==new
