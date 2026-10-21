"""Signed connector controls. Authority is independently resolved on each device."""
import hashlib
from urllib.parse import urlsplit
from .encoding import fields, hex_bytes, milliseconds, parse, wire
from .identity import verify_signature
from .registry_store import RegistryStore, signed, domain, now
from .keyring import AgeKeyring
from .ulid import generate, parse as parse_ulid

SCHEMAS = {
 'gateway': {'gateway_public_key','origin','issued_at','issuer_device'},
 'outbox-key': {'device_id','outbox_public_key','issued_at'},
 'connector-policy': {'app_id','display_name_hash','oauth_client_id_hash','read','remember','max_label','allow_cross_profile','limits','state','issuer_device','issued_at'},
 'connector-secret-window': {'app_id','not_before','not_after','issuer_device'},
 'outbox-item': {'outbox_id','app_id','created_at','recipients','gateway_key_id'},
}


def exact(value, keys):
    if type(value) is not dict or set(value)!=keys: raise ValueError('invalid_record')


def digest(value): return hashlib.sha256(value.encode('utf-8')).hexdigest()

def gateway_id(record):
    return hashlib.sha256(b'AgentMesh/gateway-key-id/v1\x00'+hex_bytes(record['gateway_public_key'],32)).hexdigest()


def shape(record,kind):
    fields(record,SCHEMAS[kind]|{'format','signature'},'agentmesh-'+kind+'-v1')
    hex_bytes(record['signature'],64)
    for k in ('issued_at','not_before','not_after','created_at'):
        if k in record: milliseconds(record[k])
    for k in ('issuer_device','device_id','app_id'):
        if k in record: hex_bytes(record[k],16)
    for k in ('gateway_public_key','outbox_public_key','display_name_hash','oauth_client_id_hash','gateway_key_id'):
        if k in record: hex_bytes(record[k],32)
    if kind=='gateway':
        u=urlsplit(record['origin'])
        if u.scheme!='https' or not u.netloc or u.username or u.password or u.query or u.fragment or u.path: raise ValueError('invalid_record')
    if kind=='connector-secret-window':
        if not 0<record['not_after']-record['not_before']<=3600000: raise ValueError('invalid_record')
    if kind=='connector-policy':
        if record['max_label'] not in ('general','internal') or record['state'] not in ('active','revoked') or record['allow_cross_profile'] is not False: raise ValueError('invalid_record')
        exact(record['limits'],{'recall_limit_max','requests_per_minute'})
        for k,maximum in (('recall_limit_max',20),('requests_per_minute',1000)):
            if type(record['limits'][k]) is not int or not 1<=record['limits'][k]<=maximum: raise ValueError('invalid_record')
        if type(record['read']) is not list or len(record['read'])>100: raise ValueError('invalid_record')
        seen=set()
        for entry in record['read']:
            exact(entry,{'profile','scopes'}); hex_bytes(entry['profile'],16)
            if entry['profile'] in seen: raise ValueError('invalid_record')
            seen.add(entry['profile'])
            if entry['scopes'] is not None:
                if type(entry['scopes']) is not list or not entry['scopes'] or len(set(entry['scopes']))!=len(entry['scopes']): raise ValueError('invalid_record')
                for scope in entry['scopes']: hex_bytes(scope,16)
        if record['remember'] is not None:
            exact(record['remember'],{'profile','scope'})
            for v in record['remember'].values(): hex_bytes(v,16)
    if kind=='outbox-item':
        parse_ulid(record['outbox_id'])
        if type(record['recipients']) is not list or not record['recipients']: raise ValueError('invalid_record')
        seen=set()
        from .keys import decode_ciphertext
        for r in record['recipients']:
            exact(r,{'device_id','ciphertext'}); hex_bytes(r['device_id'],16); decode_ciphertext(r['ciphertext'])
            if r['device_id'] in seen: raise ValueError('invalid_record')
            seen.add(r['device_id'])
    return record


def gateway_record(device,public,origin,*,issued_at=None):
    return signed('gateway',device._private,gateway_public_key=public.hex(),origin=origin,issued_at=now() if issued_at is None else issued_at,issuer_device=device.device_id)


def outbox_key_record(device,public,*,issued_at=None):
    return signed('outbox-key',device._private,device_id=device.device_id,outbox_public_key=public.hex(),issued_at=now() if issued_at is None else issued_at)


def policy_record(device,app_id,display_name,client_id,*,read,remember,max_label='internal',limits=None,state='active',issued_at=None):
    return signed('connector-policy',device._private,app_id=app_id,display_name_hash=digest(display_name),oauth_client_id_hash=digest(client_id),read=read,remember=remember,max_label=max_label,allow_cross_profile=False,limits=limits or {'recall_limit_max':20,'requests_per_minute':60},state=state,issuer_device=device.device_id,issued_at=now() if issued_at is None else issued_at)


def secret_record(device,app_id,not_before,not_after):
    return signed('connector-secret-window',device._private,app_id=app_id,not_before=not_before,not_after=not_after,issuer_device=device.device_id)


class ConnectorRegistry:
    def __init__(self,storage,account,scopes):
        self.storage,self.account,self.scopes=storage,account,list(scopes)

    def context(self):
        registry=RegistryStore(self.storage,self.account).load()
        return registry,AgeKeyring(self.storage,registry,self.scopes)

    def publish(self,record):
        kind=record['format'].removeprefix('agentmesh-').removesuffix('-v1'); shape(record,kind)
        if kind=='gateway': path='registry/gateway/'+gateway_id(record)+'.json'
        elif kind=='outbox-key': path='registry/outbox-keys/'+record['device_id']+'.json'
        elif kind=='outbox-item': path='outbox/'+record['outbox_id']+'.json'
        else: path='connectors/'+record['app_id']+('/policy/' if kind=='connector-policy' else '/secret/')+generate()+'.json'
        self.storage.put(path,wire(record)); return path

    def records(self,prefix,kind):
        for path in sorted(self.storage.list(prefix)):
            try:
                r=shape(parse(self.storage.get(path)),kind)
                leaf=path.removeprefix(prefix)
                if '/' in leaf or not leaf.endswith('.json'): continue
                if kind in ('connector-policy','connector-secret-window'): parse_ulid(leaf[:-5])
                yield path,r
            except (ValueError,KeyError,TypeError): continue

    def reader(self,ring,device,at):
        # A certificate alone is never read authority. Validate at least one grant chain.
        return any(ring.authorized(device,s.profile_id,s.scope_id,at) for s in self.scopes)

    def covers(self,ring,device,policy,at,*,remember=True):
        targets=[(e['profile'],s) for e in policy['read'] for s in (e['scopes'] or [e['profile']])]
        if remember and policy['remember'] is not None:
            e=policy['remember']; targets.append((e['profile'],e['scope']))
        for profile,scope in targets:
            if not ring.authorized(device,profile,scope,at): return False
        return True

    def narrowing(self,old,new,ring):
        if old is None: return False
        if new['state']=='revoked': return True
        if old['state']=='revoked': return False
        if new['oauth_client_id_hash']!=old['oauth_client_id_hash']: return False
        if new['remember'] is not None and new['remember']!=old['remember']: return False
        if new['max_label']=='internal' and old['max_label']=='general': return False
        if any(new['limits'][k]>old['limits'][k] for k in old['limits']): return False
        for entry in new['read']:
            previous=next((e for e in old['read'] if e['profile']==entry['profile']),None)
            if previous is None: return False
            roots=set(previous['scopes'] or [previous['profile']])
            if any(not ring.ancestors(entry['profile'],s)&roots for s in (entry['scopes'] or [entry['profile']])): return False
        return True

    def policy(self,app_id,*,at=None):
        hex_bytes(app_id,16); at=now() if at is None else at
        registry,ring=self.context(); current=None
        # Signed issue time precedes path tie-break: moving old bytes to a new path cannot widen a newer policy.
        rows=sorted(self.records('connectors/'+app_id+'/policy/','connector-policy'),key=lambda p:(p[1]['issued_at'],p[0]))
        for path,r in rows:
            try:
                if r['app_id']!=app_id or r['issued_at']>at: continue
                public=registry.device_public(r['issuer_device'],at=r['issued_at'])
                verify_signature(r,public,domain('connector-policy'))
                if not self.reader(ring,r['issuer_device'],r['issued_at']): continue
                if not self.narrowing(current,r,ring) and not self.covers(ring,r['issuer_device'],r,r['issued_at']): continue
                current=r
            except (ValueError,KeyError,TypeError): continue
        return current

    def window(self,app_id,*,at=None):
        at=now() if at is None else at; registry,ring=self.context(); accepted=[]
        for path,r in self.records('connectors/'+app_id+'/secret/','connector-secret-window'):
            try:
                if r['app_id']!=app_id or not r['not_before']<=at<r['not_after']: continue
                public=registry.device_public(r['issuer_device'],at=at)
                verify_signature(r,public,domain('connector-secret-window'))
                p=self.policy(app_id,at=r['not_before'])
                if p is None or p['state']!='active' or not self.reader(ring,r['issuer_device'],r['not_before']): continue
                if not self.covers(ring,r['issuer_device'],p,r['not_before'],remember=False): continue
                accepted.append((r['not_before'],path,r))
            except (ValueError,KeyError,TypeError): continue
        return max(accepted,key=lambda x:x[:2])[2] if accepted else None

    def gateway(self,key_id,*,at=None):
        hex_bytes(key_id,32); at=now() if at is None else at; registry,ring=self.context(); accepted=[]
        for path,r in self.records('registry/gateway/','gateway'):
            try:
                identifier=gateway_id(r)
                if path!='registry/gateway/'+identifier+'.json' or r['issued_at']>at: continue
                verify_signature(r,registry.device_public(r['issuer_device'],at=at),domain('gateway'))
                if not self.reader(ring,r['issuer_device'],r['issued_at']): continue
                accepted.append((r['issued_at'],identifier,r))
            except (ValueError,KeyError,TypeError): continue
        if not accepted: raise ValueError('denied')
        selected=max(accepted,key=lambda x:x[:2])
        if selected[1]!=key_id: raise ValueError('denied')
        return selected[2]

    def outbox_keys(self,*,at=None):
        at=now() if at is None else at; registry,ring=self.context(); result=[]
        for path,r in self.records('registry/outbox-keys/','outbox-key'):
            try:
                if path!='registry/outbox-keys/'+r['device_id']+'.json' or r['issued_at']>at: continue
                verify_signature(r,registry.device_public(r['device_id'],at=at),domain('outbox-key'))
                result.append(r)
            except (ValueError,KeyError,TypeError): continue
        return result
