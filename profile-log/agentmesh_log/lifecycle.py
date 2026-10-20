"""Presence-controlled revocation, rotation and monotonic profile destruction."""
import secrets
from .registry_store import RegistryStore, revocation, signed, shape, domain, now
from .keyring import AgeKeyring, sync_control
from .identity import verify_signature
from .encoding import wire, parse
from .presence import PresenceManager
from .events import wrap_path
from . import ulid


def scopes_of(state): return [s for tree in state.trees for s in tree.scopes.values()]


def rotate_scopes(state,storage,scopes,*,reason='rotate',only_device=None,recovery_record=None):
    registry=RegistryStore(storage,state.account.public_record()).load()
    if recovery_record is not None: registry.add_recovery(recovery_record)
    from .registry_store import read_profile_recipient,read_profile_recovery,recovery_id
    ring=AgeKeyring(storage,registry,scopes_of(state))
    grants=list(ring.records()); eligible={}
    for record in grants:
        try: ring.validate(record)
        except ValueError: continue
        device=record['recipient_device']
        if device=='recovery': continue
        try: registry.device_public(device)
        except ValueError: continue
        eligible.setdefault(device,set()).add(record['scope'])
    if only_device is not None: eligible={only_device:set(scopes)}
    for tree in state.trees:
        ordered=[sid for sid in tree.keys if sid in scopes]
        # Rotate every accessible descendant as well as explicitly granted roots.
        for sid in ordered:
            old=tree.keys[sid]; key=tree.rotate(sid)
            age=read_profile_recipient(storage,registry,tree.root.scope_id,state.device.device_id)
            ring.publish(key,state.device,state.device.device_id,age)
            ring.epoch(key,state.device,previous=old.key_id,reason=reason)
        for wrap in tree.wraps.values(): storage.put(wrap_path(wrap),wire(wrap))
        for sid in ordered:
            key=tree.keys[sid]
            for device,held in eligible.items():
                if device==state.device.device_id: continue
                if held & ring.ancestors(key.profile_id,sid):
                    ring.publish(key,state.device,device,read_profile_recipient(storage,registry,tree.root.scope_id,device))
            if sid==tree.root.scope_id:
                master=recovery_record or (registry.current_recovery() if registry.recoveries else None)
                if master:
                    recipient=read_profile_recovery(storage,registry,tree.root.scope_id,recovery_id(master))['age_recipient']
                    ring.publish(key,state.device,'recovery',recipient)
    return state


def remove_device(home,store,storage,device_id,*,console):
    PresenceManager(home,store).require('device','remove',console=console)
    state=sync_control(home,store,storage)
    if device_id==state.device.device_id: raise ValueError('cannot remove current device')
    rs=RegistryStore(storage,state.account.public_record()); registry=rs.load(); registry.device_public(device_id)
    ring=AgeKeyring(storage,registry,scopes_of(state)); held=set()
    for record in ring.records():
        if record['recipient_device']!=device_id: continue
        try: ring.validate(record); held.add(record['scope'])
        except ValueError: continue
    affected={s.scope_id for s in scopes_of(state) if held & ring.ancestors(s.profile_id,s.scope_id)}
    if any(s not in t.keys for t in state.trees for s in affected if s in t.scopes): raise ValueError('insufficient rotation authority')
    rs.publish_revocation(revocation(device_id,state.device))
    rotate_scopes(state,storage,affected,reason='revoke'); store.save(state)
    sync_control(home,store,storage)


def profile_state(home,store,storage,profile,state_name,*,console,confirmation=None,shares=None):
    if state_name not in ('active','archived','deleted'): raise ValueError('invalid profile state')
    summary='Old recovery shares can still decrypt this profile until destroyed, or its stored objects are removed by hand. New shares will be issued.' if state_name=='deleted' else 'Archive this profile.'
    PresenceManager(home,store).require('profile','delete' if state_name=='deleted' else 'archive',console=console,summary=summary,one_shot=state_name=='deleted')
    state=store.load(); tree=next(t for t in state.trees if t.root.scope_id==profile)
    if state_name=='deleted' and confirmation!=tree.root.name: raise ValueError('confirmation_required')
    registry=RegistryStore(storage,state.account.public_record()).load(); ring=AgeKeyring(storage,registry,scopes_of(state))
    if not ring.authorized(state.device.device_id,profile,profile,now()): raise ValueError('denied')
    replacement=None
    if state_name=='deleted':
        from .recovery import prepare_delete_recovery
        replacement,new_shares=prepare_delete_recovery(state,storage,profile,shares)
    record=signed('profile-state',state.device._private,profile=profile,id=ulid.generate(),state=state_name,issuer_device=state.device.device_id)
    storage.put(f"profiles/{profile}/state/{record['id']}.json",wire(record))
    sync_control(home,store,storage)
    if replacement is not None:
        RegistryStore(storage,state.account.public_record()).publish_recovery(replacement)
        sync_control(home,store,storage)
        return new_shares


def apply_profile_states(home,state,storage,ring):
    latest={}; deleted=set(state.control.get('deleted',[]))
    for path in storage.list('profiles/'):
        try:
            record=shape(parse(storage.get(path)),'profile-state'); ulid.parse(record['id'])
            if path!=f"profiles/{record['profile']}/state/{record['id']}.json" or record['state'] not in ('active','archived','deleted'): continue
            at=ulid.timestamp(record['id']); public=ring.registry.device_public(record['issuer_device'],at=at)
            verify_signature(record,public,domain('profile-state'))
            if not ring.authorized(record['issuer_device'],record['profile'],record['profile'],at): continue
            if record['state']=='deleted': deleted.add(record['profile'])
            old=latest.get(record['profile'])
            if old is None or record['id']>old['id']: latest[record['profile']]=record
        except ValueError: continue
    state.control['profile_states']={p:r['state'] for p,r in latest.items()}
    state.control['deleted']=sorted(deleted)
    if deleted:
        for profile in deleted: state.control.get('profile_ages',{}).pop(profile,None)
        for tree in state.trees:
            if tree.root.scope_id in deleted: tree.keys.clear(); tree.epochs.clear(); tree.wraps.clear()
        state.grants={aid:g for aid,g in state.grants.items() if g.default_profile not in deleted}
        for path in (home.root/'index').glob('*.snap'): path.unlink()
