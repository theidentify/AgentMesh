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
    def sync(*args):calls.append(1);return original(*args)
    monkeypatch.setattr(keyring,'sync_control',sync)
    b.clock=lambda:ticks[0]
    def call():
        raw=wire({'tool':'recall','arguments':{'query':'marker'}})
        return b.handle(raw,request_headers(k,pin,p['app_id'],raw,ts=ticks[0]))
    assert 'data' in call();assert 'data' in call();assert len(calls)==1
    cr.publish(signed('connector-policy',s.load().device._private,**{a:v for a,v in p.items() if a not in ('format','signature','state','issued_at')},state='revoked',issued_at=now()))
    assert 'data' in call();assert len(calls)==1
    ticks[0]+=15001
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
            argv=plistlib.loads(data)['ProgramArguments']
            assert argv[-8:]==['--s3-bucket','bucket','--s3-prefix','v1/','--s3-endpoint','https://s3.example','--s3-credentials','keychain:agentmesh-r2']
        else:
            assert b'--s3-bucket bucket --s3-prefix v1/ --s3-endpoint https://s3.example --s3-credentials keychain:agentmesh-r2' in data
