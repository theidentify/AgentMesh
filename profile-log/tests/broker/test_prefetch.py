import io
import threading
import time
from types import SimpleNamespace
import pytest
from agentmesh_log.broker import service
from agentmesh_log.storage import StorageError
from agentmesh_log.storage.s3 import S3Storage


class Remote:
    def list(self,prefix): return []
    def get(self,key):
        time.sleep(.001)
        if key.endswith('/3'): raise StorageError('missing')
        return key.encode()


def stage(remote,n=40):
    return service.StagedRemote(remote,{'events/0'},dict(keys={f'events/{i}' for i in range(n)},skip={'events/1'}))


def test_parallel(monkeypatch):
    monkeypatch.setattr(service,'PREFETCH_WORKERS',1)
    expected=stage(Remote()).data
    monkeypatch.setattr(service,'PREFETCH_WORKERS',8)
    remote=service.SyncRemote(Remote()); result=stage(remote)
    assert result.data==expected and len(expected)==37
    assert remote.io['class_b']==38 and remote.errors==1
    assert remote.io['bytes_down']==sum(map(len,expected.values()))


def test_overlap(monkeypatch):
    barrier=threading.Barrier(4)
    class Parallel(Remote):
        def get(self,key): barrier.wait(timeout=3); return b'x'
    monkeypatch.setattr(service,'PREFETCH_WORKERS',4)
    assert len(stage(Parallel(),6).data)==4


def test_unexpected():
    class Broken(Remote):
        def get(self,key): raise RuntimeError('unexpected')
    with pytest.raises(RuntimeError,match='unexpected'): stage(Broken())


@pytest.mark.parametrize('n,quiet',[(202,True),(203,False)],ids=['small','large'])
def test_progress(monkeypatch,n,quiet):
    stream=io.StringIO(); ticks=iter(range(1000))
    monkeypatch.setattr(service,'sys',SimpleNamespace(stderr=stream))
    monkeypatch.setattr(service,'time',SimpleNamespace(monotonic=lambda:next(ticks)))
    stage(Remote(),n)
    lines=stream.getvalue().splitlines()
    assert lines==([] if quiet else [f'profile-log sync: fetched {i}/201' for i in range(5,202,5)])


def test_no_stderr(monkeypatch):
    monkeypatch.setattr(service,'sys',SimpleNamespace(stderr=None))
    assert len(stage(Remote(),203).data)==200


@pytest.mark.parametrize('hooks',[False,True])
def test_s3_counts(hooks):
    callbacks={}
    class Client:
        meta=SimpleNamespace(events=SimpleNamespace(register=lambda name,fn:callbacks.update({name:fn}))) if hooks else None
        def get_object(self,**kwargs):
            time.sleep(.001)
            if hooks: callbacks['before-send.s3.GetObject'](None)
            return {'Body':io.BytesIO(b'abc')}
    remote=service.SyncRemote(S3Storage('bucket',client=Client()))
    # Control listings are independent of this GET-only fixture.
    remote.list=lambda prefix: []
    assert len(stage(remote,102).data)==100
    assert remote.counters()==dict(bytes_up=0,bytes_down=300,class_a=0,class_b=100)
    assert remote.errors==0
