"""Transport-independent broker policy, serialized state changes and threaded IPC."""
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import os
import math
import sys
import time
import threading
from types import SimpleNamespace
from ..encoding import wire, parse, typed
from ..events import prepare_event, attach_signature, verify_event, open_event, event_path
from ..model import Label
from ..errors import LabelLoweringRequiresConfirmation
from .keystore import FileKeyStore, validate_lock_wait
from ..storage import StorageError
from .. import keyring
from ..presence import PresenceManager
from .grants import names, resolve, readable, descendants
from .replica import Replica
from .audit import Audit
from .detect import sanitize
from .index import AgentIndex
from .query import analyze_query
from .handshake import Challenge
from .transport import UnixServer, PipeServer

SCHEMAS = {'whoami': set(), 'recall': set('query project task limit profiles scopes compat mode'.split()),
    'remember':set('body scope tags task label outbox_id'.split()), 'revise':set('body scope tags task label revises'.split()),
    'connector_window': {'app_id'}, 'connector_close_secret': {'app_id'}, 'sign': {'signature'}, 'import': set('source row_hash created_at scope body'.split()), 'import_ledger': set()}


class _NotConfigured:
    def fuse(self, plan, keyword, authorized, limit):
        return keyword, dict(requested='hybrid', effective='keyword', degraded='not_configured')


_NOT_CONFIGURED=_NotConfigured()

PREFETCH_WORKERS=16

COUNTS=('uploaded','downloaded','invalid','errors','outbox','local_objects','remote_objects')
IO=('bytes_up','bytes_down','class_a','class_b')
TOTALS=tuple('total_'+k for k in IO)
NETWORK=set(IO+TOTALS)|{'totals_since'}
REPLICA_PREFIXES=('events/','keys/')
PREFIXES=REPLICA_PREFIXES+tuple(p for p in keyring.CONTROL_PREFIXES if p not in REPLICA_PREFIXES)


def sync_status_valid(row):
    if type(row) is not dict: return False
    required=set(COUNTS)|{'at','outcome','interval_s','backend'}
    if set(row) not in (required,required|NETWORK): return False
    if NETWORK<=set(row):
        if any(type(row[k]) is not int or not 0<=row[k]<=2**53-1 for k in IO+TOTALS): return False
        from ..heartbeats import iso_valid
        if not iso_valid(row['totals_since']): return False
    if any(type(row[k]) is not int or row[k]<0 for k in COUNTS): return False
    if row['outcome'] not in ('ok','credentials_missing','credentials_refused','remote_error','error'): return False
    if row['backend'] not in ('s3','filesystem'): return False
    if type(row['interval_s']) not in (int,float) or not math.isfinite(row['interval_s']) or row['interval_s']<=0: return False
    try:
        at=row['at']
        return type(at) is str and at.endswith('Z') and datetime.fromisoformat(at[:-1]+'+00:00').tzinfo is not None
    except (ValueError,TypeError): return False


class SyncRemote:
    def __init__(self,remote):
        self.remote=remote; self.keys=set(); self.uploaded=0; self.errors=0; self._lock=threading.Lock()
        self.io=dict.fromkeys(IO,0); self.start=dict(getattr(remote,'io',self.io))

    def counters(self):
        source=getattr(self.remote,'io',None)
        return {k:source[k]-self.start[k] for k in IO} if source is not None else dict(self.io)

    def call(self,name,*args):
        try: return getattr(self.remote,name)(*args)
        except Exception:
            with self._lock: self.errors+=1
        raise StorageError('remote operation failed') from None

    def list(self,prefix,*,start_after=None):
        with self._lock: self.io['class_a']+=1
        try: keys=set(self.remote.list(prefix) if start_after is None else self.remote.list(prefix,start_after=start_after))
        except Exception:
            with self._lock: self.errors+=1
        else:
            self.keys=(self.keys if start_after is not None else {k for k in self.keys if not k.startswith(prefix)})|keys
            return iter(sorted(keys))
        raise StorageError('remote operation failed') from None

    def put(self,key,data):
        with self._lock: self.io['class_a']+=1
        self.call('put',key,data)
        with self._lock: self.io['bytes_up']+=len(data); self.keys.add(key); self.uploaded+=1

    def get(self,key):
        with self._lock: self.io['class_b']+=1
        data=self.call('get',key)
        with self._lock: self.io['bytes_down']+=len(data)
        return data

    def head(self,key):
        with self._lock: self.io['class_b']+=1
        return self.call('head',key)


class StagedRemote:
    """One immutable listing and prefetched objects; writes wait for the flush.

    events/ and keys/ come from the replica's (usually incremental) plan; control prefixes stay small and are listed whole."""
    def __init__(self,remote,local,plan):
        controls={key for prefix in keyring.CONTROL_PREFIXES if prefix not in REPLICA_PREFIXES
            for key in remote.list(prefix)}
        self.keys={key for key in set(plan['keys'])|controls if not key.startswith(keyring.CONTROL_EXCLUSIONS)}
        self.data={}; self.uploads=[]
        missing=sorted(self.keys-local-plan['skip']); last=time.monotonic()
        # Storage holds a pre-created boto3 client, never a session per worker.
        with ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as pool:
            pending={pool.submit(remote.get,key):key for key in missing}
            for count,future in enumerate(as_completed(pending),1):
                try: self.data[pending[future]]=future.result()
                except StorageError: pass
                if len(missing)>200 and sys.stderr is not None:
                    now=time.monotonic()
                    if now-last>=5:
                        print(f'profile-log sync: fetched {count}/{len(missing)}',file=sys.stderr); last=now

    def list(self,prefix,*,start_after=None):
        return iter(sorted(k for k in self.keys if k.startswith(prefix) and (start_after is None or k>start_after)))

    def get(self,key):
        if key not in self.data: raise StorageError('remote operation failed')
        return self.data[key]

    def put(self,key,data):
        self.uploads.append((key,data)); self.keys.add(key); self.data[key]=data


@dataclass
class Session:
    certificate: dict
    pending: object = None

    @property
    def agent_id(self): return self.certificate['agent_id']


def body_valid(body):
    if type(body) is str: return True
    if type(body) is not dict or not {'kind','content'} <= set(body): return False
    if set(body)-{'kind','content','project','task_ref','confidence','metadata'}: return False
    if any(type(body[k]) is not str for k in ('kind','content')): return False
    if any(k in body and body[k] is not None and type(body[k]) is not str for k in ('project','task_ref')): return False
    if 'metadata' in body and type(body['metadata']) is not dict: return False
    confidence=body.get('confidence',.6)
    return type(confidence) in (int,float) and 0<=confidence<=1


class Broker:
    def __init__(self, home, *, store=None, on_sync=None, on_overview=None, lock_wait=2.0):
        from ..oskeystore import select_store
        validate_lock_wait(lock_wait)
        self.lock_wait=lock_wait
        self.home,self.store,self.lock = home,store or select_store(home),threading.RLock()
        self.request_lock=threading.Lock()
        self.presence=PresenceManager(home,self.store)
        self.state=self.store.load(); self.fingerprint=self.store.fingerprint()
        self.replica=Replica(home,self.state); self.audit=Audit(home.root/'audit.jsonl',self.state.audit_key)
        self.indexes={}; self.index_versions={}; self.stopped=threading.Event(); self.server=None
        self._payload_cache={}
        self.on_sync=on_sync; self._last_sync=None; self._sync_written=None; self._sync_outcome=None
        from ..heartbeats import Heartbeats
        self.heartbeats=Heartbeats(); self._sync_row=None
        self._totals=dict.fromkeys(TOTALS,0); self._totals_since=datetime.now(timezone.utc).isoformat()
        try:
            from .. import settings
            old=parse(settings.read_raw(home.root/'sync-status.json'))
            if sync_status_valid(old) and NETWORK<=set(old):
                self._totals={k:old[k] for k in TOTALS}; self._totals_since=old['totals_since']
        except Exception: pass
        self.on_overview=on_overview; self._overview_written=None
        self._remote_keys=set(); self.sync_thread=None
        self._sync_lock=threading.Lock(); self._attempt_lock=threading.Lock()
        from .imports import Imports
        self.imports=Imports(self)
        self.semantic=None; self._semantic_wake=threading.Event()
        try:
            from .. import settings
            from .semantic import Semantic
            self.semantic=Semantic.from_settings(home.root/'index',self.state.index_master_key,settings.load().get('semantic'))
            if self.semantic is not None: self.semantic.fetch_in_fuse=False
        except Exception: self.semantic=None
        for grant in self.state.grants.values(): self.index(grant)

    def refresh(self):
        fingerprint=self.store.fingerprint()
        if fingerprint!=self.fingerprint:
            state=self.store.load()
            if state.control.get('deleted')!=self.state.control.get('deleted'):
                self._payload_cache.clear()
                for index in self.indexes.values(): index.close()
                self.indexes.clear(); self.index_versions.clear()
            self.replica.update_state(state)
            self.state=state; self.fingerprint=fingerprint; self.audit.key=state.audit_key
            for aid in list(self.indexes):
                grant=state.grants.get(aid)
                if grant is None or self.index_versions[aid][0]!=grant.digest():
                    self.indexes.pop(aid).close(); self.index_versions.pop(aid,None)

    def scope(self, sid):
        for tree in self.state.trees:
            if sid in tree.scopes: return tree.scopes[sid]
        raise ValueError('denied')

    def _open(self, envelope, data):
        identifier=envelope['id']
        if identifier not in self._payload_cache:
            key=next(k for t in self.state.trees for epochs in t.epochs.values() for k in epochs
                if k.key_id==envelope['scope_key_id'])
            payload=open_event(data,self.state.registry(),key)
            self._payload_cache[identifier]=(envelope,payload)
        return self._payload_cache[identifier]

    def payloads(self):
        for path in sorted(self.replica.keys()):
            if not path.startswith('events/'): continue
            identifier=path.rsplit('/',1)[-1].removesuffix('.json')
            if identifier in self._payload_cache:
                yield self._payload_cache[identifier]; continue
            try:
                data=self.replica.storage.get(path); envelope=verify_event(data,self.state.registry())
                if event_path(envelope)!=path: continue
                yield self._open(envelope,data)
            except (ValueError,KeyError,StopIteration): continue

    def row(self, envelope, payload, grant):
        if envelope['scope'] not in readable(self.state,grant): return None
        from .connectors import effective
        grant=effective(grant)
        if Label[payload['label']]>Label[grant.max_label]: return None
        if payload['label']=='secret' and not grant.allow_secret: return None
        scope_names=names(self.state); body=payload['body']
        if type(body) is str: body=dict(kind='fact',content=body)
        elif not body_valid(body): body=dict(kind='fact',content=wire(body).decode())
        from .imports import admitted
        migration=admitted(self,envelope,payload)
        meta={**body.get('metadata',{}),'author_device':envelope['device']} if migration else {}
        instant=datetime.fromtimestamp(payload['created_at']/1000,timezone.utc).isoformat().replace('+00:00','Z')
        return dict(id=envelope['id'],source_type='durable',kind=body['kind'],content=body['content'],
            project=body.get('project'),task_ref=body.get('task_ref') or payload['task'],confidence=body.get('confidence',.6),
            scope=scope_names[envelope['scope']],profile=scope_names[envelope['profile']],label=payload['label'],
            tags=payload['tags'],created_at=instant,updated_at=instant,revises=payload['revises'],
            _migration=meta, **({'_outbox':body['metadata']['outbox_id']} if type(body.get('metadata')) is dict and 'outbox_id' in body['metadata'] else {}))

    def index(self, grant):
        from .connectors import effective
        grant=effective(grant)
        aid=grant.agent_id; digest=grant.digest()
        if aid in self.indexes and self.index_versions[aid][0]==digest: return self.indexes[aid]
        if aid in self.indexes: self.indexes.pop(aid).close()
        version=digest,self.replica.watermark()
        index=AgentIndex(aid,self.state.index_master_key); path=self.home.root/'index'/(aid+'.snap')
        if not index.load(path,*version):
            for envelope,payload in self.payloads():
                row=self.row(envelope,payload,grant)
                if row is not None: index.add(row)
            index.save(path,*version)
        self.indexes[aid]=index; self.index_versions[aid]=version
        return index

    def ingest(self, envelope, data):
        try: _,payload=self._open(envelope,data)
        except (ValueError,KeyError,StopIteration): return
        self.imports.add(envelope,payload)
        for grant in self.state.grants.values():
            row=self.row(envelope,payload,grant)
            if row is not None: self.indexes[grant.agent_id].add(row)
        self._semantic_wake.set()

    def flush_indexes(self):
        watermark=self.replica.watermark()
        for aid,index in self.indexes.items():
            version=self.state.grants[aid].digest(),watermark
            if index.dirty or self.index_versions[aid]!=version:
                index.save(self.home.root/'index'/(aid+'.snap'),*version)
                self.index_versions[aid]=version

        if self.semantic is not None:
            self.semantic.save({r['content'] for i in self.indexes.values() for r in i.rows()})

    def backfill_once(self):
        semantic=self.semantic
        if semantic is None or not semantic.ensure(): return 0
        with self.lock:
            rows=[r for index in self.indexes.values() for r in index.rows()]
            texts=semantic.pending(rows)
            store=semantic.store
        try: pairs=semantic.embed_texts(texts)
        except (RuntimeError,ValueError): return 0
        with self.lock:
            if semantic.store is not store: return 0
            for text,vector in pairs: store.put(text,vector)
            semantic.save()
        return len(pairs)

    def request(self, session, request):
        if self.semantic is not None and type(request) is dict and request.get('op')=='recall' \
                and request.get('mode','hybrid')!='keyword' and type(request.get('query')) is str and 1<=len(request['query'])<=2048:
            self.semantic.prefetch(request['query'])
        # Lock order: request_lock -> home file lock -> self.lock (inside _request).
        with self.request_lock:
            try:
                with self.home.lock(wait=self.lock_wait): return self._request(session,request)
            except FileExistsError: return {'error':'busy'}

    def _request(self, session, request):
        with self.lock:
            audit=dict(agent_id=session.agent_id,op='invalid',outcome='invalid')
            pending=session.pending; session.pending=None
            if not isinstance(request,dict) or request.get('op')!='sign': self.imports.release(session)
            try:
                self.refresh()
                if type(request) is not dict: raise ValueError('invalid_request')
                op=request.get('op')
                if type(op) is not str or op not in SCHEMAS: raise ValueError('invalid_request')
                audit['op']=op
                if op=='recall': audit['query']=request.get('query')
                if set(request)-({'op'}|SCHEMAS[op]): raise ValueError('invalid_request')
                grant=self.state.grants.get(session.agent_id)
                if grant is None or grant.agent_public_key!=session.certificate['agent_public_key']: raise ValueError('denied')
                self.state.registry().verify_agent(session.certificate)
                first=session.agent_id not in self.state.control.get('seen_agents',[])
                if first: self.presence.require('first_access','access')
                response=self.dispatch(session,grant,request,pending,audit)
                if first:
                    self.state.control.setdefault('seen_agents',[]).append(session.agent_id)
                    self.store.save(self.state); self.fingerprint=self.store.fingerprint()
                wire(response)
                audit['outcome']='ok'
            except LabelLoweringRequiresConfirmation:
                response={'error':'label_lowering_requires_confirmation'}; audit['outcome']='denied'
            except ValueError as exc:
                code=str(exc) if str(exc) in ('denied','invalid_request','unavailable','presence_required','busy') else 'invalid_request'
                response={'error':code}; audit['outcome']='denied' if code in ('denied','presence_required') else ('error' if code=='unavailable' else 'invalid')
            except Exception:
                response={'error':'internal'}; audit['outcome']='error'
            if session.pending is None: self.imports.release(session)
            try: self.audit.append(**audit)
            except Exception:
                session.pending=None; self.imports.release(session); return {'error':'unavailable'}
            return response

    def dispatch(self, session, grant, req, pending, audit):
        op=req['op']; scope_names=names(self.state); allowed=readable(self.state,grant)
        if op=='connector_close_secret':
            from .connectors import close_secret
            return close_secret(self,session,req)
        if op=='connector_window':
            from .connectors import window
            return window(self,session,req)
        if op=='import': return self.imports.prepare(session,grant,req,audit)
        if op=='import_ledger':
            if not grant.allow_import: raise ValueError('denied')
            return {'entries':self.imports.ledger(grant), 'foreign_authority':any(
                e['device']!=self.state.device.device_id and type(p['body']) is dict and
                type(p['body'].get('metadata')) is dict and 'source' in p['body']['metadata']
                for e,p in self.payloads())}
        if op=='whoami':
            result=dict(agent_id=session.agent_id,device_id=self.state.device.device_id,read=sorted(scope_names[s] for s in allowed),
                write=sorted(scope_names[s] for s in grant.write),default_profile=scope_names[grant.default_profile],allow_secret=grant.allow_secret)
            if self.state.control.get('phase4'): result['alerts']=self.state.control.get('alerts',[])
            return result
        if op=='recall':
            mode=req.get('mode')
            if mode is not None and mode not in ('keyword','hybrid'): raise ValueError('invalid_request')
            query=req.get('query'); limit=req.get('limit',12)
            if type(req.get('compat',False)) is not bool: raise ValueError('invalid_request')
            if type(query) is not str or not 1<=len(query)<=2048 or not query.strip(): raise ValueError('invalid_request')
            if type(limit) is not int or not 1<=limit<=100: raise ValueError('invalid_request')
            for field in ('project','task'):
                if req.get(field) is not None and type(req[field]) is not str: raise ValueError('invalid_request')
            selected=req.get('profiles')
            if selected is None: selected=[scope_names[grant.default_profile]]
            requested=req.get('scopes')
            if type(selected) is not list or any(type(v) is not str for v in selected): raise ValueError('invalid_request')
            if requested is not None and (type(requested) is not list or any(type(v) is not str for v in requested)): raise ValueError('invalid_request')
            profiles={resolve(self.state,n) for n in selected}
            if any(self.scope(p).kind!='profile' or not any(self.scope(s).profile_id==p for s in allowed) for p in profiles): raise ValueError('denied')
            audit['cross_profile']=len(profiles)>1
            if len(profiles)>1 and not grant.allow_cross_profile: raise ValueError('denied')
            if len(profiles)>1: self.presence.require('cross_profile','recall')
            scopes={s for s in allowed if self.scope(s).profile_id in profiles}
            if requested is not None:
                roots={resolve(self.state,n) for n in requested}
                if not roots<=scopes: raise ValueError('denied')
                scopes &= descendants(self.state,roots)
            audit.update(profiles=sorted(profiles),scopes=sorted(scopes))
            semantic=None if mode=='keyword' else self.semantic
            if mode=='hybrid' and semantic is None: semantic=_NOT_CONFIGURED
            result=self.index(grant).recall(analyze_query(query,project=req.get('project'),task=req.get('task')),
                limit,{scope_names[s] for s in scopes},{scope_names[p] for p in profiles},compat=req.get('compat',False),semantic=semantic)
            audit['mode']=result['query_plan'].get('retrieval',{}).get('effective','keyword')
            if any(r['label']=='secret' for r in result['results']):
                if grant.connector_app_id is not None:
                    from ..registry_store import now
                    if not grant.allow_secret or now()>=grant.secret_until: raise ValueError('denied')
                else: self.presence.require('secret_read','read')
            audit['result_ids']=[r['id'] for r in result['results']]
            return result
        if op in ('remember','revise'):
            if 'body' not in req or not body_valid(req['body']): raise ValueError('invalid_request')
            from .imports import RESERVED
            if type(req['body']) is dict and RESERVED & set(req['body'].get('metadata',{})): raise ValueError('invalid_request')
            tags=req.get('tags',[]); task=req.get('task'); agent_label=req.get('label'); name=req.get('scope')
            if type(tags) is not list or any(type(t) is not str for t in tags): raise ValueError('invalid_request')
            if task is not None and type(task) is not str: raise ValueError('invalid_request')
            if name is not None and type(name) is not str: raise ValueError('invalid_request')
            if agent_label is not None and (type(agent_label) is not str or agent_label not in Label.__members__): raise ValueError('invalid_request')
            sid=grant.default_write if name is None else resolve(self.state,name)
            if sid not in grant.write or (name is None and self.scope(sid).kind=='common'): raise ValueError('denied')
            self.check_write_epoch(sid)
            previous=None; revises=req.get('revises')
            if op=='revise':
                from ..ulid import parse as parse_ulid
                parse_ulid(revises)
                old=self.index(grant).event(revises)
                if old is None or old['scope']!=scope_names[sid] or sid not in allowed: raise ValueError('denied')
                if old.get('_migration'): raise ValueError('denied')
                if old['label']=='secret' and not grant.allow_secret: raise ValueError('denied')
                previous=Label[old['label']]
            from .connectors import outbox_body
            body,duplicate=outbox_body(self,grant,req)
            if duplicate is not None: return duplicate
            body,tags,task,detected,redactions=sanitize(body,tags,task)
            label=max(self.scope(sid).default_label,detected,Label[agent_label] if agent_label else Label.general)
            cert=session.certificate
            agent=SimpleNamespace(account_id=cert['account_id'],device_id=cert['device_id'],agent_id=cert['agent_id'],certificate=lambda:dict(cert))
            key=next(t.keys[sid] for t in self.state.trees if sid in t.keys)
            unsigned=prepare_event(key.write_key,agent,body,label=label,revises=revises,tags=tags,task=task,previous_label=previous)
            session.pending=(unsigned,label.name,redactions,grant.digest())
            audit.update(scopes=[sid],profiles=[self.scope(sid).profile_id],redactions=redactions,label=label.name)
            return dict(stage='sign',unsigned=unsigned)
        if op=='sign':
            if pending is None or set(req)!={'op','signature'}: raise ValueError('invalid_request')
            unsigned,label,redactions,digest=pending[:4]
            migration=pending[4] if len(pending)>4 else None
            if digest!=grant.digest() or unsigned['scope'] not in grant.write: raise ValueError('denied')
            self.check_write_epoch(unsigned['scope'],unsigned['scope_key_id'])
            data=attach_signature(unsigned,req['signature']); verify_event(data,self.state.registry())
            for current in self.state.grants.values(): self.index(current)
            envelope=self.replica.put_event(data)
            self.ingest(envelope,data)
            audit.update(scopes=[unsigned['scope']],profiles=[unsigned['profile']],redactions=redactions,label=label,result_ids=[unsigned['id']])
            result=dict(id=unsigned['id'],label=label,redactions=sorted(redactions))
            if migration:
                audit['op']='import'
                result['outcome']=migration['outcome']
                if migration['next'] is not None: return self.imports.prepare(session,grant,migration['next'],audit)
            return result
        raise ValueError('invalid_request')

    def check_write_epoch(self,sid,key_id=None):
        profile=self.scope(sid).profile_id
        if profile in self.state.control.get('deleted',[]) or self.state.control.get('profile_states',{}).get(profile,'active')!='active':
            raise ValueError('denied')
        if self.state.control.get('phase4'):
            from ..keyring import AgeKeyring
            ring=AgeKeyring(self.replica.storage,self.state.registry(),[s for t in self.state.trees for s in t.scopes.values()])
            current=ring.current(profile,sid)['key_id']
            local=next(t.keys[sid] for t in self.state.trees if sid in t.keys)
            if current!=local.key_id or (key_id is not None and key_id!=current): raise ValueError('denied')

    def local_keys(self):
        prefixes=dict.fromkeys(REPLICA_PREFIXES+keyring.CONTROL_PREFIXES)
        return {key for prefix in prefixes for key in self.replica.storage.list(prefix)
            if not key.startswith(keyring.CONTROL_EXCLUSIONS)}

    def sync_once(self, remote):
        with self._sync_lock:
            remote=remote if isinstance(remote,SyncRemote) else SyncRemote(remote)
            # Filesystem listings are safe alongside immutable local writes.
            with self.lock: self.refresh(); prepared=self.replica.prepare()
            local=self.local_keys()
            try: plan=self.replica.plan(remote,prepared=prepared); staged=StagedRemote(remote,local,plan)
            except StorageError: return dict(uploaded=0,downloaded=0,invalid=0,errors=remote.errors)
            failed=set()
            try:
                with self.lock:
                    self.refresh(); before=self.local_keys()
                    if self.state.control.get('phase4'):
                        from ..keyring import sync_control
                        sync_control(self.home,self.store,staged); self.refresh()
                    controls=self.local_keys()-before
                    plan['keys']=plan['keys']|{k for k in staged.keys if k.startswith(REPLICA_PREFIXES)}
                    for grant in self.state.grants.values(): self.index(grant)
                    result=self.replica.sync_once(staged,on_event=self.ingest,plan=plan)
                    result['downloaded']+=len(controls)
                    if not remote.errors: self._remote_keys=set(staged.keys)
                    if self.state.control.get('phase4'): result['alerts']=self.state.control.get('alerts',[])
            finally:
                # Even a partial control apply may have queued immutable uploads.
                for key,data in staged.uploads:
                    try: remote.put(key,data)
                    except StorageError: failed.add(key)
                with self.lock:
                    self.replica.remote_keys.difference_update(failed)
                    self._remote_keys.difference_update(failed)
            result['uploaded']=remote.uploaded
            result['errors']=max(result['errors']+len(failed),remote.errors)
            return result

    def sync_attempt(self,remote,interval):
        # Serialize status publication too; sync_once also protects the CLI path.
        with self._attempt_lock: self._sync_attempt(remote,interval)

    def _sync_attempt(self,remote,interval):
        row=dict.fromkeys(COUNTS,0); row.update(outcome='ok',interval_s=interval,backend=getattr(remote,'backend','filesystem'))
        measured=None
        try:
            target=remote() if callable(remote) else remote
            measured=SyncRemote(target)
            result=self.sync_once(measured)
            for k in ('uploaded','downloaded','invalid','errors'): row[k]=result[k]
            if row['errors']: row['outcome']='remote_error'
        except StorageError as exc:
            row['outcome']={'S3 credentials missing':'credentials_missing','S3 credentials refused':'credentials_refused'}.get(str(exc),'remote_error')
            row['errors']=1
        except Exception:
            row['outcome']='error'; row['errors']=1
        try:
            with self.lock:
                local=self.local_keys()
                if measured is not None:
                    row['uploaded']=measured.uploaded
                    row['errors']=max(row['errors'],measured.errors)
                row.update(outbox=len(local-self._remote_keys),local_objects=len(local),remote_objects=len(self._remote_keys))
        except Exception: row['outcome']='error'; row['errors']+=1
        row['at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        if not sync_status_valid(row):
            row=dict.fromkeys(COUNTS,0); row.update(at=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
                outcome='error',errors=1,interval_s=interval,backend='s3')
        if row['outcome']!=self._sync_outcome:
            try: print('profile-log sync: '+row['outcome']+' uploaded='+str(row['uploaded'])+' outbox='+str(row['outbox']),file=sys.stderr)
            except Exception: pass
            else: self._sync_outcome=row['outcome']
        now=time.monotonic()
        if measured is not None and self.state.control.get('phase4'):
            # Wall clock, not monotonic: macOS stops the monotonic clock while asleep, which held heartbeats back after wake.
            try: self.heartbeats.update(measured,self.state,row,tick=time.time())
            except Exception as exc: self.heartbeats.error(exc)
        io=measured.counters() if measured is not None else dict.fromkeys(IO,0)
        for k in IO: self._totals['total_'+k]+=io[k]
        row.update(io,**self._totals,totals_since=self._totals_since)
        # Persist every cycle with traffic, otherwise retain the existing idle throttle.
        counts={k:v for k,v in row.items() if k not in {'at',*NETWORK}}
        traffic=any(io.values())
        if traffic or counts!=self._last_sync or self._sync_written is None or now-self._sync_written>=600:
            try:
                if self.on_sync is not None: self.on_sync(dict(row))
            except Exception: pass
            else: self._last_sync=counts; self._sync_written=now
        self._sync_row=dict(row)
        self._publish_overview(now)

    def _publish_overview(self, now):
        # Content-free status refresh (overview and keys, from one state); at most once a minute and never fatal to sync.
        if self.on_overview is None or (self._overview_written is not None and now-self._overview_written<60): return
        self._overview_written=now
        try:
            from ..status import overview, keys
            with self.lock: value=dict(overview=overview(self.state,heartbeats=self.heartbeats.rows,last_sync=self._sync_row),keys=keys(self.state))
            self.on_overview(value)
        except Exception: pass

    def connection(self, conn):
        session=None
        try:
            try:
                h=conn.receive()
                with self.lock: self.refresh(); challenge=Challenge(self.state,h)
                conn.send(challenge.message); auth=conn.receive()
                with self.lock:
                    self.refresh(); aid=challenge.verify(auth,self.state)
                    session=Session(auth['agent_cert'])
            except Exception:
                try: self.audit.append(agent_id=None,op='handshake',outcome='unauthenticated')
                except Exception: conn.send({'error':'unavailable'}); return
                conn.send({'error':'unauthenticated'}); return
            try: self.audit.append(agent_id=aid,op='handshake',outcome='ok')
            except Exception: conn.send({'error':'unavailable'}); return
            conn.send({'format':'agentmesh-broker-ready-v1','agent_id':aid})
            while not self.stopped.is_set():
                try: req=conn.receive()
                except EOFError: return
                except Exception:
                    try: self.audit.append(agent_id=aid,op='invalid',outcome='invalid')
                    except Exception: pass
                    return
                conn.send(self.request(session,req))
        except (OSError,ValueError,EOFError): pass
        finally:
            with self.lock:
                if session is not None: self.imports.release(session)
            conn.close()

    def serve(self, *, server=None, remote=None, sync_interval=None, on_sync=None, on_overview=None):
        if sync_interval is not None and (remote is None or type(sync_interval) not in (int,float) or not math.isfinite(sync_interval) or sync_interval<=0):
            raise ValueError('invalid sync configuration')
        if on_sync is not None: self.on_sync=on_sync
        if on_overview is not None: self.on_overview=on_overview
        self.server=server or (PipeServer if os.name=='nt' else UnixServer)(self.home.address(self.state.device.device_id))
        if sync_interval is not None:
            def sync_loop():
                while not self.stopped.is_set():
                    try: self.sync_attempt(remote,sync_interval)
                    except Exception: pass
                    if self.stopped.wait(sync_interval): break
            self.sync_thread=threading.Thread(target=sync_loop,daemon=True); self.sync_thread.start()
        def semantic_loop():
            while not self.stopped.is_set():
                while self.backfill_once()>0 and not self.stopped.is_set(): pass
                self._semantic_wake.wait(300); self._semantic_wake.clear()
        if self.semantic is not None: threading.Thread(target=semantic_loop,daemon=True).start()
        while not self.stopped.is_set():
            try: conn=self.server.accept()
            except ValueError:
                try: self.audit.append(agent_id=None,op='handshake',outcome='unauthenticated')
                except Exception: pass
                continue
            except OSError:
                if self.stopped.is_set(): break
                raise
            threading.Thread(target=self.connection,args=(conn,),daemon=True).start()

    def close(self):
        self.stopped.set()
        if self.server: self.server.close()
        with self.lock:
            self.refresh()
            self.flush_indexes()
            for index in self.indexes.values(): index.close()
            self.indexes.clear(); self.index_versions.clear()
