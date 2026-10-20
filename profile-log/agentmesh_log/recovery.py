"""SLIP-39 recovery ceremonies. Secrets and shares remain caller-owned in RAM."""
import secrets
from dataclasses import asdict
from pathlib import Path
import shamir_mnemonic
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from .agekeys import DeviceAgeIdentity, encrypt
from .registry_store import RegistryStore, signed, shape, domain, now, recovery_id
from .identity import DeviceIdentity, sign, DEVICE_DOMAIN, verify_signature
from .model import Account
from .encoding import wire, parse, hex_bytes
from .keyring import AgeKeyring, initialize_keyring, sync_control
from .keys import decode_ciphertext, ScopeReadKey
from .pairing import registry_cache
from .presence import PresenceManager
from .broker.handshake import b64
from .broker.keystore import BrokerState, atomic_write
from .lifecycle import rotate_scopes, scopes_of
from . import ulid


class RecoveryIdentity:
    def __init__(self,secret):
        if type(secret) is not bytes or len(secret)!=32: raise ValueError('invalid recovery secret')
        def derive(info): return HKDF(algorithm=hashes.SHA256(),length=32,salt=None,info=info).derive(secret)
        self._private=Ed25519PrivateKey.from_private_bytes(derive(b'AgentMesh/recovery_signing/v1\x00'))
        self.age=DeviceAgeIdentity.from_raw(derive(b'AgentMesh/recovery_age/v1\x00'))
        self.device_id='recovery'
    @property
    def public(self): return self._private.public_key().public_bytes_raw().hex()


def split_secret(secret):
    return shamir_mnemonic.generate_mnemonics(group_threshold=1,groups=[(2,3)],master_secret=secret,
        passphrase=b'',extendable=True,iteration_exponent=1)[0]


def combine_shares(shares):
    try:
        if len(shares)<2: raise ValueError()
        secret=shamir_mnemonic.combine_mnemonics(shares,passphrase=b'')
        if len(secret)!=32: raise ValueError()
        return secret
    except Exception: raise ValueError('invalid recovery shares') from None


def matches(identity,record):
    return identity.public==record['recovery_signing_public'] and identity.age.recipient==record['recovery_age_recipient']


def _record(account,identity,private,previous=None):
    return signed('recovery-record',private,account_id=account['account_id'],recovery_signing_public=identity.public,
        recovery_age_recipient=identity.age.recipient,previous=previous,issued_at=now())


def tree_records(state):
    records=[]
    for tree in state.trees:
        if tree.root.scope_id in state.control.get('deleted',[]) or tree.root.scope_id not in tree.epochs: continue
        records.append(dict(root=tree.root.scope_id,
            scopes=[{**asdict(s),'default_label':s.default_label.name} for s in tree.scopes.values()],
            epochs={tree.root.scope_id:[k.private_bytes().hex() for k in tree.epochs[tree.root.scope_id]]},wraps={}))
    return records


def _history_payload(tree):
    root=tree['root']
    return dict(root=root,scopes=tree['scopes'],roots=[dict(key_id=ScopeReadKey.from_private(root,root,bytes.fromhex(raw)).key_id,private=raw)
        for raw in tree['epochs'][root]])


def _write_history(storage,record,tree,recipient,signer):
    rid=recovery_id(record); profile=tree['root']
    envelope=signed('recovery-history',signer._private,id=ulid.generate(),profile=profile,recovery_key_id=rid,
        issuer_device=signer.device_id,issued_at=now(),age=b64(encrypt(wire(_history_payload(tree)),recipient)))
    storage.put(f"keyring/{profile}/recovery/{rid}/{envelope['id']}.json",wire(envelope))


def publish_bundle(storage,record,master,trees):
    """Fresh random recovery identity per profile; no private identity retained locally."""
    rid=recovery_id(record)
    for tree in trees:
        identity=DeviceAgeIdentity(); profile=tree['root']
        envelope=signed('profile-recovery',master._private,profile_id=profile,recovery_key_id=rid,
            age_recipient=identity.recipient,issued_at=now(),age=b64(encrypt(identity.raw(),master.age.recipient)))
        storage.put(f'registry/profile-recovery/{profile}/{rid}.json',wire(envelope))
        _write_history(storage,record,tree,identity.recipient,master)


def publish_history(state,storage,record,*,signer=None):
    from .registry_store import read_profile_recovery
    registry=RegistryStore(storage,state.account.public_record()).load()
    registry.add_recovery(record)
    for tree in tree_records(state):
        recipient=read_profile_recovery(storage,registry,tree['root'],recovery_id(record))['age_recipient']
        _write_history(storage,record,tree,recipient,signer or state.device)


def load_bundle(storage,registry,record,master):
    """Verify and decrypt roots/metadata in memory, including later root epochs."""
    from .registry_store import read_profile_recovery
    from .model import Scope,Label
    rid=recovery_id(record); identities={}; latest={}; trees=[]
    for path in storage.list('registry/profile-recovery/'):
        if not path.endswith('/'+rid+'.json'): continue
        envelope=shape(parse(storage.get(path)),'profile-recovery'); profile=envelope['profile_id']
        bound=read_profile_recovery(storage,registry,profile,rid)
        identity=DeviceAgeIdentity.from_raw(master.age.decrypt(decode_ciphertext(bound['age'])))
        if identity.recipient!=bound['age_recipient']: raise ValueError('invalid profile recovery identity')
        identities[profile]=identity
    for path in storage.list('keyring/'):
        if '/recovery/'+rid+'/' not in path: continue
        envelope=shape(parse(storage.get(path)),'recovery-history'); ulid.parse(envelope['id'])
        if path!=f"keyring/{envelope['profile']}/recovery/{rid}/{envelope['id']}.json" or envelope['recovery_key_id']!=rid: raise ValueError('invalid recovery history')
        public=bytes.fromhex(record['recovery_signing_public']) if envelope['issuer_device']=='recovery' else registry.device_public(envelope['issuer_device'],at=envelope['issued_at'])
        verify_signature(envelope,public,domain('recovery-history'))
        previous=latest.get(envelope['profile'])
        if previous is None or envelope['id']>previous['id']: latest[envelope['profile']]=envelope
    for profile,envelope in latest.items():
        if profile not in identities: raise ValueError('missing profile recovery identity')
        meta=parse(identities[profile].decrypt(decode_ciphertext(envelope['age'])))
        if set(meta)!={'root','scopes','roots'} or meta['root']!=profile: raise ValueError('invalid recovery history')
        roots=[]
        for value in meta['roots']:
            key=ScopeReadKey.from_private(profile,profile,hex_bytes(value['private'],32))
            if key.key_id!=value['key_id']: raise ValueError('invalid recovery history')
            roots.append(value['private'])
        trees.append(dict(root=profile,scopes=meta['scopes'],epochs={profile:roots},wraps={}))
    scopes=[Scope(**{**s,'default_label':Label[s['default_label']]}) for tree in trees for s in tree['scopes']]
    ring=AgeKeyring(storage,registry,scopes)
    for envelope in latest.values():
        if envelope['issuer_device']!='recovery' and not ring.authorized(envelope['issuer_device'],envelope['profile'],envelope['profile'],envelope['issued_at']):
            raise ValueError('unauthorized recovery history')
    for grant in ring.records():
        profile=grant['profile']
        if grant['recipient_device']!='recovery' or profile not in identities: continue
        try: key=ring.open(grant,'recovery',identities[profile])
        except ValueError: continue
        tree=next(t for t in trees if t['root']==profile)
        raw=key.private_bytes().hex()
        if raw not in tree['epochs'][profile]: tree['epochs'][profile].append(raw)
    return trees


def create_recovery(home,store,storage,*,console):
    PresenceManager(home,store).require('recovery','create',console=console)
    state=store.load(); shares=create_recovery_material(state,storage)
    store.save(state); sync_control(home,store,storage)
    return shares


def create_recovery_material(state,storage):
    from .registry_store import read_profile_recovery
    rs=RegistryStore(storage,state.account.public_record())
    if rs.load().recoveries or state.account._root is None: raise ValueError('recovery_already_created')
    secret=secrets.token_bytes(32); identity=RecoveryIdentity(secret)
    record=_record(state.account.public_record(),identity,state.account._root)
    rs.publish_recovery(record); publish_bundle(storage,record,identity,tree_records(state))
    ring=AgeKeyring(storage,rs.load(),scopes_of(state))
    for tree in state.trees:
        recipient=read_profile_recovery(storage,ring.registry,tree.root.scope_id,recovery_id(record))['age_recipient']
        for key in tree.epochs[tree.root.scope_id]: ring.publish(key,state.device,'recovery',recipient)
    state.control['registry_records']=registry_cache(rs)
    return split_secret(secret)


def test_recovery(home,store,storage,shares,*,console):
    PresenceManager(home,store).require('recovery','test',console=console,one_shot=True)
    state=store.load(); registry=RegistryStore(storage,state.account.public_record()).load()
    shares=shares() if callable(shares) else shares
    try: return matches(RecoveryIdentity(combine_shares(shares)),registry.current_recovery())
    except ValueError: return False


def prepare_delete_recovery(state,storage,profile,shares):
    shares=shares() if callable(shares) else shares
    master=RecoveryIdentity(combine_shares(shares or []))
    registry=RegistryStore(storage,state.account.public_record()).load(); current=registry.current_recovery()
    if not matches(master,current): raise ValueError('recovery_mismatch')
    trees=[t for t in load_bundle(storage,registry,current,master) if t['root']!=profile and t['root'] not in state.control.get('deleted',[])]
    secret=secrets.token_bytes(32); replacement=RecoveryIdentity(secret)
    record=_record(state.account.public_record(),replacement,master._private,recovery_id(current))
    publish_bundle(storage,record,replacement,trees)
    return record,split_secret(secret)


def use_recovery(home,store,storage,shares,*,account,console):
    PresenceManager(home,store).require('recovery','use',console=console,one_shot=True)
    shares=shares() if callable(shares) else shares; account=account() if callable(account) else account
    identity=RecoveryIdentity(combine_shares(shares)); rs=RegistryStore(storage,account); registry=rs.load()
    current=registry.current_recovery()
    if not matches(identity,current): raise ValueError('recovery_mismatch')
    trees=load_bundle(storage,registry,current,identity)
    rid=recovery_id(current); device=DeviceIdentity(); age=DeviceAgeIdentity()
    cert=sign(dict(format='agentmesh-device-cert-v1',account_id=account['account_id'],device_id=device.device_id,
        device_public_key=device.public_bytes.hex(),issuer='recovery',issued_at=now()),identity._private,DEVICE_DOMAIN)
    from .registry_store import device_record,profile_recipient
    rs.publish_device(device_record(device,cert,age))
    profiles={t['root']:DeviceAgeIdentity().secret() for t in trees}
    for profile,secret in profiles.items(): rs.publish_profile_recipient(profile_recipient(device,profile,DeviceAgeIdentity.parse(secret)))
    registry=rs.load()
    state=BrokerState.from_record(dict(format='agentmesh-broker-state-v2',account=account,account_private_key=None,device_id=device.device_id,
        device_private_key=device.private_bytes().hex(),certificates=list(registry.certificates.values()),trees=trees,grants={},
        index_master_key=secrets.token_bytes(32).hex(),audit_key=secrets.token_bytes(32).hex(),
        control=dict(phase4=True,age_identity=age.secret(),profile_ages=profiles,registry_records=registry_cache(rs))))
    store.save(state); state=sync_control(home,store,storage)
    ring=AgeKeyring(storage,registry,scopes_of(state))
    for tree in state.trees:
        if tree.root.scope_id in state.control.get('deleted',[]): continue
        recipient=DeviceAgeIdentity.parse(profiles[tree.root.scope_id]).recipient
        for epochs in tree.epochs.values():
            for key in epochs: ring.publish(key,device,device.device_id,recipient)
    alert=signed('recovery-alert',identity._private,account_id=account['account_id'],id=ulid.generate(),recovery_key_id=rid,
        device_id=device.device_id,issued_at=now())
    storage.put('alerts/recovery/'+alert['id']+'.json',wire(alert))
    new_secret=secrets.token_bytes(32); replacement=RecoveryIdentity(new_secret)
    new_record=_record(account,replacement,identity._private,rid)
    publish_bundle(storage,new_record,replacement,tree_records(state))
    affected={sid for t in state.trees for sid in t.keys}
    rotate_scopes(state,storage,affected,reason='recovery',only_device=device.device_id,recovery_record=new_record)
    publish_history(state,storage,new_record,signer=replacement)
    rs.publish_recovery(new_record)
    state.control['registry_records']=registry_cache(rs); store.save(state); sync_control(home,store,storage)
    return split_secret(new_secret)


def collect_alerts(state,storage,registry):
    alerts={r['id']:r for r in state.control.get('alerts',[])}
    for path in storage.list('alerts/recovery/'):
        try:
            record=shape(parse(storage.get(path)),'recovery-alert'); ulid.parse(record['id'])
            if path!='alerts/recovery/'+record['id']+'.json' or record['account_id']!=registry.account_id: continue
            recovery=registry.recoveries[record['recovery_key_id']]
            verify_signature(record,bytes.fromhex(recovery['recovery_signing_public']),domain('recovery-alert'))
            alerts[record['id']]=record
        except (ValueError,KeyError): continue
    state.control['alerts']=[alerts[k] for k in sorted(alerts)]


def write_shares(shares,out,home,replica):
    target=Path(out).resolve()
    if any(target==Path(root).resolve() or Path(root).resolve() in target.parents for root in (home,replica)):
        raise ValueError('unsafe recovery output')
    target.mkdir(mode=0o700,parents=True,exist_ok=False)
    for i,share in enumerate(shares,1): atomic_write(target/f'share-{i}.txt',(share+'\n').encode('utf-8'))
