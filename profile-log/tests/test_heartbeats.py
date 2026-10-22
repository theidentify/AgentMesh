"""MD gates: generated identities, disposable storage, no live services."""
from copy import deepcopy
from datetime import datetime, timezone
import pytest
from agentmesh_log import heartbeats as hb, settings, status
from agentmesh_log.agekeys import DeviceAgeIdentity, encrypt_many
from agentmesh_log.broker.handshake import b64
from agentmesh_log.encoding import wire, parse
from agentmesh_log.identity import DeviceIdentity
from agentmesh_log.keys import decode_ciphertext
from agentmesh_log.model import Account
from agentmesh_log.registry_store import RegistryStore, device_record, revocation, signed
from agentmesh_log.storage.filesystem import FilesystemStorage


@pytest.fixture
def mesh(tmp_path):
    account=Account(); devices=[DeviceIdentity() for _ in range(3)]; ages=[DeviceAgeIdentity() for _ in devices]
    storage=FilesystemStorage(tmp_path/'remote'); rs=RegistryStore(storage,account.public_record())
    for d,a in zip(devices,ages):
        rs.publish_device(device_record(d,d.certificate(account,issuer=None if d is devices[0] else devices[0],issued_at=1),a,issued_at=2))
    return storage,rs,devices,ages


def payload(d):
    return dict(device_id=d.device_id,name='DEVICE_NAME_SENTINEL',platform='macos',app_version=None,last_sync_at=1000,last_sync_outcome='ok')


def make(mesh,at=1000):
    storage,rs,ds,ages=mesh
    return hb.make(ds[0],rs.load(),payload(ds[0]),at=at)


def test_md1_roundtrip(mesh):
    storage,rs,ds,ages=mesh; record=make(mesh)
    assert set(record)=={'format','device_id','issued_at','age','signature'}
    for age in ages: assert hb.open_record(hb.path(record),record,rs.load(),age,at=1000)==dict(payload(ds[0]),issued_at=1000)
    with pytest.raises(ValueError): hb.open_record(hb.path(record),record,rs.load(),DeviceAgeIdentity(),at=1000)
    storage.put(hb.path(record),wire(record)); later=make(mesh,2000); storage.put(hb.path(later),wire(later))
    assert parse(storage.get(hb.path(record)))==later and len(list(storage.list(hb.PREFIX)))==1
    from agentmesh_log.storage import ObjectExists
    storage.put('events/immutable.json',b'one')
    with pytest.raises(ObjectExists): storage.put('events/immutable.json',b'two')


def test_md2_revocation(mesh):
    storage,rs,ds,ages=mesh; record=make(mesh)
    rs.publish_revocation(revocation(ds[1].device_id,ds[0],revoked_at=2000))
    next_record=make(mesh,3000)
    for age in (ages[0],ages[2]): assert age.decrypt(decode_ciphertext(next_record['age']))==wire(payload(ds[0]))
    with pytest.raises(ValueError): ages[1].decrypt(decode_ciphertext(next_record['age']))
    rs.publish_revocation(revocation(ds[0].device_id,ds[2],revoked_at=4000))
    storage.put(hb.path(record),wire(record))
    assert hb.read(storage,rs.load(),ages[2],at=5000)=={}
    with pytest.raises(ValueError): hb.make(ds[0],rs.load(),payload(ds[0]),at=5000)


@pytest.mark.parametrize('bad',['signature','age','path','inner','future','name','platform','extra','version','sync','outcome','shape','unknown'],ids=['signature','age','path','inner','future','name','platform','extra','version','sync','outcome','shape','unknown'])
def test_md3_tamper(mesh,bad):
    storage,rs,ds,ages=mesh; record=make(mesh); path=hb.path(record)
    if bad=='signature': record['signature']='00'*64
    elif bad=='age': record['age']=b64(b'changed ciphertext')
    elif bad=='path': path=hb.PREFIX+ds[1].device_id+'.json'
    elif bad=='future': record=make(mesh,601001)
    elif bad=='shape': record['name']='unsealed'
    elif bad=='unknown': record['device_id']='ab'*16
    else:
        body=payload(ds[0])
        field,value={'inner':('device_id',ds[1].device_id),'name':('name','bad\nname'),
            'platform':('platform','plan9'),'extra':('extra',1),'version':('app_version','v'*65),
            'sync':('last_sync_at',True),'outcome':('last_sync_outcome','created')}[bad]
        body[field]=value
        record=signed('device-heartbeat',ds[0]._private,device_id=ds[0].device_id,issued_at=1000,
            age=b64(encrypt_many(wire(body),[a.recipient for a in ages])))
    with pytest.raises(ValueError): hb.open_record(path,record,rs.load(),ages[0],at=1000)
    storage.put(path,wire(record)); assert hb.read(storage,rs.load(),ages[0],at=1000)=={}


@pytest.mark.parametrize('name',['','x'*65,'bad\x00','bad\x7f','bad\u200b',42,None],ids=['empty','long','nul','del','format','number','null'])
def test_md3_name_settings(name):
    with pytest.raises(ValueError): settings.validate({'device':{'name':name}})


def test_md5_local_values(monkeypatch):
    settings.validate({'device':{'name':'My Mac'}})
    monkeypatch.setattr(hb.socket,'gethostname',lambda:'host.local')
    monkeypatch.setattr(hb.sys,'platform','darwin')
    assert hb.local('ab'*16)['name']=='host' and hb.local('ab'*16)['platform']=='macos'
    settings.save_user({'device':{'name':'My Mac'}})
    assert hb.local('ab'*16)['name']=='My Mac'
    for value,expected in [('win32','windows'),('linux','linux'),('ios','ios'),('android','android'),('other-os','other')]:
        monkeypatch.setattr(hb.sys,'platform',value); assert hb.local('ab'*16)['platform']==expected
    with pytest.raises(ValueError): settings.validate({'device':{'platform':'linux'}})


def state_for(mesh):
    from types import SimpleNamespace
    storage,rs,ds,ages=mesh
    return SimpleNamespace(device=ds[0],control={'age_identity':ages[0].secret(),'phase4':True},registry=rs.load)


def test_md4_throttle_cache(mesh,monkeypatch):
    storage,rs,ds,ages=mesh; state=state_for(mesh); cache=hb.Heartbeats(); calls=[]
    put,get,listing=storage.put,storage.get,storage.list
    monkeypatch.setattr(storage,'put',lambda p,d:(calls.append(('put',p)),put(p,d))[-1])
    monkeypatch.setattr(storage,'get',lambda p:(calls.append(('get',p)),get(p))[-1])
    monkeypatch.setattr(storage,'list',lambda p,**kw:(calls.append(('list',p)),listing(p,**kw))[-1])
    # Registry reads are the broker's job; freeze its verified snapshot for this cost check.
    reg=rs.load(); state.registry=lambda:reg; calls.clear()
    row={'at':'2026-10-22T00:00:00Z','outcome':'ok'}
    cache.update(storage,state,row,tick=0,at=1000)
    assert calls==[('put',hb.PREFIX+ds[0].device_id+'.json'),('list',hb.PREFIX),('get',hb.PREFIX+ds[0].device_id+'.json')]
    first=deepcopy(cache.rows); calls.clear()
    for tick in (1,60,899): cache.update(storage,state,row,tick=tick,at=2000)
    assert calls==[] and cache.rows==first
    cache.update(storage,state,{**row,'outcome':'error'},tick=899,at=3000)
    assert [c[0] for c in calls]==['put']; calls.clear()
    cache.update(storage,state,{**row,'outcome':'error'},tick=900,at=4000)
    assert [c[0] for c in calls]==['list','get']; calls.clear()
    cache.update(storage,state,{**row,'outcome':'error'},tick=1799,at=5000)
    assert [c[0] for c in calls]==['put']; calls.clear()
    rs.publish_revocation(revocation(ds[1].device_id,ds[0],revoked_at=6000)); reg=rs.load(); calls.clear()
    cache.update(storage,state,{**row,'outcome':'error'},tick=1800,at=7000)
    assert calls[0][0]=='put'
    with pytest.raises(ValueError): ages[1].decrypt(decode_ciphertext(parse(get(hb.PREFIX+ds[0].device_id+'.json'))['age']))
    new=DeviceIdentity(); age=DeviceAgeIdentity()
    rs.publish_device(device_record(new,new.certificate(reg,issuer=ds[0],issued_at=1),age,issued_at=2))
    reg=rs.load(); calls.clear(); cache.update(storage,state,{**row,'outcome':'error'},tick=1801,at=8000)
    assert [c[0] for c in calls]==['put']
    assert parse(age.decrypt(decode_ciphertext(parse(get(hb.PREFIX+ds[0].device_id+'.json'))['age'])))['name']


def test_md4_broker_failures_and_read_cost(tmp_path,monkeypatch,capsys):
    from agentmesh_log.broker.service import Broker
    from agentmesh_log.broker.keystore import BrokerHome, FileKeyStore
    from agentmesh_log.oskeystore import initialize
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE','1')
    home=BrokerHome(tmp_path/'home'); initialize(home,['work'],file_keystore=True)
    rows=[]; views=[]; broker=Broker(home,on_sync=rows.append,on_overview=views.append)
    remote=FilesystemStorage(tmp_path/'remote'); put=remote.put; get=remote.get; reads=[]; ticks=[0]
    monkeypatch.setattr('agentmesh_log.broker.service.time.monotonic',lambda:ticks[0])
    def fail(path,data):
        if path.startswith(hb.PREFIX): raise OSError('PRIVATE_FAILURE')
        return put(path,data)
    monkeypatch.setattr(remote,'put',fail)
    try:
        broker.sync_attempt(remote,60)
        assert rows[-1]['outcome']=='ok' and rows[-1]['errors']==0 and views[-1]['devices'][0]['this_device']
        assert 'PRIVATE_FAILURE' not in capsys.readouterr().err
        monkeypatch.setattr(remote,'put',put); ticks[0]=900; broker.sync_attempt(remote,60)
        assert list(remote.list(hb.PREFIX))
        monkeypatch.setattr(remote,'get',lambda p:(reads.append(p),get(p))[-1])
        ticks[0]=901; broker.sync_attempt(remote,60)
        assert not any(p.startswith(hb.PREFIX) for p in reads)
        assert not list(broker.replica.storage.list(hb.PREFIX))
    finally: broker.close()


def test_md5_status(mesh,monkeypatch):
    from agentmesh_log.broker.keystore import BrokerState
    storage,rs,ds,ages=mesh; state=BrokerState.create(['work']); state.device=ds[0]
    state.registry=rs.load
    record=hb.make(ds[1],rs.load(),payload(ds[1]),at=1000); storage.put(hb.path(record),wire(record))
    rows=hb.read(storage,rs.load(),ages[0],at=1000)
    sync={'at':'2026-10-22T00:00:00Z','outcome':'ok'}
    view=status.overview(state,heartbeats=rows,last_sync=sync)
    byid={d['device_id']:d for d in view['devices']}; local=byid[ds[0].device_id]; peer=byid[ds[1].device_id]; missing=byid[ds[2].device_id]
    assert local['this_device'] and local['name'] and local['platform'] and local['last_seen_at']
    assert local['last_sync_at']==sync['at'] and local['last_sync_outcome']=='ok'
    assert peer['name']=='DEVICE_NAME_SENTINEL' and peer['last_seen_at']=='1970-01-01T00:00:01+00:00'
    assert not peer['this_device'] and all(missing[k] is None for k in ('name','platform','last_seen_at','last_sync_at','last_sync_outcome'))
    value=dict(format=status.FORMAT,updated_at=status.timestamp(),overview=view); assert status.valid(value)
    for key,bad in [('name','x'*65),('platform','wat'),('last_seen_at','bad'),('last_sync_at',42),('last_sync_outcome','created'),('this_device',1),('extra',1)]:
        v=deepcopy(value); v['overview']['devices'][0][key]=bad; assert not status.valid(v)
    rs.publish_revocation(revocation(ds[1].device_id,ds[0],revoked_at=2000))
    view=status.overview(state,heartbeats=rows,last_sync=sync)
    assert next(d for d in view['devices'] if d['device_id']==ds[1].device_id)['name'] is None


def test_md8_marker_and_verify(tmp_path,monkeypatch):
    from agentmesh_log import verify_remote
    from agentmesh_log.broker.service import Broker
    from agentmesh_log.broker.keystore import BrokerHome
    from agentmesh_log.oskeystore import initialize
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE','1'); settings.save_user({'device':{'name':'DEVICE_NAME_SENTINEL'}})
    home=BrokerHome(tmp_path/'home'); initialize(home,['work'],file_keystore=True)
    remote=FilesystemStorage(tmp_path/'remote'); broker=Broker(home)
    try:
        broker.sync_attempt(remote,60)
        assert list(remote.list(hb.PREFIX))
        assert all(b'DEVICE_NAME_SENTINEL' not in remote.get(p) for p in remote.list(''))
        markers=tmp_path/'markers'; markers.write_text('DEVICE_NAME_SENTINEL\n')
        args=verify_remote.arguments(['--home',str(home.root),'--filesystem',str(remote.root),'--markers',str(markers)])
        result=verify_remote.verify(args); assert result['ok'] and result['marker_hits']==0 and result['missing_local']==0
        remote.put('unknown/positive',b'DEVICE_NAME_SENTINEL')
        result=verify_remote.verify(args); assert not result['ok'] and result['marker_hits']==1
    finally: broker.close()


def test_md3_boundary_and_new_identity(mesh):
    storage,rs,ds,ages=mesh; record=make(mesh,601000)
    assert hb.open_record(hb.path(record),record,rs.load(),ages[0],at=1000)['issued_at']==601000
    storage.put(hb.path(record),wire(record))
    assert hb.read(storage,rs.load(),DeviceAgeIdentity(),at=1000)=={}


def test_md4_failed_attempts_throttled(mesh,monkeypatch,capsys):
    storage,rs,ds,ages=mesh; state=state_for(mesh); cache=hb.Heartbeats(); calls=[]
    reg=rs.load(); state.registry=lambda:reg; cache.rows={'prior':{}}
    def fail(name):
        def call(*args): calls.append(name); raise OSError('PRIVATE_FAILURE')
        return call
    monkeypatch.setattr(storage,'put',fail('put')); monkeypatch.setattr(storage,'list',fail('list'))
    row=dict(at='2026-10-22T00:00:00Z',outcome='remote_error')
    for tick in (0,1,899): cache.update(storage,state,row,tick=tick,at=1000)
    assert calls==['put','list'] and cache.rows=={}
    cache.update(storage,state,row,tick=900,at=1000)
    assert calls==['put','list','put','list'] and 'PRIVATE_FAILURE' not in capsys.readouterr().err


def test_md1_filesystem_guards(tmp_path,monkeypatch):
    from agentmesh_log.storage import filesystem, StorageError, ObjectExists
    for portable in (False,True):
        if portable: monkeypatch.setattr(filesystem,'_DIR_FD',False)
        root=tmp_path/str(portable); storage=FilesystemStorage(root); key=hb.PREFIX+'ab'*16+'.json'
        storage.put(key,b'first'); storage.put(key,b'second'); assert storage.get(key)==b'second'
        for invalid in ('registry/heartbeats/not-device.json','registry/heartbeats/'+('ab'*16)+'/extra.json','registry/devices/'+('ab'*16)+'.json'):
            storage.put(invalid,b'first')
            with pytest.raises(ObjectExists): storage.put(invalid,b'second')
        target=root/'protected'; target.write_bytes(b'untouched'); (root/key).unlink(); (root/key).symlink_to(target)
        with pytest.raises(StorageError): storage.put(key,b'changed')
        assert target.read_bytes()==b'untouched'


def test_md5_hostname(monkeypatch):
    monkeypatch.setattr(hb.socket,'gethostname',lambda:'a'*70+'.local')
    assert hb.local('ab'*16)['name']=='a'*64
    monkeypatch.setattr(hb.socket,'gethostname',lambda:'bad\n')
    assert hb.local('ab'*16)['name']=='device'
