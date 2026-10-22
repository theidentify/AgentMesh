import json
import os
import threading
import time
import pytest
from agentmesh_log.broker import service
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.storage import StorageError
from agentmesh_log.storage.filesystem import FilesystemStorage
from .support import setup_home, remember, recall

SECRET='fedc9876'*8


class Server:
    def __init__(self): self.closed=threading.Event()
    def accept(self): self.closed.wait(5); raise OSError('closed')
    def close(self): self.closed.set()


@pytest.fixture(autouse=True)
def _file_keystore(monkeypatch):
    # CI temp roots may sit outside tempfile.gettempdir(); these homes are disposable.
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE','1')


def test_immediate_retry_and_recovery(tmp_path,monkeypatch,capsys):
    home,*_=setup_home(tmp_path); rows=[]; ready=threading.Event(); server=Server()
    b=Broker(home,on_sync=lambda row:(rows.append(row),ready.set()))
    calls=[]
    def sync(remote):
        calls.append(time.monotonic())
        if len(calls)==1: raise StorageError(SECRET)
        if len(calls)==2: raise RuntimeError(SECRET)
        return dict(uploaded=0,downloaded=0,invalid=0,errors=0)
    monkeypatch.setattr(b,'sync_once',sync)
    thread=threading.Thread(target=b.serve,kwargs=dict(server=server,remote=object(),sync_interval=.3)); start=time.monotonic(); thread.start()
    try:
        assert ready.wait(1) and calls[0]-start<.2
        deadline=time.monotonic()+3
        while len(rows)<3 and time.monotonic()<deadline: time.sleep(.01)
        assert [r['outcome'] for r in rows[:3]]==['remote_error','error','ok']
        # Windows timers can wake ~16 ms early; .25 still proves the interval wait.
        assert calls[1]-calls[0]>=.25 and thread.is_alive()
        assert all(service.sync_status_valid(r) for r in rows)
    finally: b.close(); thread.join(2)
    output=capsys.readouterr().err
    assert SECRET not in output and len(output.splitlines())==3


def test_throttle_schema_hook(tmp_path,monkeypatch,capsys):
    home,*_=setup_home(tmp_path); rows=[]; b=Broker(home,on_sync=rows.append)
    remote=FilesystemStorage(tmp_path/'remote'); now=[0]
    monkeypatch.setattr(service.time,'monotonic',lambda:now[0])
    try:
        b.sync_attempt(remote,60); b.sync_attempt(remote,60) # upload count changed
        b.sync_attempt(remote,60); assert len(rows)==3
        now[0]=599; b.sync_attempt(remote,60); assert len(rows)==4
        now[0]=600; b.sync_attempt(remote,60); assert len(rows)==5
        row=rows[-1]; assert service.sync_status_valid(row)
        for field,value in [('extra',SECRET),('outcome',SECRET),('uploaded',True),('outbox',-1),
            ('interval_s',float('nan')),('backend',SECRET),('at','yesterday')]:
            assert not service.sync_status_valid(dict(row,**{field:value}))
        for field in row:
            bad=dict(row); del bad[field]; assert not service.sync_status_valid(bad)
        def fail(row): raise RuntimeError(SECRET)
        b.on_sync=fail; now[0]=1200; b.sync_attempt(remote,60)
        b.on_sync=rows.append; b.sync_attempt(remote,60); assert len(rows)==6
    finally: b.close()
    assert capsys.readouterr().err.count('profile-log sync: ok')==1


def test_control_counts(tmp_path):
    from agentmesh_log.oskeystore import initialize
    from agentmesh_log.broker.keystore import BrokerHome
    home=BrokerHome(tmp_path/'home'); initialize(home,['work'],file_keystore=True)
    rows=[]; b=Broker(home,on_sync=rows.append); remote=FilesystemStorage(tmp_path/'remote')
    try:
        b.sync_attempt(remote,60); row=rows[-1]
        count=len([p for p in remote.list('') if not p.startswith('registry/heartbeats/')])
        assert count>len(b.replica.keys())
        assert row['uploaded']==row['local_objects']==row['remote_objects']==count
        assert row['outbox']==0 and row['outcome']=='ok'
        class Bad:
            def list(self,prefix): raise OSError(SECRET)
        b.sync_attempt(Bad(),60)
        assert rows[-1]['outcome']=='remote_error' and rows[-1]['errors']>=1
        assert SECRET not in json.dumps(rows)
    finally: b.close()


def test_cli_retry_status(tmp_path,monkeypatch):
    import agentmesh_log.cli as cli
    import agentmesh_log.storage.r2creds as creds
    from .test_cli import cli as invoke
    home,state,store,agents=setup_home(tmp_path); calls=[]; rows=[]
    remote=FilesystemStorage(tmp_path/'remote')
    def load(service):
        calls.append(service)
        if len(calls)==1: raise StorageError('S3 credentials missing')
        if len(calls)==2: raise StorageError('S3 credentials refused')
        return ('abcd0123'*4,SECRET)
    monkeypatch.setattr(creds,'load_keychain_credentials',load)
    import agentmesh_log.storage.s3 as s3
    monkeypatch.setattr(s3,'S3Storage',lambda *a,**kw:remote)
    def serve(self,*,remote,sync_interval,on_sync=None,on_overview=None):
        if on_sync is not None: self.on_sync=on_sync
        for i in range(3):
            self.sync_attempt(remote,sync_interval)
            path=home.root/'sync-status.json'; rows.append(json.loads(path.read_bytes()))
            if os.name!='nt': assert path.stat().st_mode&0o777==0o600
            s=Session(agents[0].certificate()); assert 'id' in remember(self,s,agents[0])
            assert recall(self,s)['results']
        assert not list(home.root.glob('*.tmp'))
    monkeypatch.setattr(Broker,'serve',serve)
    result=invoke('broker','run','--home',home.root,'--s3-bucket','profile-r2','--s3-prefix','v1/',
        '--s3-endpoint','https://s3.amazonaws.com','--s3-credentials','keychain:org.agentmesh.profile-log.r2','--sync-interval','60')
    assert result.returncode==0, result.stdout
    assert len(calls)==3
    assert [r['outcome'] for r in rows]==['credentials_missing','credentials_refused','ok']
    assert all(r['backend']=='s3' for r in rows)
    assert SECRET not in repr(rows)+result.stdout+result.stderr


def test_local_run_unchanged(tmp_path,monkeypatch):
    from .test_cli import cli as invoke
    home,*_=setup_home(tmp_path); seen=[]
    monkeypatch.setattr(Broker,'serve',lambda self,**kw:seen.append(kw))
    assert invoke('broker','run','--home',home.root).returncode==0
    assert seen[0]['remote'] is None and seen[0]['sync_interval'] is None
    assert not (home.root/'sync-status.json').exists()


def test_connections_offline(tmp_path):
    import os
    import tempfile
    from pathlib import Path
    from agentmesh_log.broker.transport import UnixServer, UnixClient, PipeServer, PipeClient
    from agentmesh_log.broker.client import BrokerClient
    with tempfile.TemporaryDirectory(prefix='r2-',dir=None if os.name=='nt' else '/tmp') as root:
        home,state,store,agents=setup_home(Path(root).resolve()); rows=[]; attempted=threading.Event()
        b=Broker(home,on_sync=lambda row:(rows.append(row),attempted.set()))
        class Bad:
            def list(self,prefix): raise OSError(SECRET)
        server=None; thread=None; client=None
        try:
            try: server=(PipeServer if os.name=='nt' else UnixServer)(home.address(state.device.device_id))
            except PermissionError: pytest.fail('IPC acceptance blocked: sandbox denies socket binding',pytrace=False)
            thread=threading.Thread(target=b.serve,kwargs=dict(server=server,remote=Bad(),sync_interval=.05),daemon=True); thread.start()
            assert attempted.wait(2)
            channel=(PipeClient if os.name=='nt' else UnixClient)(home.address(state.device.device_id))
            client=BrokerClient(agents[0],state.device.public_bytes,channel)
            assert 'agent_id' in client.request({'op':'whoami'})
            assert 'id' in client.write('migrate database')
            assert client.request({'op':'recall','query':'migrate database'})['results']
            assert rows[-1]['outcome']=='remote_error' and b.sync_thread.is_alive()
        finally:
            if client: client.close()
            b.close()
            if thread: thread.join(.2)


def test_download_and_invalid_counts(tmp_path):
    home,state,store,agents=setup_home(tmp_path); rows=[]; b=Broker(home,on_sync=rows.append)
    remote=FilesystemStorage(tmp_path/'remote')
    try:
        from agentmesh_log.events import seal_event,event_path,verify_event
        sid=state.grants[agents[0].agent_id].default_write
        data=seal_event(state.trees[0].keys[sid].write_key,agents[0],'migrate database')
        remote.put(event_path(verify_event(data,state.registry())),data)
        remote.put('events/bad.json',b'{}')
        b.sync_attempt(remote,60)
        assert rows[-1]['downloaded']==1 and rows[-1]['invalid']==1
        assert rows[-1]['remote_objects']==rows[-1]['local_objects']+1
    finally: b.close()


def test_memory_connection(tmp_path):
    import queue
    from agentmesh_log.broker.client import BrokerClient
    home,state,store,agents=setup_home(tmp_path); seen=threading.Event(); b=Broker(home,on_sync=lambda row:seen.set())
    class Channel:
        def __init__(self,inbox,outbox): self.inbox,self.outbox=inbox,outbox
        def send(self,value): self.outbox.put(value)
        def receive(self):
            value=self.inbox.get(timeout=3)
            if value is None: raise EOFError()
            return value
        def close(self): self.outbox.put(None)
    inbox,outbox=queue.Queue(),queue.Queue(); incoming=Channel(inbox,outbox); outgoing=Channel(outbox,inbox)
    pending=queue.Queue(); pending.put(incoming)
    class Acceptor:
        def accept(self):
            conn=pending.get(timeout=3)
            if conn is None: raise OSError()
            return conn
        def close(self): pending.put(None)
    class Bad:
        def list(self,prefix): raise RuntimeError(SECRET)
    thread=threading.Thread(target=b.serve,kwargs=dict(server=Acceptor(),remote=Bad(),sync_interval=.05)); thread.start()
    client=None
    try:
        assert seen.wait(2)
        client=BrokerClient(agents[0],state.device.public_bytes,outgoing)
        assert 'id' in client.write('migrate database')
        assert client.request({'op':'recall','query':'migrate database'})['results']
        assert b.sync_thread.is_alive()
    finally:
        if client: client.close()
        b.close(); thread.join(2)


def test_failed_list_keeps_counts(tmp_path):
    home,*_=setup_home(tmp_path); rows=[]; b=Broker(home,on_sync=rows.append)
    remote=FilesystemStorage(tmp_path/'remote')
    class Bad:
        def list(self,prefix): raise OSError(SECRET)
    try:
        b.sync_attempt(remote,60); count=rows[-1]['remote_objects']; assert count>0
        b.sync_attempt(Bad(),60)
        assert rows[-1]['remote_objects']==count
        assert rows[-1]['outcome']=='remote_error'
    finally: b.close()


def test_completion_timestamp(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    home,*_=setup_home(tmp_path); rows=[]; b=Broker(home,on_sync=rows.append)
    times=[datetime(2026,1,1,tzinfo=timezone.utc),datetime(2026,1,2,tzinfo=timezone.utc)]; now=[times[0]]
    class Clock(datetime):
        @classmethod
        def now(cls,tz): return now[0]
    monkeypatch.setattr(service,'datetime',Clock)
    def sync(remote): now[0]=times[1]; return dict(uploaded=0,downloaded=0,invalid=0,errors=0)
    monkeypatch.setattr(b,'sync_once',sync)
    try:
        b.sync_attempt(object(),60)
        assert rows[-1]['at']=='2026-01-02T00:00:00Z'
    finally: b.close()


@pytest.mark.parametrize('broken',[False,True])
def test_cli_shared_sync_status(tmp_path,monkeypatch,broken):
    from agentmesh_log import settings, status
    from .test_cli import cli as invoke
    home,*_=setup_home(tmp_path); path=settings.root()/'profile-log-status.json'
    if broken: path.mkdir()
    row=dict(at='2026-10-20T00:00:00Z',outcome='ok',interval_s=60,backend='filesystem',**{k:0 for k in service.COUNTS})
    def serve(self,**kw): kw['on_sync'](row)
    monkeypatch.setattr(Broker,'serve',serve)
    result=invoke('broker','run','--home',home.root)
    assert result.returncode==0, result.stdout
    assert json.loads((home.root/'sync-status.json').read_bytes())==row
    if broken: assert path.is_dir()
    else:
        value=json.loads(path.read_bytes()); assert status.valid(value)
        assert value['last_sync']==dict(row,last_ok_at=row['at'])
