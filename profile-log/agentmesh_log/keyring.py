"""Signed, single-recipient age grants and authenticated scope epoch selection."""
from typing import Protocol
from .agekeys import encrypt
from .broker.handshake import b64
from .encoding import wire, parse
from .keys import ScopeReadKey, decode_ciphertext
from .identity import verify_signature
from .registry_store import shape, signed, domain, now
from .errors import KeyAccessError


class Keyring(Protocol):
    def open(self, record, recipient_device, identity) -> ScopeReadKey: ...
    def current(self, profile, scope) -> dict: ...


def grant_path(record):
    shape(record,'keyring-grant')
    return f"keyring/{record['profile']}/{record['scope']}/{record['key_id']}/{record['recipient_device']}.json"


def epoch_path(record):
    shape(record,'epoch')
    return f"keyring/{record['profile']}/{record['scope']}/epochs/{record['key_id']}.json"


class AgeKeyring:
    def __init__(self, storage, registry, scopes):
        self.storage,self.registry = storage,registry
        self.scopes = {s.scope_id:s for s in scopes}

    def ancestors(self, profile, scope):
        result=set()
        while scope is not None:
            if scope in result or scope not in self.scopes or self.scopes[scope].profile_id != profile:
                raise KeyAccessError('invalid scope topology')
            result.add(scope); scope=self.scopes[scope].parent
        return result

    def records(self):
        for path in self.storage.list('keyring/'):
            if '/epochs/' in path or '/metadata/' in path or '/recovery/' in path: continue
            try:
                record=shape(parse(self.storage.get(path)),'keyring-grant')
                if grant_path(record)==path: yield record
            except ValueError: continue

    def _signature(self, record):
        shape(record,'keyring-grant')
        public=self.registry.device_public(record['issuer_device'],at=record['issued_at'])
        verify_signature(record,public,domain('keyring-grant'))
        self.ancestors(record['profile'],record['scope'])
        if record['recipient_device']=='recovery' and record['scope']!=record['profile']:
            raise KeyAccessError('recovery requires profile root')

    def authorized(self, device, profile, scope, at, seen=None):
        ancestors=self.ancestors(profile,scope)
        seen=set() if seen is None else seen
        marker=(device,profile,scope)
        if marker in seen: return False
        seen=seen|{marker}
        for record in self.records():
            if record['recipient_device']!=device or record['profile']!=profile or record['scope'] not in ancestors or record['issued_at']>at:
                continue
            try:
                self._signature(record)
                issuer=record['issuer_device']
                # The account-certified first device (or recovery-certified replacement)
                # anchors initial self-grants. Ordinary device certificates do not.
                cert=self.registry.certificates.get(issuer,{})
                if issuer==device and cert.get('issuer') in ('account','recovery'): return True
                if self.authorized(issuer,profile,record['scope'],record['issued_at'],seen): return True
            except ValueError: continue
        return False

    def validate(self, record):
        self._signature(record)
        issuer=record['issuer_device']; cert=self.registry.certificates[issuer]
        bootstrap=issuer==record['recipient_device'] and cert['issuer'] in ('account','recovery')
        if not bootstrap and not self.authorized(issuer,record['profile'],record['scope'],record['issued_at']):
            raise KeyAccessError('unauthorized key issuer')
        return record

    def make_grant(self,key,issuer,recipient_device,recipient,*,issued_at=None):
        return signed('keyring-grant',issuer._private,profile=key.profile_id,scope=key.scope_id,key_id=key.key_id,
            recipient_device=recipient_device,issuer_device=issuer.device_id,issued_at=now() if issued_at is None else issued_at,
            age=b64(encrypt(key.private_bytes(),recipient)))

    def publish(self,key,issuer,recipient_device,recipient,*,issued_at=None):
        self.check_recipient(key.profile_id,recipient_device,recipient)
        record=self.make_grant(key,issuer,recipient_device,recipient,issued_at=issued_at)
        self.validate(record)
        path=grant_path(record)
        if path in set(self.storage.list(path)):
            existing=parse(self.storage.get(path)); self.validate(existing)
            if grant_path(existing)!=path: raise KeyAccessError('invalid grant path')
            return existing
        self.storage.put(path,wire(record)); return record

    def check_recipient(self,profile,device,recipient):
        from .registry_store import read_profile_recipient,read_profile_recovery
        if device=='recovery':
            for rid in self.registry.recoveries:
                try: record=read_profile_recovery(self.storage,self.registry,profile,rid)
                except ValueError: continue
                if record['age_recipient']==recipient: return
            raise KeyAccessError('unknown profile recovery recipient')
        if read_profile_recipient(self.storage,self.registry,profile,device)!=recipient:
            raise KeyAccessError('profile recipient mismatch')

    def open(self,record,recipient_device,identity):
        self.validate(record)
        if record['recipient_device']!=recipient_device: raise KeyAccessError('wrong recipient')
        self.check_recipient(record['profile'],recipient_device,identity.recipient)
        raw=identity.decrypt(decode_ciphertext(record['age']))
        key=ScopeReadKey.from_private(record['profile'],record['scope'],raw)
        if key.key_id!=record['key_id']: raise KeyAccessError('scope key mismatch')
        return key

    def epoch(self,key,issuer,*,previous=None,created_at=None,reason='create'):
        record=signed('epoch',issuer._private,profile=key.profile_id,scope=key.scope_id,key_id=key.key_id,
            previous_key_id=previous,created_at=now() if created_at is None else created_at,reason=reason,issuer_device=issuer.device_id)
        self.validate_epoch(record); self.storage.put(epoch_path(record),wire(record)); return record

    def validate_epoch(self,record):
        shape(record,'epoch')
        if record['reason'] not in ('create','rotate','revoke','recovery'): raise KeyAccessError('invalid epoch reason')
        if (record['previous_key_id'] is None)!=(record['reason']=='create'): raise KeyAccessError('invalid epoch predecessor')
        public=self.registry.device_public(record['issuer_device'],at=record['created_at'])
        verify_signature(record,public,domain('epoch'))
        if not self.authorized(record['issuer_device'],record['profile'],record['scope'],record['created_at']):
            raise KeyAccessError('unauthorized epoch issuer')

    def current(self,profile,scope):
        records={}
        for path in self.storage.list(f'keyring/{profile}/{scope}/epochs/'):
            try:
                record=parse(self.storage.get(path)); self.validate_epoch(record)
                if epoch_path(record)==path: records[record['key_id']]=record
            except ValueError: continue
        valid={}
        while True:
            before=len(valid)
            for kid,r in records.items():
                parent=r['previous_key_id']
                if parent is None or (parent in valid and valid[parent]['created_at']<=r['created_at']): valid[kid]=r
            if len(valid)==before: break
        parents={r['previous_key_id'] for r in valid.values()}
        leaves=[r for k,r in valid.items() if k not in parents]
        if not leaves: raise KeyAccessError('missing current epoch')
        return max(leaves,key=lambda r:(r['created_at'],r['key_id']))


def initialize_keyring(state,storage):
    """Publish initial device bindings and the creator's anchored self-grants."""
    from .agekeys import DeviceAgeIdentity
    from .registry_store import RegistryStore, device_record, profile_recipient
    from .pairing import registry_cache
    from .events import wrap_path
    age=DeviceAgeIdentity.parse(state.control['age_identity']) if 'age_identity' in state.control else DeviceAgeIdentity()
    state.control.update(age_identity=age.secret(),phase4=True)
    rs=RegistryStore(storage,state.account.public_record())
    storage.put('registry/account.json',wire(state.account.public_record()))
    cert=next(c for c in state.certificates if c['device_id']==state.device.device_id)
    path='registry/devices/'+state.device.device_id+'.json'
    if path not in set(storage.list('registry/devices/')): rs.publish_device(device_record(state.device,cert,age))
    state.control['registry_records']=registry_cache(rs)
    ring=AgeKeyring(storage,rs.load(),[s for t in state.trees for s in t.scopes.values()])
    for tree in state.trees:
        if tree.root.scope_id in state.control.get('deleted',[]): continue
        profiles=state.control.setdefault('profile_ages',{})
        if tree.root.scope_id not in profiles: profiles[tree.root.scope_id]=DeviceAgeIdentity().secret()
        profile_age=DeviceAgeIdentity.parse(profiles[tree.root.scope_id])
        pp=f'registry/profile-recipients/{tree.root.scope_id}/{state.device.device_id}.json'
        if pp not in set(storage.list(pp)): rs.publish_profile_recipient(profile_recipient(state.device,tree.root.scope_id,profile_age))
        for sid,epochs in tree.epochs.items():
            previous=None
            for key in epochs:
                path=f'keyring/{key.profile_id}/{sid}/{key.key_id}/{state.device.device_id}.json'
                if path not in set(storage.list(path)): ring.publish(key,state.device,state.device.device_id,profile_age.recipient)
                ep=f'keyring/{key.profile_id}/{sid}/epochs/{key.key_id}.json'
                if ep not in set(storage.list(ep)): ring.epoch(key,state.device,previous=previous,reason='create' if previous is None else 'rotate')
                previous=key.key_id
        for wrap in tree.wraps.values(): storage.put(wrap_path(wrap),wire(wrap))
    state.control['registry_records']=registry_cache(rs)
    return ring


def sync_control(home,store,remote,*,apply_lock=None):
    """Pin immutable control objects locally, then validate before importing keys."""
    from .storage.filesystem import FilesystemStorage
    from .registry_store import RegistryStore
    from .pairing import registry_cache
    from .agekeys import DeviceAgeIdentity
    from .keys import unwrap_key
    from .events import wrap_path
    local=FilesystemStorage(home.root/'replica')
    prefixes=('registry/','keyring/','keys/','profiles/','alerts/','connectors/')
    for prefix in prefixes:
        local_paths={p for p in local.list(prefix) if not p.startswith('connectors/gateway-state/')}
        remote_paths={p for p in remote.list(prefix) if not p.startswith('connectors/gateway-state/')}
        for path in sorted(local_paths-remote_paths): remote.put(path,local.get(path))
        for path in sorted(remote_paths-local_paths):
            data=remote.get(path)
            try: parse(data)
            except ValueError: continue
            local.put(path,data)
    # Remote transfer never holds the caller's local state locks. Reload after
    # transfer so concurrent local provisioning cannot be overwritten.
    from contextlib import nullcontext
    with apply_lock() if apply_lock else nullcontext():
        state=store.load()
        rs=RegistryStore(local,state.account.public_record()); registry=rs.load()
        state.control['registry_records']=registry_cache(rs)
        state.certificates=list(registry.certificates.values())
        import_metadata(state,local,registry)
        from .bridge.inboxes import import_inboxes
        import_inboxes(state,local,registry)
        ring=AgeKeyring(local,registry,[s for t in state.trees for s in t.scopes.values()])
        from .lifecycle import apply_profile_states
        apply_profile_states(home,state,local,ring)
        deleted=set(state.control.get('deleted',[]))
        def add(key):
            if key.profile_id in deleted: return False
            tree=next((t for t in state.trees if t.root.scope_id==key.profile_id),None)
            if tree is None or key.scope_id not in tree.scopes: return False
            epochs=tree.epochs.setdefault(key.scope_id,[])
            if any(k.key_id==key.key_id for k in epochs): return False
            epochs.append(key); return True
        for record in ring.records():
            if record['recipient_device']!=state.device.device_id: continue
            secret=state.control.get('profile_ages',{}).get(record['profile'])
            if secret is None: continue
            try: add(ring.open(record,state.device.device_id,DeviceAgeIdentity.parse(secret)))
            except ValueError: continue
        wraps=[]
        for path in local.list('keys/'):
            try:
                record=parse(local.get(path))
                if wrap_path(record)==path: wraps.append(record)
            except ValueError: continue
        changed=True
        while changed:
            changed=False
            keys={k.key_id:k for t in state.trees for epochs in t.epochs.values() for k in epochs}
            for wrap in wraps:
                parent=keys.get(wrap['parent_key_id'])
                if parent is None: continue
                try: changed=add(unwrap_key(wrap,parent)) or changed
                except ValueError: continue
        for tree in state.trees:
            for sid,epochs in tree.epochs.items():
                try: current=ring.current(tree.root.scope_id,sid)['key_id']
                except ValueError: continue
                matches=[k for k in epochs if k.key_id==current]
                if matches:
                    tree.keys[sid]=matches[0]
                    tree.epochs[sid]=[k for k in epochs if k.key_id!=current]+matches
        from .recovery import collect_alerts
        collect_alerts(state,local,registry)
        store.save(state)
        return state


def publish_recipient(home,store,storage,profile):
    """Prepare a local random identity before another device grants a new profile."""
    from .encoding import hex_bytes
    from .agekeys import DeviceAgeIdentity
    from .registry_store import RegistryStore,profile_recipient,read_profile_recipient
    hex_bytes(profile,16)
    state=store.load()
    if profile in state.control.get('deleted',[]): raise KeyAccessError('profile deleted')
    profiles=state.control.setdefault('profile_ages',{})
    if profile not in profiles:
        profiles[profile]=DeviceAgeIdentity().secret()
        store.save(state)  # Persist the private identity before publishing its immutable binding.
    identity=DeviceAgeIdentity.parse(profiles[profile]); rs=RegistryStore(storage,state.account.public_record())
    path=f'registry/profile-recipients/{profile}/{state.device.device_id}.json'
    if path not in set(storage.list(path)): rs.publish_profile_recipient(profile_recipient(state.device,profile,identity))
    if read_profile_recipient(storage,rs.load(),profile,state.device.device_id)!=identity.recipient:
        raise KeyAccessError('profile recipient mismatch')


def regrant(state,storage,device,scopes):
    from dataclasses import asdict
    from .registry_store import RegistryStore,read_profile_recipient
    from .broker.grants import descendants
    registry=RegistryStore(storage,state.account.public_record()).load(); registry.device_public(device)
    ring=AgeKeyring(storage,registry,[s for t in state.trees for s in t.scopes.values()])
    selected=descendants(state,set(scopes))
    if not selected: raise KeyAccessError('empty grant')
    targets=[]
    for tree in state.trees:
        allowed=selected & tree.keys.keys()
        if not allowed: continue
        recipient=read_profile_recipient(storage,registry,tree.root.scope_id,device)
        targets.append((tree,allowed,recipient))
    for tree,allowed,recipient in targets:
        for sid in sorted(allowed):
            for key in tree.epochs[sid]: ring.publish(key,state.device,device,recipient)
        path=f'keyring/{tree.root.scope_id}/metadata/{device}.json'
        if path not in set(storage.list(path)):
            meta=dict(root=tree.root.scope_id,scopes=[{**asdict(s),'default_label':s.default_label.name} for s in tree.scopes.values()])
            record=signed('scope-meta',state.device._private,profile=tree.root.scope_id,recipient_device=device,
                issuer_device=state.device.device_id,issued_at=now(),age=b64(encrypt(wire(meta),recipient)))
            storage.put(path,wire(record))


def import_metadata(state,storage,registry):
    """Import a newly granted profile only after recipient and issuer validation."""
    from .agekeys import DeviceAgeIdentity
    from .registry_store import read_profile_recipient
    from .broker.keystore import BrokerState
    known={t.root.scope_id for t in state.trees}|set(state.control.get('deleted',[]))
    for profile,secret in state.control.get('profile_ages',{}).items():
        if profile in known: continue
        path=f'keyring/{profile}/metadata/{state.device.device_id}.json'
        if path not in set(storage.list(path)): continue
        try:
            record=shape(parse(storage.get(path)),'scope-meta')
            if record['profile']!=profile or record['recipient_device']!=state.device.device_id: raise KeyAccessError('invalid metadata binding')
            verify_signature(record,registry.device_public(record['issuer_device'],at=record['issued_at']),domain('scope-meta'))
            identity=DeviceAgeIdentity.parse(secret)
            if read_profile_recipient(storage,registry,profile,state.device.device_id)!=identity.recipient: raise KeyAccessError('profile recipient mismatch')
            meta=parse(identity.decrypt(decode_ciphertext(record['age'])))
            if set(meta)!={'root','scopes'} or meta['root']!=profile: raise KeyAccessError('invalid metadata')
            candidate=state.record(); candidate['trees']=candidate['trees']+[dict(root=profile,scopes=meta['scopes'],epochs={},wraps={})]
            tree=BrokerState.from_record(candidate).trees[-1]
            ring=AgeKeyring(storage,registry,tree.scopes.values())
            for scope in tree.scopes: ring.ancestors(profile,scope)
            if not any(ring.authorized(record['issuer_device'],profile,sid,record['issued_at']) for sid in tree.scopes):
                raise KeyAccessError('unauthorized metadata')
            state.trees.append(tree)
        except (ValueError,KeyError,TypeError): continue
