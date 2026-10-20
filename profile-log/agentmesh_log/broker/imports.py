"""Reserved migration provenance and replica-derived import heads."""
import copy
import re
import time
from types import SimpleNamespace
from ..events import prepare_event
from ..model import Label
from .detect import sanitize
from .grants import resolve, names

RESERVED = {'source','row_hash','evidence_event_ids','retired'}
TABLES = {'memory_items','memory_summaries'}
SOURCE_FIELDS = {'table','id','scope','scope_key','memory_key','version','created_at','updated_at'}


def source_key(source):
    if type(source) is not dict or set(source) != {'table','id'} or source['table'] not in TABLES or type(source['id']) is not int:
        raise ValueError('invalid_request')
    return source['table'], source['id']


def validate(body, source, digest):
    from .service import body_valid
    if not body_valid(body) or type(body) is not dict: raise ValueError('invalid_request')
    meta=body.get('metadata')
    if type(meta) is not dict or set(meta)!=RESERVED: raise ValueError('invalid_request')
    src=meta['source']
    if type(src) is not dict or set(src)!=SOURCE_FIELDS: raise ValueError('invalid_request')
    if {k:src[k] for k in ('table','id')}!=source: raise ValueError('invalid_request')
    if src['scope'] not in ('global','project','task','session'): raise ValueError('invalid_request')
    if any(src[k] is not None and type(src[k]) is not str for k in ('scope_key','memory_key','created_at','updated_at')): raise ValueError('invalid_request')
    if src['version'] is not None and type(src['version']) is not int: raise ValueError('invalid_request')
    if meta['row_hash']!=digest or type(meta['retired']) is not bool: raise ValueError('invalid_request')
    if type(meta['evidence_event_ids']) is not list or any(type(v) is not int for v in meta['evidence_event_ids']): raise ValueError('invalid_request')


def admitted(broker, envelope, payload):
    body=payload['body']
    if type(body) is not dict or type(body.get('metadata')) is not dict: return None
    meta=body['metadata']; src=meta.get('source')
    if type(src) is not dict: return None
    try:
        from ..ulid import timestamp
        broker.state.registry().verify_historical_agent(envelope['agent_cert'], timestamp(envelope['id']))
        if envelope['device'] == broker.state.device.device_id:
            grant=broker.state.grants.get(envelope['agent'])
            if not ((grant is not None and grant.allow_import) or
                    envelope['agent'] in broker.state.control.get('import_agents', [])):
                return None
    except (ValueError, KeyError): return None
    try:
        source={k:src[k] for k in ('table','id')}; key=source_key(source)
        validate(body,source,meta.get('row_hash'))
        if type(meta['row_hash']) is not str or not re.fullmatch('[0-9a-f]{64}',meta['row_hash']): return None
    except (ValueError,KeyError,TypeError): return None
    return key


class Imports:
    def __init__(self, broker):
        self.broker=broker; self.entries={}; self.reservations={}
        # Migration authors are explicit. Ordinary snapshot-only restarts need
        # no decryption; inspect envelopes before opening migration bodies.
        for path in sorted(broker.replica.keys()):
            if not path.startswith('events/'): continue
            try:
                from ..events import verify_event
                data=broker.replica.storage.get(path)
                envelope=verify_event(data,broker.state.registry())
                grant=broker.state.grants.get(envelope['agent'])
                if (envelope['device'] != broker.state.device.device_id or
                        envelope['agent'] in broker.state.control.get('import_agents', []) or
                        (grant is not None and grant.allow_import)):
                    _,payload=broker._open(envelope,data)
                    self.add(envelope,payload)
            except (ValueError,KeyError,StopIteration): continue

    def add(self,envelope,payload):
        key=admitted(self.broker,envelope,payload)
        if key is not None: self.entries.setdefault(key,{})[envelope['id']]=(envelope,payload)

    def head(self,key):
        values=self.entries.get(key,{})
        if not values: return None
        # ULIDs track import order, while payload timestamps preserve source age.
        return values[max(values)]

    def release(self,session):
        for key,owner in list(self.reservations.items()):
            if owner is session: del self.reservations[key]

    def ledger(self,grant):
        result=[]; scope_names=names(self.broker.state)
        for key in sorted(self.entries):
            envelope,payload=self.head(key)
            if envelope['scope'] not in grant.write: continue
            result.append(dict(source=dict(table=key[0],id=key[1]),event=envelope['id'],
                chain=sorted(self.entries[key]),scope=scope_names[envelope['scope']],
                status='retired' if payload['body']['metadata']['retired'] else
                ('created' if len(self.entries[key])==1 else 'revised'),reason=None))
        return result

    def prepare(self,session,grant,req,audit):
        b=self.broker
        if not grant.allow_import: raise ValueError('denied')
        if set(req)!={'op','source','row_hash','created_at','scope','body'}: raise ValueError('invalid_request')
        key=source_key(req['source']); digest=req['row_hash']; stamp=req['created_at']
        if type(digest) is not str or not re.fullmatch('[0-9a-f]{64}',digest): raise ValueError('invalid_request')
        if type(stamp) is not int or not 0<=stamp<2**63 or stamp>int(time.time()*1000)+300000: raise ValueError('invalid_request')
        if type(req['scope']) is not str: raise ValueError('invalid_request')
        sid=resolve(b.state,req['scope'])
        if sid not in grant.write: raise ValueError('denied')
        if key in self.reservations and self.reservations[key] is not session: raise ValueError('busy')
        body=req['body']
        if body is not None:
            validate(body,req['source'],digest)
            if body['metadata']['retired']: raise ValueError('invalid_request')
        old=self.head(key); previous=None; revises=None; continuation=None
        if old:
            env,payload=old; oldbody=payload['body']; retired=oldbody['metadata']['retired']
            if env['scope'] not in grant.write: raise ValueError('denied')
            if body is None and retired: return dict(outcome='unchanged',id=env['id'])
            if body is not None and not retired and sid==env['scope'] and digest==oldbody['metadata']['row_hash']:
                return dict(outcome='unchanged',id=env['id'])
            if body is None or (sid!=env['scope'] and not retired):
                if body is not None: continuation=copy.deepcopy(req)
                sid=env['scope']; body=copy.deepcopy(oldbody)
                body['content']=''; body['metadata']['retired']=True
                body['metadata']['row_hash']=digest
                outcome='retired'; revises=env['id']; previous=Label[payload['label']]
            elif sid==env['scope']:
                outcome='revised'; revises=env['id']; previous=Label[payload['label']]
            else: outcome='created'
        elif body is None: return dict(outcome='unchanged',id=None)
        else: outcome='created'
        b.check_write_epoch(sid)
        body,tags,task,detected,redactions=sanitize(body,[],body.get('task_ref'))
        label=max(b.scope(sid).default_label,detected,previous or Label.general)
        cert=session.certificate
        agent=SimpleNamespace(account_id=cert['account_id'],device_id=cert['device_id'],agent_id=cert['agent_id'],certificate=lambda:dict(cert))
        epoch=next(t.keys[sid] for t in b.state.trees if sid in t.keys)
        unsigned=prepare_event(epoch.write_key,agent,body,label=label,revises=revises,tags=tags,task=task,created_at=stamp,previous_label=previous)
        self.reservations[key]=session
        session.pending=(unsigned,label.name,redactions,grant.digest(),dict(key=key,outcome=outcome,next=continuation))
        audit.update(op='import',scopes=[sid],profiles=[b.scope(sid).profile_id],redactions=redactions,label=label.name)
        return dict(stage='sign',unsigned=unsigned)
