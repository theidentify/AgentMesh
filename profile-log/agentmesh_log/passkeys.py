"""Device-only WebAuthn authority. The relay never verifies or grants credentials."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import webauthn
from webauthn.helpers import options_to_json, decode_credential_public_key
from webauthn.helpers.structs import AuthenticatorSelectionCriteria, ResidentKeyRequirement, UserVerificationRequirement, PublicKeyCredentialDescriptor
from .connector import digest, exact
from .connector_cli import controls, approval_proposal, apply_approval
from .encoding import fields, hex_bytes, milliseconds, parse, wire, typed
from .registry_store import signed, domain, now
from .identity import verify_signature
from .presence import PresenceManager
from .broker.audit import Audit
from .storage.filesystem import FilesystemStorage


def b64(value): return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def unb64(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]+',value) or len(value)>16384: raise ValueError('denied')
    raw=base64.urlsafe_b64decode(value+'='*(-len(value)%4))
    if b64(raw)!=value: raise ValueError('denied')
    return raw


def credential_hash(raw): return hashlib.sha256(raw).hexdigest()


def settings(state):
    value={'rp_id':'status.krisb8.com','origin':'https://status.krisb8.com','profiles':['personal','work'],'remember':'personal/inbox','max_label':'internal',**state.control.get('passkey_settings',{})}
    rp=value['rp_id']
    if type(rp) is not str or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)+',rp) or value['origin']!='https://'+rp: raise ValueError('invalid_request')
    if type(value['profiles']) is not list or not value['profiles'] or any(type(n) is not str or not n for n in value['profiles']) or type(value['remember']) is not str or not value['remember'].endswith('/inbox') or value['max_label'] not in ('general','internal'): raise ValueError('invalid_request')
    return value


class Passkeys:
    # CLI administration owns its lock; ceremonies lock only local state transitions.
    def __init__(self,home,store,storage,*,clock=now,update=None):
        self.home,self.store,self.storage,self.clock=home,store,storage,clock
        self.local=FilesystemStorage(home.root/'replica');self.update=update or home.lock

    def credentials(self):
        state=self.store.load(); cr=controls(state,self.local); registry,ring=cr.context(); at=self.clock(); result={}; removed=set()
        for kind,prefix in (('passkey-removal','registry/passkey-removals/'),('passkey','registry/passkeys/')):
            for path in self.local.list(prefix):
                try:
                    r=parse(self.local.get(path)); keys={'credential_id_sha256','removed_at','issuer_device'} if kind=='passkey-removal' else {'credential_id','public_key','label','rp_id','created_at','issuer_device'}
                    fields(r,keys|{'format','signature'},'agentmesh-'+kind+'-v1');hex_bytes(r['issuer_device'],16)
                    timestamp=r['removed_at' if kind=='passkey-removal' else 'created_at'];milliseconds(timestamp)
                    if timestamp>at: continue
                    verify_signature(r,registry.device_public(r['issuer_device'],at=at),domain(kind))
                    if not cr.reader(ring,r['issuer_device'],at): continue
                    if kind=='passkey-removal': identifier=r['credential_id_sha256'];hex_bytes(identifier,32)
                    else:
                        identifier=credential_hash(unb64(r['credential_id']));decode_credential_public_key(unb64(r['public_key']))
                        if type(r['label']) is not str or not 1<=len(r['label'])<=100 or any(ord(c)<32 for c in r['label']) or type(r['rp_id']) is not str: continue
                    if path!=prefix+identifier+'.json': continue
                    if kind=='passkey-removal': removed.add(identifier)
                    elif identifier not in removed and r['rp_id']==settings(state)['rp_id']: result[identifier]=r
                except Exception: continue
        return result

    def local_record(self,kind,identifier,**values):
        state=self.store.load();record=signed(kind,state.device._private,**values,issuer_device=state.device.device_id)
        path=('registry/passkeys/' if kind=='passkey' else 'registry/passkey-removals/')+identifier+'.json'
        data=wire(record);self.local.put(path,data);return path,data

    def publish(self,kind,identifier,**values):
        path,data=self.local_record(kind,identifier,**values)
        self.storage.put(path,data)

    def add(self,label,console):
        if type(label) is not str or not 1<=len(label)<=100 or any(ord(c)<32 for c in label): raise ValueError('invalid_request')
        self.authority()
        PresenceManager(self.home,self.store).require('device','approve',console=console,one_shot=True)
        code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(10))
        state=self.store.load(); at=self.clock(); codes={k:v for k,v in state.control.get('passkey_codes',{}).items() if v['exp']>at}
        codes[digest(code)]={'exp':at+600000,'label':label};state.control['passkey_codes']=codes;self.store.save(state)
        return {'code':code}

    def listing(self):
        return {'passkeys':[{'label':r['label'],'credential_hash_prefix':key[:16],'created_at':r['created_at'],'issuer':r['issuer_device']} for key,r in sorted(self.credentials().items())]}

    def remove(self,prefix,console):
        if type(prefix) is not str or not re.fullmatch('[0-9a-f]{1,64}',prefix): raise ValueError('invalid_request')
        matches=[k for k in self.credentials() if k.startswith(prefix)]
        if len(matches)>1: raise ValueError('ambiguous_passkey')
        if not matches: raise ValueError('unknown_passkey')
        self.authority();PresenceManager(self.home,self.store).require('device','revoke',console=console,one_shot=True)
        self.publish('passkey-removal',matches[0],credential_id_sha256=matches[0],removed_at=self.clock())
        return {'status':'ok'}

    def authority(self):
        state=self.store.load();cr=controls(state,self.local);registry,ring=cr.context();at=self.clock()
        registry.device_public(state.device.device_id,at=at)
        if not cr.reader(ring,state.device.device_id,at): raise ValueError('denied')

    def challenge(self,state,purpose,**binding):
        at=self.clock(); cfg=settings(state)
        value={'purpose':purpose,**binding,'nonce':secrets.token_hex(32),'exp':at+120000,'device_id':state.device.device_id,'rp_id':cfg['rp_id'],'origin':cfg['origin']}
        raw=hashlib.sha256(typed(value)).digest();identifier=raw.hex();value['challenge']=b64(raw)
        with self.update():
            current=self.store.load()
            if current.device.device_id!=state.device.device_id or settings(current)!=cfg: raise ValueError('denied')
            if purpose=='register':
                code=current.control.get('passkey_codes',{}).get(binding['code_hash'])
                if not code or code['exp']<=self.clock(): raise ValueError('denied')
            pending={k:v for k,v in current.control.get('passkey_challenges',{}).items() if v['exp']>at}
            # Bound protected state growth even when an authenticated relay is hostile.
            if len(pending)>=100: raise ValueError('denied')
            pending[identifier]=value;current.control['passkey_challenges']=pending;self.store.save(current)
        return value,identifier

    def take(self,state,args,purpose):
        if args['device_id']!=state.device.device_id: raise ValueError('wrong_device')
        identifier=args['challenge_id'];hex_bytes(identifier,32)
        value=state.control.get('passkey_challenges',{}).get(identifier)
        if not value or value['purpose']!=purpose or value['exp']<=self.clock(): raise ValueError('denied')
        cfg=settings(state)
        if value['rp_id']!=cfg['rp_id'] or value['origin']!=cfg['origin']: raise ValueError('denied')
        return value

    def handle(self,args):
        uploads=[];outcome='denied';step=args.get('step') if type(args) is dict else None
        try:
            schemas={'register_begin':{'code'},'register_finish':{'device_id','challenge_id','credential'},'approve_begin':{'code_hash','client_id_hash','client_name'},'approve_finish':{'device_id','challenge_id','code_hash','assertion'}}
            if step not in schemas: raise ValueError('denied')
            exact(args,schemas[step]|{'step'});self.authority()
            state=self.store.load();cfg=settings(state)
            if step=='register_begin':
                if type(args['code']) is not str or len(args['code'])!=10: raise ValueError('denied')
                hashed=digest(args['code']);code=None
                for key,value in state.control.get('passkey_codes',{}).items():
                    if hmac.compare_digest(key,hashed): code=value
                if not code or code['exp']<=self.clock(): raise ValueError('denied')
                value,identifier=self.challenge(state,'register',code_hash=hashed)
                options=webauthn.generate_registration_options(rp_id=cfg['rp_id'],rp_name='AgentMesh',user_name='owner',user_id=bytes.fromhex(state.account.account_id),challenge=unb64(value['challenge']),authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.PREFERRED,user_verification=UserVerificationRequirement.REQUIRED),exclude_credentials=[PublicKeyCredentialDescriptor(id=unb64(r['credential_id'])) for r in self.credentials().values()])
                result={'options':json.loads(options_to_json(options)),'device_id':state.device.device_id,'challenge_id':identifier}
            elif step=='register_finish':
                value=self.take(state,args,'register');code=state.control.get('passkey_codes',{}).get(value['code_hash'])
                if not code or code['exp']<=self.clock(): raise ValueError('denied')
                verified=webauthn.verify_registration_response(credential=args['credential'],expected_challenge=unb64(value['challenge']),expected_rp_id=cfg['rp_id'],expected_origin=cfg['origin'],require_user_verification=True)
                identifier=credential_hash(verified.credential_id)
                with self.update():
                    current=self.store.load()
                    if self.take(current,args,'register')!=value: raise ValueError('denied')
                    current_code=current.control.get('passkey_codes',{}).get(value['code_hash'])
                    if current_code!=code or current_code['exp']<=self.clock(): raise ValueError('denied')
                    self.authority()
                    # A removal is permanent for this credential ID, even if re-enrolled.
                    if list(self.local.list('registry/passkey-removals/'+identifier+'.json')) or identifier in self.credentials(): raise ValueError('denied')
                    del current.control['passkey_codes'][value['code_hash']];del current.control['passkey_challenges'][args['challenge_id']]
                    current.control.setdefault('passkey_counters',{})[identifier]=verified.sign_count;self.store.save(current)
                    uploads.append(self.local_record('passkey',identifier,credential_id=b64(verified.credential_id),public_key=b64(verified.credential_public_key),label=code['label'],rp_id=cfg['rp_id'],created_at=self.clock()))
                result={'status':'ok'}
            elif step=='approve_begin':
                hex_bytes(args['code_hash'],32);hex_bytes(args['client_id_hash'],32)
                if type(args['client_name']) is not str or not 1<=len(args['client_name'])<=256: raise ValueError('denied')
                credentials=self.credentials()
                if not credentials: raise ValueError('denied')
                policy,_=approval_proposal(state,self.local,cfg['profiles'],cfg['remember'],max_label=cfg['max_label'])
                value,identifier=self.challenge(state,'approve',code_hash=args['code_hash'],client_id_hash=args['client_id_hash'],display_name_hash=digest(args['client_name']),policy_digest=hashlib.sha256(typed(policy)).hexdigest(),policy=policy,profiles=cfg['profiles'],remember=cfg['remember'],client_name=args['client_name'])
                options=webauthn.generate_authentication_options(rp_id=cfg['rp_id'],challenge=unb64(value['challenge']),timeout=120000,user_verification=UserVerificationRequirement.REQUIRED,allow_credentials=[PublicKeyCredentialDescriptor(id=unb64(r['credential_id'])) for r in credentials.values()])
                result={'options':json.loads(options_to_json(options)),'device_id':state.device.device_id,'challenge_id':identifier,'policy':policy,'profile_names':cfg['profiles'],'remember_name':cfg['remember']}
            else:
                value=self.take(state,args,'approve')
                if args['code_hash']!=value['code_hash']: raise ValueError('denied')
                if hashlib.sha256(typed(value['policy'])).hexdigest()!=value['policy_digest'] or digest(value['client_name'])!=value['display_name_hash']: raise ValueError('denied')
                # Recheck local binding integrity, including client and nonce.
                binding={k:v for k,v in value.items() if k!='challenge'}
                if b64(hashlib.sha256(typed(binding)).digest())!=value['challenge']: raise ValueError('denied')
                identifier=credential_hash(unb64(args['assertion']['id']));credential=self.credentials().get(identifier)
                if credential is None: raise ValueError('denied')
                verified=webauthn.verify_authentication_response(credential=args['assertion'],expected_challenge=unb64(value['challenge']),expected_rp_id=cfg['rp_id'],expected_origin=cfg['origin'],credential_public_key=unb64(credential['public_key']),credential_current_sign_count=state.control.get('passkey_counters',{}).get(identifier,0),require_user_verification=True)
                with self.update():
                    current=self.store.load()
                    if self.take(current,args,'approve')!=value: raise ValueError('denied')
                    self.authority()
                    if self.credentials().get(identifier)!=credential or current.control.get('passkey_counters',{}).get(identifier,0)!=state.control.get('passkey_counters',{}).get(identifier,0): raise ValueError('denied')
                    # Recheck after unlocked verification and consume before any publication.
                    del current.control['passkey_challenges'][args['challenge_id']];current.control.setdefault('passkey_counters',{})[identifier]=verified.new_sign_count;self.store.save(current)
                    prefixes=('registry/','keyring/','keys/','connectors/inboxes/')
                    before={p for prefix in prefixes for p in self.local.list(prefix)}
                    result=apply_approval(self.home,self.store,self.local,value['code_hash'],value['client_id_hash'],value['client_name'],value['profiles'],value['remember'],value['policy'])
                    added={p for prefix in prefixes for p in self.local.list(prefix)}-before
                    # New inbox controls precede its policy; all bytes are already durable locally.
                    uploads.extend((p,self.local.get(p)) for p in sorted(added))
                    ref=result['payload']['policy_ref'];uploads.append((ref,self.local.get(ref)))
            # Neither verification nor remote publication holds bridge/home state locks.
            for path,data in uploads: self.storage.put(path,data)
            outcome='ok';return result
        except Exception as error:
            return {'error':'wrong_device' if isinstance(error,ValueError) and str(error)=='wrong_device' else 'denied'}
        finally:
            state=self.store.load();Audit(self.home.root/'audit.jsonl',state.audit_key).append(agent_id=None,op='connector_passkey',outcome=outcome)
