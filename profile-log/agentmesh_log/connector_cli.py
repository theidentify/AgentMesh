"""Explicit, presence-backed connector administration; secrets never enter errors."""
from pathlib import Path
import re
import secrets
import urllib.request
from .connector import ConnectorRegistry, policy_record, secret_record, gateway_record, digest
from .registry_store import signed, now
from .encoding import wire, parse, hex_bytes
from .presence import PresenceManager
from .broker.grants import resolve, names
from .broker.keystore import BrokerHome
from .oskeystore import select_store
from .storage.filesystem import FilesystemStorage


def controls(state,storage): return ConnectorRegistry(storage,state.account.public_record(),[s for t in state.trees for s in t.scopes.values()])


def admin_request(state,action,payload):
    origin=state.control.get('connector_origin')
    if not origin: raise ValueError('unavailable')
    envelope=signed('connector-admin',state.device._private,device_id=state.device.device_id,ts=now(),nonce=secrets.token_hex(16),action=action,payload=payload)
    request=urllib.request.Request(origin+'/admin/'+action,data=wire(envelope),headers={'Content-Type':'application/json','User-Agent':'agentmesh-log/1'},method='POST')
    # Refuse redirects: signed administration must only reach the pinned origin.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=10) as response: result=parse(response.read(1048577))
        if 'error' in result: raise ValueError('unavailable')
        return result
    except Exception: raise ValueError('unavailable') from None


def approval_proposal(state,storage,profiles,remember,*,scope_id=None,max_label='internal'):
    cr=controls(state,storage); registry,ring=cr.context(); device=state.device.device_id; at=now()
    registry.device_public(device,at=at)
    reads=[{'profile':resolve(state,name),'scopes':None} for name in profiles]
    if not reads or len({e['profile'] for e in reads})!=len(reads) or max_label not in ('general','internal'): raise ValueError('invalid_request')
    parent,sep,leaf=remember.rpartition('/')
    if not sep or leaf!='inbox': raise ValueError('invalid_request')
    root=resolve(state,parent)
    try: target=resolve(state,remember); missing=False
    except ValueError: target=root; missing=True
    proposal={'read':reads,'remember':{'profile':root,'scope':target},'max_label':max_label,'allow_cross_profile':False,'limits':{'recall_limit_max':20,'requests_per_minute':60}}
    if not cr.reader(ring,device,at) or not cr.covers(ring,device,proposal,at): raise ValueError('denied')
    if missing: proposal['remember']['scope']=scope_id or secrets.token_hex(16)
    return proposal,missing


def apply_approval(home,store,storage,code_hash,client_id_hash,client_name,profiles,remember,proposal):
    # Caller has proved presence (console or WebAuthn). Recheck authority and exact proposal before writes.
    state=store.load()
    current,missing=approval_proposal(state,storage,profiles,remember,scope_id=proposal['remember']['scope'],max_label=proposal['max_label'])
    if current!=proposal: raise ValueError('denied')
    if missing:
        from .admin import create_scope
        create_scope(home,state,remember,scope_id=proposal['remember']['scope'])
    app=secrets.token_hex(16)
    record=signed('connector-policy',state.device._private,app_id=app,display_name_hash=digest(client_name),oauth_client_id_hash=client_id_hash,**proposal,state='active',issuer_device=state.device.device_id,issued_at=now())
    state.control.setdefault('connector_names',{})[app]=client_name
    store.save(state)
    local=FilesystemStorage(home.root/'replica')
    for prefix in ('registry/','keyring/','keys/'):
        existing=set(storage.list(prefix))
        for path in local.list(prefix):
            if path not in existing: storage.put(path,local.get(path))
    if missing:
        from .bridge.inboxes import publish
        publish(state,storage,proposal['remember']['scope'])
    ref=controls(state,storage).publish(record)
    if storage is not local: local.put(ref,wire(record))
    return signed('connector-admin',state.device._private,device_id=state.device.device_id,ts=now(),nonce=secrets.token_hex(16),action='approve',payload={'code_hash':code_hash,'app_id':app,'policy_ref':ref})


def approve(home,store,storage,code,profiles,remember,console,*,admin=admin_request):
    if not re.fullmatch(r'[0-9]{6}',code): raise ValueError('invalid_request')
    state=store.load(); proposal,_=approval_proposal(state,storage,profiles,remember)
    pending=admin(state,'pending',{'code_hash':digest(code)})
    if type(pending.get('client_id')) is not str or type(pending.get('client_name')) is not str: raise ValueError('invalid_request')
    PresenceManager(home,store).require('agent','approve',console=console,one_shot=True,summary='Approve the displayed connector with the selected profile access.')
    envelope=apply_approval(home,store,storage,digest(code),digest(pending['client_id']),pending['client_name'],profiles,remember,proposal)
    admin(store.load(),'approve',envelope['payload'])
    return {'app_id':envelope['payload']['app_id'],'state':'active'}


def allow_secret(home,store,storage,app,minutes,console):
    if type(minutes) is not int or not 1<=minutes<=60: raise ValueError('invalid_request')
    PresenceManager(home,store).require('secret_read','open',console=console,one_shot=True)
    state=store.load(); cr=controls(state,storage); at=now(); policy=cr.policy(app,at=at)
    registry,ring=cr.context(); registry.device_public(state.device.device_id,at=at)
    if policy is None or policy['state']!='active' or not cr.covers(ring,state.device.device_id,policy,at,remember=False) or not cr.reader(ring,state.device.device_id,at): raise ValueError('denied')
    cr.publish(secret_record(state.device,app,at,at+minutes*60000))
    return {'app_id':app,'not_after':at+minutes*60000}


def remote_flags(cmd):
    group=cmd.add_mutually_exclusive_group()
    group.add_argument('--filesystem'); group.add_argument('--s3-bucket')
    cmd.add_argument('--s3-prefix',default=''); cmd.add_argument('--s3-endpoint'); cmd.add_argument('--s3-credentials')


def storage_for(args,home):
    # Same explicit OS-stored credentials as the broker's remote sync; never the boto credential chain.
    from .cli import remote_storage
    return remote_storage(args) or FilesystemStorage(home.root/'replica')


def add_parsers(commands):
    connector=commands.add_parser('connector'); operations=connector.add_subparsers(dest='action',required=True)
    for action in ('gateway-init','approve','list','revoke','allow-secret'):
        cmd=operations.add_parser(action); cmd.add_argument('--home',required=True); remote_flags(cmd)
        if action=='gateway-init': cmd.add_argument('--origin',required=True); cmd.add_argument('--public-key-file',required=True)
        if action=='approve':
            cmd.add_argument('code'); cmd.add_argument('--profiles',required=True); cmd.add_argument('--remember',required=True)
        if action in ('revoke','allow-secret'): cmd.add_argument('--app',required=True)
        if action=='allow-secret': cmd.add_argument('--minutes',type=int,default=15)
    cmd=operations.add_parser('passkey'); actions=cmd.add_subparsers(dest='passkey_action',required=True)
    for action in ('add','list','remove','settings'):
        sub=actions.add_parser(action);sub.add_argument('--home',required=True);remote_flags(sub)
        if action=='add': sub.add_argument('--label',required=True)
        if action=='remove': sub.add_argument('credential_hash_prefix')
        if action=='settings':
            sub.add_argument('--rp-id');sub.add_argument('--origin');sub.add_argument('--profiles');sub.add_argument('--remember');sub.add_argument('--max-label',choices=['general','internal'])
    bridge=commands.add_parser('bridge'); operations=bridge.add_subparsers(dest='action',required=True)
    serve=operations.add_parser('serve'); serve.add_argument('--home',required=True); serve.add_argument('--host',default='127.0.0.1'); serve.add_argument('--port',type=int,default=8766); remote_flags(serve)
    render=operations.add_parser('render-service'); remote_flags(render); render.add_argument('--home',required=True); render.add_argument('--output',required=True); render.add_argument('--python',required=True); render.add_argument('--platform',choices=['macos','windows'],required=True); render.add_argument('--port',type=int,default=8766)


def execute(args,*,backend,console):
    # All connector writers share the bridge lock file: never restore stale ceremony state.
    if args.command=='connector':
        with BrokerHome(args.home).lock(): return _execute(args,backend=backend,console=console)
    return _execute(args,backend=backend,console=console)


def _execute(args,*,backend,console):
    if args.command=='bridge' and args.action=='render-service':
        from .bridge.services import render
        return render(args.output,args.home,args.python,args.platform,args.port,filesystem=args.filesystem,s3_bucket=args.s3_bucket,s3_prefix=args.s3_prefix,s3_endpoint=args.s3_endpoint,s3_credentials=args.s3_credentials)
    home=BrokerHome(args.home); store=select_store(home,backend); storage=storage_for(args,home)
    if args.command=='bridge':
        from .bridge import Bridge
        if args.host!='127.0.0.1': raise ValueError('loopback_required')
        Bridge(home,store,storage).serve(host=args.host,port=args.port); return {'ok':True}
    if args.action=='passkey':
        from .passkeys import Passkeys,settings
        service=Passkeys(home,store,storage)
        if args.passkey_action=='add': return service.add(args.label,console)
        if args.passkey_action=='list': return service.listing()
        if args.passkey_action=='remove': return service.remove(args.credential_hash_prefix,console)
        state=store.load();cfg=settings(state)
        changes={k:getattr(args,k) for k in ('rp_id','origin','profiles','remember','max_label') if getattr(args,k) is not None}
        if changes:
            if 'profiles' in changes: changes['profiles']=changes['profiles'].split(',')
            if 'rp_id' in changes and 'origin' not in changes: changes['origin']='https://'+changes['rp_id']
            state.control['passkey_settings']={**cfg,**changes};settings(state)
            service.authority();PresenceManager(home,store).require('device','approve',console=console,one_shot=True)
            fresh=store.load();fresh.control['passkey_settings']=state.control['passkey_settings'];store.save(fresh)
        return {'settings':settings(store.load())}
    state=store.load(); cr=controls(state,storage)
    if args.action=='list':
        apps=sorted({p.split('/')[1] for p in storage.list('connectors/') if re.fullmatch('[0-9a-f]{32}',p.split('/')[1])})
        return {'apps':[{'app_id':a,'display_name':state.control.get('connector_names',{}).get(a),'state':p['state']} for a in apps if (p:=cr.policy(a)) is not None]}
    if args.action=='gateway-init':
        raw=hex_bytes(Path(args.public_key_file).read_text(encoding='utf-8').strip(),32)
        record=gateway_record(state.device,raw,args.origin)
        from .connector import shape
        shape(record,'gateway')
        PresenceManager(home,store).require('device','approve',console=console,one_shot=True)
        cr.publish(record); state=store.load(); state.control['connector_origin']=args.origin; store.save(state)
        return {'ok':True}
    if args.action=='approve':
        return approve(home,store,storage,args.code,args.profiles.split(','),args.remember,console)
    app=args.app
    if not re.fullmatch('[0-9a-f]{32}',app):
        matches=[]
        for a,n in state.control.get('connector_names',{}).items():
            if n.casefold()!=app.casefold(): continue
            policy=cr.policy(a)
            if policy is not None and policy['state']=='revoked': continue
            if args.action=='allow-secret' and policy is None: continue
            matches.append(a)
        if not matches: raise ValueError('unknown_app')
        if len(matches)>1: raise ValueError('ambiguous_app')
        app=matches[0]
    hex_bytes(app,16)
    if args.action=='allow-secret': return allow_secret(home,store,storage,app,args.minutes,console)
    PresenceManager(home,store).require('agent','revoke',console=console,one_shot=True)
    policy=cr.policy(app); registry,ring=cr.context()
    registry.device_public(state.device.device_id)
    if policy is None or not cr.reader(ring,state.device.device_id,now()): raise ValueError('denied')
    record=signed('connector-policy',state.device._private,**{k:v for k,v in policy.items() if k not in ('signature','format','state','issuer_device','issued_at')},state='revoked',issuer_device=state.device.device_id,issued_at=now())
    ref=cr.publish(record); admin_request(state,'revoke',{'app_id':app,'policy_ref':ref})
    return {'app_id':app,'state':'revoked'}
