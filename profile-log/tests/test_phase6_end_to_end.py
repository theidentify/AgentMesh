"""P6-1..P6-8: Node Worker against a real Python device, broker IPC and loopback bridge."""
import errno
import os
import queue
import json
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
import pytest
from phase6_support import device
from agentmesh_log.bridge import bind_server
from agentmesh_log.broker.transport import UnixServer,PipeServer
from agentmesh_log.encoding import wire
from agentmesh_log.connector import secret_record
from agentmesh_log.registry_store import now
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption

DRIVER=Path(__file__).resolve().parents[1]/'gateway/tests/e2e-driver.mjs'


@pytest.mark.parametrize('prefix',['','v1/'],ids=['root','v1'])
def test_p6_e2e(prefix):
    if not shutil.which('node'):pytest.skip('Phase 6 e2e requires node')
    if not (DRIVER.parents[1]/'node_modules').exists():pytest.skip('Phase 6 e2e requires gateway/node_modules')
    assert DRIVER.exists(),'Node e2e driver must exist'
    with tempfile.TemporaryDirectory(prefix='amp6',dir='/tmp' if os.name!='nt' else None) as temporary:
        root=Path(temporary).resolve()
        try:
            probe=socket.socket();probe.bind(('127.0.0.1',0));probe.close()
        except OSError as exc:
            if exc.errno in (errno.EPERM,errno.EACCES):pytest.skip('Phase 6 e2e: sandbox denies loopback binding; reviewer must rerun outside sandbox')
            raise
        home,store,storage,cr,p,key,pin,broker,bridge=device(root,native=True,remote_root=root/'bucket'/prefix if prefix else None)
        try:server=(PipeServer if os.name=='nt' else UnixServer)(home.address(store.load().device.device_id))
        except OSError as exc:
            broker.close()
            if exc.errno in (errno.EPERM,errno.EACCES):pytest.skip('Phase 6 e2e: sandbox denies broker IPC binding')
            raise
        thread=threading.Thread(target=lambda:broker.serve(server=server),daemon=True);thread.start()
        http=bind_server(bridge,'127.0.0.1',0);http_thread=threading.Thread(target=http.serve_forever,daemon=True);http_thread.start()
        process=subprocess.Popen(['node',str(DRIVER)],cwd=DRIVER.parents[1],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        def command(value):
            process.stdin.write(json.dumps(value)+'\n');process.stdin.flush()
            replies=queue.Queue()
            threading.Thread(target=lambda:replies.put(process.stdout.readline()),daemon=True).start()
            line=replies.get(timeout=20)
            result=json.loads(line);assert 'driver_error' not in result,'Node e2e driver failed';return result
        try:
            state=store.load()
            result=command({'op':'init','prefix':prefix,'directory':str(root/'bucket' if prefix else home.root/'replica'),'account':state.account.public_record(),'device_id':state.device.device_id,'device_key':state.device.private_bytes().hex(),'gateway_key':key.private_bytes(Encoding.DER,PrivateFormat.PKCS8,NoEncryption()).hex(),'gateway_id':__import__('agentmesh_log.connector',fromlist=['gateway_id']).gateway_id(pin),'bridge':f'http://127.0.0.1:{http.server_port}','read':p['read'],'remember':p['remember']})
            app=result['app_id']
            assert command({'op':'tool','tool':'remember','arguments':{'body':'live-e2e-marker'}})['status']=='stored'
            assert command({'op':'tool','tool':'recall','arguments':{'query':'live-e2e-marker'}})['data']['results']
            http.shutdown();http.server_close();http_thread.join(5)
            assert command({'op':'tool','tool':'recall','arguments':{'query':'live-e2e-marker'}})=={'error':'agentmesh_offline'}
            assert command({'op':'tool','tool':'remember','arguments':{'body':'queued-e2e-marker'}})['status']=='queued'
            assert bridge.drain()['stored']==1
            http=bind_server(bridge,'127.0.0.1',0);http_thread=threading.Thread(target=http.serve_forever,daemon=True);http_thread.start()
            command({'op':'route','bridge':f'http://127.0.0.1:{http.server_port}'})
            assert command({'op':'tool','tool':'recall','arguments':{'query':'queued-e2e-marker'}})['data']['results']
            command({'op':'tool','tool':'remember','arguments':{'body':'secret-e2e-marker','label':'secret'}})
            assert not command({'op':'tool','tool':'recall','arguments':{'query':'secret-e2e-marker'}})['data']['results']
            # Wide enough for slow CI runners (Windows) to recall inside the window.
            start=now();cr.publish(secret_record(state.device,app,start,start+5000));bridge.controls(force=True)
            assert command({'op':'tool','tool':'recall','arguments':{'query':'secret-e2e-marker'}})['data']['results']
            import time
            while now()<=start+5000: time.sleep(0.1)
            assert not command({'op':'tool','tool':'recall','arguments':{'query':'secret-e2e-marker'}})['data']['results']
            assert command({'op':'revoke'})['ok'];bridge.controls(force=True)
            assert command({'op':'tool','tool':'recall','arguments':{'query':'live-e2e-marker'}})['error'] in ('denied','invalid_token')
            assert command({'op':'worker_check_disabled','tool':'recall','arguments':{'query':'live-e2e-marker'}})=={'error':'denied'}
            if prefix:
                assert {p.relative_to(root/'bucket').parts[0] for p in (root/'bucket').rglob('*')}=={'v1'}
            for path in storage.list(''):
                raw=storage.get(path)
                assert all(marker not in raw for marker in (b'live-e2e-marker',b'queued-e2e-marker',b'secret-e2e-marker'))
        finally:
            process.terminate();process.communicate(timeout=10);http.shutdown();http.server_close();broker.close();thread.join(5)


def test_p6_e2e_inprocess(tmp_path):
    run_inprocess(tmp_path)


def test_p6_e2e_prefix(tmp_path):
    """P6-1/P6-6: actual Node Worker and bridge share only bucket/v1/."""
    run_inprocess(tmp_path,prefix='v1/')


def run_inprocess(tmp_path,*,prefix=''):
    """Same Node Worker and Python device without native socket permissions."""
    import base64
    import time
    from agentmesh_log.connector import gateway_id
    if not shutil.which('node'):pytest.skip('Phase 6 e2e requires node')
    if not (DRIVER.parents[1]/'node_modules').exists():pytest.skip('Phase 6 e2e requires gateway/node_modules')
    home,store,storage,cr,p,key,pin,broker,bridge=device(tmp_path,remote_root=tmp_path/'bucket'/prefix if prefix else None)
    process=subprocess.Popen(['node',str(DRIVER)],cwd=DRIVER.parents[1],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    online=True
    def send(value):process.stdin.write(json.dumps(value)+'\n');process.stdin.flush()
    replies=queue.Queue()
    def read_loop():
        for line in process.stdout:replies.put(line)
    threading.Thread(target=read_loop,daemon=True).start()
    def command(value):
        send(value)
        while True:
            result=json.loads(replies.get(timeout=20))
            assert 'driver_error' not in result,'Node e2e driver failed'
            if 'bridge_request' not in result:return result
            req=result['bridge_request']
            response=bridge.handle(base64.b64decode(req['body']),req['headers']) if online else {'error':'agentmesh_offline'}
            send({'op':'bridge_response','id':req['id'],'body':response,'status':(403 if 'error' in response else 200) if online else 502})
    try:
        state=store.load()
        initialized=command({'op':'init','ipc':True,'prefix':prefix,'directory':str(tmp_path/'bucket' if prefix else home.root/'replica'),'account':state.account.public_record(),'device_id':state.device.device_id,'device_key':state.device.private_bytes().hex(),'gateway_key':key.private_bytes(Encoding.DER,PrivateFormat.PKCS8,NoEncryption()).hex(),'gateway_id':gateway_id(pin),'bridge':'https://bridge.example','read':p['read'],'remember':p['remember']})
        app=initialized['app_id']
        assert command({'op':'tool','tool':'remember','arguments':{'body':'live-e2e-marker'}})['status']=='stored'
        assert command({'op':'tool','tool':'recall','arguments':{'query':'live-e2e-marker'}})['data']['results']
        online=False
        assert command({'op':'tool','tool':'recall','arguments':{'query':'live-e2e-marker'}})=={'error':'agentmesh_offline'}
        assert command({'op':'tool','tool':'remember','arguments':{'body':'queued-e2e-marker'}})['status']=='queued'
        online=True;assert bridge.drain()['stored']==1
        assert command({'op':'tool','tool':'recall','arguments':{'query':'queued-e2e-marker'}})['data']['results']
        assert command({'op':'tool','tool':'remember','arguments':{'body':'secret-e2e-marker','label':'secret'}})['status']=='stored'
        before=command({'op':'tool','tool':'recall','arguments':{'query':'secret-e2e-marker'}})
        assert before['data']['results']==[]
        start=now();cr.publish(secret_record(state.device,app,start,start+5000));bridge.controls(force=True)
        assert command({'op':'tool','tool':'recall','arguments':{'query':'secret-e2e-marker'}})['data']['results']
        while now()<=start+5000: time.sleep(0.1)
        assert command({'op':'tool','tool':'recall','arguments':{'query':'secret-e2e-marker'}})==before
        assert command({'op':'revoke'})['ok'];bridge.controls(force=True)
        assert command({'op':'tool','tool':'recall','arguments':{'query':'live-e2e-marker'}})['error'] in ('denied','invalid_token')
        assert command({'op':'worker_check_disabled','tool':'recall','arguments':{'query':'live-e2e-marker'}})=={'error':'denied'}
        if prefix:
            bucket=tmp_path/'bucket'
            assert {path.parts[0] for path in (p.relative_to(bucket) for p in bucket.rglob('*'))}=={'v1'}
            assert all(path.split('/')[0] in {'registry','keyring','keys','profiles','alerts','connectors','outbox','events','archive'} for path in storage.list(''))
        for path in storage.list(''):
            assert all(marker not in storage.get(path) for marker in (b'live-e2e-marker',b'queued-e2e-marker',b'secret-e2e-marker'))
    finally:
        process.terminate();process.communicate(timeout=10);broker.close()
