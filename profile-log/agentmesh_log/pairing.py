"""Storage-carried commit/reveal pairing with single-use local transcript pins."""
import hashlib
import hmac
import secrets
from dataclasses import asdict
from .encoding import fields, hex_bytes, milliseconds, wire, parse, typed
from .identity import DeviceIdentity, sign, verify_signature, DEVICE_DOMAIN
from .agekeys import DeviceAgeIdentity, validate_recipient, encrypt
from .registry_store import now, domain, signed, device_record, RegistryStore
from .broker.handshake import b64
from .keys import decode_ciphertext

COMMIT_DOMAIN=b'AgentMesh/pairing-commitment/v1\x00'
SAS_DOMAIN=b'AgentMesh/pairing-sas/v1\x00'
SCHEMAS={
 'pair-commit':{'request_id','joiner_signing_public','joiner_age_recipient','slot_recipients','commitment','issued_at'},
 'pair-approver':{'request_id','approver_device_id','Na','commit_hash'},
 'pair-reveal':{'request_id','Nj'},
 'pair-result':{'request_id','approver_device_id','commit_hash','approver_hash','certificate_path','metadata_paths','grant_paths','slot_assignments'},
}


def digest(record): return hashlib.sha256(wire(record)).hexdigest()

def shape(record,kind):
    fields(record,SCHEMAS[kind]|{'format','signature'},'agentmesh-'+kind+'-v1')
    hex_bytes(record['request_id'],16); hex_bytes(record['signature'],64)

def commitment(commit,nj):
    return hashlib.sha256(COMMIT_DOMAIN+typed(dict(request_id=commit['request_id'],joiner_signing_public=commit['joiner_signing_public'],
        joiner_age_recipient=commit['joiner_age_recipient'],slot_recipients=commit['slot_recipients'],Nj=nj))).hexdigest()

def check_commit(commit,clock=now):
    shape(commit,'pair-commit'); milliseconds(commit['issued_at']); hex_bytes(commit['commitment'],32)
    if not 0<=clock()-commit['issued_at']<=600000: raise ValueError('pairing_expired')
    validate_recipient(commit['joiner_age_recipient'])
    slots=commit['slot_recipients']
    if type(slots) is not list or len(slots)!=8 or len(set(slots))!=8: raise ValueError('pairing_slots')
    for recipient in slots: validate_recipient(recipient)
    verify_signature(commit,hex_bytes(commit['joiner_signing_public'],32),domain('pair-commit'))

def check_reveal(commit,approval,reveal):
    if approval is None: raise ValueError('pairing_order')
    shape(approval,'pair-approver'); shape(reveal,'pair-reveal')
    hex_bytes(reveal['Nj'],32); hex_bytes(approval['Na'],32)
    if approval['request_id']!=commit['request_id'] or reveal['request_id']!=commit['request_id'] or approval['commit_hash']!=digest(commit):
        raise ValueError('pairing_mismatch')
    if not hmac.compare_digest(commitment(commit,reveal['Nj']),commit['commitment']): raise ValueError('pairing_mismatch')
    verify_signature(reveal,hex_bytes(commit['joiner_signing_public'],32),domain('pair-reveal'))

def sas(commit,approval,reveal):
    check_reveal(commit,approval,reveal)
    data=dict(commit_hash=digest(commit),approver_hash=digest(approval),Nj=reveal['Nj'],Na=approval['Na'])
    return f"{int.from_bytes(hashlib.sha256(SAS_DOMAIN+typed(data)).digest()[:4],'big')%1000000:06d}"

def approver_fingerprint(registry,device_id):
    return hashlib.sha256(typed(dict(account=registry.account_id,device=device_id,public=registry.device_public(device_id).hex()))).hexdigest()


class JoinRequest:
    def __init__(self,storage,*,request_id=None,clock=now):
        self.storage,self.clock=storage,clock
        self.device=DeviceIdentity(); self.age=DeviceAgeIdentity()
        self.slots=[DeviceAgeIdentity() for _ in range(8)]
        self.request_id=request_id or self.device.device_id; hex_bytes(self.request_id,16)
        self.device.device_id=self.request_id
        self.nj=secrets.token_bytes(32).hex(); self.approval=None; self.finished=False
        base=dict(request_id=self.request_id,joiner_signing_public=self.device.public_bytes.hex(),joiner_age_recipient=self.age.recipient,slot_recipients=[a.recipient for a in self.slots])
        self.commit=signed('pair-commit',self.device._private,**base,commitment=commitment(base,self.nj),issued_at=clock())
        if list(storage.list(self.prefix)): raise ValueError('pairing_reused')
        storage.put(self.prefix+'commit.json',wire(self.commit))

    @property
    def prefix(self): return 'pairing/'+self.request_id+'/'
    def qr_payload(self): return 'agentmesh-pair:v1:'+self.request_id+':'+digest(self.commit)
    def render_qr(self,stream):
        import segno
        segno.make(self.qr_payload()).terminal(out=stream,compact=True)

    def reveal(self,approval,registry):
        check_commit(self.commit,self.clock)
        if approval is None: raise ValueError('pairing_order')
        shape(approval,'pair-approver')
        if self.approval is not None or approval['request_id']!=self.request_id or approval['commit_hash']!=digest(self.commit):
            raise ValueError('pairing_mismatch')
        verify_signature(approval,registry.device_public(approval['approver_device_id']),domain('pair-approver'))
        self.approval=dict(approval)
        record=signed('pair-reveal',self.device._private,request_id=self.request_id,Nj=self.nj)
        check_reveal(self.commit,approval,record); self.storage.put(self.prefix+'reveal.json',wire(record))
        return record

    def finish(self,registry,home,store):
        from .broker.keystore import BrokerState
        from .model import Account
        from .keyring import AgeKeyring, grant_path
        if self.finished or self.approval is None: raise ValueError('pairing_order')
        check_commit(self.commit,self.clock)
        result=parse(self.storage.get(self.prefix+'result.json')); shape(result,'pair-result')
        if (result['request_id'],result['commit_hash'],result['approver_hash'],result['approver_device_id']) != (
                self.request_id,digest(self.commit),digest(self.approval),self.approval['approver_device_id']): raise ValueError('pairing_mismatch')
        public=registry.device_public(result['approver_device_id'])
        verify_signature(result,public,domain('pair-result'))
        if result['certificate_path']!=self.prefix+'certificate.json': raise ValueError('pairing_mismatch')
        cert=parse(self.storage.get(result['certificate_path']))
        if cert['device_id']!=self.device.device_id or cert['device_public_key']!=self.device.public_bytes.hex(): raise ValueError('pairing_mismatch')
        registry.add_device(cert)
        account={'format':'agentmesh-account-v1','account_id':registry.account_id,'root_public_key':registry._root_public.hex()}
        rs=RegistryStore(self.storage,account)
        rs.publish_device(device_record(self.device,cert,self.age))
        assignments=result['slot_assignments']
        if type(assignments) is not dict or not assignments or len(assignments)>8: raise ValueError('pairing_slots')
        values=list(assignments.values())
        if any(type(v) is not int or not 0<=v<8 for v in values) or len(set(values))!=len(values): raise ValueError('pairing_slots')
        from .registry_store import profile_recipient
        profiles={}
        for profile,slot in assignments.items():
            hex_bytes(profile,16); identity=self.slots[slot]
            profiles[profile]=identity.secret()
            rs.publish_profile_recipient(profile_recipient(self.device,profile,identity))
        registry=rs.load()
        trees=[]
        for path in result['metadata_paths']:
            record=parse(self.storage.get(path))
            from .registry_store import shape as record_shape
            record_shape(record,'scope-meta')
            if path!=f"keyring/{record['profile']}/metadata/{self.request_id}.json" or record['recipient_device']!=self.request_id or record['issuer_device']!=result['approver_device_id']:
                raise ValueError('pairing_mismatch')
            verify_signature(record,public,domain('scope-meta'))
            if record['profile'] not in profiles: raise ValueError('pairing_slots')
            identity=DeviceAgeIdentity.parse(profiles[record['profile']])
            from .registry_store import read_profile_recipient
            if read_profile_recipient(self.storage,registry,record['profile'],self.device.device_id)!=identity.recipient: raise ValueError('pairing_mismatch')
            meta=parse(identity.decrypt(decode_ciphertext(record['age'])))
            if meta['root']!=record['profile']: raise ValueError('pairing_mismatch')
            trees.append(dict(root=meta['root'],scopes=meta['scopes'],epochs={},wraps={}))
        state_record=dict(format='agentmesh-broker-state-v2',account=account,account_private_key=None,device_id=self.device.device_id,
            device_private_key=self.device.private_bytes().hex(),certificates=list(registry.certificates.values()),trees=trees,grants={},
            index_master_key=secrets.token_bytes(32).hex(),audit_key=secrets.token_bytes(32).hex(),
            control=dict(age_identity=self.age.secret(),profile_ages=profiles,phase4=True,registry_records=registry_cache(rs)))
        state=BrokerState.from_record(state_record)
        ring=AgeKeyring(self.storage,registry,[s for t in state.trees for s in t.scopes.values()])
        for path in result['grant_paths']:
            record=parse(self.storage.get(path))
            if path!=grant_path(record): raise ValueError('pairing_mismatch')
            if record['profile'] not in profiles: raise ValueError('pairing_slots')
            key=ring.open(record,self.device.device_id,DeviceAgeIdentity.parse(profiles[record['profile']]))
            tree=next(t for t in state.trees if t.root.scope_id==key.profile_id)
            tree.epochs.setdefault(key.scope_id,[]).append(key); tree.keys[key.scope_id]=key
        from .keyring import sync_control
        store.save(state); sync_control(home,store,self.storage)
        self.slots.clear()
        self.finished=True
        return store.load()


class Approver:
    def __init__(self,storage,device,registry,used,*,clock=now,persist=lambda:None):
        self.storage,self.device,self.registry,self.used,self.clock,self.persist=storage,device,registry,used,clock,persist
        self.commit=self.approval=None; self.confirmed=False

    def begin(self,request_id):
        hex_bytes(request_id,16); prefix='pairing/'+request_id+'/'
        if request_id in self.used or list(self.storage.list(prefix+'approver.json')) or list(self.storage.list(prefix+'reveal.json')):
            raise ValueError('pairing_reused')
        commit=parse(self.storage.get(prefix+'commit.json')); check_commit(commit,self.clock)
        if commit['request_id']!=request_id: raise ValueError('pairing_mismatch')
        self.used[request_id]='started'; self.persist()
        self.commit=commit
        self.approval=signed('pair-approver',self.device._private,request_id=request_id,approver_device_id=self.device.device_id,
            Na=secrets.token_bytes(32).hex(),commit_hash=digest(commit))
        self.storage.put(prefix+'approver.json',wire(self.approval)); return self.approval

    def confirm(self,reveal,compare,*,fingerprint=None):
        check_commit(self.commit,self.clock); check_reveal(self.commit,self.approval,reveal)
        request_id=self.commit['request_id']
        if self.used.get(request_id)!='started': raise ValueError('pairing_reused')
        self.used[request_id]='consumed'; self.persist()
        accepted=hmac.compare_digest(fingerprint,digest(self.commit)) if fingerprint is not None else compare(sas(self.commit,self.approval,reveal)) is True
        if not accepted: raise ValueError('pairing_denied')
        self.confirmed=True

    def grant(self,state,scopes):
        from .keyring import AgeKeyring, grant_path
        from .broker.grants import descendants
        if not self.confirmed: raise ValueError('pairing_order')
        request_id=self.commit['request_id']; prefix='pairing/'+request_id+'/'
        scopes=set(scopes)
        if not scopes: raise ValueError('pairing_empty')
        ring=AgeKeyring(self.storage,self.registry,[s for t in state.trees for s in t.scopes.values()])
        selected=descendants(state,scopes)
        cert=sign(dict(format='agentmesh-device-cert-v1',account_id=state.account.account_id,device_id=request_id,
            device_public_key=self.commit['joiner_signing_public'],issuer=self.device.device_id,issued_at=self.clock()),self.device._private,DEVICE_DOMAIN)
        paths=[]; metadata=[]; assignments={}
        for tree in state.trees:
            allowed=selected & tree.keys.keys()
            if not allowed: continue
            slot=len(assignments)
            if slot>=8: raise ValueError('pairing_slots')
            assignments[tree.root.scope_id]=slot
            recipient=self.commit['slot_recipients'][slot]
            for sid in sorted(allowed):
                for key in tree.epochs[sid]:
                    record=ring.make_grant(key,self.device,request_id,recipient)
                    ring.validate(record); self.storage.put(grant_path(record),wire(record)); paths.append(grant_path(record))
            meta=dict(root=tree.root.scope_id,scopes=[{**asdict(s),'default_label':s.default_label.name} for s in tree.scopes.values()])
            record=signed('scope-meta',self.device._private,profile=tree.root.scope_id,recipient_device=request_id,issuer_device=self.device.device_id,
                issued_at=self.clock(),age=b64(encrypt(wire(meta),recipient)))
            path=f'keyring/{tree.root.scope_id}/metadata/{request_id}.json'; self.storage.put(path,wire(record)); metadata.append(path)
        self.storage.put(prefix+'certificate.json',wire(cert))
        result=signed('pair-result',self.device._private,request_id=request_id,approver_device_id=self.device.device_id,
            commit_hash=digest(self.commit),approver_hash=digest(self.approval),certificate_path=prefix+'certificate.json',metadata_paths=metadata,grant_paths=paths,slot_assignments=assignments)
        self.storage.put(prefix+'result.json',wire(result)); self.confirmed=False
        return result


def registry_cache(registry_store):
    return {path:parse(registry_store.storage.get(path)) for path in registry_store.storage.list('registry/')}
