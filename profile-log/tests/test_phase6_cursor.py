"""P6-6 incremental drain, clock skew overlap, restart and daily reconciliation."""
from phase6_support import device
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.storage.s3 import S3Storage
from agentmesh_log.ulid import ALPHABET


def floor_id(ms):
    return ''.join(ALPHABET[((ms<<80)>>shift)&31] for shift in range(125,-1,-5))


def test_p6_cursor(tmp_path):
    home,store,storage,cr,p,k,pin,broker,b=device(tmp_path)
    calls=[];original=storage.list
    def listing(prefix,*,start_after=None):
        if prefix=='outbox/':calls.append(start_after)
        return original(prefix,start_after=start_after)
    storage.list=listing
    clock=[b.clock()];b.clock=lambda:clock[0]
    ms=clock[0];latest='outbox/'+floor_id(ms)+'.json'
    storage.put(latest,b'{}')  # considered malformed records also advance the bounded cursor
    assert b.drain()['rejected']==1
    assert calls==[None]
    assert store.load().control['connector_outbox_cursor']==floor_id(ms)
    late='outbox/'+floor_id(ms-120000)+'.json';storage.put(late,b'{}')
    assert b.drain()['rejected']==2
    assert calls[-1]=='outbox/'+floor_id(ms-600000)
    old='outbox/'+floor_id(ms-3600000)+'.json';storage.put(old,b'{}')
    assert b.drain()['rejected']==2
    clock[0]+=86400000
    assert b.drain()['rejected']==3 and calls[-1] is None
    from agentmesh_log.bridge import Bridge
    restarted=Bridge(home,store,storage,clock=lambda:clock[0],client_factory=b.client_factory)
    assert restarted.drain()['rejected']==3 and calls[-1] is None
    # A forged future path must not advance the cursor past legitimate arrivals.
    storage.put('outbox/'+floor_id(clock[0]+86400000)+'.json',b'{}')
    restarted.drain()
    assert store.load().control['connector_outbox_cursor']==floor_id(ms)
    broker.close()


def test_p6_start_after(tmp_path):
    import boto3
    from moto import mock_aws
    with mock_aws():
        client=boto3.client('s3',region_name='us-east-1',aws_access_key_id='testing',aws_secret_access_key='testing')
        client.create_bucket(Bucket='phase6-cursor')
        remote=S3Storage('phase6-cursor',prefix='v1/',client=client)
        requests=[]
        client.meta.events.register('before-parameter-build.s3.ListObjectsV2',lambda params,**kw:requests.append(dict(params)))
        for storage in (FilesystemStorage(tmp_path/'objects'),remote):
            for key in ('outbox/a','outbox/b','outbox/c'):storage.put(key,b'{}')
            assert list(storage.list('outbox/',start_after='outbox/b'))==['outbox/c']
            assert list(storage.list('outbox/'))==['outbox/a','outbox/b','outbox/c']
        assert requests[0]['StartAfter']=='v1/outbox/b'
        assert requests[0]['Prefix']=='v1/outbox/'
