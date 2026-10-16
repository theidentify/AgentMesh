"""Dependency scheduling uses disposable, portable fixtures only."""
import os
import random
import uuid

import pytest

import memory_sync as sync
import resolution_store as store
import identity_backup
import sqlite_memory
from test_memory_sync import GROUP


@pytest.fixture
def chain(tmp_path):
    db=tmp_path/'receiver.db'; exchange=tmp_path/'exchange'
    sqlite_memory.init_database(db)
    sync.initialize(db,'windows',GROUP)
    directory=exchange/'changes'/'mac'
    for path in (exchange,directory.parent,directory): identity_backup.private_mkdir(path)
    def make(length, key='chain', start=0):
        parent=None; result=[]
        ids=list(range(start+1,start+length+1)); random.Random(47).shuffle(ids)
        for n,ident in enumerate(ids):
            rev=str(uuid.UUID(int=100000+start+n))
            body=[dict(table='summary_state',key=[key],parent=parent,revision=rev,
                       row=dict(consumer=key,last_event_id=n+1,updated_at='fixture'))]
            p=dict(format=sync.FORMAT,group=GROUP,node='mac',uuid=str(uuid.UUID(int=ident)),
                   body=body,checksum=sync.digest(body))
            path=directory/(p['uuid']+'.json')
            path.write_text(sync.canonical(p),encoding='utf-8')
            result.append((path,p)); parent=rev
        return result
    def order(items):
        for n,(path,_) in enumerate(items):
            stamp=1_700_000_000_000_000_000+n*1_000_000_000
            os.utime(path,ns=(stamp,stamp))
    return db,exchange,make,order


def instrument(monkeypatch):
    attempts=[]; original=sync._apply
    def apply(c,p):
        attempts.append(p['uuid'])
        return original(c,p)
    monkeypatch.setattr(sync,'_apply',apply)
    return attempts


@pytest.mark.parametrize('indexed',[False,True],ids=['legacy','indexed'])
def test_shuffled_chain_linear(chain,monkeypatch,indexed):
    db,exchange,make,order=chain
    packets=make(200)
    random.Random(32).shuffle(packets); order(packets)
    if indexed:
        with sync.connect(db) as c,c: store.migrate(c)
    attempts=instrument(monkeypatch)
    assert sync.receive(db,exchange)==dict(applied=200,pending=0,conflict=0,invalid=0,unavailable=False)
    assert len(attempts)<=400
    with sync.connect(db) as c:
        assert c.execute('SELECT last_event_id FROM summary_state').fetchone()[0]==200


@pytest.mark.parametrize('indexed',[False,True],ids=['legacy','indexed'])
def test_interleaved_head_last_depth_first(chain,monkeypatch,indexed):
    db,exchange,make,order=chain
    a=make(5,'a'); b=make(5,'b',100)
    order([a[4],b[4],a[3],b[3],a[2],b[2],a[1],b[1],a[0],b[0]])
    if indexed:
        with sync.connect(db) as c,c: store.migrate(c)
    attempts=instrument(monkeypatch)
    assert sync.receive(db,exchange)['applied']==10
    expected=[p['uuid'] for _,p in a]+[p['uuid'] for _,p in b]
    assert attempts[-10:]==expected
    assert len(attempts)==18


@pytest.mark.parametrize('indexed',[False,True],ids=['legacy','indexed'])
def test_unchanged_outcomes(chain,indexed):
    db,exchange,make,order=chain
    missing=make(2,'missing'); missing[0][0].unlink()
    conflict=make(1,'conflict',100)[0]
    invalid=make(1,'invalid',200)[0]; invalid[0].write_text('{}',encoding='utf-8')
    with sync.connect(db) as c,c:
        c.execute("INSERT INTO summary_state VALUES('conflict',9,'local')")
    sync.capture(db)
    if indexed:
        with sync.connect(db) as c,c: store.migrate(c)
    result=sync.receive(db,exchange)
    assert result==dict(applied=0,pending=1,conflict=1,invalid=1,unavailable=False)
    with sync.connect(db) as c:
        diagnostics={r['path']:(r['kind'],r['message']) for r in c.execute('SELECT * FROM _sync_diagnostics')}
        expected=sync.canonical([['pending','summary_state',sync.canonical(['missing']),None,missing[1][1]['body'][0]['parent']]])
        assert diagnostics[str(missing[1][0])]==('pending',expected)
        assert diagnostics[str(conflict[0])][0]=='conflict'
        assert diagnostics[str(invalid[0])][0]=='invalid'
    sync.receive(db,exchange)
    with sync.connect(db) as c:
        assert {r['path']:(r['kind'],r['message']) for r in c.execute('SELECT * FROM _sync_diagnostics')}==diagnostics


@pytest.mark.parametrize('indexed',[False,True],ids=['legacy','indexed'])
def test_pending_diagnostics_written_only_at_end(chain,monkeypatch,indexed):
    from contextlib import contextmanager
    db,exchange,make,order=chain
    packets=make(4); order(list(reversed(packets)))
    held=make(2,'held',100); held[0][0].unlink()
    if indexed:
        with sync.connect(db) as c,c: store.migrate(c)
    writes=[]; original=sync.connect
    @contextmanager
    def connect(*args,**kwargs):
        with original(*args,**kwargs) as c:
            c.set_trace_callback(lambda sql: writes.append(sql) if sql.startswith('INSERT OR REPLACE INTO _sync_diagnostics') else None)
            yield c
    monkeypatch.setattr(sync,'connect',connect)
    report=sync.receive(db,exchange)
    assert report['applied']==4 and report['pending']==1
    assert len(writes)==1
    assert str(held[1][0]) in writes[0]


@pytest.mark.parametrize('indexed',[False,True],ids=['legacy','indexed'])
def test_interrupt_before_dependency_attempt(chain,monkeypatch,indexed):
    db,exchange,make,order=chain
    packets=make(4); order(list(reversed(packets)))
    if indexed:
        with sync.connect(db) as c,c: store.migrate(c)
    attempts=instrument(monkeypatch)
    report=sync.receive(db,exchange,interrupt=lambda: len(attempts)>=5)
    assert report['interrupted'] and report['applied']==2
    assert attempts[-2:]==[p['uuid'] for _,p in packets[:2]]
    assert report['pending']==2
    assert sync.receive(db,exchange)['applied']==2


def test_v2_chain(chain,monkeypatch):
    import resolution_apply as app
    import resolution_protocol as protocol
    db,exchange,make,order=chain
    packets=make(20); order(list(reversed(packets)))
    for path,p in packets:
        for ch in p['body']:
            parent=ch.pop('parent'); ch.update(parents=[parent] if parent else [],origin=None)
        p.update(format='omp-memory-changes-v2',kind='change',covers=[],evidence=[])
        p['checksum']=protocol.payload_digest({k:p[k] for k in ('group','node','uuid','kind','body','covers','evidence')})
        path.write_text(sync.canonical(p),encoding='utf-8')
    order(list(reversed(packets)))
    attempts=[]; original=app.receive_indexed
    def receive(*args,**kwargs):
        attempts.append(1); return original(*args,**kwargs)
    monkeypatch.setattr(app,'receive_indexed',receive)
    assert sync.receive(db,exchange)==dict(applied=20,pending=0,conflict=0,invalid=0,unavailable=False)
    # Includes the mandatory first-migration replay.
    assert len(attempts)<=60


def test_mtime_then_filename_order(chain,monkeypatch):
    db,exchange,make,order=chain
    packets=[make(1,str(n),n*100)[0] for n in range(3)]
    order([packets[2],packets[1],packets[0]])
    stamp=packets[1][0].stat().st_mtime_ns
    os.utime(packets[0][0],ns=(stamp,stamp))
    attempts=instrument(monkeypatch)
    assert sync.receive(db,exchange)['applied']==3
    assert attempts==[packets[n][1]['uuid'] for n in (2,0,1)]


@pytest.mark.parametrize('indexed',[False,True],ids=['legacy','indexed'])
def test_multiple_parents_are_retried_under_normal_rules(chain,monkeypatch,indexed):
    db,exchange,make,order=chain
    a=make(2,'a'); b=make(2,'b',100)
    path,packet=a[1]
    packet['body']+=b[1][1]['body']; packet['checksum']=sync.digest(packet['body'])
    path.write_text(sync.canonical(packet),encoding='utf-8'); b[1][0].unlink()
    order([a[1],a[0],b[0]])
    if indexed:
        with sync.connect(db) as c,c: store.migrate(c)
    attempts=instrument(monkeypatch)
    assert sync.receive(db,exchange)==dict(applied=3,pending=0,conflict=0,invalid=0,unavailable=False)
    assert attempts.count(packet['uuid'])==3
    with sync.connect(db) as c:
        assert [r[0] for r in c.execute('SELECT last_event_id FROM summary_state')]==[2,2]


def test_no_parent_hint_uses_fallback(chain,monkeypatch):
    db,exchange,make,order=chain
    held=make(1,'held')[0]; other=make(1,'other',100)[0]; order([held,other])
    original=sync._apply; applied=[]
    def apply(c,p):
        if p['uuid']==held[1]['uuid'] and not applied: raise sync.Pending('waiting for foreign key targets')
        result=original(c,p); applied.append(p['uuid']); return result
    monkeypatch.setattr(sync,'_apply',apply)
    assert sync.receive(db,exchange)['applied']==2
    assert applied==[other[1]['uuid'],held[1]['uuid']]


def test_interrupt_after_resolution_clears_deferred_covered_diagnostic(tmp_path):
    import resolution_apply as app
    from test_resolution_apply import branches,resolve,activate
    a,b,db,p,q=branches(tmp_path)
    # Precreate helper fixture directories with Windows-safe private permissions.
    for path in (a.with_suffix('.activation'),db.with_suffix('.activation'),
                 tmp_path/'activation-exchange',tmp_path/'activation-exchange'/'.stfolder'):
        identity_backup.private_mkdir(path)
    resolution=resolve(app,a,[p,q],dict(x=5,y=2,z=4)); activate(db)
    exchange=tmp_path/'exchange'; identity_backup.private_mkdir(exchange)
    identity_backup.private_mkdir(exchange/'changes')
    for n,packet in enumerate((p,q,resolution)):
        directory=exchange/'changes'/packet['node']
        if not directory.exists(): identity_backup.private_mkdir(directory)
        path=directory/(packet['uuid']+'.json'); path.write_text(sync.canonical(packet),encoding='utf-8')
        stamp=1_700_000_000_000_000_000+n*1_000_000_000; os.utime(path,ns=(stamp,stamp))
    checks=[]
    def interrupt(): checks.append(1); return len(checks)>3
    report=sync.receive(db,exchange,interrupt=interrupt)
    assert report==dict(applied=2,pending=0,conflict=0,invalid=0,unavailable=False,interrupted=True)
