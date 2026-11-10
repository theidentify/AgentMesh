"""Explicit, presence-backed connector administration; secrets never enter errors."""
from pathlib import Path
import re
import secrets
import urllib.error
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


# Error codes a connector command may return. Each names the fix; none carries a code, key or token.
CODES={
    'remember_must_end_with_inbox':'--remember must be a profile (work) or a profile inbox (work/inbox).',
    'no_gateway_origin':'This device has no gateway origin and the shared registry has no single gateway record. Run `connector gateway-use --origin https://...` (sets only the local pointer).',
    'gateway_origin_unknown':'That origin is not in a verified registry/gateway record. Check the URL, or ask the device that ran gateway-init.',
    'code_not_found_or_expired':'The gateway has no live request for that code. Codes last 10 minutes; reload the connector page for a new one.',
    'code_already_used':'That request was already approved, rejected or completed. Reload the connector page for a new code.',
    'code_mismatch':'The code does not belong to the chosen request.',
    'gateway_denied':'The gateway refused this device\'s signed request (unknown device, clock skew or a stale gateway).',
    'gateway_clock_skew':'This device\'s clock is more than 60 seconds away from the gateway\'s.',
    'gateway_unreachable':'The gateway did not answer. Check the network and the gateway origin.',
    'gateway_unavailable':'The gateway answered with an error.',
    'code_required':'Give the 6-digit code shown on the connector page.',
    'invalid_code':'The code is 6 digits.',
    'unknown_profile':'A profile in --profiles does not exist on this device.',
    'not_authorized':'This device cannot read every profile and inbox in the request.',
}
GATEWAY_CODES={'code_not_found_or_expired','code_already_used','gateway_clock_skew'}


def published_gateways(state,storage):
    """Origins of verified registry/gateway records, newest first; a bad record is skipped, never trusted."""
    from .connector import gateway_id
    from .registry_store import domain
    from .identity import verify_signature
    cr=controls(state,storage)
    try: registry,ring=cr.context()
    except Exception: return []
    at=now(); found={}
    for path,r in cr.records('registry/gateway/','gateway'):
        try:
            if path!='registry/gateway/'+gateway_id(r)+'.json' or r['issued_at']>at: continue
            verify_signature(r,registry.device_public(r['issuer_device'],at=at),domain('gateway'))
            if not cr.reader(ring,r['issuer_device'],r['issued_at']): continue
            if found.get(r['origin'],0)<r['issued_at']: found[r['origin']]=r['issued_at']
        except (ValueError,KeyError,TypeError): continue
    return sorted(found,key=lambda o:-found[o])


def gateway_origin(state,storage):
    """The local pointer, else the one published gateway. Never writes anything."""
    origin=state.control.get('connector_origin')
    if origin: return origin
    published=published_gateways(state,storage)
    if len(published)==1: return published[0]
    raise ValueError('no_gateway_origin')


def gateway_error(status,body):
    try: code=parse(body).get('error')
    except Exception: code=None
    if code in GATEWAY_CODES: return code
    if code=='clock_skew': return 'gateway_clock_skew'
    if status in (401,403) or code=='denied': return 'gateway_denied'
    return 'gateway_unavailable'


def admin_request(state,action,payload,*,origin=None,storage=None):
    origin=origin or state.control.get('connector_origin') or (gateway_origin(state,storage) if storage is not None else None)
    if not origin: raise ValueError('no_gateway_origin')
    envelope=signed('connector-admin',state.device._private,device_id=state.device.device_id,ts=now(),nonce=secrets.token_hex(16),action=action,payload=payload)
    request=urllib.request.Request(origin+'/admin/'+action,data=wire(envelope),headers={'Content-Type':'application/json','User-Agent':'agentmesh-log/1'},method='POST')
    # Refuse redirects: signed administration must only reach the pinned origin.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=10) as response: body=response.read(1048577)
    except urllib.error.HTTPError as error:
        try: body=error.read(4096)
        except Exception: body=b''
        raise ValueError(gateway_error(error.code,body)) from None
    except Exception: raise ValueError('gateway_unreachable') from None
    try: result=parse(body)
    except Exception: raise ValueError('gateway_unavailable') from None
    if not isinstance(result,dict): raise ValueError('gateway_unavailable')
    if 'error' in result: raise ValueError(gateway_error(200,body))
    return result


def bound_admin(state,storage):
    origin=gateway_origin(state,storage)
    return lambda s,action,payload: admin_request(s,action,payload,origin=origin)


def remember_target(remember,profiles):
    """`work` means work/inbox; nothing means <first profile>/inbox. Anything else must end in /inbox."""
    if remember is None or remember=='': return profiles[0]+'/inbox'
    if '/' not in remember: return remember+'/inbox'
    if remember.rpartition('/')[2]!='inbox' or not remember.rpartition('/')[0]: raise ValueError('remember_must_end_with_inbox')
    return remember


def default_access(state,profiles,remember):
    """Profiles default to the approval settings the gateway page uses; remember follows the first profile."""
    if not profiles:
        from .passkeys import settings
        cfg=settings(state); profiles=list(cfg['profiles'])
        if remember is None and 'passkey_settings' in state.control: remember=cfg['remember']
    return profiles,remember_target(remember,profiles)


def approval_proposal(state,storage,profiles,remember,*,scope_id=None,max_label='internal'):
    cr=controls(state,storage); registry,ring=cr.context(); device=state.device.device_id; at=now()
    registry.device_public(device,at=at)
    try: reads=[{'profile':resolve(state,name),'scopes':None} for name in profiles]
    except ValueError: raise ValueError('unknown_profile') from None
    if not reads or len({e['profile'] for e in reads})!=len(reads) or max_label not in ('general','internal'): raise ValueError('invalid_request')
    parent,sep,leaf=remember.rpartition('/')
    if not sep or leaf!='inbox': raise ValueError('remember_must_end_with_inbox')
    try: root=resolve(state,parent)
    except ValueError: raise ValueError('unknown_profile') from None
    try: target=resolve(state,remember); missing=False
    except ValueError: target=root; missing=True
    proposal={'read':reads,'remember':{'profile':root,'scope':target},'max_label':max_label,'allow_cross_profile':False,'limits':{'recall_limit_max':20,'requests_per_minute':60}}
    if not cr.reader(ring,device,at) or not cr.covers(ring,device,proposal,at): raise ValueError('not_authorized')
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


def approve(home,store,storage,code,profiles,remember,console,*,admin=None,code_hash=None):
    """Approve one gateway request. `code_hash` (from `pending`) only narrows: the typed code must match it."""
    if code is None or code=='': raise ValueError('code_required')
    code=code.strip().replace(' ','')
    if not re.fullmatch(r'[0-9]{6}',code): raise ValueError('invalid_code')
    if code_hash is not None and digest(code)!=code_hash: raise ValueError('code_mismatch')
    state=store.load(); profiles,remember=default_access(state,profiles,remember)
    proposal,_=approval_proposal(state,storage,profiles,remember)
    if admin is None: admin=bound_admin(state,storage)
    pending=admin(state,'pending',{'code_hash':digest(code)})
    if type(pending.get('client_id')) is not str or type(pending.get('client_name')) is not str: raise ValueError('gateway_unavailable')
    name=pending['client_name'][:64].replace('\n',' ')
    PresenceManager(home,store).require('agent','approve',console=console,one_shot=True,summary='Approve connector "%s": read %s, remember into %s.'%(name,', '.join(profiles),remember))
    envelope=apply_approval(home,store,storage,digest(code),digest(pending['client_id']),pending['client_name'],profiles,remember,proposal)
    admin(store.load(),'approve',envelope['payload'])
    return {'app_id':envelope['payload']['app_id'],'state':'active','client_name':pending['client_name'],'read':profiles,'remember':remember}


def pending_requests(state,storage,*,admin=None):
    """Live gateway requests: client name and times only. The code itself never leaves the browser."""
    if admin is None: admin=bound_admin(state,storage)
    result=admin(state,'list',{})
    rows=result.get('requests')
    if type(rows) is not list: raise ValueError('gateway_unavailable')
    out=[]
    for row in rows[:100]:
        if not isinstance(row,dict) or type(row.get('code_hash')) is not str or not re.fullmatch('[0-9a-f]{64}',row['code_hash']): continue
        if type(row.get('expires')) is not int or type(row.get('created')) is not int: continue
        out.append({'code_hash':row['code_hash'],'client_name':str(row.get('client_name','App'))[:256],'created':row['created'],'expires':row['expires']})
    return {'requests':sorted(out,key=lambda r:r['created']),'capped':result.get('capped') is True}


def use_gateway(store,storage,origin):
    """Point this device at an already-published gateway. Writes only the local pointer, never the registry."""
    state=store.load(); published=published_gateways(state,storage)
    if origin is None:
        if len(published)!=1: raise ValueError('no_gateway_origin')
        origin=published[0]
    origin=origin.rstrip('/')
    if origin not in published: raise ValueError('gateway_origin_unknown')
    state.control['connector_origin']=origin; store.save(state)
    return {'origin':origin}


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
    cmd.add_argument('--s3-prefix',default=None); cmd.add_argument('--s3-endpoint'); cmd.add_argument('--s3-credentials')


def storage_for(args,home):
    # Same explicit OS-stored credentials as the broker's remote sync; never the boto credential chain.
    from .cli import remote_storage
    return remote_storage(args) or FilesystemStorage(home.root/'replica')


BROKER_LABEL='org.agentmesh.profile-log.broker'


def broker_config(plist=None):
    """--home and the R2 remote from the broker's own launchd definition, or {} when there is none to read.

    After `control consolidate` the supervisor runs the broker from <home>/control/units, and LaunchAgents holds
    no broker plist. Without that copy the CLI fell back to the local replica: a connector policy reached R2 only
    on the broker's next sync, after the gateway had already refused the approval for a missing policy.

    Only flag values are taken; the credentials flag is a keystore reference, never a secret."""
    import plistlib
    if plist is None:
        from . import settings
        for path in (settings.root()/'control'/'units'/(BROKER_LABEL+'.plist'),Path.home()/'Library'/'LaunchAgents'/(BROKER_LABEL+'.plist')):
            found=broker_config(path)
            if found: return found
        return {}
    path=Path(plist)
    try: argv=plistlib.loads(path.read_bytes()).get('ProgramArguments')
    except Exception: return {}
    if type(argv) is not list or not all(type(a) is str for a in argv): return {}
    flags={'--home':'home','--filesystem':'filesystem','--s3-bucket':'s3_bucket','--s3-prefix':'s3_prefix','--s3-endpoint':'s3_endpoint','--s3-credentials':'s3_credentials'}
    found={}
    for i,a in enumerate(argv[:-1]):
        if a in flags: found[flags[a]]=argv[i+1]
    return found


def fill_from_broker(args,config=None):
    """Missing --home and remote flags come from the broker, but only for the broker's own home:
    a test or scratch home never inherits the real remote."""
    config=broker_config() if config is None else config
    if getattr(args,'home',None) is None:
        from . import settings
        args.home=config.get('home') or str(settings.root()/'profile-log')
    explicit=getattr(args,'filesystem',None) or getattr(args,'s3_bucket',None)
    same=config.get('home') and Path(config['home']).expanduser().resolve()==Path(args.home).expanduser().resolve()
    if not explicit and same:
        for key in ('filesystem','s3_bucket','s3_endpoint','s3_credentials','s3_prefix'):
            if config.get(key) is not None and getattr(args,key,None) is None: setattr(args,key,config[key])
    if getattr(args,'s3_prefix',None) is None: args.s3_prefix=''
    return args


def add_parsers(commands):
    connector=commands.add_parser('connector'); operations=connector.add_subparsers(dest='action',required=True)
    for action in ('gateway-init','gateway-use','approve','pending','list','revoke','allow-secret'):
        cmd=operations.add_parser(action); cmd.add_argument('--home',default=None,help="default: the broker's home"); remote_flags(cmd)
        if action=='gateway-init': cmd.add_argument('--origin',required=True); cmd.add_argument('--public-key-file',required=True)
        if action=='gateway-use': cmd.add_argument('--origin',help='default: the one published gateway')
        if action=='approve':
            cmd.add_argument('code',nargs='?',help='the 6-digit code on the connector page; asked for when left out')
            cmd.add_argument('--profiles',help='comma-separated; default: the approval settings (personal,work)')
            cmd.add_argument('--remember',help='a profile (work) or its inbox (work/inbox); default: <first profile>/inbox')
        if action in ('revoke','allow-secret'): cmd.add_argument('--app',required=True)
        if action=='allow-secret': cmd.add_argument('--minutes',type=int,default=15)
    cmd=operations.add_parser('passkey'); actions=cmd.add_subparsers(dest='passkey_action',required=True)
    for action in ('add','list','remove','settings'):
        sub=actions.add_parser(action);sub.add_argument('--home',default=None);remote_flags(sub)
        if action=='add': sub.add_argument('--label',required=True)
        if action=='remove': sub.add_argument('credential_hash_prefix')
        if action=='settings':
            sub.add_argument('--rp-id');sub.add_argument('--origin');sub.add_argument('--profiles');sub.add_argument('--remember');sub.add_argument('--max-label',choices=['general','internal'])
    bridge=commands.add_parser('bridge'); operations=bridge.add_subparsers(dest='action',required=True)
    serve=operations.add_parser('serve'); serve.add_argument('--home',required=True); serve.add_argument('--host',default='127.0.0.1'); serve.add_argument('--port',type=int,default=8766); remote_flags(serve)
    render=operations.add_parser('render-service'); remote_flags(render); render.add_argument('--home',required=True); render.add_argument('--output',required=True); render.add_argument('--python',required=True); render.add_argument('--platform',choices=['macos','windows'],required=True); render.add_argument('--port',type=int,default=8766)


def execute(args,*,backend,console):
    # All connector writers share the bridge lock file: never restore stale ceremony state.
    if args.command=='connector': fill_from_broker(args)
    elif getattr(args,'s3_prefix',None) is None: args.s3_prefix=''
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
    if args.action=='gateway-use': return use_gateway(store,storage,args.origin)
    if args.action=='pending': return pending_requests(state,storage)
    if args.action=='approve':
        code=args.code
        if code is None and callable(getattr(console,'isatty',None)) and console.isatty():
            try: code=console.input('Code shown on the connector page: ')
            except (EOFError,KeyboardInterrupt): code=None
        profiles=[p for p in args.profiles.split(',') if p] if args.profiles else None
        return approve(home,store,storage,code,profiles,args.remember,console)
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
    ref=cr.publish(record); admin_request(state,'revoke',{'app_id':app,'policy_ref':ref},storage=storage)
    return {'app_id':app,'state':'revoked'}
