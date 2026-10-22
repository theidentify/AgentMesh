import json
import subprocess
from pathlib import Path
import pytest
from phase6_support import device
from test_phase6_records import fixture
from agentmesh_log.bridge import STATUS_APP, request_headers
from agentmesh_log.connector import secret_record
from agentmesh_log.encoding import wire, parse
from agentmesh_log.registry_store import signed, now, revocation
from agentmesh_log.broker.connectors import provision


def call(b, key, pin, args, app=STATUS_APP):
    raw=wire({'tool':'admin','arguments':args})
    return b.handle(raw,request_headers(key,pin,app,raw,ts=b.clock()))


def test_p6_s11_device_actions(tmp_path):
    h,s,storage,cr,p,key,pin,broker,b=device(tmp_path)
    try:
        args={'action':'reject','code_hash':'cd'*32}
        result=call(b,key,pin,args)
        assert result['action']=='reject' and result['payload']=={'code_hash':'cd'*32}
        assert result['format']=='agentmesh-connector-admin-v1'
        assert call(b,key,pin,args,p['app_id'])=={'error':'denied'}
        for args in ({'action':'approve','code_hash':'cd'*32},{'action':'reject','code_hash':'cd'*32,'app_id':p['app_id']},{'action':'revoke','app_id':'ff'*16},{'action':'close_secret','app_id':p['app_id']},{'action':'revoke','app_id':p['app_id'],'state':'active'}):
            assert call(b,key,pin,args)=={'error':'denied'}
        assert cr.policy(p['app_id'])==p
        at=now();cr.publish(secret_record(s.load().device,p['app_id'],at,at+60000))
        path=provision(h,s,p);client=b.client_factory(b.bridge_key)
        assert client.request({'op':'connector_window','app_id':p['app_id']})=={'status':'ok'}
        assert any(g.allow_secret for g in s.load().grants.values())
        ordinary=b.client_factory(path)
        assert ordinary.request({'op':'connector_close_secret','app_id':p['app_id']})=={'error':'denied'}
        result=call(b,key,pin,{'action':'close_secret','app_id':p['app_id']})
        assert result=={'status':'ok'}
        assert cr.window(p['app_id']) is None
        assert client.request({'op':'connector_window','app_id':p['app_id']})=={'error':'denied'}
        assert all(not g.allow_secret and g.secret_until==0 for g in s.load().grants.values())
        result=call(b,key,pin,{'action':'revoke','app_id':p['app_id']})
        assert result['action']=='revoke'
        assert cr.policy(p['app_id'])['state']=='revoked'
        rows=[parse(x) for x in (h.root/'audit.jsonl').read_bytes().splitlines()]
        for action in ('reject','revoke','close_secret'):
            assert any(r['op']=='connector_'+action and r['outcome']=='ok' for r in rows)
        assert any(r['op']=='connector_revoke' and r['outcome']=='denied' for r in rows)
        assert all(r['query_chars']==0 for r in rows)
    finally: broker.close()


def test_p6_s12_close_cross_verifier(tmp_path):
    cr,p,a,b,c,tree,registry=fixture(tmp_path)
    w=secret_record(a,p['app_id'],10,100);cr.publish(w)
    def close(issuer,at):
        return signed('connector-secret-close',issuer._private,app_id=p['app_id'],closed_at=at,issuer_device=issuer.device_id)
    def check(expected,at=20):
        assert (cr.window(p['app_id'],at=at) is None)==expected
        payload={'account':cr.account,'app':p['app_id'],'at':at,'records':{path:json.loads(cr.storage.get(path)) for path in cr.storage.list('')}}
        result=subprocess.run(['node','tests/close-vector.mjs'],cwd=Path(__file__).parents[1]/'gateway',input=json.dumps(payload),text=True,capture_output=True,check=True)
        assert json.loads(result.stdout)==expected
    check(False)
    cr.publish(close(c,100));check(False)  # exclusive end
    cr.publish(close(c,9));check(False)  # before window
    cr.publish(close(c,15));check(False,at=14);check(True)  # write-only may close
    check(True,at=100);check(True,at=200)  # never extends an expired window
    registry.publish_revocation(revocation(c.device_id,a,revoked_at=18))
    check(True)  # later revocation cannot undo a valid past reduction
    # A separate window is unaffected by earlier closes; a revoked signer cannot close it.
    cr.publish(secret_record(a,p['app_id'],30,100))
    cr.publish(close(c,35));check(False,at=40)
    # An out-of-range close cannot reopen a window that was already closed.
    cr.publish(close(b,35));check(True,at=40)
    cr.publish(close(b,200));check(True,at=40);check(True,at=200)
    # Close records alone cannot open a window for another app.
    other='ef'*16
    cr.publish(signed('connector-secret-close',b._private,app_id=other,closed_at=35,issuer_device=b.device_id))
    assert cr.window(other,at=40) is None
    payload={'account':cr.account,'app':other,'at':40,'records':{path:json.loads(cr.storage.get(path)) for path in cr.storage.list('')}}
    result=subprocess.run(['node','tests/close-vector.mjs'],cwd=Path(__file__).parents[1]/'gateway',input=json.dumps(payload),text=True,capture_output=True,check=True)
    assert json.loads(result.stdout) is True



@pytest.mark.parametrize('args',[
    {'action':'approve','code_hash':'cd'*32},
    {'action':'allow_secret','app_id':'ab'*16},
    {'action':'revoke','app_id':'ab'*16,'read':[]},
    {'action':'close_secret','app_id':'ab'*16,'not_after':9999999999999},
    {'action':'reject','code_hash':'cd'*32,'policy_ref':'anything'},
    {'action':'reject','code_hash':'not-a-hash'},
],ids=['approve','open','widen','extend','extra','bad-hash'])
def test_p6_s18_no_elevation(tmp_path,args):
    h,s,storage,cr,p,key,pin,broker,b=device(tmp_path)
    try:
        before={path:storage.get(path) for path in storage.list('connectors/')}
        assert call(b,key,pin,args)=={'error':'denied'}
        assert {path:storage.get(path) for path in storage.list('connectors/')}==before
        assert cr.window(p['app_id']) is None
    finally: broker.close()


def test_p6_s19_admin_transport(tmp_path):
    h,s,storage,cr,p,key,pin,broker,b=device(tmp_path)
    try:
        raw=wire({'tool':'admin','arguments':{'action':'reject','code_hash':'cd'*32}})
        headers=request_headers(key,pin,STATUS_APP,raw)
        assert b.handle(raw,headers)['action']=='reject'
        assert b.handle(raw,headers)=={'error':'denied'}
        for extra in ({'x-agentmesh-ts':'1'},{'x-agentmesh-signature':'00'*64},{'x-agentmesh-app':p['app_id']}):
            assert b.handle(raw,{**request_headers(key,pin,STATUS_APP,raw),**extra})=={'error':'denied'}
    finally: broker.close()


def test_p6_s20_close_publish_failure(tmp_path):
    h,s,storage,cr,p,key,pin,broker,b=device(tmp_path)
    try:
        at=now();cr.publish(secret_record(s.load().device,p['app_id'],at,at+60000))
        provision(h,s,p);client=b.client_factory(b.bridge_key)
        assert client.request({'op':'connector_window','app_id':p['app_id']})=={'status':'ok'}
        class Offline:
            def put(self,*args): raise OSError('offline')
        b.storage=Offline();b.controls=lambda **kwargs:cr
        assert call(b,key,pin,{'action':'close_secret','app_id':p['app_id']})=={'error':'denied'}
        assert cr.window(p['app_id']) is None
        assert all(not g.allow_secret and g.secret_until==0 for g in s.load().grants.values())
    finally: broker.close()


def test_p6_s23_write_only_bridge_close(tmp_path):
    from copy import copy
    from agentmesh_log.identity import DeviceIdentity
    from agentmesh_log.agekeys import DeviceAgeIdentity
    from agentmesh_log.registry_store import RegistryStore, device_record
    h,s,storage,cr,p,key,pin,broker,b=device(tmp_path)
    try:
        state=copy(s.load());issuer=state.device;closer=DeviceIdentity()
        RegistryStore(storage,state.account.public_record()).publish_device(device_record(
            closer,closer.certificate(state.account,issuer=issuer),DeviceAgeIdentity()))
        state.device=closer
        class ClosingStore:
            def load(self): return state
        b.store=ClosingStore()
        registry,ring=cr.context()
        assert not cr.reader(ring,closer.device_id,now())
        at=now();cr.publish(secret_record(issuer,p['app_id'],at,at+60000))
        assert call(b,key,pin,{'action':'close_secret','app_id':p['app_id']})=={'status':'ok'}
        assert cr.window(p['app_id']) is None
    finally: broker.close()
