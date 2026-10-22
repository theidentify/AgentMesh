import hashlib
import threading
import pytest
from agentmesh_log.bridge import Bridge, request_headers, bind_server
from agentmesh_log.connector import gateway_record
from agentmesh_log.encoding import wire
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_phase6_records import fixture


def make(tmp_path):
    cr,p,a,b,c,tree,reg=fixture(tmp_path)
    key=Ed25519PrivateKey.generate(); pin=gateway_record(a,key.public_key().public_bytes_raw(),'https://example.workers.dev',issued_at=5); cr.publish(pin)
    bridge=Bridge.__new__(Bridge); bridge.controls=lambda:cr; bridge.clock=lambda:100; bridge.nonces={}; bridge.lock=threading.RLock()
    bridge.invoke=lambda app,body,**kwargs:{'status':'ok'}
    return bridge,key,p,pin


def test_p6_5_transport(tmp_path):
    b,k,p,pin=make(tmp_path); raw=wire({'tool':'whoami','arguments':{}})
    def headers(**kwargs): return request_headers(k,pin,p['app_id'],raw,ts=100,**kwargs)
    assert b.handle(raw,headers(nonce='01'*16))=={'status':'ok'}
    assert b.handle(raw,headers(nonce='01'*16))=={'error':'denied'}
    assert b.handle(raw,{})=={'error':'denied'}
    for field,value in [('x-agentmesh-app','cd'*16),('x-agentmesh-ts','60101'),('x-agentmesh-signature','00'*64),('x-agentmesh-key','00'*32)]:
        h=headers(nonce='02'*16); h[field]=value
        assert b.handle(raw,h)=={'error':'denied'}
    assert b.handle(raw+b' ',headers(nonce='03'*16))=={'error':'denied'}
    old=request_headers(k,pin,p['app_id'],raw,ts=60101,nonce='04'*16)
    assert b.handle(raw,old)=={'error':'denied'}


def test_p6_5_loopback_logs(tmp_path,capsys):
    b,k,p,pin=make(tmp_path)
    for address in ('0.0.0.0','::1','localhost','192.0.2.1'):
        with pytest.raises(ValueError,match='loopback_required'): bind_server(b,address,0)
    raw=b'private-body-marker'
    assert b.handle(raw,{})=={'error':'denied'}
    assert 'private-body-marker' not in ''.join(capsys.readouterr())


@pytest.mark.parametrize('profile,allowed',[('personal',True),('work',False),('unknown',False)],ids=['allowed','denied','unknown'])
def test_p6_3_profile_names(tmp_path,profile,allowed):
    from phase6_support import device
    h,s,storage,cr,p,k,pin,broker,b=device(tmp_path)
    calls=[]; factory=b.client_factory
    def client(path):
        calls.append(1)
        if not allowed: raise AssertionError('broker contacted')
        return factory(path)
    b.client_factory=client
    try:
        result=b.invoke(p['app_id'],{'tool':'recall','arguments':{'query':'marker','profile':profile}})
        if allowed: assert 'data' in result and calls==[1]
        else: assert result=={'error':'denied'} and calls==[]
    finally: broker.close()
