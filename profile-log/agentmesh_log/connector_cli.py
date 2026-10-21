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
    request=urllib.request.Request(origin+'/admin/'+action,data=wire(envelope),headers={'Content-Type':'application/json'},method='POST')
    # Refuse redirects: signed administration must only reach the pinned origin.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=10) as response: result=parse(response.read(1048577))
        if 'error' in result: raise ValueError('unavailable')
        return result
    except Exception: raise ValueError('unavailable') from None


def approve(home,store,storage,code,profiles,remember,console,*,admin=admin_request):
    if not re.fullmatch(r'[0-9]{6}',code): raise ValueError('invalid_request')
    state=store.load(); cr=controls(state,storage); registry,ring=cr.context(); device=state.device.device_id; at=now()
    registry.device_public(device,at=at)
    reads=[{'profile':resolve(state,name),'scopes':None} for name in profiles]
    if not reads or len({e['profile'] for e in reads})!=len(reads): raise ValueError('invalid_request')
    parent,sep,leaf=remember.rpartition('/')
    if not sep or leaf!='inbox': raise ValueError('invalid_request')
    root=resolve(state,parent); tree=next(t for t in state.trees if t.root.scope_id==root)
    try: target=resolve(state,remember); missing=False
    except ValueError: target=root; missing=True
    proposal={'read':reads,'remember':{'profile':root,'scope':target}}
    if not cr.reader(ring,device,at) or not cr.covers(ring,device,proposal,at): raise ValueError('denied')
    pending=admin(state,'pending',{'code_hash':digest(code)})
    if type(pending.get('client_id')) is not str or type(pending.get('client_name')) is not str: raise ValueError('invalid_request')
    PresenceManager(home,store).require('agent','approve',console=console,one_shot=True,summary='Approve the displayed connector with the selected profile access.')
    state=store.load()
    if missing:
        from .admin import create_scope
        create_scope(home,state,remember)
        target=resolve(state,remember)
    app=secrets.token_hex(16)
    record=policy_record(state.device,app,pending['client_name'],pending['client_id'],read=reads,remember={'profile':root,'scope':target})
    state.control.setdefault('connector_names',{})[app]=pending['client_name']
    store.save(state)
    # Publish local control additions through the explicit configured storage.
    local=FilesystemStorage(home.root/'replica')
    for prefix in ('registry/','keyring/','keys/'):
        existing=set(storage.list(prefix))
        for path in local.list(prefix):
            if path not in existing: storage.put(path,local.get(path))
    if missing:
        from .bridge.inboxes import publish
        publish(state,storage,target)
    ref=controls(state,storage).publish(record)
    admin(state,'approve',{'code_hash':digest(code),'app_id':app,'policy_ref':ref})
    return {'app_id':app,'state':'active'}


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
    cmd.add_argument('--s3-prefix',default=''); cmd.add_argument('--s3-endpoint')


def storage_for(args,home):
    if args.filesystem: return FilesystemStorage(args.filesystem)
    if args.s3_bucket:
        from .storage.s3 import S3Storage
        if not args.s3_endpoint or not args.s3_endpoint.startswith('https://'): raise ValueError('invalid_request')
        return S3Storage(args.s3_bucket,prefix=args.s3_prefix,endpoint_url=args.s3_endpoint)
    return FilesystemStorage(home.root/'replica')


def add_parsers(commands):
    connector=commands.add_parser('connector'); operations=connector.add_subparsers(dest='action',required=True)
    for action in ('gateway-init','approve','list','revoke','allow-secret'):
        cmd=operations.add_parser(action); cmd.add_argument('--home',required=True); remote_flags(cmd)
        if action=='gateway-init': cmd.add_argument('--origin',required=True); cmd.add_argument('--public-key-file',required=True)
        if action=='approve':
            cmd.add_argument('code'); cmd.add_argument('--profiles',required=True); cmd.add_argument('--remember',required=True)
        if action in ('revoke','allow-secret'): cmd.add_argument('--app',required=True)
        if action=='allow-secret': cmd.add_argument('--minutes',type=int,default=15)
    bridge=commands.add_parser('bridge'); operations=bridge.add_subparsers(dest='action',required=True)
    serve=operations.add_parser('serve'); serve.add_argument('--home',required=True); serve.add_argument('--host',default='127.0.0.1'); serve.add_argument('--port',type=int,default=8766); remote_flags(serve)
    render=operations.add_parser('render-service'); remote_flags(render); render.add_argument('--home',required=True); render.add_argument('--output',required=True); render.add_argument('--python',required=True); render.add_argument('--platform',choices=['macos','windows'],required=True); render.add_argument('--port',type=int,default=8766)


def execute(args,*,backend,console):
    if args.command=='bridge' and args.action=='render-service':
        from .bridge.services import render
        return render(args.output,args.home,args.python,args.platform,args.port,filesystem=args.filesystem,s3_bucket=args.s3_bucket,s3_prefix=args.s3_prefix,s3_endpoint=args.s3_endpoint)
    home=BrokerHome(args.home); store=select_store(home,backend); storage=storage_for(args,home)
    if args.command=='bridge':
        from .bridge import Bridge
        if args.host!='127.0.0.1': raise ValueError('loopback_required')
        Bridge(home,store,storage).serve(host=args.host,port=args.port); return {'ok':True}
    state=store.load(); cr=controls(state,storage)
    if args.action=='list':
        apps=sorted({p.split('/')[1] for p in storage.list('connectors/') if re.fullmatch('[0-9a-f]{32}',p.split('/')[1])})
        return {'apps':[{'app_id':a,'state':p['state']} for a in apps if (p:=cr.policy(a)) is not None]}
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
    app=next((a for a,n in state.control.get('connector_names',{}).items() if n.casefold()==args.app.casefold()),args.app)
    hex_bytes(app,16)
    if args.action=='allow-secret': return allow_secret(home,store,storage,app,args.minutes,console)
    PresenceManager(home,store).require('agent','revoke',console=console,one_shot=True)
    policy=cr.policy(app); registry,ring=cr.context()
    registry.device_public(state.device.device_id)
    if policy is None or not cr.reader(ring,state.device.device_id,now()): raise ValueError('denied')
    record=signed('connector-policy',state.device._private,**{k:v for k,v in policy.items() if k not in ('signature','format','state','issuer_device','issued_at')},state='revoked',issuer_device=state.device.device_id,issued_at=now())
    ref=cr.publish(record); admin_request(state,'revoke',{'app_id':app,'policy_ref':ref})
    return {'app_id':app,'state':'revoked'}
