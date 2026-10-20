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
                'scope TEXT, profile TEXT, project TEXT, task_ref TEXT, folded TEXT)')
            self.connection.execute('CREATE INDEX filters ON metadata(scope,profile,project)')
            self.connection.execute('CREATE TABLE events(id TEXT PRIMARY KEY, data BLOB NOT NULL)')
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
                cursor=self.connection.execute('INSERT INTO metadata VALUES (?,?,?,?,?,?,?)',
                    (visible['id'],wire(visible),visible['scope'],visible['profile'],visible['project'],
                     (visible['task_ref'] or '').upper(),visible['content'].lower()))
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
            # Validate schema, including the incremental event table. Old snapshots rebuild.
            self.connection.execute('SELECT scope,profile,project,task_ref,folded FROM metadata LIMIT 0')
            events=[parse(r[0]) for r in self.connection.execute('SELECT data FROM events')]
            self._events,self._links,self._waiting={},{},{}
            for row in events: self._link(row)
            self.dirty=False
            return True
        except Exception:
            self.connection.close(); self._connect()
            return False

    def recall(self, plan, limit, scopes, profiles):
        matches=[]; args=[]
        if plan.terms:
            match=' OR '.join('"'+t.replace('"','""')+'"' for t in plan.terms)
            matches.append('rowid IN (SELECT rowid FROM search WHERE search MATCH ?)'); args.append(match)
        for term in dict.fromkeys((plan.query.lower(),*plan.terms,*(t.lower() for t in plan.task_refs))):
            if term: matches.append('instr(folded,?)>0'); args.append(term)
        if plan.task_refs:
            matches.append('task_ref IN (SELECT value FROM json_each(?))'); args.append(wire(list(plan.task_refs)).decode())
        sql='SELECT data FROM metadata WHERE ('+(' OR '.join(matches) or '0')+')'
        sql+=' AND scope IN (SELECT value FROM json_each(?)) AND profile IN (SELECT value FROM json_each(?))'
        args.extend((wire(sorted(scopes)).decode(),wire(sorted(profiles)).decode()))
        if plan.project is not None: sql+=' AND project=?'; args.append(plan.project)
        # These rows were validated on insertion, or authenticated on snapshot load.
        # Decode only SQL-selected candidates; do not re-run wire validation per query.
        candidates=[_decode_row(r[0]) for r in self.connection.execute(sql,args)]
        for row in candidates: _score(row,plan)
        candidates.sort(key=lambda r:(r['score'],r['updated_at'],r['id']), reverse=True)
        selected=[r for r in candidates if len(plan.terms)<2 or plan.task_refs or r['matched_terms']>=2][:limit]
        return {'query':plan.query,'query_plan':{k:list(v) if isinstance(v,tuple) else v for k,v in asdict(plan).items()},
            'candidate_counts':dict(durable=len(candidates),summaries=0,events=0),'results':selected,'evidence':[]}
