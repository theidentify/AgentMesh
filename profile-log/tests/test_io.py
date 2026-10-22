"""ST-17 counts each S3 page, payload and failed request."""
import io
from types import SimpleNamespace
from agentmesh_log.storage.s3 import S3Storage
from agentmesh_log.broker.service import SyncRemote


def test_s3_pages():
    client=SimpleNamespace(put_object=lambda **kw:None,get_object=lambda **kw:{'Body':io.BytesIO(b'12345')},get_paginator=lambda name:SimpleNamespace(paginate=lambda **kw:iter([{'Contents':[]},{'Contents':[{'Key':'a'}]}])))
    remote=SyncRemote(S3Storage('fixture',client=client))
    remote.put('a',b'123'); assert remote.get('a')==b'12345'; assert list(remote.list(''))==['a']
    assert remote.counters()==dict(bytes_up=3,bytes_down=5,class_a=3,class_b=1)


def test_s3_requests():
    import boto3
    from moto import mock_aws
    with mock_aws():
        client=boto3.client('s3',region_name='us-east-1',aws_access_key_id='testing',aws_secret_access_key='testing')
        client.create_bucket(Bucket='io-fixture')
        storage=S3Storage('io-fixture',client=client); remote=SyncRemote(storage)
        for k in ('a','b','c'): remote.put(k,b'123')
        assert list(storage.list('',page_size=1))==['a','b','c']
        assert remote.head('a')['ContentLength']==3
        assert remote.counters()==dict(bytes_up=9,bytes_down=0,class_a=6,class_b=1)


def test_s3_retry_hook():
    callbacks={}
    def register(name,fn): callbacks[name]=fn
    client=SimpleNamespace(meta=SimpleNamespace(events=SimpleNamespace(register=register)))
    store=S3Storage('fixture',client=client)
    assert 'before-send.s3.PutObject' in callbacks
    store._active.value=True; store._active.size=3
    for _ in range(2): callbacks['before-send.s3.PutObject'](request=SimpleNamespace(body=b'abc'))
    assert store.io['class_a']==2 and store.io['bytes_up']==6
