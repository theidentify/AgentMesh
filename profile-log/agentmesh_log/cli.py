"""JSON command-line interface for explicit broker administration and agent IPC."""
import argparse
import json
import math
import sys
import time
from pathlib import Path
from .encoding import wire, parse
from .identity import AgentIdentity
from .model import Label
from .broker.keystore import BrokerHome, BrokerState, FileKeyStore, atomic_write
from .broker.grants import Grant, resolve, names
from .broker.service import Broker
from .broker.client import BrokerClient
from .oskeystore import select_store, OsKeyStore, initialize, migrate
from .presence import PresenceManager, ConsolePresence


def emit(text):
    # UTF-8 bytes regardless of the console code page (Windows defaults to cp1252).
    sys.stdout.buffer.write(text.encode('utf-8') + b'\n'); sys.stdout.flush()


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error embeds arbitrary command-line values.
        emit('{"error":"invalid_request"}')
        raise SystemExit(2)


def parser():
    p=SafeParser(prog='agentmesh-log'); commands=p.add_subparsers(dest='command',required=True)
    broker=commands.add_parser('broker'); admin=broker.add_subparsers(dest='action',required=True)
    def home(cmd): cmd.add_argument('--home',required=True)
    def remote(cmd,required=False):
        group=cmd.add_mutually_exclusive_group(required=required)
        group.add_argument('--filesystem'); group.add_argument('--s3-bucket')
        cmd.add_argument('--s3-prefix',default=''); cmd.add_argument('--s3-endpoint'); cmd.add_argument('--s3-credentials')
    init=admin.add_parser('init'); home(init); init.add_argument('--profile',action='append',required=True)
    scope=admin.add_parser('scope'); scope_sub=scope.add_subparsers(dest='scope_action',required=True)
    add=scope_sub.add_parser('add'); home(add); add.add_argument('scope'); add.add_argument('--label',choices=list(Label.__members__),default='general')
    grant=admin.add_parser('grant'); home(grant); grant.add_argument('--name',required=True)
    grant.add_argument('--read',action='append',default=[]); grant.add_argument('--write',action='append',required=True)
    grant.add_argument('--default-write'); grant.add_argument('--allow-secret',action='store_true'); grant.add_argument('--allow-cross-profile',action='store_true')
    revoke=admin.add_parser('revoke'); home(revoke); revoke.add_argument('agent_id')
    run=admin.add_parser('run'); home(run); run.add_argument('--sync-interval',type=float); remote(run)
    sync=admin.add_parser('sync'); home(sync); remote(sync,True)
    recall=commands.add_parser('recall'); recall.add_argument('query'); recall.add_argument('--agent-key',required=True)
    recall.add_argument('--project'); recall.add_argument('--task'); recall.add_argument('--limit',type=int,default=12)
    recall.add_argument('--profile',action='append'); recall.add_argument('--scope',action='append')
    remember=commands.add_parser('remember'); remember.add_argument('content'); remember.add_argument('--agent-key',required=True)
    remember.add_argument('--scope'); remember.add_argument('--kind',default='fact'); remember.add_argument('--tag',action='append',default=[])
    remember.add_argument('--task')
    join=commands.add_parser('join'); home(join); remote(join,True); join.add_argument('--approver-fingerprint')
    pair=commands.add_parser('pair'); pair_sub=pair.add_subparsers(dest='action',required=True)
    for action in ('approve','regrant','recipient'):
        cmd=pair_sub.add_parser(action); home(cmd); remote(cmd,True); cmd.add_argument('identifier')
        cmd.add_argument('--scope',action='append',default=[]); cmd.add_argument('--fingerprint')
    device=commands.add_parser('device'); device_sub=device.add_subparsers(dest='action',required=True)
    listing=device_sub.add_parser('list'); home(listing)
    remove=device_sub.add_parser('remove'); home(remove); remote(remove); remove.add_argument('identifier')
    recovery=commands.add_parser('recovery'); recovery_sub=recovery.add_subparsers(dest='action',required=True)
    for action in ('create','test','use'):
        cmd=recovery_sub.add_parser(action); home(cmd); remote(cmd)
        cmd.add_argument('--share-file',action='append',default=[]); cmd.add_argument('--out'); cmd.add_argument('--account-file')
    profile=commands.add_parser('profile'); profile_sub=profile.add_subparsers(dest='action',required=True)
    for action in ('archive','delete'):
        cmd=profile_sub.add_parser(action); home(cmd); remote(cmd); cmd.add_argument('name'); cmd.add_argument('--confirm-name')
        cmd.add_argument('--share-file',action='append',default=[]); cmd.add_argument('--out')
    presence=commands.add_parser('presence'); presence_sub=presence.add_subparsers(dest='action',required=True)
    for action in ('open','status'):
        cmd=presence_sub.add_parser(action); home(cmd); cmd.add_argument('--class',dest='action_class',default='first_access')
        cmd.add_argument('--minutes',type=int,default=15)
    keystore=commands.add_parser('keystore'); key_sub=keystore.add_subparsers(dest='action',required=True)
    cmd=key_sub.add_parser('migrate'); home(cmd); cmd.add_argument('--to',choices=['os'],required=True); cmd.add_argument('--out')
    init.add_argument('--out'); init.add_argument('--file-keystore',action='store_true')
    grant.add_argument('--allow-import',action='store_true')
    recall.add_argument('--compat',action='store_true')
    migration=commands.add_parser('migrate'); operations=migration.add_subparsers(dest='action',required=True)
    for action in ('plan','run','rows','catch-up','report','verify'):
        cmd=operations.add_parser(action)
        cmd.add_argument('--db',required=action!='report')
        routing=cmd.add_mutually_exclusive_group(required=action in ('run','rows','catch-up','verify'))
        routing.add_argument('--mapping'); routing.add_argument('--settings',action='store_true')
        cmd.add_argument('--project-dir',action='append',default=[])
        if action!='plan': cmd.add_argument('--agent-key',required=True)
        if action in ('run','rows','catch-up'): cmd.add_argument('--take-over',action='store_true')
        if action=='rows': cmd.add_argument('--ref',action='append',required=True)
        if action=='report': cmd.add_argument('--out',required=True)
    terminal=commands.add_parser('admin'); terminal.add_argument('--menu',action='store_true'); terminal.add_argument('--filesystem')
    project=commands.add_parser('project'); project.add_argument('action',choices=['trust']); project.add_argument('path')
    apply_cmd=commands.add_parser('apply'); apply_cmd.add_argument('--dry-run',action='store_true'); apply_cmd.add_argument('--allow-unsupported-report',action='store_true'); apply_cmd.add_argument('--project-dir',action='append',default=[])
    settings_cmd=commands.add_parser('settings'); settings_cmd.add_argument('action',choices=['show','validate'])
    from .connector_cli import add_parsers
    add_parsers(commands)
    return p


def remote_storage(args, *, filesystem_factory=None):
    """Build a remote; an alternate filesystem factory can require an existing root."""
    if args.filesystem:
        from .storage.filesystem import FilesystemStorage
        return (filesystem_factory or FilesystemStorage)(args.filesystem)
    if args.s3_bucket:
        from .storage.s3 import S3Storage
        from .storage.r2creds import validate_remote, load_keychain_credentials
        validate_remote(args.s3_credentials,args.s3_endpoint)
        access,secret=load_keychain_credentials(args.s3_credentials.removeprefix('keychain:'))
        def factory(service,**kwargs):
            import boto3
            from botocore.config import Config
            # Bounded network time: sync holds the broker lock, and recall must not wait minutes on a stalled R2.
            config=Config(connect_timeout=5,read_timeout=20,retries=dict(max_attempts=3,mode='standard'))
            return boto3.client(service,**kwargs,config=config,aws_access_key_id=access,aws_secret_access_key=secret)
        return S3Storage(args.s3_bucket,prefix=args.s3_prefix,endpoint_url=args.s3_endpoint,client_factory=factory)
    return None


def execute(args,*,backend=None,console=None,input_fn=None):
    console=console or ConsolePresence(output=lambda text:print(text,file=sys.stderr))
    input_fn=input if input_fn is None else input_fn
    if args.command in ('connector','bridge'):
        from .connector_cli import execute as execute_connector
        return execute_connector(args,backend=backend,console=console)
    if args.command=='admin':
        from .tui import run_admin
        return run_admin(menu=args.menu,backend=backend,console=console,input_fn=input_fn,filesystem=args.filesystem)
    if args.command=='project':
        from .projects import trust
        return trust(args.path,console=console,backend=backend,output=lambda text:print(text,file=sys.stderr))
    if args.command=='apply':
        from .apply import apply_settings
        return apply_settings(dry_run=args.dry_run,allow_unsupported_report=args.allow_unsupported_report,console=console,backend=backend,project_dirs=args.project_dir,output=lambda text:print(text,file=sys.stderr))
    if args.command=='settings':
        from .settings import load
        value=load()
        return value if args.action=='show' else {'ok':True}
    if args.command=='migrate': return execute_migration(args,backend=backend)
    if args.command not in ('broker','recall','remember'):
        return execute_phase4(args,backend,console,input_fn)
    if args.command!='broker':
        client=BrokerClient.from_file(args.agent_key)
        try:
            if args.command=='recall':
                return client.request(dict(op='recall',query=args.query,project=args.project,task=args.task,
                    limit=args.limit,profiles=args.profile,scopes=args.scope,compat=args.compat))
            return client.write(dict(kind=args.kind,content=args.content),scope=args.scope,tags=args.tag,task=args.task)
        finally: client.close()
    home=BrokerHome(args.home); store=select_store(home,backend)
    if args.action=='run':
        if args.s3_bucket:
            from .storage.r2creds import validate_remote
            from .storage import validate_key
            validate_remote(args.s3_credentials,args.s3_endpoint); validate_key(args.s3_prefix,prefix=True)
            def remote(): return remote_storage(args)
            remote.backend='s3'
        else: remote=remote_storage(args)
        if args.sync_interval is not None and (remote is None or not math.isfinite(args.sync_interval) or args.sync_interval<=0):
            raise ValueError('invalid_request')
        def on_sync(row):
            from .broker.service import sync_status_valid
            if not sync_status_valid(row): raise ValueError('invalid sync status')
            atomic_write(home.root/'sync-status.json',wire(row))
            try:
                from . import status, settings
                status.synced(settings.root(),row)
            except Exception: pass
        def on_overview(value):
            from . import status, settings
            status.write(settings.root(),overview=value)
        broker=Broker(home,store=store)
        try: broker.serve(remote=remote,sync_interval=args.sync_interval,on_sync=on_sync,on_overview=on_overview)
        finally: broker.close()
        return {'ok':True}
    with home.lock():
        if args.action=='init':
            if store.path.exists(): raise ValueError('already initialized')
            shares=initialize(home,args.profile,backend=backend,file_keystore=args.file_keystore)
            return share_result(shares,args,home)
        state=store.load()
        if args.action=='scope':
            from .admin import create_scope
            create_scope(home,state,args.scope,Label[args.label])
            store.save(state)
            return {'ok':True}
        if args.action=='grant':
            PresenceManager(home,store).require('agent','grant',console=console); state=store.load()
            from .admin import create_agent
            path,grant=create_agent(home,state,args.name,args.read,args.write,args.default_write,
                args.allow_secret,args.allow_cross_profile,args.allow_import)
            store.save(state)
            return {'agent_key':str(path)}
        if args.action=='revoke':
            PresenceManager(home,store).require('agent','revoke',console=console); state=store.load()
            state.revoke(args.agent_id); store.save(state); return {'ok':True}
        if args.action=='sync':
            broker=Broker(home,store=store)
            try: return broker.sync_once(remote_storage(args))
            finally: broker.close()
    raise ValueError('invalid_request')



def execute_migration(args, *, backend=None):
    from .migrate.mapping import Mapping,plan
    from .migrate.run import run
    from .migrate.ledger import report,write_report
    from .status import caught_up
    mapping=None
    try:
        if args.action=='catch-up':
            import os
            bridge=Path(os.environ.get('AGENTMESH_PROFILE_LOG_BRIDGE',str(args.db)+'.profile-log.json'))
            if bridge.exists():
                config=parse(bridge.read_text(encoding='utf-8').encode('utf-8'))
                if config.get('format')!='agentmesh-profile-log-bridge-v1': raise ValueError('invalid bridge config')
                if config.get('enabled') is False or config.get('authority') is False:
                    result=dict(outcomes={},unmapped=[],skipped={},timestamp_fallback=0,busy_retries=0,errors=[],secret_labelled=0)
                    # Refresh counts without opening broker state on the disabled path.
                    try:
                        if getattr(args,'settings',False):
                            from .settings import load
                            from .apply import mapping_value
                            mapping=Mapping(mapping_value(load()))
                        elif args.mapping: mapping=Mapping.load(args.mapping)
                    except Exception: pass
                    caught_up(args.db,mapping,result,outcome='disabled')
                    return result
        if getattr(args,'settings',False):
            from .settings import load,root
            from .apply import open_home
            from .projects import effective_mapping
            value=load(); _,store=open_home(root(),backend)
            mapping=effective_mapping(value,store.load(),args.project_dir)
        else: mapping=Mapping.load(args.mapping) if args.mapping else None
        if args.action=='plan': return plan(args.db,mapping)
        binding=None
        if args.action in ('run','rows','catch-up','verify'):
            from .migrate.source import binding_for
            binding=binding_for(args.db)
        client=BrokerClient.from_file(args.agent_key)
        try:
            if args.action=='verify':
                from .migrate.verify import verify
                return verify(args.db,mapping,client,binding=binding)
            if args.action=='report':
                result=report(client,args.db,mapping)
                # Agent files are emitted under HOME/agents; reject replica destinations.
                home=Path(args.agent_key).resolve().parent.parent
                write_report(result,args.out,home/'replica')
                return dict(rows=len(result))
            cache=None
            if args.action=='catch-up':
                import os
                cache=Path(os.environ.get('AGENTMESH_PROFILE_LOG_CATCH_UP_CACHE',str(args.db)+'.profile-log.catch-up.json'))
            result=run(args.db,mapping,client,refs=getattr(args,'ref',None),take_over=args.take_over,cache=cache,binding=binding)
        finally: client.close()
        if args.action=='catch-up': caught_up(args.db,mapping,result)
        return result
    except Exception as exc:
        if args.action=='catch-up': caught_up(args.db,mapping,outcome='error',error=exc)
        raise


def share_result(shares,args,home):
    from .recovery import write_shares
    if getattr(args,'out',None):
        write_shares(shares,args.out,home.root,getattr(args,'filesystem',None) or home.root/'replica')
        return {'ok':True,'shares_written':3}
    return {'ok':True,'recovery_shares':shares}


class LazyStorage:
    def __init__(self,args,home): self.args,self.home,self.instance=args,home,None
    def __getattr__(self,name):
        if self.instance is None:
            from .storage.filesystem import FilesystemStorage
            self.instance=remote_storage(self.args) or FilesystemStorage(self.home.root/'replica')
        return getattr(self.instance,name)


def read_shares(args,input_fn):
    if args.share_file: return [Path(p).read_text(encoding='utf-8').strip() for p in args.share_file]
    import getpass
    return [getpass.getpass('Recovery share 1: '),getpass.getpass('Recovery share 2: ')]


def wait_object(storage,path,deadline):
    from .storage import ObjectNotFound
    while time.monotonic()<deadline:
        try: return parse(storage.get(path))
        except ObjectNotFound: time.sleep(.2)
    raise ValueError('pairing_expired')


def execute_phase4(args,backend,console,input_fn):
    from .encoding import parse
    from .storage.filesystem import FilesystemStorage
    from .keyring import sync_control,AgeKeyring,initialize_keyring
    from .registry_store import RegistryStore
    from .pairing import JoinRequest,Approver,approver_fingerprint,sas
    from .lifecycle import remove_device,profile_state,scopes_of
    from .recovery import create_recovery,test_recovery,use_recovery
    home=BrokerHome(args.home); store=select_store(home,backend); presence=PresenceManager(home,store)
    if args.command=='presence':
        if args.action=='open': presence.open(args.action_class,minutes=args.minutes,console=console)
        return {'active':presence.active(args.action_class),'action_class':args.action_class}
    if args.command=='keystore':
        return share_result(migrate(home,backend=backend,console=console),args,home)
    storage=LazyStorage(args,home) if hasattr(args,'filesystem') else FilesystemStorage(home.root/'replica')
    if args.command=='device':
        if args.action=='remove': remove_device(home,store,storage,args.identifier,console=console); return {'ok':True}
        registry=store.load().registry()
        return {'devices':[{'device_id':d,'revoked':d in registry.revocations} for d in sorted(registry.device_records)]}
    if args.command=='profile':
        state=store.load(); profile=resolve(state,args.name)
        confirmation=args.confirm_name
        if args.action=='delete' and confirmation is None and sys.stdin.isatty(): confirmation=input_fn('Type profile name: ')
        shares=profile_state(home,store,storage,profile,'deleted' if args.action=='delete' else 'archived',console=console,confirmation=confirmation,shares=lambda:read_shares(args,input_fn))
        return share_result(shares,args,home) if args.action=='delete' else {'ok':True}
    if args.command=='recovery':
        if args.action=='create': return share_result(create_recovery(home,store,storage,console=console),args,home)
        shares=lambda:read_shares(args,input_fn)
        if args.action=='test': return {'ok':test_recovery(home,store,storage,shares,console=console)}
        account=lambda:parse(Path(args.account_file).read_bytes()) if args.account_file else store.load().account.public_record()
        new=use_recovery(home,OsKeyStore(home,backend),storage,shares,account=account,console=console)
        return share_result(new,args,home)
    if args.command=='join':
        if store.path.exists() or (home.root/'keystore.sealed').exists(): raise ValueError('already initialized')
        join=JoinRequest(storage)
        print('Request: '+join.request_id,file=sys.stderr); join.render_qr(sys.stderr)
        deadline=time.monotonic()+600
        approval=wait_object(storage,join.prefix+'approver.json',deadline)
        account=parse(storage.get('registry/account.json')); registry=RegistryStore(storage,account).load()
        expected=args.approver_fingerprint or input_fn('Approver fingerprint from its screen: ')
        if expected!=approver_fingerprint(registry,approval['approver_device_id']): raise ValueError('pairing_mismatch')
        reveal=join.reveal(approval,registry); print('Pairing code: '+sas(join.commit,approval,reveal),file=sys.stderr)
        wait_object(storage,join.prefix+'result.json',deadline)
        join.finish(registry,home,OsKeyStore(home,backend))
        return {'ok':True}
    if args.command=='pair':
        presence.require('device',args.action,console=console)
        state=sync_control(home,store,storage); registry=state.registry()
        if args.action=='recipient':
            from .keyring import publish_recipient
            publish_recipient(home,store,storage,args.identifier)
            return {'ok':True}
        if args.action=='regrant':
            from .keyring import regrant
            regrant(state,storage,args.identifier,[resolve(state,name) for name in args.scope])
            return {'ok':True}
        print('Approver fingerprint: '+approver_fingerprint(registry,state.device.device_id),file=sys.stderr)
        approver=Approver(storage,state.device,registry,state.control.setdefault('pairing_used',{}),persist=lambda:store.save(state))
        approval=approver.begin(args.identifier)
        reveal=wait_object(storage,'pairing/'+args.identifier+'/reveal.json',time.monotonic()+600)
        approver.confirm(reveal,lambda code:input_fn('Code '+code+' matches joiner? Type yes: ')=='yes',fingerprint=args.fingerprint)
        presence.require('device','approve',console=console)
        scopes=[resolve(state,n) for n in args.scope]
        if not scopes: raise ValueError('pairing_empty')
        approver.grant(state,scopes)
        return {'ok':True}
    raise ValueError('invalid_request')

def main(argv=None,*,backend=None,console=None,input_fn=None):
    p=parser(); args=p.parse_args(argv)
    try:
        result=execute(args,backend=backend,console=console,input_fn=input_fn)
        emit(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 1 if 'error' in result or result.get('errors',0) else 0
    except KeyboardInterrupt: return 0
    except Exception as exc:
        code={'S3 credentials missing':'credentials_missing','S3 credentials refused':'credentials_refused',
              'staging binding missing or invalid; run apply':'staging_binding_missing',
              'staging binding device differs from broker':'staging_binding_device_mismatch',
              'staging node differs from binding':'staging_binding_node_mismatch',
              'staging identity missing; initialize ingest before apply':'staging_identity_missing',
              'invalid staging identity':'staging_identity_invalid'}.get(str(exc))
        if code is None: code=str(exc) if str(exc) in ('unknown_app','ambiguous_app','invalid_settings','presence_required','pairing_denied','pairing_expired','pairing_mismatch','pairing_reused') else 'unavailable'
        emit(json.dumps({'error':code})); return 1


if __name__=='__main__': sys.exit(main())
