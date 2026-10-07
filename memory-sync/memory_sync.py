"""Immutable SQLite row exchange. No PostgreSQL access."""
from __future__ import annotations
import json
import hashlib
import sqlite3
import uuid
import os

class Pending(Exception): pass
class Conflict(Exception): pass
from pathlib import Path
from contextlib import contextmanager

TABLES = ('ingestion_cursors','ingestion_errors','observation_events','memory_summaries','memory_items','source_sessions','memory_sources','summary_state')
KEYS = {t: ('id',) for t in TABLES}
KEYS.update(ingestion_cursors=('source_path',), memory_sources=('memory_id','event_id'), summary_state=('consumer',))
FORMAT = 'omp-memory-changes-v1'
LIMIT = 2**40
RANGES = {'windows': (LIMIT, 2*LIMIT), 'mac': (2*LIMIT, 3*LIMIT), 'linux': (3*LIMIT, 4*LIMIT)}

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

@contextmanager
def connect(db):
    path=Path(db).expanduser().resolve()
    c = sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA synchronous=FULL')
    try:
        yield c
    finally:
        c.close()

def config(c):
    r = c.execute('SELECT node,group_id FROM _sync_config').fetchone()
    if r is None: raise ValueError('sync is not initialized')
    return {'node':r[0],'group_id':r[1]}

def rows(c):
    result = {}
    for t in TABLES:
        for r in c.execute('SELECT * FROM '+t):
            r = dict(r)
            if 'metadata' in r: r['metadata'] = json.loads(r['metadata'])
            key = canonical([r[k] for k in KEYS[t]])
            result[t,key] = r
    return result

def initialize(db, node, group_id):
    if node not in RANGES: raise ValueError('node must be mac, windows or linux')
    if str(uuid.UUID(group_id)) != group_id: raise ValueError('group must be canonical UUID')
    with connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE IF NOT EXISTS _sync_config(node TEXT NOT NULL,group_id TEXT NOT NULL,importing INTEGER NOT NULL DEFAULT 0)')
        existing = c.execute('SELECT node,group_id FROM _sync_config').fetchone()
        if existing:
            if tuple(existing) != (node, group_id): raise ValueError('node/group mismatch')
            return dict(existing)
        c.execute('CREATE TABLE _sync_shadow(table_name TEXT,key TEXT,row TEXT,revision TEXT NOT NULL,PRIMARY KEY(table_name,key))')
        c.execute('CREATE TABLE _sync_history(revision TEXT PRIMARY KEY,table_name TEXT,key TEXT)')
        c.execute('CREATE TABLE _sync_outbox(uuid TEXT PRIMARY KEY,packet TEXT NOT NULL,published INTEGER NOT NULL DEFAULT 0)')
        c.execute('CREATE TABLE _sync_receipts(uuid TEXT PRIMARY KEY,checksum TEXT NOT NULL)')
        c.execute('CREATE TABLE _sync_diagnostics(path TEXT PRIMARY KEY,kind TEXT NOT NULL,message TEXT NOT NULL)')
        c.execute('CREATE TABLE _sync_counters(table_name TEXT PRIMARY KEY,next_id INTEGER NOT NULL)')
        if c.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('baseline foreign key violations')
        for (t,k),r in rows(c).items():
            for field in KEYS[t]:
                value=r[field]
                if field in ('id','memory_id','event_id'):
                    if type(value) is not int or not 0<value<4*LIMIT: raise ValueError('baseline ID outside supported ranges')
                elif not isinstance(value,str) or not value: raise ValueError('baseline has invalid text key')
            rev = 'baseline:'+digest([t,json.loads(k),r])
            c.execute('INSERT INTO _sync_shadow VALUES(?,?,?,?)',(t,k,canonical(r),rev))
            c.execute('INSERT OR IGNORE INTO _sync_history VALUES(?,?,?)',(rev,t,k))
        c.execute('INSERT INTO _sync_config(node,group_id) VALUES(?,?)',(node,group_id))
        for t in TABLES:
            if KEYS[t] == ('id',):
                low,high = RANGES[node]
                c.execute('INSERT INTO _sync_counters VALUES(?,?)',(t,low))
                allowed = f'(NEW.id >= {low} AND NEW.id < {high})'
                if node=='mac': allowed += f' OR (NEW.id>0 AND NEW.id<{LIMIT})'
                c.execute(f"CREATE TRIGGER _sync_guard_{t} BEFORE INSERT ON {t} WHEN (SELECT importing FROM _sync_config)=0 AND NOT ({allowed}) BEGIN SELECT RAISE(ABORT,'explicit ID outside local range: use allocate_id'); END")
            condition=' OR '.join(f'NEW.{k} IS NOT OLD.{k}' for k in KEYS[t])
            c.execute(f"CREATE TRIGGER _sync_key_{t} BEFORE UPDATE ON {t} WHEN {condition} BEGIN SELECT RAISE(ABORT,'sync primary keys are immutable'); END")
        return {'node':node,'group_id':group_id}

def allocate_id(connection, table):
    """Caller must hold BEGIN IMMEDIATE; counter participates in that transaction."""
    if table not in TABLES or KEYS[table] != ('id',): raise ValueError('table has no allocated integer ID')
    if not connection.in_transaction: raise ValueError('allocate_id requires caller BEGIN IMMEDIATE')
    node=config(connection)['node']
    low,high=RANGES[node]
    connection.execute('UPDATE _sync_counters SET next_id=next_id WHERE table_name=?',(table,))
    counter=connection.execute('SELECT next_id FROM _sync_counters WHERE table_name=?',(table,)).fetchone()[0]
    maximum=connection.execute('SELECT max(id) FROM '+table+' WHERE id>=? AND id<?',(low,high)).fetchone()[0]
    ident=max(counter,low,(maximum+1) if maximum is not None else low)
    if ident>=high: raise OverflowError('local ID range exhausted')
    connection.execute('UPDATE _sync_counters SET next_id=? WHERE table_name=?',(ident+1,table))
    return ident

def status(db):
    with connect(db) as c:
        result = config(c)
        result.update(outbox=c.execute('SELECT count(*) FROM _sync_outbox WHERE published=0').fetchone()[0],
                      received=c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0])
        result.update({k:0 for k in ('pending','conflict','invalid')})
        for r in c.execute('SELECT kind,count(*) FROM _sync_diagnostics GROUP BY kind'): result[r[0]]=r[1]
        return result

def capture(db):
    with connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        return _capture(c)

def _capture(c):
    config(c)
    current = rows(c)
    old = {(r['table_name'],r['key']):r for r in c.execute('SELECT * FROM _sync_shadow')}
    changes = []
    for t,k in sorted(current.keys() | old.keys()):
        row = current.get((t,k))
        previous = old.get((t,k))
        encoded = canonical(row) if row is not None else None
        if encoded == (previous['row'] if previous else None): continue
        revision = str(uuid.uuid4())
        changes.append({'table':t,'key':json.loads(k),'row':row,'revision':revision,'parent':previous['revision'] if previous else None})
        c.execute('INSERT OR REPLACE INTO _sync_shadow VALUES(?,?,?,?)',(t,k,encoded,revision))
        c.execute('INSERT INTO _sync_history VALUES(?,?,?)',(revision,t,k))
    if changes:
        cfg = config(c)
        packet = {'format':FORMAT,'group':cfg['group_id'],'node':cfg['node'],'uuid':str(uuid.uuid4()),'body':changes,'checksum':digest(changes)}
        c.execute('INSERT INTO _sync_outbox(uuid,packet) VALUES(?,?)',(packet['uuid'],canonical(packet)))
    return {'changes':len(changes),'packets':int(bool(changes))}


def publish(db, exchange):
    published = 0
    try:
        with connect(db) as c:
            cfg = config(c)
            packets = c.execute('SELECT uuid,packet FROM _sync_outbox WHERE published=0').fetchall()
        directory = Path(exchange)/'changes'/cfg['node']
        directory.mkdir(parents=True, exist_ok=True)
        for ident, text in packets:
            target = directory/(ident+'.json')
            if target.exists():
                if target.read_bytes() != text.encode('utf-8'): raise ValueError('immutable packet path collision: '+str(target))
            else:
                temp = directory/('.'+ident+'.'+str(uuid.uuid4())+'.tmp')
                try:
                    with temp.open('xb') as f:
                        f.write(text.encode('utf-8')); f.flush(); os.fsync(f.fileno())
                    os.replace(temp, target)
                    if os.name != 'nt':
                        fd = os.open(directory, os.O_RDONLY)
                        try: os.fsync(fd)
                        finally: os.close(fd)
                finally:
                    if temp.exists(): temp.unlink()
            with connect(db) as c, c:
                c.execute('UPDATE _sync_outbox SET published=1 WHERE uuid=?',(ident,))
            published += 1
        return {'published':published,'unavailable':False}
    except OSError as e:
        return {'published':published,'unavailable':True,'error':str(e)}

def valid_uuid(value):
    return isinstance(value,str) and str(uuid.UUID(value)) == value

def load_packet(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('duplicate JSON key')
            result[key]=value
        return result
    def invalid_constant(value): raise ValueError('non-finite JSON number: '+value)
    return json.loads(text,object_pairs_hook=pairs,parse_constant=invalid_constant)

def _validate(c, p):
    cfg = config(c)
    if not isinstance(p,dict) or set(p) != {'format','group','node','uuid','body','checksum'}: raise ValueError('invalid envelope')
    if p['format'] != FORMAT or p['group'] != cfg['group_id'] or p['node'] not in RANGES: raise ValueError('foreign group/node/format')
    if not valid_uuid(p['uuid']): raise ValueError('invalid packet UUID')
    if not isinstance(p['body'],list) or not p['body'] or p['checksum'] != digest(p['body']): raise ValueError('body checksum/shape mismatch')
    seen = set()
    for change in p['body']:
        if not isinstance(change,dict) or set(change) != {'table','key','row','revision','parent'}: raise ValueError('invalid change')
        t = change['table']
        if not isinstance(t,str) or t not in TABLES: raise ValueError('invalid table')
        key = change['key']
        if not isinstance(key,list) or len(key) != len(KEYS[t]): raise ValueError('invalid key')
        for name,value in zip(KEYS[t],key):
            if name.endswith('id') and name != 'consumer':
                if type(value) is not int or not 0 < value < 4*LIMIT: raise ValueError('invalid integer key')
            elif not isinstance(value,str) or not value: raise ValueError('invalid text key')
        identity = (t,canonical(key))
        if identity in seen: raise ValueError('duplicate change key')
        seen.add(identity)
        rev = change['revision']
        if not valid_uuid(rev): raise ValueError('invalid revision')
        parent = change['parent']
        if parent is not None:
            if not isinstance(parent,str): raise ValueError('invalid parent')
            if parent.startswith('baseline:'):
                if len(parent) != 73 or any(ch not in '0123456789abcdef' for ch in parent[9:]): raise ValueError('invalid baseline revision')
            elif not valid_uuid(parent): raise ValueError('invalid parent revision')
        if parent is None and KEYS[t]==('id',):
            low,high=RANGES[p['node']]
            ident=key[0]
            if not (low<=ident<high or (p['node']=='mac' and 0<ident<LIMIT)):
                raise ValueError('new ID outside sender allocation range')
        row = change['row']
        if row is not None:
            columns = {r['name']:r for r in c.execute('PRAGMA table_info('+t+')')}
            if not isinstance(row,dict) or set(row) != set(columns): raise ValueError('invalid row columns')
            if [row[k] for k in KEYS[t]] != key: raise ValueError('row key mismatch')
            for name,value in row.items():
                if name == 'metadata':
                    canonical(value)
                elif value is not None:
                    typ = columns[name]['type']
                    if typ == 'INTEGER' and type(value) is not int: raise ValueError('invalid integer column')
                    if typ == 'REAL' and type(value) not in (int,float): raise ValueError('invalid real column')
                    if typ == 'TEXT' and not isinstance(value,str): raise ValueError('invalid text column')

def _seen(c,p):
    existing = c.execute('SELECT checksum FROM _sync_receipts WHERE uuid=?',(p['uuid'],)).fetchone()
    if existing:
        if existing[0] != p['checksum']: raise ValueError('packet UUID reused')
        return True
    if p['node'] == config(c)['node']:
        own = c.execute('SELECT packet FROM _sync_outbox WHERE uuid=?',(p['uuid'],)).fetchone()
        if not own or json.loads(own[0]) != p: raise ValueError('unknown packet claiming local node')
        return True
    return False

def _apply(c,p):
    if _seen(c,p): return False
    # Validate causality for the entire packet before touching application rows.
    problems = []
    for ch in p['body']:
        t,k = ch['table'],canonical(ch['key'])
        old = c.execute('SELECT revision FROM _sync_shadow WHERE table_name=? AND key=?',(t,k)).fetchone()
        revision = old[0] if old else None
        if revision != ch['parent']:
            known = c.execute('SELECT 1 FROM _sync_history WHERE revision=? AND table_name=? AND key=?',(ch['parent'],t,k)).fetchone()
            baseline_mismatch=isinstance(ch['parent'],str) and ch['parent'].startswith('baseline:')
            problems.append(('conflict' if ch['parent'] is None or known or baseline_mismatch else 'pending',t,k,revision,ch['parent']))
        used = c.execute('SELECT 1 FROM _sync_history WHERE revision=?',(ch['revision'],)).fetchone()
        if used: problems.append(('conflict',t,k,revision,'reused revision'))
    if problems:
        if any(p[0]=='conflict' for p in problems): raise Conflict(canonical(problems))
        raise Pending(canonical(problems))
    c.execute('UPDATE _sync_config SET importing=1')
    # Child links first: RESTRICT is immediate even with deferred foreign keys.
    deletes = [ch for ch in p['body'] if ch['row'] is None]
    deletes.sort(key=lambda ch: (ch['table'] != 'memory_sources', ch['table'] == 'observation_events'))
    for ch in deletes:
        t=ch['table']
        where=' AND '.join(k+'=?' for k in KEYS[t])
        c.execute('DELETE FROM '+t+' WHERE '+where,ch['key'])
    for ch in p['body']:
        if ch['row'] is None: continue
        t,row = ch['table'],ch['row']
        columns = list(row)
        values = [canonical(row[k]) if k=='metadata' else row[k] for k in columns]
        update = ','.join(k+'=excluded.'+k for k in columns if k not in KEYS[t])
        sql = 'INSERT INTO '+t+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+') ON CONFLICT ('+','.join(KEYS[t])+') DO '+('UPDATE SET '+update if update else 'NOTHING')
        c.execute(sql,values)
    # Cascades/triggers must not erase an unrelated concurrent row off-packet.
    actual=rows(c)
    expected={(r['table_name'],r['key']):json.loads(r['row']) for r in c.execute('SELECT * FROM _sync_shadow WHERE row IS NOT NULL')}
    for ch in p['body']:
        ident=(ch['table'],canonical(ch['key']))
        if ch['row'] is None: expected.pop(ident,None)
        else: expected[ident]=ch['row']
    if actual != expected: raise Conflict('off-packet cascade or trigger changed a concurrent row')
    violations=c.execute('PRAGMA foreign_key_check').fetchall()
    if violations:
        missing=[]
        for table,rowid,parent,fkid in violations:
            foreign=[r for r in c.execute('PRAGMA foreign_key_list('+table+')') if r['id']==fkid]
            child=c.execute('SELECT * FROM '+table+' WHERE rowid=?',(rowid,)).fetchone()
            key=canonical([child[r['from']] for r in foreign])
            known=c.execute('SELECT 1 FROM _sync_shadow WHERE table_name=? AND key=?',(parent,key)).fetchone()
            missing.append((parent,key,bool(known)))
        if any(known for _,_,known in missing): raise Conflict('foreign key targets deleted or changed: '+canonical(missing))
        raise Pending('waiting for foreign key targets: '+canonical(missing))
    for ch in p['body']:
        t,k=ch['table'],canonical(ch['key'])
        c.execute('INSERT OR REPLACE INTO _sync_shadow VALUES(?,?,?,?)',(t,k,canonical(ch['row']) if ch['row'] is not None else None,ch['revision']))
        c.execute('INSERT INTO _sync_history VALUES(?,?,?)',(ch['revision'],t,k))
    c.execute('UPDATE _sync_config SET importing=0')
    c.execute('INSERT INTO _sync_receipts VALUES(?,?)',(p['uuid'],p['checksum']))
    return True

def receive(db, exchange):
    capture(db)
    root=Path(exchange)
    if not root.is_dir(): return {'applied':0,'pending':0,'conflict':0,'invalid':0,'unavailable':True}
    paths = sorted((root/'changes').glob('*/*.json'))
    applied=0
    # Multiple passes let predecessors arriving after successors unlock a chain.
    retry=paths
    while retry:
        pending=[]; progress=False
        for path in retry:
            try:
                p=load_packet(path.read_text(encoding='utf-8'))
                with connect(db) as c, c:
                    c.execute('BEGIN IMMEDIATE')
                    _validate(c,p)
                    if _seen(c,p): changed=False
                    else:
                        _capture(c)
                        changed=_apply(c,p)
                    c.execute('DELETE FROM _sync_diagnostics WHERE path=?',(str(path),))
                applied+=int(changed); progress |= changed
            except (OSError,ValueError,TypeError,KeyError,sqlite3.IntegrityError,Pending,Conflict) as e:
                kind='pending' if isinstance(e,Pending) else 'conflict' if isinstance(e,(Conflict,sqlite3.IntegrityError)) else 'invalid'
                with connect(db) as c, c:
                    c.execute('INSERT OR REPLACE INTO _sync_diagnostics VALUES(?,?,?)',(str(path),kind,str(e)))
                if kind=='pending': pending.append(path)
        if not progress: break
        retry=pending
    report=status(db)
    return {'applied':applied,**{k:report[k] for k in ('pending','conflict','invalid')},'unavailable':False}

def cycle(db, exchange):
    captured=capture(db)
    sent=publish(db,exchange)
    received=receive(db,exchange)
    return {'capture':captured,'publish':sent,'receive':received}


def main(argv=None):
    import argparse
    import time
    import sys
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init'); init.add_argument('db'); init.add_argument('--node',choices=tuple(RANGES),required=True); init.add_argument('--group',required=True)
    once=sub.add_parser('once'); once.add_argument('db'); once.add_argument('exchange')
    watch=sub.add_parser('watch'); watch.add_argument('db'); watch.add_argument('exchange'); watch.add_argument('--interval',type=float,default=60)
    stat=sub.add_parser('status'); stat.add_argument('db')
    args=parser.parse_args(argv)
    try:
        if args.command=='init': report=initialize(args.db,args.node,args.group)
        elif args.command=='status': report=status(args.db)
        elif args.command=='once': report=cycle(args.db,args.exchange)
        else:
            if args.interval<=0: parser.error('interval must be positive')
            while True:
                try: report=cycle(args.db,args.exchange)
                except (OSError,sqlite3.Error,ValueError) as e: report={'error':str(e)}
                print(canonical(report),flush=True)
                time.sleep(args.interval)
        print(canonical(report),flush=True)
        return 0
    except KeyboardInterrupt: return 0
    except (OSError,sqlite3.Error,ValueError) as e:
        print(canonical({'error':str(e)}),file=sys.stderr)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
