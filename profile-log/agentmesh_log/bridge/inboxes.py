"""Additive encrypted inbox metadata for devices paired before inbox creation."""
from dataclasses import asdict
from ..agekeys import DeviceAgeIdentity,encrypt
from ..broker.handshake import b64
from ..connector import exact
from ..encoding import parse,wire,fields,hex_bytes,milliseconds
from ..identity import verify_signature
from ..keyring import AgeKeyring
from ..keys import decode_ciphertext
from ..model import Scope,Label
from ..registry_store import signed,domain,now,read_profile_recipient


def publish(state,storage,scope):
    tree=next(t for t in state.trees if scope in t.scopes);s=tree.scopes[scope]
    if s.parent!=tree.root.scope_id or s.name!='inbox': raise ValueError('denied')
    registry=state.registry();ring=AgeKeyring(storage,registry,[s for t in state.trees for s in t.scopes.values()]);at=now()
    if not ring.authorized(state.device.device_id,s.profile_id,s.profile_id,at): raise ValueError('denied')
    for device in registry.certificates:
        try:
            registry.device_public(device)
            if not ring.authorized(device,s.profile_id,scope,at): continue
            recipient=read_profile_recipient(storage,registry,s.profile_id,device)
            record=signed('connector-inbox',state.device._private,profile=s.profile_id,scope=scope,recipient_device=device,issuer_device=state.device.device_id,issued_at=at,
                age=b64(encrypt(wire({**asdict(s),'default_label':s.default_label.name}),recipient)))
            path=f'connectors/inboxes/{s.profile_id}/{scope}/{device}.json'
            if path not in set(storage.list(path)):storage.put(path,wire(record))
        except ValueError:continue


def import_inboxes(state,storage,registry):
    ring=AgeKeyring(storage,registry,[s for t in state.trees for s in t.scopes.values()])
    for path in storage.list('connectors/inboxes/'):
        try:
            r=parse(storage.get(path));fields(r,{'format','signature','profile','scope','recipient_device','issuer_device','issued_at','age'},'agentmesh-connector-inbox-v1')
            for k in ('profile','scope','recipient_device','issuer_device'):hex_bytes(r[k],16)
            milliseconds(r['issued_at'])
            if path!=f"connectors/inboxes/{r['profile']}/{r['scope']}/{state.device.device_id}.json" or r['recipient_device']!=state.device.device_id or r['issued_at']>now():continue
            verify_signature(r,registry.device_public(r['issuer_device'],at=r['issued_at']),domain('connector-inbox'))
            if not ring.authorized(r['issuer_device'],r['profile'],r['profile'],r['issued_at']):continue
            tree=next(t for t in state.trees if t.root.scope_id==r['profile'])
            if r['scope'] in tree.scopes or r['profile'] in state.control.get('deleted',[]):continue
            identity=DeviceAgeIdentity.parse(state.control['profile_ages'][r['profile']])
            if read_profile_recipient(storage,registry,r['profile'],state.device.device_id)!=identity.recipient:continue
            value=parse(identity.decrypt(decode_ciphertext(r['age'])))
            exact(value,{'profile_id','scope_id','parent','kind','name','default_label'})
            s=Scope(**{**value,'default_label':Label[value['default_label']]})
            if s.profile_id!=r['profile'] or s.scope_id!=r['scope'] or s.parent!=tree.root.scope_id or s.name!='inbox' or s.kind!='project':continue
            if any(x.parent==s.parent and x.name==s.name for x in tree.scopes.values()):continue
            tree.scopes[s.scope_id]=s
        except (ValueError,KeyError,TypeError,StopIteration):continue
