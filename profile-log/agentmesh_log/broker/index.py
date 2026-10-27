"""Per-agent RAM-only FTS5 indexes with incremental revision components."""
from dataclasses import asdict
import os
from json import loads as _decode_row
import sqlite3
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from ..encoding import wire, parse, typed
from ..storage.filesystem import _check
from .keystore import atomic_write
from .query import _score
from .heads import heads


class AgentIndex:
    def __init__(self, agent_id, master_key):
        self.agent_id = agent_id
        self.key = HKDF(algorithm=hashes.SHA256(),length=32,salt=None,
            info=b'AgentMesh/index-snapshot/v1\x00'+agent_id.encode()).derive(master_key)
        self._connect()

    def _connect(self):
        self.connection = sqlite3.connect(':memory:', check_same_thread=False)
        self.connection.execute('PRAGMA temp_store=MEMORY')
        try:
            self.connection.execute('CREATE VIRTUAL TABLE search USING fts5(content, tokenize=unicode61)')
            self.connection.execute('CREATE TABLE metadata(id TEXT PRIMARY KEY, data BLOB NOT NULL, '
                'scope TEXT, profile TEXT, project TEXT, task_ref TEXT, folded TEXT, '
                'source_device TEXT, source_table TEXT, source_scope TEXT, scope_key TEXT, retired INTEGER)')
            self.connection.execute('CREATE INDEX filters ON metadata(scope,profile,project)')
            self.connection.execute('CREATE TABLE events(id TEXT PRIMARY KEY, data BLOB NOT NULL)')
            self.connection.execute('PRAGMA user_version=6')
        except sqlite3.Error: self.connection.close(); raise ValueError('unavailable') from None
        self._events, self._links, self._waiting = {}, {}, {}
        self.dirty = True

    def close(self): self.connection.close()

    def _link(self, row):
        identifier=row['id']; self._events[identifier]=row; self._links.setdefault(identifier,set())
        previous=row.get('revises')
        if previous is not None:
            if previous in self._events:
                if self._events[previous]['scope']==row['scope']:
                    self._links[identifier].add(previous); self._links[previous].add(identifier)
            else: self._waiting.setdefault((row['scope'],previous),set()).add(identifier)
        for child in self._waiting.pop((row['scope'],identifier),set()):
            self._links[identifier].add(child); self._links[child].add(identifier)

    def add(self, row):
        identifier=row['id']
        if row.get('_outbox'):
            duplicates=[r for r in self._events.values() if r.get('_outbox')==row['_outbox']]
            if duplicates:
                if min(r['id'] for r in duplicates)<=identifier: return
                self.replace([r for r in self._events.values() if r.get('_outbox')!=row['_outbox']]+[row]); return
        if identifier in self._events: return
        row={**row,'revises':row.get('revises')}
        self._link(row)
        component=set(); pending=[identifier]
        while pending:
            current=pending.pop()
            if current in component: continue
            component.add(current); pending.extend(self._links[current]-component)
        with self.connection:
            self.connection.execute('INSERT INTO events VALUES (?,?)',(identifier,wire(row)))
            # Only this connected revision component can change its visible head.
            for current in component:
                old=self.connection.execute('SELECT rowid FROM metadata WHERE id=?',(current,)).fetchone()
                if old:
                    self.connection.execute('DELETE FROM search WHERE rowid=?',old)
                    self.connection.execute('DELETE FROM metadata WHERE rowid=?',old)
            for visible in heads([self._events[i] for i in component]):
                visible.pop('revises',None)
                if visible.get('_migration',{}).get('retired'): continue
                cursor=self.connection.execute('INSERT INTO metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (visible['id'],wire(visible),visible['scope'],visible['profile'],visible['project'],
                     (visible['task_ref'] or '').upper(),visible['content'].lower(),
                     visible.get('_migration',{}).get('source',{}).get('device'),
                     visible.get('_migration',{}).get('source',{}).get('table'),
                     visible.get('_migration',{}).get('source',{}).get('scope'),
                     visible.get('_migration',{}).get('source',{}).get('scope_key'),0))
                self.connection.execute('INSERT INTO search(rowid,content) VALUES (?,?)',
                    (cursor.lastrowid,visible['content']))
        self.dirty=True

    def replace(self, rows):
        with self.connection:
            for table in ('search','metadata','events'): self.connection.execute('DELETE FROM '+table)
        self._events,self._links,self._waiting={},{},{}
        for row in rows: self.add(row)
        self.dirty=True

    def event(self, identifier):
        row=self._events.get(identifier)
        return dict(row) if row is not None else None

    def rows(self):
        return [parse(r[0]) for r in self.connection.execute('SELECT data FROM metadata')]

    def aad(self, grant_digest, replica_watermark):
        return typed(dict(agent_id=self.agent_id, grant_digest=grant_digest, replica_watermark=replica_watermark))

    def save(self, path, grant_digest, replica_watermark):
        nonce = os.urandom(12)
        atomic_write(path, nonce+ChaCha20Poly1305(self.key).encrypt(nonce,self.connection.serialize(),self.aad(grant_digest,replica_watermark)))
        self.dirty=False

    def load(self, path, grant_digest, replica_watermark):
        try:
            _check(path); raw = path.read_bytes()
            data = ChaCha20Poly1305(self.key).decrypt(raw[:12],raw[12:],self.aad(grant_digest,replica_watermark))
            self.connection.deserialize(data)
            self.connection.execute('PRAGMA temp_store=MEMORY')
            if self.connection.execute('PRAGMA user_version').fetchone()[0]!=6: raise ValueError('old snapshot')
            # Validate schema, including the incremental event table. Old snapshots rebuild.
            self.connection.execute('SELECT scope,profile,project,task_ref,folded,source_device,source_table,source_scope,scope_key,retired FROM metadata LIMIT 0')
            events=[parse(r[0]) for r in self.connection.execute('SELECT data FROM events')]
            self._events,self._links,self._waiting={},{},{}
            for row in events: self._link(row)
            self.dirty=False
            return True
        except Exception:
            self.connection.close(); self._connect()
            return False

    def recall(self, plan, limit, scopes, profiles, compat=False, semantic=None):
        build=self._compat_candidates if compat else self._candidates
        candidates=build(plan,scopes,profiles,cap=limit)
        selected=[r for r in candidates if (compat and r['source_type']=='summary')
            or len(plan.terms)<2 or plan.task_refs or r['matched_terms']>=2]
        query_plan={k:list(v) if isinstance(v,tuple) else v for k,v in asdict(plan).items()}
        if semantic is not None:
            selected,query_plan['retrieval']=semantic.fuse(plan,selected,
                lambda:build(plan,scopes,profiles,cap=None,textual=False),limit)
        if compat:
            counts=dict(durable=sum(r['source_type']=='durable' for r in candidates),
                summaries=sum(r['source_type']=='summary' for r in candidates),events=0)
        else: counts=dict(durable=len(candidates),summaries=0,events=0)
        return {'query':plan.query,'query_plan':query_plan,'candidate_counts':counts,
            'results':selected[:limit],'evidence':[]}

    def _candidates(self, plan, scopes, profiles, *, cap=None, textual=True):
        matches=[]; args=[]
        if textual:
            if plan.terms:
                match=' OR '.join('"'+t.replace('"','""')+'"' for t in plan.terms)
                matches.append('rowid IN (SELECT rowid FROM search WHERE search MATCH ?)'); args.append(match)
            for term in dict.fromkeys((plan.query.lower(),*plan.terms,*(t.lower() for t in plan.task_refs))):
                if term: matches.append('instr(folded,?)>0'); args.append(term)
            if plan.task_refs:
                matches.append('task_ref IN (SELECT value FROM json_each(?))'); args.append(wire(list(plan.task_refs)).decode())
        text=('('+(' OR '.join(matches) or '0')+')') if textual else '1'
        sql='SELECT data FROM metadata WHERE '+text
        sql+=' AND scope IN (SELECT value FROM json_each(?)) AND profile IN (SELECT value FROM json_each(?))'
        args.extend((wire(sorted(scopes)).decode(),wire(sorted(profiles)).decode()))
        if plan.project is not None: sql+=' AND project=?'; args.append(plan.project)
        # These rows were validated on insertion, or authenticated on snapshot load.
        # Decode only SQL-selected candidates; do not re-run wire validation per query.
        candidates=[_decode_row(r[0]) for r in self.connection.execute(sql,args)]
        for row in candidates:
            row.pop('_outbox',None)
            row.pop('_migration',None)
            _score(row,plan)
        candidates.sort(key=lambda r:(r['score'],r['updated_at'],r['id']), reverse=True)
        return candidates

    def _compat_candidates(self, plan, scopes, profiles, *, cap=None, textual=True):
        matches=[]; args=[]
        if textual:
            for term in dict.fromkeys((plan.query.lower(),*plan.terms,*(t.lower() for t in plan.task_refs))):
                if term: matches.append('instr(folded,?)>0'); args.append(term)
        tasks=wire(list(plan.task_refs)).decode()
        if textual and plan.task_refs:
            matches.append('upper(scope_key) IN (SELECT value FROM json_each(?))'); args.append(tasks)
        text='('+(' OR '.join(matches) or '0')+')' if textual else '1'
        selection="(source_scope='global' OR (? IS NULL AND ?=0) OR project=? OR scope_key=? OR upper(scope_key) IN (SELECT value FROM json_each(?)))"
        summary="(source_table='memory_summaries' AND ((source_scope='project' AND scope_key=?) OR (source_scope='task' AND upper(scope_key) IN (SELECT value FROM json_each(?)))))"
        sql='SELECT data FROM metadata WHERE scope IN (SELECT value FROM json_each(?)) AND profile IN (SELECT value FROM json_each(?)) AND ('
        values=[wire(sorted(scopes)).decode(),wire(sorted(profiles)).decode()]
        sql+="(source_table='memory_items' AND "+selection+' AND '+text+') OR '+summary
        values += [plan.project,plan.project is not None or len(plan.task_refs),plan.project,plan.project,tasks,*args,plan.project,tasks]
        # Unmigrated events keep the ordinary project/text selection.
        legacy=text.replace('upper(scope_key)','task_ref') if textual else '1'
        legacy_args=list(args)
        if textual and plan.terms:
            legacy='('+legacy+' OR rowid IN (SELECT rowid FROM search WHERE search MATCH ?))'
            legacy_args.append(' OR '.join('"'+t.replace('"','""')+'"' for t in plan.terms))
        sql+=' OR (source_table IS NULL AND (? IS NULL OR project=?) AND '+legacy+'))'
        values += [plan.project,plan.project,*legacy_args]
        candidates=[_decode_row(r[0]) for r in self.connection.execute(sql,values)]
        # The hybrid caller requests its SQLite candidate-pool size. Apply each
        # source table's chronological cap before scoring, as SQLite recall does.
        durable=[]; summaries=[]
        for row in candidates:
            source=row.get('_migration',{}).get('source',{})
            (summaries if source.get('table')=='memory_summaries' else durable).append(row)
        def chronology(row):
            source=row.get('_migration',{}).get('source',{})
            return (source.get('updated_at') or row['updated_at'],source.get('id',0),row['id'])
        candidates=sorted(durable,key=chronology,reverse=True)[:cap]+sorted(summaries,key=chronology,reverse=True)[:cap]
        for row in candidates:
            row.pop('_outbox',None)
            meta=row.pop('_migration',{})
            source=meta.get('source')
            row['source_ref']={k:source[k] for k in ('device','table','id') if k in source} if source else None
            row['source']=source
            row['author_device']=meta.get('author_device')
            row['evidence_event_ids']=meta.get('evidence_event_ids',[])
            row['source_type']='summary' if source and source['table']=='memory_summaries' else 'durable'
            if source:
                row['scope_key']=source['scope_key']
                row['updated_at']=source.get('updated_at') or row['updated_at']
                row['created_at']=source.get('created_at') or row['created_at']
                if row['source_type']=='summary' and source['scope']!='project': row['project']=plan.project
            _score(row,plan)
        candidates.sort(key=lambda r:(r['score'],r['updated_at'],r['source_ref']['id'] if r['source_ref'] else 0,r['id']),reverse=True)
        return candidates
