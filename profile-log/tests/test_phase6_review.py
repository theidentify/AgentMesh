"""Independent Phase 6 boundaries found by reviewer mutation testing."""
import copy
import errno
import threading
import urllib.request
import pytest
from phase6_support import device, DirectClient
from agentmesh_log.bridge import Bridge, bind_server, request_headers
from agentmesh_log.encoding import wire, parse
from agentmesh_log.registry_store import signed, now
from agentmesh_log.storage.filesystem import FilesystemStorage


def test_p6_3_bridge_spy(tmp_path):
    h,s,storage,cr,p,k,pin,broker,b=device(tmp_path)
    def forbidden(*args): raise AssertionError('broker contacted')
    b.client_factory=forbidden
    work=s.load().trees[1].root.scope_id
    assert b.invoke(p['app_id'],{'tool':'recall','arguments':{'query':'marker','profile':work}})=={'error':'denied'}
    historical=copy.deepcopy(p);historical['remember']={'profile':work,'scope':work}
    assert b.invoke(p['app_id'],{'tool':'remember','arguments':{'body':'marker'}},policy=historical)=={'error':'denied'}
    broker.close()


def test_p6_3_provision_boundary(tmp_path):
    from agentmesh_log.broker.connectors import provision
    h,s,storage,cr,p,k,pin,broker,b=device(tmp_path)
    p=copy.deepcopy(p);p['read'][0]['scopes']=[p['remember']['scope']]
    client=DirectClient(provision(h,s,p),broker)
    assert 'error' not in client.request({'op':'recall','query':'marker','scopes':['personal/inbox']})
    assert client.request({'op':'recall','query':'marker','profiles':['work']})=={'error':'denied'}
    assert client.request({'op':'recall','query':'marker','scopes':['personal/common']})=={'error':'denied'}
    lines=[parse(x) for x in (h.root/'audit.jsonl').read_bytes().splitlines()]
    assert lines and all(r['agent_id']==client.agent.agent_id for r in lines)
    broker.close()


def test_p6_5_http_logs(tmp_path,capfd,caplog):
    h,s,storage,cr,p,k,pin,broker,b=device(tmp_path)
    try: server=bind_server(b,'127.0.0.1',0)
    except OSError as exc:
        broker.close()
        if exc.errno in (errno.EPERM,errno.EACCES):pytest.skip('P6-5 real HTTP logs: sandbox denies loopback binding')
        raise
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        raw=wire({'tool':'recall','arguments':{'query':'private-http-log-marker'}})
        for valid in (True,False):
            headers=request_headers(k,pin,p['app_id'],raw)
            if not valid:headers['x-agentmesh-signature']='00'*64
            req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/bridge',data=raw,headers=headers)
            try:
                with urllib.request.urlopen(req,timeout=5) as response:response.read()
            except urllib.error.HTTPError as exc:assert not valid and exc.code==403
        captured=capfd.readouterr()
        assert 'private-http-log-marker' not in captured.out+captured.err+caplog.text
    finally:server.shutdown();server.server_close();thread.join(5);broker.close()


def test_p6_sync_private(tmp_path):
    from agentmesh_log.keyring import sync_control
    h,s,local,cr,p,k,pin,broker,b=device(tmp_path)
    remote=FilesystemStorage(tmp_path/'remote')
    local.put('connectors/gateway-state/local',b'{}');remote.put('connectors/gateway-state/remote',b'{}')
    sync_control(h,s,remote)
    assert list(local.list('connectors/gateway-state/'))==['connectors/gateway-state/local']
    assert list(remote.list('connectors/gateway-state/'))==['connectors/gateway-state/remote']
    broker.close()


def test_p6_7_sync_interval(tmp_path,monkeypatch):
    from agentmesh_log import keyring
    h,s,remote,cr,p,k,pin,broker,b=device(tmp_path,remote_root=tmp_path/'remote')
    ticks=[now()];calls=[];original=keyring.sync_control
    def sync(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(keyring,'sync_control',sync)
    b.clock=lambda:ticks[0]
    def call():
        raw=wire({'tool':'recall','arguments':{'query':'marker'}})
        return b.handle(raw,request_headers(k,pin,p['app_id'],raw,ts=ticks[0]))
    b.controls(force=True)
    assert 'data' in call();assert 'data' in call();assert len(calls)==1
    cr.publish(signed('connector-policy',s.load().device._private,**{a:v for a,v in p.items() if a not in ('format','signature','state','issued_at')},state='revoked',issued_at=now()))
    assert 'data' in call();assert len(calls)==1
    ticks[0]+=15001
    assert 'data' in call();assert len(calls)==1
    b.controls(force=True)
    assert call()=={'error':'denied'};assert len(calls)==2
    b.drain();assert len(calls)==3
    broker.close()


def test_p6_service_remote(tmp_path):
    import plistlib
    from agentmesh_log.cli import parser
    from agentmesh_log.connector_cli import execute
    for platform in ('macos','windows'):
        args=parser().parse_args(['bridge','render-service','--home',str(tmp_path/'home'),'--output',str(tmp_path/platform),'--python',str(tmp_path/'python'),'--platform',platform,'--s3-bucket','bucket','--s3-prefix','v1/','--s3-endpoint','https://s3.example','--s3-credentials','keychain:agentmesh-r2'])
        result=execute(args,backend=None,console=None)
        from pathlib import Path
        data=Path(result['rendered']).read_bytes()
        if platform=='macos':
            value=plistlib.loads(data); argv=value['ProgramArguments']
            assert value['StandardOutPath']==str(tmp_path/'logs'/'bridge.out.log')
            assert value['StandardErrorPath']==str(tmp_path/'logs'/'bridge.err.log')
            assert argv[-8:]==['--s3-bucket','bucket','--s3-prefix','v1/','--s3-endpoint','https://s3.example','--s3-credentials','keychain:agentmesh-r2']
        else:
            assert b'--s3-bucket bucket --s3-prefix v1/ --s3-endpoint https://s3.example --s3-credentials keychain:agentmesh-r2' in data


@pytest.mark.parametrize('tool', ['recall','whoami'],ids=['recall','whoami'])
def test_p6_request_local(tmp_path,tool):
    import time
    h,s,remote,cr,p,k,pin,broker,b=device(tmp_path,remote_root=tmp_path/'remote')
    b.controls(force=True)
    class Unavailable:
        calls=0
        def fail(self,*args,**kwargs):
            self.calls+=1
            raise RuntimeError('remote unavailable')
        list=get=put=fail
    b.storage=Unavailable(); b.last_control_sync=0
    try:
        raw=wire({'tool':tool,'arguments':{'query':'marker'} if tool=='recall' else {}})
        start=time.monotonic()
        assert 'error' not in b.handle(raw,request_headers(k,pin,p['app_id'],raw))
        assert time.monotonic()-start<1
        assert b.storage.calls==0
    finally: broker.close()


def test_p6_background_sync(tmp_path,monkeypatch):
    from agentmesh_log import bridge,keyring
    h,s,remote,cr,p,k,pin,broker,b=device(tmp_path,remote_root=tmp_path/'remote')
    b.sync_interval=20
    served=threading.Event(); refreshed=threading.Event(); release=threading.Event(); calls=[]
    original=keyring.sync_control
    def sync(*args,**kwargs):
        calls.append(1)
        if len(calls)==1:
            assert release.wait(2)
            raise RuntimeError('private-sync-marker')
        result=original(*args,**kwargs); refreshed.set(); return result
    class Server:
        def serve_forever(self): served.set(); b.stopped.wait(3)
        def server_close(self): pass
    monkeypatch.setattr(bridge,'bind_server',lambda *args:Server())
    monkeypatch.setattr(keyring,'sync_control',sync)
    monkeypatch.setattr(b,'drain',lambda:None)
    thread=threading.Thread(target=b.serve,daemon=True);thread.start()
    try:
        assert served.wait(1)
        assert len(calls)==1
        cr.publish(signed('connector-policy',s.load().device._private,**{a:v for a,v in p.items() if a not in ('format','signature','state','issued_at')},state='revoked',issued_at=now()))
        release.set(); assert refreshed.wait(2)
        raw=wire({'tool':'whoami','arguments':{}})
        assert b.handle(raw,request_headers(k,pin,p['app_id'],raw))=={'error':'denied'}
        assert len(calls)>=2
    finally: release.set(); b.stopped.set(); thread.join(2); broker.close()


@pytest.mark.parametrize('prefix',['registry/','outbox/'],ids=['control','outbox'])
def test_p6_drain_concurrent(tmp_path,monkeypatch,prefix):
    from agentmesh_log import bridge
    h,s,remote,cr,p,k,pin,broker,b=device(tmp_path,remote_root=tmp_path/'remote')
    entered=threading.Event();release=threading.Event();served=threading.Event();finished=threading.Event()
    original=remote.list
    def listing(value,**kwargs):
        if value==prefix:
            entered.set(); assert release.wait(3)
        return original(value,**kwargs)
    monkeypatch.setattr(remote,'list',listing)
    class Server:
        def serve_forever(self): served.set(); b.stopped.wait(4)
        def server_close(self): pass
    monkeypatch.setattr(bridge,'bind_server',lambda *args:Server())
    thread=threading.Thread(target=b.serve,daemon=True);thread.start()
    response=[]
    def request():
        raw=wire({'tool':'recall','arguments':{'query':'marker'}})
        with b.lock: response.append(b.handle(raw,request_headers(k,pin,p['app_id'],raw)))
        finished.set()
    worker=threading.Thread(target=request,daemon=True)
    try:
        assert served.wait(1); assert entered.wait(1)
        worker.start(); assert finished.wait(.9)
        assert 'data' in response[0]
    finally:
        release.set(); b.stopped.set(); thread.join(2)
        if worker.ident: worker.join(2)
        broker.close()
