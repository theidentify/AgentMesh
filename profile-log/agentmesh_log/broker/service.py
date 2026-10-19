"""Transport-independent broker policy, serialized state changes and threaded IPC."""
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import threading
from types import SimpleNamespace
from ..encoding import wire, parse, typed
from ..events import prepare_event, attach_signature, verify_event, open_event, event_path
from ..model import Label
from ..errors import LabelLoweringRequiresConfirmation
from .keystore import FileKeyStore
from .grants import names, resolve, readable, descendants
from .replica import Replica
from .audit import Audit
from .detect import sanitize
from .index import AgentIndex
from .query import analyze_query
from .handshake import Challenge
from .transport import UnixServer, PipeServer

SCHEMAS = {'whoami': set(), 'recall': set('query project task limit profiles scopes'.split()),
    'remember':set('body scope tags task label'.split()), 'revise':set('body scope tags task label revises'.split()),
    'sign': {'signature'}}


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
    def __init__(self, home):
        self.home,self.store,self.lock = home,FileKeyStore(home),threading.RLock()
        self.state=self.store.load(); self.fingerprint=self.store.fingerprint()
        self.replica=Replica(home,self.state); self.audit=Audit(home.root/'audit.jsonl',self.state.audit_key)
        self.indexes={}; self.index_versions={}; self.stopped=threading.Event(); self.server=None
        self._payload_cache={}
        for grant in self.state.grants.values(): self.index(grant)

    def refresh(self):
        fingerprint=self.store.fingerprint()
        if fingerprint!=self.fingerprint:
            state=self.store.load(); self.replica.update_state(state)
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
        # TODO(phase4-presence): require presence for secret reads.
        if payload['label']=='secret' and not grant.allow_secret: return None
        scope_names=names(self.state); body=payload['body']
        if type(body) is str: body=dict(kind='fact',content=body)
        elif not body_valid(body): body=dict(kind='fact',content=wire(body).decode())
        instant=datetime.fromtimestamp(payload['created_at']/1000,timezone.utc).isoformat().replace('+00:00','Z')
        return dict(id=envelope['id'],source_type='durable',kind=body['kind'],content=body['content'],
            project=body.get('project'),task_ref=body.get('task_ref') or payload['task'],confidence=body.get('confidence',.6),
            scope=scope_names[envelope['scope']],profile=scope_names[envelope['profile']],label=payload['label'],
            tags=payload['tags'],created_at=instant,updated_at=instant,revises=payload['revises'])

    def index(self, grant):
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
        for grant in self.state.grants.values():
            row=self.row(envelope,payload,grant)
            if row is not None: self.indexes[grant.agent_id].add(row)

    def flush_indexes(self):
        watermark=self.replica.watermark()
        for aid,index in self.indexes.items():
            version=self.state.grants[aid].digest(),watermark
            if index.dirty or self.index_versions[aid]!=version:
                index.save(self.home.root/'index'/(aid+'.snap'),*version)
                self.index_versions[aid]=version

    def request(self, session, request):
        with self.lock:
            audit=dict(agent_id=session.agent_id,op='invalid',outcome='invalid')
            pending=session.pending; session.pending=None
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
                response=self.dispatch(session,grant,request,pending,audit)
                wire(response)
                audit['outcome']='ok'
            except LabelLoweringRequiresConfirmation:
                response={'error':'label_lowering_requires_confirmation'}; audit['outcome']='denied'
            except ValueError as exc:
                code=str(exc) if str(exc) in ('denied','invalid_request','unavailable') else 'invalid_request'
                response={'error':code}; audit['outcome']='denied' if code=='denied' else ('error' if code=='unavailable' else 'invalid')
            except Exception:
                response={'error':'internal'}; audit['outcome']='error'
            try: self.audit.append(**audit)
            except Exception: session.pending=None; return {'error':'unavailable'}
            return response

    def dispatch(self, session, grant, req, pending, audit):
        op=req['op']; scope_names=names(self.state); allowed=readable(self.state,grant)
        if op=='whoami':
            return dict(agent_id=session.agent_id,read=sorted(scope_names[s] for s in allowed),
                write=sorted(scope_names[s] for s in grant.write),default_profile=scope_names[grant.default_profile],allow_secret=grant.allow_secret)
        if op=='recall':
            query=req.get('query'); limit=req.get('limit',12)
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
            # TODO(phase4-presence): cross-profile access requires presence.
            if len(profiles)>1 and not grant.allow_cross_profile: raise ValueError('denied')
            scopes={s for s in allowed if self.scope(s).profile_id in profiles}
            if requested is not None:
                roots={resolve(self.state,n) for n in requested}
                if not roots<=scopes: raise ValueError('denied')
                scopes &= descendants(self.state,roots)
            audit.update(profiles=sorted(profiles),scopes=sorted(scopes))
            result=self.index(grant).recall(analyze_query(query,project=req.get('project'),task=req.get('task')),
                limit,{scope_names[s] for s in scopes},{scope_names[p] for p in profiles})
            audit['result_ids']=[r['id'] for r in result['results']]
            return result
        if op in ('remember','revise'):
            if 'body' not in req or not body_valid(req['body']): raise ValueError('invalid_request')
            tags=req.get('tags',[]); task=req.get('task'); agent_label=req.get('label'); name=req.get('scope')
            if type(tags) is not list or any(type(t) is not str for t in tags): raise ValueError('invalid_request')
            if task is not None and type(task) is not str: raise ValueError('invalid_request')
            if name is not None and type(name) is not str: raise ValueError('invalid_request')
            if agent_label is not None and (type(agent_label) is not str or agent_label not in Label.__members__): raise ValueError('invalid_request')
            sid=grant.default_write if name is None else resolve(self.state,name)
            if sid not in grant.write or (name is None and self.scope(sid).kind=='common'): raise ValueError('denied')
            previous=None; revises=req.get('revises')
            if op=='revise':
                from ..ulid import parse as parse_ulid
                parse_ulid(revises)
                old=self.index(grant).event(revises)
                if old is None or old['scope']!=scope_names[sid] or sid not in allowed: raise ValueError('denied')
                if old['label']=='secret' and not grant.allow_secret: raise ValueError('denied')
                previous=Label[old['label']]
            body,tags,task,detected,redactions=sanitize(req['body'],tags,task)
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
            unsigned,label,redactions,digest=pending
            if digest!=grant.digest() or unsigned['scope'] not in grant.write: raise ValueError('denied')
            data=attach_signature(unsigned,req['signature']); verify_event(data,self.state.registry())
            for current in self.state.grants.values(): self.index(current)
            envelope=self.replica.put_event(data)
            self.ingest(envelope,data)
            audit.update(scopes=[unsigned['scope']],profiles=[unsigned['profile']],redactions=redactions,label=label,result_ids=[unsigned['id']])
            return dict(id=unsigned['id'],label=label,redactions=sorted(redactions))
        raise ValueError('invalid_request')

    def sync_once(self, remote):
        with self.lock:
            self.refresh()
            for grant in self.state.grants.values(): self.index(grant)
            return self.replica.sync_once(remote,on_event=self.ingest)

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
            # TODO(phase4-presence): first access by an agent requires presence.
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
        finally: conn.close()

    def serve(self, *, server=None, remote=None, sync_interval=None):
        self.server=server or (PipeServer if os.name=='nt' else UnixServer)(self.home.address(self.state.device.device_id))
        if sync_interval is not None:
            if remote is None or sync_interval<=0: raise ValueError('invalid sync configuration')
            def sync_loop():
                while not self.stopped.wait(sync_interval): self.sync_once(remote)
            threading.Thread(target=sync_loop,daemon=True).start()
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
