"""ST gates: synthetic files, clocks, processes and HTTP transports only."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
import pytest
import adapters
import storage

NOW=datetime(2026,10,22,12,tzinfo=timezone.utc)

def put(root,name,data=b'1234'):
    path=root/name; path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data if isinstance(data,bytes) else json.dumps(data).encode()); return path

def test_st1_cache():
    clock=[0]; calls=[]; entered=threading.Event(); release=threading.Event()
    def compute():
        calls.append(1); entered.set(); assert release.wait(2); return {'available':True,'n':len(calls)}
    cache=storage.Cache(compute,clock=lambda:clock[0])
    assert cache.get()=={'available':False,'pending':True}
    assert entered.wait(1); assert cache.get()['pending']; assert len(calls)==1
    release.set(); cache.thread.join(2)
    assert cache.get()['n']==1
    clock[0]=599; assert cache.get()['n']==1
    def fail(): raise ValueError('PRIVATE PATH')
    cache.compute=fail; clock[0]=600; cache.get(); cache.thread.join(2)
    assert cache.get()=={'available':True,'n':1,'error':'ValueError'}

def test_st2_sqlite(tmp_path,monkeypatch):
    db=tmp_path/'mac.db'
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE memory_items(id); INSERT INTO memory_items VALUES(1); CREATE TABLE _sync_old(x); CREATE TABLE pg_old(x);')
    original=adapters.sqlite_ro; calls=[]
    @contextmanager
    def ro(path):
        calls.append(path)
        with original(path) as c:
            with pytest.raises(sqlite3.OperationalError): c.execute('DELETE FROM memory_items')
            yield c
    monkeypatch.setattr(adapters,'sqlite_ro',ro)
    before={p:(p.stat().st_mtime_ns,p.read_bytes()) for p in tmp_path.iterdir()}
    value=storage.sqlite_stats(db); storage.disk_stats(tmp_path)
    assert calls==[db] and value['rows']['memory_items']==1
    assert value['rows']['summary_state'] is None and value['dbstat']
    assert value['reclaimable_bytes']==value['page_size']*value['freelist_count']
    assert {r['name'] for r in value['tables'] if r['retired']}=={'_sync_old','pg_old'}
    assert all((p.stat().st_mtime_ns,p.read_bytes())==v for p,v in before.items())

def test_st3_fallback(tmp_path,monkeypatch):
    db=tmp_path/'mac.db'
    with sqlite3.connect(db) as c: c.execute('CREATE TABLE memory_items(id)')
    original=adapters.sqlite_ro
    @contextmanager
    def ro(path):
        with original(path) as c:
            class NoStat:
                def execute(self,sql,*args):
                    if 'dbstat' in sql: raise sqlite3.OperationalError('unavailable')
                    return c.execute(sql,*args)
            yield NoStat()
    monkeypatch.setattr(adapters,'sqlite_ro',ro)
    result=storage.sqlite_stats(db)
    assert result['dbstat'] is False and result['rows']['memory_items']==0
    assert result['tables']==[dict(name='memory_items',bytes=None,rows=0,retired=False,note=None)]

def test_st5_walk(tmp_path):
    root=tmp_path/'root'; put(root,'logs/a',b'123'); outside=tmp_path/'outside'; put(outside,'huge',b'x'*10000)
    (root/'logs/link').symlink_to(outside,target_is_directory=True)
    value=storage.disk_stats(root)
    assert value['bytes']==3 and not value['truncated']
    assert storage.disk_stats(root,limit=1)['truncated']
    (root/'backups').symlink_to(outside,target_is_directory=True)
    assert storage.disk_stats(root)['bytes']==3

def test_st6_r2():
    row=dict(remote_objects=8,local_objects=4,interval_s=60,uploaded=2,downloaded=3)
    value=storage.r2_stats(row,100,2,NOW)
    assert value['bytes']==200 and value['estimate']
    assert value['class_a_month']==43200*5 and value['class_b_month']==43200*3
    row.update(totals_since='2026-10-20T12:00:00Z',total_bytes_up=400,total_bytes_down=800,total_class_a=10,total_class_b=20)
    value=storage.r2_stats(row,100,2,NOW)
    assert value['class_a_month']==150 and value['class_b_month']==300
    assert value['network']['bytes_up_day']==200 and not value['ops_estimate']

def run(model='claude-haiku-5-5',**kw):
    return dict(model=model,start='2026-10-22T10:00:00Z',input_total_tokens=100000,input_uncached_tokens=100000,cache_read_tokens=0,output_tokens=1000,**kw)

def test_st8_costs(tmp_path):
    prices=storage.load_pricing(tmp_path)['prices']
    low=storage.r2_cost(dict(bytes=1e9,class_a_month=10,class_b_month=20),prices)
    assert low['total']==0 and low['storage']['headroom_percent']==90
    high=storage.r2_cost(dict(bytes=20e9,class_a_month=2e6,class_b_month=11e6),prices)
    assert high['total']==pytest.approx(.15+4.5+.36)
    a=run(); b=dict(a,input_total_tokens=100001); c=run('claude-sonnet-5-5')
    assert storage.run_cost(a,prices)['cost']==pytest.approx(.0105)
    assert storage.run_cost(b,prices)['cost']==pytest.approx(.0525)
    costs=storage.summary_cost([a,b,c],prices,NOW)
    assert costs['month']==pytest.approx(.273) and costs['week']==pytest.approx(.273)
    assert costs['projection']==pytest.approx(.273*30/7) and len(costs['models'])==2

def test_st9_unknown(tmp_path):
    prices=storage.load_pricing(tmp_path)['prices']
    assert storage.run_cost(run('unknown'),prices)['cost'] is None
    assert storage.r2_cost({},prices)['total'] is None
    assert storage.summary_cost([run('unknown')],prices,NOW)['month'] is None
    # A failed run without token counts must not hide the priced runs' total.
    failed=dict(run(),input_total_tokens=None,input_uncached_tokens=None,output_tokens=None)
    mixed=storage.summary_cost([run(),failed],prices,NOW)
    assert mixed['month']==pytest.approx(.0105) and mixed['unpriced_runs']['month']==1
    assert storage.run_cost(dict(run(),input_uncached_tokens=None),prices)['breakdown_unknown']

def test_st10_pricing(tmp_path):
    default=storage.load_pricing(tmp_path)['prices']
    path=put(tmp_path,'insight-pricing.json',{'energy':{'watts_per_core':10}})
    assert storage.load_pricing(tmp_path)['prices']['energy']==dict(default['energy'],watts_per_core=10)
    for bad in ({'energy':{'watts_per_core':-1}},{'as_of':'2026-02-30'},{'r2':{'storage':True}},{'extra':1}):
        path.write_text(json.dumps(bad)); value=storage.load_pricing(tmp_path)
        assert value['invalid'] and value['prices']==default

def test_st11_processes():
    assert [storage.ps_seconds(v) for v in ('01:02','01:02.50','02:01:02','3-02:01:02')]==[62,62.5,7262,266462]
    # macOS cputime uses single-digit minutes, e.g. '0:12.34' (seen live).
    assert [storage.ps_seconds(v) for v in ('0:12.34','12:05.10','1:02:03')]==[12.34,725.1,3723]
    for v in ('x','1:60','1:2','1-25:00:00','-1:00','1:00xxx'):
        with pytest.raises(ValueError): storage.ps_seconds(v)
    calls=[]
    def runner(argv,**kw):
        calls.append(argv); assert kw['timeout']==5 and not kw.get('shell')
        return SimpleNamespace(returncode=0,stdout='"PID" = 42;' if argv[0]=='launchctl' else '42' if argv[0]=='pgrep' else '42 01:00 2048 02:00:00')
    rows=storage.services(runner)
    assert len([c for c in calls if c[0]=='ps'])==1 and ['pgrep','-x','cloudflared'] in calls
    assert all(r['cpu_s']==60 and r['cpu_day']==720 and r['rss_mb']==2 for r in rows)

def test_st12_missing():
    rows=storage.services(lambda *a,**k:SimpleNamespace(returncode=1,stdout=''))
    assert all(r['state']=='not running' and r['cpu_s'] is None for r in rows)

def test_st13_jobs(tmp_path):
    put(tmp_path,'status/launchd-summary.log',b'\n'.join(json.dumps(r).encode() for r in [
        dict(at='2026-10-22T10:00:00Z',exit=0,wall_s=10,cpu_s=2,maxrss_mb=4),
        dict(at='2026-10-20T10:00:00Z',exit=0,wall_s=20,cpu_s=3,maxrss_mb=5),
        dict(at='2026-09-20T10:00:00Z',exit=0)]))
    result=storage.job_stats(tmp_path,NOW)[1]
    assert result['today']['wall_s']==10 and result['week']['cpu_s']==5
    assert result['projection']['cpu_s']==pytest.approx(5*30/7)
    assert result['cpu_share']==pytest.approx(5/30) and result['waiting_s']==25
    assert result['runs_day']==pytest.approx(2/7)
    put(tmp_path,'status/launchd-ingest.log',dict(at='2026-10-22T10:00:00Z',exit=0))
    result=storage.job_stats(tmp_path,NOW)[0]
    assert result['today']['runs']==1 and result['today']['cpu_s'] is None

def test_st14_background(tmp_path,monkeypatch):
    calls=[]
    def runner(*a,**kw):
        assert threading.current_thread() is not threading.main_thread(); calls.append(a)
        return SimpleNamespace(returncode=1,stdout='')
    observer=storage.Observer(tmp_path,tmp_path,None,runner=runner,fetcher=lambda url: '')
    cache=storage.Cache(observer.compute)
    assert cache.get()['pending']; cache.thread.join(3)
    assert calls and cache.get()['available']

def test_st15_buffers(tmp_path):
    put(tmp_path,'profile-log/sync-status.json',dict(outbox=2,at='2026-10-22T11:00:00Z',interval_s=60))
    put(tmp_path,'jobs/.bounded_digest_state.json',dict(pending={'pending_ids':[1,2]}))
    obs=storage.Observer(tmp_path,tmp_path,None,runner=lambda *a,**k:SimpleNamespace(returncode=1,stdout=''),fetcher=lambda u:'',clock=lambda:NOW)
    data=obs.compute(); rows={r['category']:r for r in data['buffers']}
    assert rows['R2 upload outbox']['warning'] and rows['Summary backlog']['count']==2
    assert rows['Gateway remember outbox']['count'] is None
    put(tmp_path,'jobs/.bounded_digest_state.json',dict(pending={'pending_ids':[1,2,3]}))
    rows={r['category']:r for r in obs.compute()['buffers']}
    assert rows['Summary backlog']['warning']
    assert 'bridge writes drain status' in data['followups']

def test_st16_leftovers(tmp_path):
    for name,size in [('gateway-old/a',3),('gateway-new/a',5),('status/insight-release-old/a',7),('status/insight-release-now/a',11),('profile-log/index/gone.snap',13),('profile-log/index/live.snap',17),('profile-log/agents/live.key',1),('app/profile-log.old-1/a',19),('versions/a',23),('exchange/a',29),('memory-sync/data/baseline.jsonl',31),('memory-sync/data/agentmesh.stdout.log',37)]: put(tmp_path,name,b'x'*size)
    os.utime(tmp_path/'gateway-old',(1,1)); os.utime(tmp_path/'gateway-new',(2,2))
    value=storage.disk_stats(tmp_path,running=tmp_path/'status/insight-release-now')
    rows={r['category']:r for r in value['reclaimable']}
    assert rows['gateway-*']['bytes']==3 and rows['status']['bytes']==7
    assert rows['orphan snapshots']['bytes']==13 and rows['orphan snapshots']['count']==1
    assert rows['old packet baseline']['bytes']==31 and rows['old worker logs']['bytes']==37
    assert all(name not in json.dumps(value) for name in ('gone.snap','live.key','gateway-old','insight-release-now'))

def test_st18_tunnel():
    text='cloudflared_tunnel_total_requests 20\ncloudflared_tunnel_response_by_code{code="200",host="SECRET"} 18\ncloudflared_tunnel_rx_bytes_total 100\nother_secret 99\n'
    value=storage.parse_tunnel(text)
    assert value==dict(requests=20,responses={'200':18},bytes={'cloudflared_tunnel_rx_bytes_total':100})
    with pytest.raises(ValueError): storage.fetch_tunnel('http://example.com:20241/metrics')
    class Connection:
        def __init__(self,*args,**kwargs): assert kwargs['timeout']==2
        def request(self,*args): pass
        def getresponse(self): return SimpleNamespace(status=200,read=lambda n:b'x'*n)
        def close(self): pass
    with pytest.raises(ValueError): storage.fetch_tunnel('http://127.0.0.1:20241/metrics',connection=Connection)
    class Slow(Connection):
        def getresponse(self): raise TimeoutError()
    with pytest.raises(TimeoutError): storage.fetch_tunnel('http://127.0.0.1:20241/metrics',connection=Slow)

def test_st19_allowlist():
    import remote_access
    assert remote_access.ALLOWED_PATHS==frozenset(('/', '/metrics.html','/health','/api/profile-log','/api/metrics','/api/connections','/api/operations','/api/storage'))


def test_st18_deadline_and_redirect():
    tick=[0]
    class Connection:
        sock=None
        def __init__(self,*a,**kw): pass
        def request(self,*a): pass
        def getresponse(self):
            tick[0]=3; return SimpleNamespace(status=200,read=lambda n:b'')
        def close(self): pass
    with pytest.raises(TimeoutError): storage.fetch_tunnel('http://127.0.0.1:20241/metrics',connection=Connection,clock=lambda:tick[0])
    class Redirect(Connection):
        def getresponse(self): return SimpleNamespace(status=302)
    with pytest.raises(ValueError): storage.fetch_tunnel('http://127.0.0.1:20241/metrics',connection=Redirect)


def test_st15_audit_and_unknown(tmp_path):
    now=[NOW]; obs=storage.Observer(tmp_path,tmp_path,None,runner=lambda *a,**k:SimpleNamespace(returncode=1,stdout=''),fetcher=lambda u:'',clock=lambda:now[0])
    value=obs.compute(); rows={r['category']:r for r in value['buffers']}
    assert rows['Broker audit log']['bytes'] is None
    raw={'ts':int(NOW.timestamp()*1000)-86400000,'query_hmac':'PRIVATE'}
    path=put(tmp_path,'profile-log/audit.jsonl',raw)
    obs.previous={}; rows={r['category']:r for r in obs.compute()['buffers']}
    assert rows['Broker audit log']['growth_day']==path.stat().st_size
    assert 'PRIVATE' not in json.dumps(rows)


def test_st18_two_samples(tmp_path):
    from datetime import timedelta
    now=[NOW]; values=[20]
    obs=storage.Observer(tmp_path,tmp_path,None,runner=lambda *a,**k:SimpleNamespace(returncode=1,stdout=''),fetcher=lambda u:'cloudflared_tunnel_total_requests '+str(values[0]),clock=lambda:now[0])
    assert obs.compute()['network']['tunnel']['per_day'] is None
    now[0]+=timedelta(days=1); values[0]=50
    tunnel=obs.compute()['network']['tunnel']
    assert tunnel['per_day']['requests']==30 and tunnel['projection']['requests']==900
    now[0]+=timedelta(days=1); values[0]=1
    assert obs.compute()['network']['tunnel']['per_day']['requests'] is None


def test_st8_windows_and_writes(tmp_path):
    prices=storage.load_pricing(tmp_path)['prices']; a=run()
    rows=[a,dict(a,start='2026-10-02T00:00:00Z'),dict(a,start='2026-09-30T00:00:00Z'),dict(a,start='2026-10-23T00:00:00Z')]
    result=storage.summary_cost(rows,prices,NOW)
    assert result['month']==pytest.approx(.021) and result['week']==pytest.approx(.0105)
    row=dict(a,cache_write_5m_tokens=100,cache_write_1h_tokens=200)
    assert storage.run_cost(row,prices)['cost']==pytest.approx(.0105+.0000125+.00004)


def test_st5_missing_tree(tmp_path):
    result=storage.disk_stats(tmp_path/'absent')
    assert result['bytes'] is None


def test_st15_ingest(tmp_path):
    db=tmp_path/'mac.db'
    with sqlite3.connect(db) as c:
        c.executescript((Path(__file__).parents[1]/'memory-sync/schema.sql').read_text())
        c.execute("INSERT INTO source_sessions(source_path,source_agent) VALUES('PRIVATE','codex')")
        c.execute("INSERT INTO ingestion_cursors(source_path,updated_at) VALUES('PRIVATE','2026-10-20T12:00:00Z')")
        c.execute("INSERT INTO observation_events(event_key,source_path,source_agent,kind,content,source_hash,occurred_at) VALUES('a','PRIVATE','codex','user','SECRET','x','2026-10-20T12:00:00Z')")
    obs=storage.Observer(tmp_path,tmp_path,db,runner=lambda *a,**k:SimpleNamespace(returncode=1,stdout=''),fetcher=lambda u:'',clock=lambda:NOW)
    rows={r['category']:r for r in obs.compute()['buffers']}
    assert rows['Ingest codex']['warning'] and rows['Ingest cursor codex']['age_s']==2*86400
    assert 'PRIVATE' not in json.dumps(rows) and 'SECRET' not in json.dumps(rows)


def test_st6_snapshot_rate():
    row=dict(at='2026-10-21T12:00:00Z',totals_since='2026-10-20T12:00:00Z',total_bytes_up=400,total_bytes_down=800,total_class_a=10,total_class_b=20)
    value=storage.r2_stats(row,100,2,NOW)
    assert value['network']['bytes_up_day']==400


def test_st14_request(tmp_path,monkeypatch):
    import io
    import server
    entered=threading.Event(); release=threading.Event(); caller=threading.current_thread(); threads=[]
    def compute(self):
        threads.append(threading.current_thread()); entered.set(); assert release.wait(2)
        return {'available':True}
    monkeypatch.setattr(storage.Observer,'compute',compute)
    monkeypatch.setattr(server,'ThreadingHTTPServer',lambda address,handler:SimpleNamespace(server_port=51877,handler=handler))
    app=server.make_server(tmp_path,0,agentmesh_home=tmp_path)
    class Client:
        def __init__(self): self.output=io.BytesIO()
        def makefile(self,*a): return io.BytesIO(b'GET /api/storage HTTP/1.0\r\nHost: 127.0.0.1:51877\r\n\r\n')
        def sendall(self,value): self.output.write(value)
    client=Client()
    try:
        app.handler(client,('127.0.0.1',1),app)
        assert entered.wait(1) and threads[0] is not caller
        assert json.loads(client.output.getvalue().split(b'\r\n\r\n')[1])=={'available':False,'pending':True}
    finally:
        release.set()
        for thread in threads: thread.join(2)
