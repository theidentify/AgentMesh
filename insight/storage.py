"""Background-only, content-free storage, cost and resource observations."""
from datetime import date, datetime, timezone
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import socket
import stat
import subprocess
import threading
import time
from urllib.parse import urlsplit
import adapters

DAY=86400
IO=('bytes_up','bytes_down','class_a','class_b')
DEFAULTS={'as_of':'2026-10-22','sources':['https://developers.cloudflare.com/r2/pricing/','https://platform.claude.com/docs/en/about-claude/pricing'],
    'r2':{'storage':.015,'class_a':4.5,'class_b':.36,'free_storage_gb':10,'free_class_a':1000000,'free_class_b':10000000,'egress':0},
    'models':{'claude-haiku-5-5':{'input':.10,'write_5m':.125,'write_1h':.20,'read':.01,'output':.50,'long':{'input':.50,'write_5m':.625,'write_1h':1.,'read':.05,'output':2.50}},
        'claude-sonnet-5-5':{'input':2.,'write_5m':2.50,'write_1h':4.,'read':.10,'output':10.},
        'claude-opus-5-5':{'input':4.,'write_5m':5.,'write_1h':8.,'read':.20,'output':20.}},
    'cloudflare':{'Worker':'Free: 100,000 requests/day, 10 ms CPU/request. See Cloudflare dashboard (requests, CPU ms).',
        'KV':'Free: 100,000 reads/day; 1,000 writes, deletes and lists/day; 1 GB storage. See Cloudflare dashboard.',
        'Access':'Free plan: up to 50 users. See Cloudflare dashboard.'},
    'energy':{'watts_per_core':5,'price_per_kwh':None}}


def number(v): return v if type(v) in (int,float) and math.isfinite(v) and 0<=v<=2**53-1 else None

def instant(v):
    try:
        d=datetime.fromisoformat(v.replace('Z','+00:00'))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError,AttributeError,OverflowError): return None


def safe_path(path):
    path=Path(path).absolute()
    return not any(p.is_symlink() for p in (path,*path.parents))


def read_bytes(path,cap=1024*1024,tail=False):
    if not safe_path(path): raise ValueError('symlink')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(fd,'rb') as f:
        info=os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode): raise ValueError('regular file required')
        offset=max(0,info.st_size-cap) if tail else 0
        f.seek(offset); data=f.read(cap+1)
        if len(data)>cap: raise ValueError('oversized')
    return data.split(b'\n',1)[-1] if offset else data


def read_json(path):
    try:
        value=json.loads(read_bytes(path)); return value if type(value) is dict else {}
    except (OSError,ValueError,RecursionError): return {}


class Cache:
    def __init__(self,compute,ttl=600,clock=time.monotonic):
        self.compute,self.ttl,self.clock=compute,ttl,clock
        self.value=None; self.expires=0; self.lock=threading.Lock(); self.thread=None; self.running=False; self.error=None

    def get(self):
        with self.lock:
            value=dict(self.value) if self.value is not None else {'available':False,'pending':True}
            if self.error: value['error']=self.error
            if not self.running and self.clock()>=self.expires:
                self.running=True; self.thread=threading.Thread(target=self._load,daemon=True); self.thread.start()
            return value

    def _load(self):
        try: value=self.compute()
        except Exception as exc:
            with self.lock: self.error=type(exc).__name__
        else:
            with self.lock: self.value=value; self.error=None
        finally:
            with self.lock: self.expires=self.clock()+self.ttl; self.running=False


def file_size(path):
    try:
        if not safe_path(path): return None
        s=Path(path).lstat(); return s.st_size if stat.S_ISREG(s.st_mode) else None
    except FileNotFoundError: return 0
    except OSError: return None


def sqlite_stats(path):
    result=dict(available=False,dbstat=None,rows={},tables=[])
    if path is None or not safe_path(path): return result
    result.update(db_bytes=file_size(path),wal_bytes=file_size(str(path)+'-wal'),shm_bytes=file_size(str(path)+'-shm'))
    try:
        with adapters.sqlite_ro(path) as c:
            for key in ('page_size','page_count','freelist_count'): result[key]=c.execute('PRAGMA '+key).fetchone()[0]
            result['reclaimable_bytes']=result['page_size']*result['freelist_count']
            names=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            def count(name): return c.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0]
            result['rows']={n:count(n) if n in names else None for n in ('observation_events','memory_items','memory_summaries','summary_state')}
            try:
                sizes=[dict(name=r[0],bytes=r[1],rows=None) for r in c.execute('SELECT name,SUM(pgsize) FROM dbstat GROUP BY name ORDER BY SUM(pgsize) DESC')]
                result['dbstat']=True
            except sqlite3.OperationalError:
                sizes=[dict(name=n,bytes=None,rows=count(n)) for n in names]; result['dbstat']=False
            for row in sizes:
                row.update(retired=row['name'].startswith(('_sync_','_pg_','pg_')),note=None)
                if row['retired']: row['note']='removed in 7b'
            result['tables']=sizes[:15] if result['dbstat'] else sizes
            result['retired']=[dict(category=prefix,bytes=sum(r['bytes'] for r in sizes if r['name'].startswith(prefix)) if result['dbstat'] else None,retired=True,note='removed in 7b') for prefix in ('_sync_','_pg_','pg_')]
            result['available']=True
    except (OSError,sqlite3.Error): pass
    return result


def disk_stats(root,limit=200000,running=None):
    root=Path(root); running=Path(running or __file__).absolute()
    if running.suffix=='.py': running=running.parent
    files={}; dirs={}; entries=0; truncated=False; incomplete=False
    available=safe_path(root) and root.is_dir()
    stack=[root] if available else []
    while stack:
        directory=stack.pop()
        try:
            with os.scandir(directory) as scan:
                for e in scan:
                    if entries>=limit: truncated=True; stack.clear(); break
                    entries+=1
                    if e.is_symlink(): continue
                    rel=Path(e.path).relative_to(root).as_posix()
                    try:
                        s=e.stat(follow_symlinks=False)
                        if stat.S_ISDIR(s.st_mode): dirs[rel]=s.st_mtime; stack.append(Path(e.path))
                        elif stat.S_ISREG(s.st_mode): files[rel]=s.st_size
                    except OSError: incomplete=True
        except FileNotFoundError: pass
        except OSError: incomplete=True
    def selected(prefix): return {p:n for p,n in files.items() if p==prefix or p.startswith(prefix+'/')}
    def totals(paths): return dict(bytes=sum(paths.values()),count=len(paths))
    categories=[dict(category=n,**totals(selected(n))) for n in ('memory-sync','profile-log','exchange','logs','backups','jobs','venv','versions','app','status')]
    gateways=sorted((p for p in dirs if '/' not in p and p.startswith('gateway-')),key=lambda p:(dirs[p],p),reverse=True)
    categories.append(dict(category='gateway-*',bytes=sum(sum(selected(p).values()) for p in gateways),count=len(gateways)))
    categories.append(dict(category='other',**totals({p:n for p,n in files.items() if p.split('/')[0] not in {r['category'] for r in categories} and not p.split('/')[0].startswith('gateway-')})))
    reclaim=[]
    def leftover(category,paths,note,count=None):
        row=dict(category=category,**totals(paths),note=note)
        if count is not None: row['count']=count
        if row['bytes'] or row['count']: reclaim.append(row)
    oldgate={f:n for p in gateways[1:] for f,n in selected(p).items()}
    leftover('gateway-*',oldgate,'keep the one wrangler deploys from',len(gateways[1:]))
    releases=[p for p in dirs if p.startswith('status/insight-release-') and p.count('/')==1]
    oldreleases=[p for p in releases if (root/p).absolute()!=running]
    leftover('status',{f:n for p in oldreleases for f,n in selected(p).items()},'Insight releases other than the running one',len(oldreleases))
    oldapps=[p for p in dirs if p.startswith('app/profile-log.old-') and p.count('/')==1]
    leftover('app',{f:n for p in oldapps for f,n in selected(p).items()},'old profile-log applications',len(oldapps))
    for cat,note in [('exchange','packet sync retired (Phase 7a)'),('versions','old releases')]: leftover(cat,selected(cat),note)
    leftover('old packet baseline',{p:n for p,n in files.items() if p in ('memory-sync/data/baseline.jsonl','memory-sync/data/baseline.jsonl.gz')},'old packet baseline')
    leftover('old worker logs',{p:n for p,n in files.items() if re.fullmatch(r'memory-sync/data/agentmesh\.std[^/]*\.log',p)},'old worker logs')
    leftover('orphan snapshots',{p:n for p,n in files.items() if not truncated and not incomplete and re.fullmatch(r'profile-log/index/[^/]+\.snap',p) and 'profile-log/agents/'+Path(p).stem+'.key' not in files},'index snapshots for removed agents')
    for row in categories:
        row['reclaimable_bytes']=sum(r['bytes'] for r in reclaim if r['category']==row['category'])
        if row['category']=='status': row['release_count']=len(releases); row['release_bytes']=sum(sum(selected(p).values()) for p in releases)
    replica=totals(selected('profile-log/replica')); replica['split']=[dict(category=p,**totals(selected('profile-log/replica/'+p))) for p in ('events','keys','registry','connectors')]
    replica['split'].append(dict(category='other',**totals({p:n for p,n in selected('profile-log/replica').items() if p.split('/')[2] not in ('events','keys','registry','connectors')})))
    prefixes={p for p in dirs if p.startswith('profile-log/replica/events/') and p.count('/')==4}
    profile=dict(**totals(selected('profile-log')),replica=replica,index=totals(selected('profile-log/index')),audit_bytes=files.get('profile-log/audit.jsonl'),prefixes=len(prefixes))
    return dict(bytes=sum(files.values()) if available else None,categories=sorted(categories,key=lambda r:r['bytes'],reverse=True),reclaimable=reclaim,profile=profile,truncated=truncated,incomplete=incomplete)


def load_pricing(root):
    import copy
    defaults=copy.deepcopy(DEFAULTS)
    def merge(base,value):
        if type(value) is not dict or set(value)-set(base): raise ValueError('keys')
        for k,v in value.items():
            old=base[k]
            if type(old) is dict: merge(old,v)
            elif k=='as_of':
                if type(v) is not str or date.fromisoformat(v).isoformat()!=v: raise ValueError('date')
                base[k]=v
            elif k=='sources':
                if type(v) is not list or any(type(s) is not str or not s.startswith('https://') for s in v): raise ValueError('sources')
                base[k]=v
            elif type(old) is str:
                if type(v) is not str or len(v)>500: raise ValueError('text')
                base[k]=v
            else:
                if not (k=='price_per_kwh' and v is None) and number(v) is None: raise ValueError('number')
                base[k]=v
    try:
        shipped=Path(__file__).with_name('pricing.json')
        if shipped.exists(): merge(defaults,json.loads(read_bytes(shipped)))
        value=copy.deepcopy(defaults); override=Path(root)/'insight-pricing.json'
        if override.exists() or override.is_symlink(): merge(value,json.loads(read_bytes(override)))
        return dict(prices=value,invalid=False)
    except (OSError,ValueError,TypeError,RecursionError): return dict(prices=copy.deepcopy(DEFAULTS),invalid=True)


def r2_stats(row,replica_bytes,prefixes,now):
    get=lambda k:number(row.get(k))
    local,remote,interval=get('local_objects'),get('remote_objects'),get('interval_s')
    size=replica_bytes*remote/local if replica_bytes is not None and remote is not None and local else None
    syncs=30*DAY/interval if interval else None
    a=syncs*(prefixes+1+(get('uploaded_average') if get('uploaded_average') is not None else get('uploaded') or 0)) if syncs is not None and prefixes is not None else None
    b=syncs*get('downloaded') if syncs is not None and get('downloaded') is not None else None
    since=instant(row.get('totals_since')); end=instant(row.get('at')) or now
    elapsed=(end-since).total_seconds() if since and end<=now else 0
    exact=elapsed>0 and all(get('total_'+k) is not None for k in IO)
    network={k+'_day':get('total_'+k)*DAY/elapsed if exact else None for k in IO}
    network.update({k+'_month':v*30 if v is not None else None for k,v in [(k,network[k+'_day']) for k in IO]})
    if exact: a,b=network['class_a_month'],network['class_b_month']
    return dict(objects=remote,bytes=size,estimate=True,interval_s=interval,lists_per_sync=prefixes+1 if prefixes is not None else None,class_a_month=a,class_b_month=b,ops_estimate=not exact,network=network)


def r2_cost(value,prices):
    p=prices['r2']; rows={}
    for key,used,free,price in [('storage',value.get('bytes')/1e9 if value.get('bytes') is not None else None,p['free_storage_gb'],p['storage']),('class_a',value.get('class_a_month'),p['free_class_a'],p['class_a']/1e6),('class_b',value.get('class_b_month'),p['free_class_b'],p['class_b']/1e6)]:
        rows[key]=dict(used=used,free=free,cost=max(0,used-free)*price if used is not None else None,headroom_percent=max(0,(free-used)/free*100) if used is not None and free else None)
    return dict(rows,total=sum(r['cost'] for r in rows.values()) if all(r['cost'] is not None for r in rows.values()) else None,estimate=True)


def run_cost(row,prices):
    p=prices['models'].get(row.get('model')); result=dict(cost=None,breakdown_unknown=False)
    if p is None: return result
    total=number(row.get('input_total_tokens'))
    if 'long' in p:
        if total is None: return result
        if total>100000: p=p['long']
    uncached,read,out=(number(row.get(k)) for k in ('input_uncached_tokens','cache_read_tokens','output_tokens'))
    if uncached is None or read is None: uncached,read=total,0; result['breakdown_unknown']=True
    if uncached is None or out is None: return result
    cost=uncached*p['input']+read*p['read']+out*p['output']
    for key,price in [('cache_write_5m_tokens','write_5m'),('cache_write_1h_tokens','write_1h')]:
        tokens=number(row.get(key))
        if tokens is not None: cost+=tokens*p[price]
    result['cost']=cost/1e6; return result


def summary_cost(runs,prices,now):
    month=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    dated=[(r,instant(r.get('end') or r.get('start'))) for r in runs]
    def cost(rows):
        values=[v for v in (run_cost(r,prices)['cost'] for r in rows) if v is not None]
        return sum(values) if values else None
    current=[r for r,t in dated if t and month<=t<=now]; week=[r for r,t in dated if t and 0<=(now-t).total_seconds()<=7*DAY]
    weekly=cost(week)
    api=any(r.get('billing_source')=='api_key' for r in current+week)
    priced=lambda rows:sum(run_cost(r,prices)['cost'] is None for r in rows)
    return dict(month=cost(current),week=weekly,unpriced_runs=dict(month=priced(current),week=priced(week)),projection=weekly*30/7 if weekly is not None else None,estimate=True,
        models=[dict(model=m,month=cost([r for r in current if r.get('model')==m]),week=cost([r for r in week if r.get('model')==m])) for m in sorted({r.get('model') or 'unknown' for r in current+week})],
        breakdown_unknown=any(run_cost(r,prices)['breakdown_unknown'] for r in current+week),api_key_recorded=api,
        note='API-equivalent cost. '+('Some runs record an API key source; those runs may be billed per token.' if api else 'The summary job runs on your Claude subscription, so this is not billed per token; it shows what the same usage would cost on the API. Subscription 429s are shared with Claude Code.'))


def ps_seconds(value):
    m=re.fullmatch(r'(?:(?:(\d+)-)?(\d+):)?(\d+):(\d{2})(?:\.(\d{1,2}))?',value)
    if not m: raise ValueError('invalid ps time')
    d,h,minute,second,cc=m.groups(); d=int(d or 0); hours=int(h or 0); minutes=int(minute); seconds=int(second)
    if seconds>=60 or (h is not None and minutes>=60) or (m[1] is not None and hours>=24): raise ValueError('invalid ps time')
    return d*DAY+hours*3600+minutes*60+seconds+float('0.'+(cc or '0'))


def services(runner=None):
    runner=runner or subprocess.run
    def call(argv):
        try:
            p=runner(argv,capture_output=True,text=True,timeout=5)
            return p.stdout if p.returncode==0 else ''
        except (OSError,subprocess.SubprocessError): return ''
    rows=[]; ids=[]
    for name,label in [('broker','org.agentmesh.profile-log.broker'),('bridge','org.agentmesh.profile-log.bridge'),('Insight','org.agentmesh.insight'),('cloudflared',None)]:
        raw=call(['launchctl','list',label]) if label else call(['pgrep','-x','cloudflared'])
        pids=re.findall(r'"PID"\s*=\s*(\d+)',raw) if label else re.findall(r'^\d+$',raw,re.M)
        pids=[p for p in pids if 0<int(p)<2**31]; ids.append(pids)
        rows.append(dict(service=name,state='not running' if not pids else 'unknown',cpu_s=None,cpu_day=None,rss_mb=None,estimate=True))
    allids=sorted({p for group in ids for p in group}); parsed={}
    if allids:
        for line in call(['ps','-o','pid=,time=,rss=,etime=','-p',','.join(allids)]).splitlines():
            try:
                pid,cpu,rss,elapsed=line.split(); cpu=ps_seconds(cpu); elapsed=ps_seconds(elapsed)
                if not rss.isdigit() or elapsed<=0: continue
                parsed[pid]=(cpu,cpu*DAY/elapsed,int(rss)/1024)
            except ValueError: continue
    for row,pids in zip(rows,ids):
        if pids and all(p in parsed for p in pids):
            row.update(state='running',**dict(zip(('cpu_s','cpu_day','rss_mb'),(sum(parsed[p][i] for p in pids) for i in range(3)))))
    return rows


def job_stats(root,now):
    result=[]
    for name in ('ingest','summary'):
        try: lines=read_bytes(Path(root)/'status'/('launchd-'+name+'.log'),tail=True).decode('utf-8','replace').splitlines(); available=True
        except (OSError,ValueError): lines=[]; available=False
        rows=[]
        for line in lines:
            try:
                r=json.loads(line); t=instant(r.get('at'))
                if t and type(r.get('exit')) is int and 0<=(now-t).total_seconds()<=7*DAY: rows.append((r,t))
            except (ValueError,AttributeError): pass
        def totals(selected):
            out=dict(runs=len(selected) if available else None)
            for k in ('wall_s','cpu_s'):
                vals=[number(r.get(k)) for r,t in selected]
                out[k]=sum(vals) if available and all(v is not None for v in vals) else None
            return out
        today=totals([(r,t) for r,t in rows if t.date()==now.date()]); week=totals(rows)
        wall,cpu=week['wall_s'],week['cpu_s']
        result.append(dict(job=name,today=today,week=week,projection={k:v*30/7 if v is not None else None for k,v in week.items()},runs_day=len(rows)/7 if available else None,cpu_share=cpu/wall if cpu is not None and wall else None,waiting_s=max(0,wall-cpu) if name=='summary' and wall is not None and cpu is not None else None,estimate=True))
    result.append(dict(job='org.agentmesh.profile-log.catch-up',state='not recorded'))
    return result


def parse_tunnel(text):
    result=dict(requests=None,responses={},bytes={})
    for line in text.splitlines():
        m=re.fullmatch(r'(cloudflared_tunnel_[a-zA-Z0-9_]+)(?:\{([^\n]*)\})?\s+(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?:\s+\d+)?',line)
        if not m: continue
        name,labels,raw=m.groups(); n=number(float(raw))
        if n is None: continue
        if name=='cloudflared_tunnel_total_requests': result['requests']=(result['requests'] or 0)+n
        elif name=='cloudflared_tunnel_response_by_code':
            code=re.search(r'(?:^|,)\s*code="([1-5]\d\d)"(?:,|$)',labels or '')
            if code: result['responses'][code[1]]=result['responses'].get(code[1],0)+n
        elif '_bytes_' in name: result['bytes'][name]=result['bytes'].get(name,0)+n
    return result


def fetch_tunnel(url,connection=None,clock=time.monotonic):
    parsed=urlsplit(url)
    if parsed.scheme!='http' or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path!='/metrics' or not ipaddress.ip_address(parsed.hostname).is_loopback: raise ValueError('loopback required')
    c=(connection or http.client.HTTPConnection)(parsed.hostname,parsed.port or 80,timeout=2)
    start=clock(); response=None; expired=threading.Event()
    def abort():
        expired.set()
        sock=getattr(c,'sock',None) or getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
        if sock:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
    timer=threading.Timer(2,abort); timer.daemon=True; timer.start()
    try:
        c.request('GET','/metrics'); response=c.getresponse()
        if response.status!=200: raise ValueError('metrics unavailable')
        data=response.read(1024*1024+1)
        if expired.is_set() or clock()-start>=2: raise TimeoutError()
        if len(data)>1024*1024: raise ValueError('metrics oversized')
        return data.decode('utf-8')
    finally: timer.cancel(); c.close()


class Observer:
    def __init__(self,home,root,sqlite,metrics_logs=None,*,runner=None,fetcher=None,clock=None,running=None):
        self.home,self.root,self.sqlite=Path(home),Path(root),sqlite
        self.metrics_logs=metrics_logs; self.runner=runner; self.fetcher=fetcher or fetch_tunnel
        self.clock=clock or (lambda:datetime.now(timezone.utc)); self.running=running
        self.previous={}; self.tunnel=None

    def runs(self):
        from metrics import read_jsonl
        paths=adapters.layout(self.home,self.root)['summary']['paths'] if self.metrics_logs is None else self.metrics_logs
        try:
            if any(not safe_path(p) for p in paths): raise ValueError('symlink')
            return list({r['run_id']:r for r in read_jsonl(paths) if r.get('record_type')=='run'}.values()),bool(paths)
        except (OSError,ValueError,KeyError,AttributeError): return [],False

    def buffers(self,sql,disk,sync,runs,now):
        rows=[]; previous=self.previous
        def add(category,count=None,size=None,at=None,threshold=None,note=None):
            t=instant(at); age=max(0,(now-t).total_seconds()) if t else None
            measure=count if count is not None else size
            old=previous.get(category); grew=old is not None and measure is not None and measure>old
            warning=bool(grew or (count!=0 and age is not None and threshold is not None and age>threshold))
            rows.append(dict(category=category,count=count,bytes=size,age_s=age,warning=warning,note=note))
            previous[category]=measure
            return rows[-1]
        add('R2 upload outbox',number(sync.get('outbox')),at=sync.get('at'),threshold=3*(number(sync.get('interval_s')) or 60))
        add('Gateway remember outbox',note='unknown; bridge writes drain status')
        state=read_json(adapters.layout(self.home,self.root)['coordinator']['path']); pending=state.get('pending')
        ids=(pending or {}).get('pending_ids') if type(pending) is dict else state.get('pending_ids')
        count=len(ids) if type(ids) is list else 0 if 'pending' in state and pending is None else next((number(r.get('events_pending_after')) for r in reversed(runs) if number(r.get('events_pending_after')) is not None),None)
        oldest=None; ingest=[]; cursor=None; cursors=[]
        if self.sqlite and safe_path(self.sqlite):
            try:
                with adapters.sqlite_ro(self.sqlite) as c:
                    if type(ids) is list and ids and len(ids)<=500 and all(type(i) is int for i in ids):
                        oldest=c.execute('SELECT MIN(occurred_at) FROM observation_events WHERE id IN ('+','.join('?' for _ in ids)+')',ids).fetchone()[0]
                    ingest=[dict(r) for r in c.execute('SELECT source_agent,MAX(occurred_at) AS at FROM observation_events GROUP BY source_agent')]
                    cursor=c.execute('SELECT MAX(updated_at) FROM ingestion_cursors').fetchone()[0]
                    cursors=[dict(r) for r in c.execute('SELECT s.source_agent,MAX(c.updated_at) AS at FROM ingestion_cursors c JOIN source_sessions s ON s.source_path=c.source_path GROUP BY s.source_agent')]
            except sqlite3.Error: pass
        add('Summary backlog',count,at=oldest,threshold=DAY)
        known={'codex','claude','hermes','omp','chatgpt'}
        for r in ingest: add('Ingest '+(r['source_agent'] if r['source_agent'] in known else 'other'),at=r['at'],threshold=DAY)
        if not ingest: add('Ingest latest event')
        for r in cursors: add('Ingest cursor '+(r['source_agent'] if r['source_agent'] in known else 'other'),at=r['at'],threshold=DAY)
        add('Ingest cursors',at=cursor,threshold=DAY)
        job=read_json(self.root/'status/job-ingest.json'); add('Ingest last ok',at=job.get('at') if job.get('ok') is True else None)
        caught=read_json(self.root/'profile-log-status.json').get('last_catch_up') or {}
        add('Catch-up waiting rows',number(caught.get('waiting')),note='unknown unless waiting is recorded; outcomes and skipped are not waiting counts')
        wal=add('Staging WAL',size=sql.get('wal_bytes')); wal['warning']|=(wal['bytes'] or 0)>64*1024*1024
        audit=disk['profile']['audit_bytes']; old=previous.get('audit_sample'); growth=None
        if old and audit is not None and old[0] is not None and now>old[1]: growth=max(0,audit-old[0])*DAY/(now-old[1]).total_seconds()
        else:
            try:
                # Read just the first bounded line; never parse or expose the audit content.
                path=self.root/'profile-log/audit.jsonl'
                if safe_path(path):
                    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
                    with os.fdopen(fd,'rb') as f:
                        if stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                            first=json.loads(f.readline(4096)); t=instant(first.get('at'))
                            if t is None and number(first.get('ts')) is not None: t=datetime.fromtimestamp(first['ts']/1000,timezone.utc)
                            if t and now>t and audit is not None: growth=audit*DAY/(now-t).total_seconds()
            except (OSError,ValueError,AttributeError,OverflowError): pass
        row=add('Broker audit log',size=audit,note='grows forever, no rotation'); row['growth_day']=growth
        previous['audit_sample']=(audit,now)
        return rows

    def compute(self):
        now=self.clock(); sql=sqlite_stats(self.sqlite); disk=disk_stats(self.root,running=self.running)
        sync=read_json(self.root/'profile-log/sync-status.json'); profile=disk['profile']
        r2=r2_stats(sync,profile['replica']['bytes'] if not disk['truncated'] else None,profile['prefixes'] if not disk['truncated'] else None,now)
        pricing=load_pricing(self.root); prices=pricing['prices']; runs,available=self.runs()
        summary=summary_cost(runs,prices,now)
        if not available: summary.update(month=None,week=None,projection=None)
        jobs=job_stats(self.root,now); local=services(self.runner)
        config=read_json(self.root/'insight-config.json'); port=config.get('cloudflared_metrics_port',20241)
        if type(port) is not int or not 1<=port<=65535: port=20241
        tunnel=dict(available=False,requests=None,responses={},bytes={},per_day=None,projection=None)
        try:
            sample=parse_tunnel(self.fetcher('http://127.0.0.1:'+str(port)+'/metrics')); tunnel.update(sample,available=sample['requests'] is not None)
            if self.tunnel and now>self.tunnel[0]:
                old=self.tunnel[1]; elapsed=(now-self.tunnel[0]).total_seconds()
                def rate(new,old): return (new-old)*DAY/elapsed if new is not None and old is not None and new>=old else None
                per=dict(requests=rate(sample['requests'],old['requests']),responses={k:rate(v,old['responses'].get(k)) for k,v in sample['responses'].items()},bytes={k:rate(v,old['bytes'].get(k)) for k,v in sample['bytes'].items()})
                tunnel['per_day']=per; tunnel['projection']=dict(requests=per['requests']*30 if per['requests'] is not None else None,responses={k:v*30 if v is not None else None for k,v in per['responses'].items()},bytes={k:v*30 if v is not None else None for k,v in per['bytes'].items()})
            self.tunnel=(now,sample)
        except (OSError,ValueError,http.client.HTTPException) as exc: tunnel['error']=type(exc).__name__
        week=[r for r in runs if (t:=instant(r.get('end') or r.get('start'))) and 0<=(now-t).total_seconds()<=7*DAY]
        def daily(key):
            vals=[v for v in (number(r.get(key)) for r in week) if v is not None]
            return sum(vals)/7 if available and vals else None
        claude={k+'_day':daily(k) for k in ('llm_api_calls','input_total_tokens','output_tokens')}
        claude.update({k.replace('_day','_month'):v*30 if v is not None else None for k,v in list(claude.items())}); claude['bytes_note']='Bytes are not recorded'; claude['estimate']=True
        cpu=[r['cpu_day'] for r in local]+[r.get('week',{}).get('cpu_s')/7 if r.get('week',{}).get('cpu_s') is not None else None for r in jobs[:2]]
        kwh=sum(cpu)/3600*prices['energy']['watts_per_core']/1000 if all(v is not None for v in cpu) else None
        return dict(available=True,computed_at=now.isoformat(),refresh_seconds=600,sqlite=sql,disk=disk,profile=profile,r2=r2,
            costs=dict(pricing=pricing,r2=r2_cost(r2,prices),summary=summary),compute=dict(services=local,jobs=jobs,summary_duration_day=daily('duration_seconds'),energy=dict(kwh_day=kwh,cost_day=kwh*prices['energy']['price_per_kwh'] if kwh is not None and prices['energy']['price_per_kwh'] is not None else None,estimate=True)),
            buffers=self.buffers(sql,disk,sync,runs,now),network=dict(r2=r2['network'],tunnel=tunnel,claude=claude,nettop='not measured: requires sampling; unstable macOS format; root-owned cloudflared cannot be attributed reliably without sudo'),
            followups=['bridge writes drain status','catch-up waiting count','catch-up job resource recording','audit log rotation','orphan index snapshots: likely pruning bug'])
