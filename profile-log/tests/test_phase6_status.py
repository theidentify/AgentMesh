"""P6-S bridge metadata operation gates."""
import pytest
from agentmesh_log.bridge import request_headers
from agentmesh_log.encoding import wire
from phase6_support import device

RESERVED='0'*32


def test_p6_s6_metadata(tmp_path,monkeypatch):
    home,store,storage,controls,p,key,pin,broker,bridge=device(tmp_path)
    def forbidden(*args,**kwargs):
        pytest.fail('status must not resolve policies or create a broker client')
    bridge.client_factory=forbidden
    monkeypatch.setattr(type(controls),'policy',forbidden)
    body=wire({'tool':'status','arguments':{}})
    result=bridge.handle(body,request_headers(key,pin,RESERVED,body))
    assert set(result)=={'device_id','now_ms','last_control_sync_ms','last_drain_ms','last_drain_result','outbox_cursor_ms'}
    assert result['device_id']==store.load().device.device_id
    assert result['last_drain_ms'] is None
    assert result['last_drain_result']=={'stored':0,'rejected':0,'errors':0}


@pytest.mark.parametrize('tool,app,args',[('whoami',RESERVED,{}),('recall',RESERVED,{'query':'x'}),('remember',RESERVED,{'body':'x'}),('status','ab'*16,{}),('status',RESERVED,{'extra':True})],ids=['whoami','recall','remember','real-app','arguments'])
def test_p6_s7_misuse(tmp_path,monkeypatch,tool,app,args):
    home,store,storage,controls,p,key,pin,broker,bridge=device(tmp_path)
    def forbidden(*a,**k): pytest.fail('reserved app or status must never reach policy resolution or the broker')
    # The reserved ID and the status tool are refused before any policy lookup.
    bridge.client_factory=forbidden; monkeypatch.setattr(type(controls),'policy',forbidden)
    body=wire({'tool':tool,'arguments':args})
    assert bridge.handle(body,request_headers(key,pin,app,body))=={'error':'denied'}


def test_p6_s8_auth_drain(tmp_path):
    *_,key,pin,broker,bridge=device(tmp_path)
    body=wire({'tool':'status','arguments':{}})
    headers=request_headers(key,pin,RESERVED,body)
    assert 'error' not in bridge.handle(body,headers)
    assert bridge.handle(body,headers)=={'error':'denied'}
    assert bridge.handle(body,request_headers(key,pin,RESERVED,body,ts=1))=={'error':'denied'}
    bad=request_headers(key,pin,RESERVED,body);bad['x-agentmesh-signature']='00'*64
    assert bridge.handle(body,bad)=={'error':'denied'}
    assert bridge.handle(body+b' ',request_headers(key,pin,RESERVED,body))=={'error':'denied'}
    result=bridge.drain()
    status=bridge.handle(body,request_headers(key,pin,RESERVED,body))
    assert status['last_drain_result']==result
    assert isinstance(status['last_drain_ms'],int)
    assert isinstance(status['outbox_cursor_ms'],int)
