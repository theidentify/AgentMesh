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

def _normalize(t, row):
    row = dict(row)
    if 'metadata' in row: row['metadata'] = json.loads(row['metadata'])
    return canonical([row[k] for k in KEYS[t]]), row


def rows(c):
    return {(t,k):row for t in TABLES for r in c.execute('SELECT * FROM '+t)
            for k,row in [_normalize(t,r)]}


def _keyed_rows(c, keys):
    result={}
    for t,k in keys:
        if t not in TABLES: raise ValueError('invalid table')
        values=json.loads(k)
        if not isinstance(values,list) or len(values)!=len(KEYS[t]): raise ValueError('invalid key')
        row=c.execute('SELECT * FROM '+t+' WHERE '+' AND '.join(col+'=?' for col in KEYS[t]),values).fetchone()
        if row is not None:
            actual,row=_normalize(t,row)
            if actual!=k: raise ValueError('noncanonical key')
            result[t,k]=row
    return result


def _shadow_rows(c, keys):
    result={}
    for t,k in keys:
        row=c.execute('SELECT row FROM _sync_shadow WHERE table_name=? AND key=?',(t,k)).fetchone()
        if row and row[0] is not None: result[t,k]=json.loads(row[0])
    return result


def _packet_keys(packet):
    body=packet.get('body')
    if not isinstance(body,list) or not body: raise ValueError('invalid packet body')
    result=set()
    for ch in body:
        if not isinstance(ch,dict) or ch.get('table') not in TABLES or not isinstance(ch.get('key'),list):
            raise ValueError('invalid packet body')
        if len(ch['key'])!=len(KEYS[ch['table']]): raise ValueError('invalid packet key')
        result.add((ch['table'],canonical(ch['key'])))
    return result


@contextmanager
def _track_writes(c):
    recursive=c.execute('PRAGMA recursive_triggers').fetchone()[0]
    c.execute('PRAGMA recursive_triggers=ON')
    try:
        c.execute('CREATE TEMP TABLE IF NOT EXISTS _sync_apply_touched(table_name TEXT,key TEXT)')
        for t in TABLES:
            for event,aliases in [('INSERT',('NEW',)),('DELETE',('OLD',)),('UPDATE',('OLD','NEW'))]:
                statements=' '.join("INSERT INTO _sync_apply_touched VALUES('"+t+"',json_array("+
                    ','.join(alias+'.'+k for k in KEYS[t])+'));' for alias in aliases)
                c.execute(f'CREATE TEMP TRIGGER IF NOT EXISTS _sync_apply_track_{t}_{event} AFTER {event} ON main.{t} BEGIN {statements} END')
        c.execute('DELETE FROM _sync_apply_touched')
        yield
    finally:
        c.execute('PRAGMA recursive_triggers='+str(recursive))


def _verify_writes(c, allowed, expected):
    touched={(r[0],canonical(json.loads(r[1]))) for r in c.execute('SELECT * FROM _sync_apply_touched')}
    return touched <= allowed and _keyed_rows(c,expected.keys() | allowed)=={k:v for k,v in expected.items() if v is not None}


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
        required = bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone())
        security = {'format':'agentmesh-security-status-v1','policy':'required' if required else 'legacy',
                    'pairing':'unknown','display_name':None,'wizard_step':'unknown','next_action':'unknown',
                    'roundtrip':'pending','roundtrip_packet_uuid':None,'roundtrip_verified_at':None,
                    'verification':None}
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_verification'").fetchone():
            record = c.execute('SELECT attempts,failed_attempts,last_success_at,last_failure_at FROM _sync_verification WHERE id=1').fetchone()
            if record: security['verification'] = dict(record)
        result['security'] = security
        from resolution_store import exists
        result['superseded'] = c.execute('SELECT count(DISTINCT packet_uuid) FROM _sync_packet_dispositions').fetchone()[0] if exists(c) else 0
        return result

def capture(db, *, security=None):
    with connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        _security_policy(c, security)
        return _capture(c, security=security)

def _security_policy(c, security=None, exchange=None):
    # Import does not load cryptography; legacy deployments remain stdlib-only.
    from signed_packets import require_policy
    require_policy(c, security, exchange)

def _capture(c, *, security=None, keys=None):
    config(c)
    if keys is not None:
        if _keyed_rows(c,keys)==_shadow_rows(c,keys): return {"changes":0,"packets":0}
        # A colliding edit captures the entire committed local transaction.
        return _capture(c,security=security)
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
        from protocol_activation import emission_enabled
        if emission_enabled(c):
            from resolution_apply import _index_local
            from resolution_protocol import Key, payload_digest
            from resolution_store import load_origins
            _index_local(c,security)
            body=[]
            for ch in changes:
                key=Key(ch['table'],tuple(ch['key']))
                origin=load_origins(c,[key])[key] if ch['parent'] is not None else None
                body.append({k:ch[k] for k in ('table','key','row','revision')} |
                            {'parents':[] if ch['parent'] is None else [ch['parent']], 'origin':origin})
            payload={k:packet[k] for k in ('group','node','uuid')}
            payload.update(kind='change',body=body,covers=[],evidence=[])
            packet=dict(payload,format='omp-memory-changes-v2',checksum=payload_digest(payload))
            if security is not None: packet=security._sign_resolution(c,packet)
        elif security is not None: packet = security.sign(packet)
        c.execute('INSERT INTO _sync_outbox(uuid,packet) VALUES(?,?)',(packet['uuid'],canonical(packet)))
        if packet['format'] in ('omp-memory-changes-v2','agentmesh-signed-changes-v3'):
            from packet_versions import decode_packet
            decode_packet(c,packet,security=security,folder=packet['node'],filename=packet['uuid']+'.json')
    return {'changes':len(changes),'packets':int(bool(changes))}


def write_packet(directory, ident, text, *, signed_packet=False, staging_id=None):
    """Durably publish stored UTF-8 bytes without replacing an immutable path.

    Shared by ordinary publish and journaled rotation. The caller validates the
    envelope and owns the DB transaction; this helper never changes packet text.
    """
    from signed_packets import no_symlinks, read_local
    directory = Path(directory)
    if signed_packet:
        directory = no_symlinks(directory)
        if not valid_uuid(ident): raise ValueError('invalid outbox UUID')
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/(ident+'.json')
    raw = text.encode('utf-8')
    if staging_id is not None:
        from signed_packets import hex_bytes
        hex_bytes(staging_id, 32)
        temp = no_symlinks(directory/('.'+ident+'.rotate-'+staging_id+'.tmp'))
        if temp.exists():
            # Journaled replay owns this exact name. A killed writer can leave
            # a prefix; no other content may be removed as rotation staging.
            if not raw.startswith(read_local(temp)):
                raise ValueError('immutable packet path collision: '+str(temp))
            temp.unlink()
    else:
        temp = directory/('.'+ident+'.'+str(uuid.uuid4())+'.tmp')
    def identical():
        if signed_packet: no_symlinks(target)
        existing = read_local(target) if signed_packet else target.read_bytes()
        if existing != raw: raise ValueError('immutable packet path collision: '+str(target))
    if signed_packet: no_symlinks(target)
    if target.exists():
        identical()
    else:
        try:
            with temp.open('xb') as f:
                f.write(raw); f.flush(); os.fsync(f.fileno())
            try:
                # An atomic hard link cannot overwrite a file created after the
                # existence check (unlike os.replace). NTFS supports this too.
                os.link(temp, target)
            except FileExistsError:
                identical()
        finally:
            if temp.exists(): temp.unlink()
    # Repeat on identical retries: a crash may have preceded directory fsync.
    if os.name != 'nt':
        fd = os.open(directory, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


def publish(db, exchange, *, security=None):
    published = 0
    try:
        with connect(db) as c, c:
            _security_policy(c, security, exchange)
            cfg = config(c)
            packets = c.execute('SELECT uuid,packet FROM _sync_outbox WHERE published=0').fetchall()
        namespace = 'signed-changes' if security is not None else 'changes'
        directory = Path(exchange)/namespace/cfg['node']
        if security is not None:
            from signed_packets import no_symlinks
            directory = no_symlinks(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for ident, text in packets:
            from signed_packets import FORMAT as signed_format
            if load_packet(text).get('format') not in (FORMAT, signed_format):
                with connect(db) as c:
                    from protocol_activation import emission_enabled
                    if not emission_enabled(c): raise ValueError('new packet emission disabled')
                from packet_versions import is_new
                if not is_new(load_packet(text)): raise ValueError('unsupported packet format')
            write_packet(directory, ident, text, signed_packet=security is not None)
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
    checksum = _receipt_checksum(p)
    existing = c.execute('SELECT checksum FROM _sync_receipts WHERE uuid=?',(p['uuid'],)).fetchone()
    if existing:
        if existing[0] != checksum: raise ValueError('packet UUID reused')
        return True
    if p['node'] == config(c)['node']:
        own = c.execute('SELECT packet FROM _sync_outbox WHERE uuid=?',(p['uuid'],)).fetchone()
        if not own or json.loads(own[0]) != p: raise ValueError('unknown packet claiming local node')
        return True
    return False

def _receipt_checksum(p):
    from packet_versions import receipt_identity
    return receipt_identity(p)

def _verify_signed(c, p, security, folder):
    """New-input signature check shared by both receive paths: a revoked key passes only
    through an operator-approved rotation manifest (verify_rotated), never plain verify."""
    from signed_packets import read_trust
    entry = read_trust(security.directory)['peers'].get(p['key_id'])
    if entry is not None and entry['revoked']:
        return security.verify_rotated(p, config(c)['group_id'], folder)
    return security.verify(p, config(c)['group_id'], folder)

def _received_signed(c, p, security, folder):
    """Return cached/verified for a committed receipt, else None for new input."""
    from signed_packets import envelope_digest, receipt_digest
    receipt = c.execute('SELECT checksum FROM _sync_receipts WHERE uuid=?', (p['uuid'],)).fetchone()
    if receipt is None: return None
    if receipt[0] != receipt_digest(p): raise ValueError('packet UUID reused')
    recorded = c.execute('SELECT digest FROM _sync_receipt_envelopes WHERE uuid=?', (p['uuid'],)).fetchone()
    complete = envelope_digest(p)
    if recorded is not None:
        if recorded[0] != complete: raise ValueError('received packet envelope changed')
        return 'cached'
    # Compatibility with receipts written before complete envelopes were pinned.
    # Require the committed content/identity AND a valid historical signature.
    security.verify_received(p, config(c)['group_id'], folder, receipt[0])
    c.execute('INSERT INTO _sync_receipt_envelopes VALUES(?,?)', (p['uuid'], complete))
    return 'verified'

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
    with _track_writes(c):
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
        expected={(ch['table'],canonical(ch['key'])):ch['row'] for ch in p['body']}
        if not _verify_writes(c,set(expected),expected):
            raise Conflict('off-packet cascade or trigger changed a concurrent row')
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
        c.execute('INSERT INTO _sync_receipts VALUES(?,?)',(p['uuid'],_receipt_checksum(p)))
        from signed_packets import FORMAT as signed_format
        if p['format'] == signed_format:
            from signed_packets import envelope_digest
            c.execute('INSERT INTO _sync_receipt_envelopes VALUES(?,?)', (p['uuid'], envelope_digest(p)))
        return True

def receive(db, exchange, *, security=None, capture_first=True):
    with connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        _security_policy(c, security, exchange)
    if capture_first: capture(db, security=security)
    root=Path(exchange)
    namespace = 'signed-changes' if security is not None else 'changes'
    if security is not None:
        from signed_packets import no_symlinks
        no_symlinks(root/namespace)
    try:
        if not root.is_dir():
            return {'applied':0,'pending':0,'conflict':0,'invalid':0,'unavailable':True}
        paths = sorted((root/namespace).glob('*/*.json'))
    except OSError as exc:
        return {'applied':0,'pending':0,'conflict':0,'invalid':0,
                'unavailable':True,'error':type(exc).__name__}
    io_error = None
    applied=0
    # Multiple passes let predecessors arriving after successors unlock a chain.
    retry=paths
    indexed=False
    while retry:
        pending=[]; conflicts=[]; progress=False
        was_indexed=indexed
        marks=None
        if security is None:
            from resolution_store import exists
            with connect(db) as c, c:
                if not exists(c):
                    c.execute('CREATE TABLE IF NOT EXISTS _sync_file_marks('
                              'path TEXT PRIMARY KEY,uuid TEXT NOT NULL,'
                              'size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL)')
                    marks={r[0]:(r[1],r[2]) for r in c.execute(
                        'SELECT path,size,mtime_ns FROM _sync_file_marks WHERE '
                        'uuid IN (SELECT uuid FROM _sync_receipts) OR '
                        'uuid IN (SELECT uuid FROM _sync_outbox WHERE published=1)')}
        for path in retry:
            verified_signature = False
            stat=None
            try:
                if marks is not None and not indexed:
                    stat=os.stat(path)
                    if marks.get(str(path)) == (stat.st_size,stat.st_mtime_ns):
                        continue
                if security is not None:
                    from signed_packets import read_local, parse
                    packet_bytes = read_local(path)
                    p = parse(packet_bytes)
                else:
                    p=load_packet(path.read_text(encoding='utf-8'))
                with connect(db) as c, c:
                    c.execute('BEGIN IMMEDIATE')
                    _security_policy(c, security, exchange)
                    from packet_versions import is_new, _has_committed_envelope
                    from resolution_store import exists
                    if is_new(p) or exists(c):
                        indexed=True
                        from resolution_apply import receive_indexed
                        # Validate transport scope before the adapter's retained scope.
                        from packet_versions import validate_wire
                        validate_wire(p,group=config(c)['group_id'],folder=path.parent.name)
                        if (security is not None or is_new(p)) and path.name != p['uuid']+'.json':
                            raise ValueError('packet filename mismatch')
                        cached_signature = security is not None and _has_committed_envelope(c,p.get('uuid'))
                        outcome, added = receive_indexed(c,p,path,security=security)
                        verified_signature = True if security is not None and not cached_signature else None
                        changed = outcome == 'applied'
                        applied += int(changed); progress |= changed or added
                        if outcome=='pending': pending.append(path)
                        if outcome=='conflict': conflicts.append(path)
                        continue
                    if security is not None:
                        from signed_packets import validate_envelope
                        validate_envelope(p, config(c)['group_id'], path.parent.name)
                        if path.name != p['uuid']+'.json': raise ValueError('signed packet filename mismatch')
                        if p['node'] == config(c)['node']:
                            own = c.execute('SELECT packet FROM _sync_outbox WHERE uuid=?', (p['uuid'],)).fetchone()
                            if own is None or packet_bytes != own[0].encode('utf-8'):
                                raise ValueError('unknown packet claiming local node')
                            verified_signature = None
                            c.execute('DELETE FROM _sync_diagnostics WHERE path=?', (str(path),))
                            continue
                        received = _received_signed(c, p, security, path.parent.name)
                        if received is not None:
                            verified_signature = True if received == 'verified' else None
                            c.execute('DELETE FROM _sync_diagnostics WHERE path=?', (str(path),))
                            continue
                        _verify_signed(c, p, security, path.parent.name)
                        verified_signature = True
                        legacy = {k: p[k] for k in ('group','node','uuid','body')}
                        legacy.update(format=FORMAT, checksum=digest(p['body']))
                        _validate(c,legacy)
                    else: _validate(c,p)
                    if _seen(c,p): changed=False
                    else:
                        _capture(c, security=security, keys=_packet_keys(p))
                        changed=_apply(c,p)
                    if security is None and stat is not None:
                        c.execute('INSERT OR REPLACE INTO _sync_file_marks VALUES(?,?,?,?)',
                                  (str(path),p['uuid'],stat.st_size,stat.st_mtime_ns))
                    c.execute('DELETE FROM _sync_diagnostics WHERE path=?',(str(path),))
                applied+=int(changed); progress |= changed
            except OSError as exc:
                # A packet that cannot be read is not an invalid packet. Retry next cycle.
                io_error = type(exc).__name__
                if marks is not None:
                    with connect(db) as c, c:
                        c.execute('DELETE FROM _sync_file_marks WHERE path=?',(str(path),))
            except (ValueError,TypeError,KeyError,sqlite3.IntegrityError,Pending,Conflict,RecursionError) as e:
                kind='pending' if isinstance(e,Pending) else 'conflict' if isinstance(e,(Conflict,sqlite3.IntegrityError)) else 'invalid'
                with connect(db) as c, c:
                    if marks is not None:
                        c.execute('DELETE FROM _sync_file_marks WHERE path=?',(str(path),))
                    # Signed failures must not leak memory contents into diagnostics/status.
                    message = type(e).__name__ if security is not None else str(e)
                    from resolution_store import exists
                    if c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_packet_paths'").fetchone():
                        c.execute('DELETE FROM _sync_packet_paths WHERE path=?',(str(path),))
                    c.execute('INSERT OR REPLACE INTO _sync_diagnostics VALUES(?,?,?)',(str(path),kind,message))
                if kind=='pending': pending.append(path)
                if kind=='conflict': conflicts.append(path)
            finally:
                if security is not None and verified_signature is not None:
                    from signed_packets import record_verification
                    record_verification(db, verified_signature)
        if not progress: break
        # The first migration must index earlier legacy files too. Thereafter
        # only held paths need retry; committed exact replays cannot unblock proof.
        retry=paths if indexed and not was_indexed else pending+conflicts if indexed else pending
    report=status(db)
    return {'applied':applied,**{k:report[k] for k in ('pending','conflict','invalid')},'unavailable':bool(io_error),
            **({'error': io_error} if io_error else {})}

def cycle(db, exchange, *, security=None):
    with connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        _security_policy(c, security, exchange)
    captured=capture(db, security=security)
    sent=publish(db,exchange, security=security)
    received=receive(db,exchange, security=security, capture_first=False)
    return {'capture':captured,'publish':sent,'receive':received}


def main(argv=None):
    import argparse
    import time
    import sys
    parser=argparse.ArgumentParser(description=__import__('brand').description(__doc__),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init'); init.add_argument('db'); init.add_argument('--node',choices=tuple(RANGES),required=True); init.add_argument('--group',required=True)
    once=sub.add_parser('once'); once.add_argument('db'); once.add_argument('exchange')
    watch=sub.add_parser('watch'); watch.add_argument('db'); watch.add_argument('exchange'); watch.add_argument('--interval',type=float,default=60)
    stat=sub.add_parser('status'); stat.add_argument('db')
    for command in (once, watch): command.add_argument('--security-dir', help='explicit strict signing and pinned peer trust')
    args=parser.parse_args(argv)
    try:
        security = None
        if getattr(args, 'security_dir', None):
            from signed_packets import Security
            security = Security(args.security_dir, os_unlock=True).require_unlocked()
        if args.command=='init': report=initialize(args.db,args.node,args.group)
        elif args.command=='status': report=status(args.db)
        elif args.command=='once': report=cycle(args.db,args.exchange,security=security)
        else:
            if args.interval<=0: parser.error('interval must be positive')
            while True:
                try: report=cycle(args.db,args.exchange,security=security)
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
