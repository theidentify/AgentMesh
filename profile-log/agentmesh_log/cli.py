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
from .broker.client import BrokerClient, BrokerUnreachable
from .oskeystore import select_store, OsKeyStore, initialize, migrate
from .presence import PresenceManager, ConsolePresence


def emit(text):
    # UTF-8 bytes regardless of the console code page (Windows defaults to cp1252).
    if sys.stdout is not None:
        if hasattr(sys.stdout,'buffer'): sys.stdout.buffer.write(text.encode('utf-8',errors='backslashreplace')+b'\n')
        else: sys.stdout.write(text+'\n')
        sys.stdout.flush()


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error embeds arbitrary command-line values.
        emit('{"error":"invalid_request"}')
        raise SystemExit(2)


def parser():
    p=SafeParser(prog='agentmesh-log'); commands=p.add_subparsers(dest='command',required=True)
    credentials=commands.add_parser('r2-credentials'); credential_sub=credentials.add_subparsers(dest='action',required=True)
    for action in ('store','check'):
        cmd=credential_sub.add_parser(action); cmd.add_argument('--ref',required=True)
        cmd.add_argument('--kind',choices=('s3','access'),default='s3')
    broker=commands.add_parser('broker'); admin=broker.add_subparsers(dest='action',required=True)
    def home(cmd): cmd.add_argument('--home',required=True)
    def remote(cmd,required=False):
        group=cmd.add_mutually_exclusive_group(required=required)
        group.add_argument('--filesystem'); group.add_argument('--s3-bucket')
        cmd.add_argument('--s3-prefix',default=''); cmd.add_argument('--s3-endpoint'); cmd.add_argument('--s3-credentials')
        cmd.add_argument('--s3-credentials-kind',choices=('s3','s3-aws','s3-generic'),help='key format (default s3: R2)')
        cmd.add_argument('--s3-region'); cmd.add_argument('--s3-path-style',action='store_true',help='path-style addressing (MinIO)')
    init=admin.add_parser('init'); home(init); init.add_argument('--profile',action='append',required=True)
    scope=admin.add_parser('scope'); scope_sub=scope.add_subparsers(dest='scope_action',required=True)
    add=scope_sub.add_parser('add'); home(add); add.add_argument('scope'); add.add_argument('--label',choices=list(Label.__members__),default='general')
    grant=admin.add_parser('grant'); home(grant); grant.add_argument('--name',required=True)
    grant.add_argument('--read',action='append',default=[]); grant.add_argument('--write',action='append',required=True)
    grant.add_argument('--default-write'); grant.add_argument('--allow-secret',action='store_true'); grant.add_argument('--allow-cross-profile',action='store_true')
    revoke=admin.add_parser('revoke'); home(revoke); revoke.add_argument('agent_id')
    run=admin.add_parser('run'); home(run); run.add_argument('--sync-interval',type=float); run.add_argument('--log-dir'); remote(run)
    sync=admin.add_parser('sync',help="without remote flags, uses the home's remote.json"); home(sync); remote(sync)
    stats=commands.add_parser('stats'); stats.add_argument('--agent-key',required=True)
    recall=commands.add_parser('recall'); recall.add_argument('query'); recall.add_argument('--agent-key',required=True)
    recall.add_argument('--project'); recall.add_argument('--task'); recall.add_argument('--limit',type=int,default=12)
    recall.add_argument('--profile',action='append'); recall.add_argument('--scope',action='append')
    recall.add_argument('--mode',choices=('keyword','hybrid'))
    recall.add_argument('--include-pending',action='store_true',help='also show staged, rejected and over-quota events')
    review_cmd=commands.add_parser('review',help='approve or reject a staged event (needs presence at this device)')
    review_cmd.add_argument('target'); review_cmd.add_argument('--verdict',choices=('approved','rejected'),required=True)
    review_cmd.add_argument('--agent-key',required=True)
    recall.add_argument('--per-profile',action='store_true',help='one request per --profile, merged like the bridge (no cross-profile grant needed)')
    memory=commands.add_parser('memory'); tree=memory.add_subparsers(dest='action',required=True)
    for action in ('ls','cat','grep'):
        cmd=tree.add_parser(action); cmd.add_argument('--agent-key',required=True)
        if action=='grep': cmd.add_argument('query'); cmd.add_argument('--limit',type=int,default=12); cmd.add_argument('--mode',choices=('keyword','hybrid'))
        cmd.add_argument('path',nargs='?' if action!='cat' else None,default='/')
    tree.choices['ls'].add_argument('--depth',type=int,choices=(1,2),default=1)
    tree.choices['cat'].add_argument('--json',action='store_true',help='full response instead of markdown')
    build=tree.add_parser('build',help='rebuild missing or stale memory tree pages (needs an allow_pages agent)')
    build.add_argument('--agent-key',required=True); build.add_argument('--scope',action='append')
    build.add_argument('--max-pages',type=int); build.add_argument('--dry-run',action='store_true')
    remember=commands.add_parser('remember'); remember.add_argument('content'); remember.add_argument('--agent-key',required=True)
    remember.add_argument('--scope'); remember.add_argument('--kind',default='fact'); remember.add_argument('--tag',action='append',default=[])
    remember.add_argument('--task')
    tasks_cmd=commands.add_parser('tasks',help='list shared task state'); tasks_cmd.add_argument('--agent-key',required=True)
    tasks_cmd.add_argument('--scope'); tasks_cmd.add_argument('--task'); tasks_cmd.add_argument('--status',action='append')
    tasks_cmd.add_argument('--mine',action='store_true')
    task_cmd=commands.add_parser('task',help='create, claim or update a shared task')
    task_cmd.add_argument('action',choices=('create','claim','update','override','note')); task_cmd.add_argument('task')
    task_cmd.add_argument('--agent-key',required=True); task_cmd.add_argument('--scope',required=True)
    for flag in ('--project','--title','--next','--handoff','--status','--label'): task_cmd.add_argument(flag)
    task_cmd.add_argument('--blocked-on',action='append'); task_cmd.add_argument('--claim',action='store_true')
    task_cmd.add_argument('--lease-minutes',type=int)
    task_cmd.add_argument('--text',help='note text (action note)'); task_cmd.add_argument('--to',help='address a note to an account or agent ID')
    notes_cmd=commands.add_parser('notes',help="read a task's notes"); notes_cmd.add_argument('task'); notes_cmd.add_argument('--agent-key',required=True)
    notes_cmd.add_argument('--scope'); notes_cmd.add_argument('--limit',type=int,default=50); notes_cmd.add_argument('--include-pending',action='store_true')
    ws=commands.add_parser('workspace',help='workspace registry: members, roles and settings (presence required to change)')
    ws_sub=ws.add_subparsers(dest='action',required=True)
    for action in ('init','show'):
        cmd=ws_sub.add_parser(action); home(cmd); cmd.add_argument('--profile',required=True)
    member=ws_sub.add_parser('member'); home(member); member.add_argument('--profile',required=True)
    member.add_argument('--account',required=True); member.add_argument('--role',choices=('owner','admin','member','contributor','none'),required=True)
    member.add_argument('--out',help='write the signed entry for another owner to co-sign instead of publishing it')
    member.add_argument('--root-fingerprint',help="pin another account's root key (fingerprint checked out of band)")
    account=ws_sub.add_parser('account',help='this account and its fingerprint, for others to pin'); home(account)
    join=ws_sub.add_parser('join',help="trust another account's workspace (after broker sync)"); home(join)
    join.add_argument('--profile-id'); join.add_argument('--owner-account'); join.add_argument('--owner-fingerprint')
    join.add_argument('--invite',help='invite token file (gives the three values above and requests membership)')
    inv=ws_sub.add_parser('invite',help='signed invites: create, list, revoke, accept joiners'); inv_sub=inv.add_subparsers(dest='invite_action',required=True)
    a=inv_sub.add_parser('create'); home(a); a.add_argument('--profile',required=True); a.add_argument('--scope',action='append',required=True)
    a.add_argument('--role',choices=('member','contributor'),default='member'); a.add_argument('--max-uses',type=int,default=1)
    a.add_argument('--days',type=int,default=7); out=a.add_mutually_exclusive_group(required=True); out.add_argument('--out')
    out.add_argument('--link',help='one-click link file for one person (agentmesh://join#…; it holds a secret, no storage credential)')
    a.add_argument('--label',help='who the link is for, shown to both sides'); remote(a)
    a=inv_sub.add_parser('list'); home(a); a.add_argument('--profile',required=True)
    a=inv_sub.add_parser('accept'); home(a); a.add_argument('--profile',required=True)
    request=a.add_mutually_exclusive_group(); request.add_argument('--request',help='one-click join request slot')
    request.add_argument('--request-file',help='a join request sent as a file')
    a.add_argument('--member-credentials',default=None,help="prompt, none, or file:PATH (JSON with kind, access_key_id, secret_access_key)")
    a.add_argument('--answer-out',help='write the answer to this file (file channel)'); remote(a)
    a=inv_sub.add_parser('requests',help='one-click join requests waiting for approval'); home(a); a.add_argument('--profile',required=True)
    a.add_argument('--request-file',action='append',default=[]); remote(a)
    a=inv_sub.add_parser('reject'); home(a); a.add_argument('--profile',required=True)
    request=a.add_mutually_exclusive_group(required=True); request.add_argument('--request'); request.add_argument('--request-file')
    a.add_argument('--reason'); a.add_argument('--answer-out'); remote(a)
    jl=ws_sub.add_parser('join-link',help='join a workspace from a one-click link: new home, request, wait, sync, agent, rules')
    jl.add_argument('--link-file',required=True,help="a file holding the link (never pass it on the command line)")
    jl.add_argument('--home',help='default: ~/.agentmesh/ws/<id8>'); jl.add_argument('--recovery-out')
    jl.add_argument('--folder',help='the shared folder, for a folder-channel workspace'); jl.add_argument('--request-out')
    jl.add_argument('--answer-file'); jl.add_argument('--wait',type=float,default=0.0,help='seconds to keep waiting for the admin')
    jl.add_argument('--agent-name'); jl.add_argument('--no-rules',action='store_true')
    jl.add_argument('--project',action='append',help='a project folder that belongs to this workspace (agent rules)')
    a=inv_sub.add_parser('revoke'); home(a); a.add_argument('--profile',required=True); a.add_argument('--id',required=True)
    clink=ws_sub.add_parser('chat-link',help='link a chat user to a workspace account (admin, presence)'); home(clink)
    clink.add_argument('--profile',required=True); clink.add_argument('--platform',choices=('slack','discord','teams','line'),required=True)
    clink.add_argument('--user',required=True); clink.add_argument('--account',required=True,help="account ID, or 'none' to unlink")
    adapter=commands.add_parser('adapter',help='run a chat adapter on this device'); adapter_sub=adapter.add_subparsers(dest='action',required=True)
    a=adapter_sub.add_parser('slack',help='Slack Socket Mode (tokens from AGENTMESH_SLACK_APP_TOKEN / AGENTMESH_SLACK_BOT_TOKEN)'); home(a)
    a.add_argument('--config',required=True); a.add_argument('--agent-key',required=True)
    wgrant=ws_sub.add_parser('grant',help="give a member's device the keys of workspace scopes"); home(wgrant)
    wgrant.add_argument('--device',required=True); wgrant.add_argument('--scope',action='append',required=True)
    setting=ws_sub.add_parser('settings'); home(setting); setting.add_argument('--profile',required=True)
    setting.add_argument('--lease-default-minutes',type=int); setting.add_argument('--lease-max-minutes',type=int)
    setting.add_argument('--review-staged',choices=('on','off')); setting.add_argument('--direct-agent',action='append')
    setting.add_argument('--no-direct-agents',action='store_true')
    setting.add_argument('--quota-agent',type=int,help='events per agent per scope per day')
    setting.add_argument('--quota-member',type=int,help='events per member per scope per day')
    setting.add_argument('--inbox-scope',action='append',help='a scope contributors may write into')
    setting.add_argument('--no-inbox-scopes',action='store_true')
    setting.add_argument('--out')
    cosign_cmd=ws_sub.add_parser('cosign'); home(cosign_cmd); cosign_cmd.add_argument('--in',dest='input',required=True); cosign_cmd.add_argument('--out',required=True)
    publish_cmd=ws_sub.add_parser('publish'); home(publish_cmd); publish_cmd.add_argument('--in',dest='input',required=True)
    attest=ws_sub.add_parser('attest',help='email attestation of members (admin-signed or OIDC)')
    attest_sub=attest.add_subparsers(dest='attest_action',required=True)
    a=attest_sub.add_parser('admin',help='vouch for an account after checking the person directly'); home(a)
    a.add_argument('--profile',required=True); a.add_argument('--account',required=True)
    a.add_argument('--channel',choices=('in_person','video','code'),required=True); a.add_argument('--email')
    a=attest_sub.add_parser('oidc-login',help='member: sign in with the email provider and write a token file'); home(a)
    a.add_argument('--profile',required=True); a.add_argument('--issuer',required=True); a.add_argument('--client-id',required=True)
    a.add_argument('--client-secret-file'); a.add_argument('--out',required=True)
    a=attest_sub.add_parser('oidc',help='admin: verify a member token file and sign the attestation'); home(a)
    a.add_argument('--in',dest='input',required=True); a.add_argument('--issuer',required=True); a.add_argument('--client-id',required=True)
    for action in ('list','check'):
        a=attest_sub.add_parser(action); home(a); a.add_argument('--profile',required=True)
        if action=='check': a.add_argument('--email')
    join=commands.add_parser('join'); home(join); remote(join,True); join.add_argument('--approver-fingerprint')
    pair=commands.add_parser('pair'); pair_sub=pair.add_subparsers(dest='action',required=True)
    for action in ('approve','regrant','recipient'):
        cmd=pair_sub.add_parser(action); home(cmd); remote(cmd,True); cmd.add_argument('identifier')
        cmd.add_argument('--scope',action='append',default=[]); cmd.add_argument('--fingerprint')
    device=commands.add_parser('device'); device_sub=device.add_subparsers(dest='action',required=True)
    listing=device_sub.add_parser('list'); home(listing)
    remove=device_sub.add_parser('remove'); home(remove); remote(remove); remove.add_argument('identifier')
    dclass=device_sub.add_parser('class',help='state the class of another device of this account'); home(dclass)
    dclass.add_argument('identifier'); dclass.add_argument('--class',dest='device_class',choices=('workstation','server','write-only'),required=True)
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
    grant.add_argument('--allow-import',action='store_true'); grant.add_argument('--allow-pages',action='store_true'); grant.add_argument('--allow-summarize',action='store_true')
    recall.add_argument('--compat',action='store_true')
    summarizer=commands.add_parser('summary'); steps=summarizer.add_subparsers(dest='action',required=True)
    delete=steps.add_parser('delete',help='confirm a quarantined deletion (typed presence)')
    delete.add_argument('--agent-key',required=True); delete.add_argument('--scope',required=True); delete.add_argument('--memory-key',required=True)
    for action in ('related','write'):
        cmd=steps.add_parser(action); cmd.add_argument('--agent-key',required=True)
        routing=cmd.add_mutually_exclusive_group(required=True)
        routing.add_argument('--mapping'); routing.add_argument('--settings',action='store_true')
        cmd.add_argument('--project-dir',action='append',default=[])
    ingest=commands.add_parser('ingest',help='the scheduled transcript jobs (personal home only)')
    ingest_sub=ingest.add_subparsers(dest='action',required=True)
    for action in ('scan','summary'):
        cmd=ingest_sub.add_parser(action); cmd.add_argument('--config')
        if action=='summary': cmd.add_argument('--force',action='store_true')
    cmd=ingest_sub.add_parser('migrate-config',help="drop memory-sync's runtime and point summary.command at the package (dry run unless --apply)")
    cmd.add_argument('--config'); cmd.add_argument('--apply',action='store_true')
    job=commands.add_parser('job',help='run one scheduled job with maintenance and quota holds and failure alerts')
    job.add_argument('action',choices=['run']); job.add_argument('rest',nargs=argparse.REMAINDER)
    terminal=commands.add_parser('admin'); terminal.add_argument('--menu',action='store_true'); terminal.add_argument('--filesystem')
    project=commands.add_parser('project'); project.add_argument('action',choices=['trust']); project.add_argument('path')
    apply_cmd=commands.add_parser('apply'); apply_cmd.add_argument('--dry-run',action='store_true'); apply_cmd.add_argument('--allow-unsupported-report',action='store_true'); apply_cmd.add_argument('--project-dir',action='append',default=[])
    settings_cmd=commands.add_parser('settings'); settings_cmd.add_argument('action',choices=['show','validate'])
    rules=commands.add_parser('rules',help='agent recall rules'); rules_sub=rules.add_subparsers(dest='action',required=True)
    a=rules_sub.add_parser('install',help="write each agent's read-only recall rules and argv manifest")
    a.add_argument('--agent-key',required=True,help='the recall agent key (read grants only)')
    a.add_argument('--profile',action='append',required=True,help='a profile to search (repeat)')
    a.add_argument('--agents',nargs='+',choices=('hermes','omp','codex','claude'))
    a.add_argument('--python',help='the interpreter agents run (default: this one)')
    a.add_argument('--home'); a.add_argument('--hermes-home'); a.add_argument('--dry-run',action='store_true')
    from .control.cli import add_parsers as add_control_parsers
    add_control_parsers(commands)
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
        from .storage.r2creds import validate_remote, load_credentials
        validate_remote(args.s3_credentials,args.s3_endpoint)
        kind=getattr(args,'s3_credentials_kind',None)
        access,secret=load_credentials(args.s3_credentials,**({'kind':kind} if kind and kind!='s3' else {}))
        path_style=bool(getattr(args,'s3_path_style',False))
        def factory(service,**kwargs):
            import boto3
            from botocore.config import Config
            # Bounded network time: sync holds the broker lock, and recall must not wait minutes on a stalled R2.
            config=Config(connect_timeout=5,read_timeout=20,retries=dict(max_attempts=3,mode='standard'),signature_version='s3v4',
                          **({'s3':{'addressing_style':'path'}} if path_style else {}))
            return boto3.client(service,**kwargs,config=config,aws_access_key_id=access,aws_secret_access_key=secret)
        return S3Storage(args.s3_bucket,prefix=args.s3_prefix,endpoint_url=args.s3_endpoint,client_factory=factory,
                         region_name=getattr(args,'s3_region',None) or 'auto')
    return None


REMOTE_FIELDS=('filesystem','s3_bucket','s3_prefix','s3_endpoint','s3_credentials','s3_credentials_kind','s3_region','s3_path_style')


def home_remote(args,home):
    """Fill missing remote flags from ``<home>/remote.json`` (written by one-click join; it names a keystore
    reference, never a secret). Explicit flags win."""
    if getattr(args,'filesystem',None) or getattr(args,'s3_bucket',None) or getattr(home,'root',None) is None: return args
    try: value=json.loads((Path(home.root)/'remote.json').read_text(encoding='utf-8'))
    except FileNotFoundError: return args
    if type(value) is not dict or not set(value)<=set(REMOTE_FIELDS)|{'layout'}: raise ValueError('invalid remote.json')
    for key in REMOTE_FIELDS:
        if key in value: setattr(args,key,value[key])
    if getattr(args,'s3_prefix',None) is None: args.s3_prefix=''
    return args


def merge_profiles(answers,limit):
    """Interleave per-profile ranks, deduplicate IDs and sum candidate counts (memory-sync's broker_recall did the same)."""
    for answer in answers:
        if 'error' in answer: return answer
    merged=dict(answers[0]); merged['results']=[]; merged['evidence']=[]; merged['candidate_counts']={}; seen=set()
    for rank in range(max(len(a['results']) for a in answers)):
        for answer in answers:
            if rank<len(answer['results']) and answer['results'][rank]['id'] not in seen:
                merged['results'].append(answer['results'][rank]); seen.add(answer['results'][rank]['id'])
    merged['results']=merged['results'][:limit]
    for answer in answers:
        for key,count in answer['candidate_counts'].items(): merged['candidate_counts'][key]=merged['candidate_counts'].get(key,0)+count
    return merged


def execute(args,*,backend=None,console=None,input_fn=None):
    if args.command=='broker' and args.action=='run' and args.log_dir is not None:
        directory=Path(args.log_dir)
        if not directory.is_absolute() or not directory.is_dir(): raise ValueError('invalid_request')
        stdout=directory.joinpath('broker.out.log').open('a',encoding='utf-8',errors='backslashreplace',buffering=1)
        try: stderr=directory.joinpath('broker.err.log').open('a',encoding='utf-8',errors='backslashreplace',buffering=1)
        except Exception:
            stdout.close(); raise
        sys.stdout,sys.stderr=stdout,stderr
    if args.command=='r2-credentials':
        from .storage.r2creds import credentials_command
        return credentials_command(args.action,args.ref,kind=args.kind)
    console=console or ConsolePresence(output=lambda text:print(text,file=sys.stderr))
    input_fn=input if input_fn is None else input_fn
    if args.command in ('connector','bridge'):
        from .connector_cli import execute as execute_connector
        return execute_connector(args,backend=backend,console=console)
    if args.command=='control':
        from .control.cli import execute as execute_control
        return execute_control(args,console=console)
    if args.command=='admin':
        from .tui import run_admin
        return run_admin(menu=args.menu,backend=backend,console=console,input_fn=input_fn,filesystem=args.filesystem)
    if args.command=='project':
        from .projects import trust
        return trust(args.path,console=console,backend=backend,output=lambda text:print(text,file=sys.stderr))
    if args.command=='apply':
        from .apply import apply_settings
        return apply_settings(dry_run=args.dry_run,allow_unsupported_report=args.allow_unsupported_report,console=console,backend=backend,project_dirs=args.project_dir,output=lambda text:print(text,file=sys.stderr))
    if args.command=='rules':
        from .sharedrules import install
        try: written=install(agent_key=args.agent_key,profiles=args.profile,python=args.python,home=args.home,agents=args.agents,
                             hermes_home=args.hermes_home,dry_run=args.dry_run)
        except ValueError as exc: return {'error':'refused','reason':str(exc)}   # local paths only; nothing is written
        return {'ok':True,'dry_run':args.dry_run,'rules':{agent:str(path) for agent,path in written.items()}}
    if args.command=='settings':
        from .settings import load
        value=load()
        return value if args.action=='show' else {'ok':True}
    if args.command=='workspace': return execute_workspace(args,backend=backend,console=console,input_fn=input_fn)
    if args.command=='adapter': return execute_adapter(args,backend=backend)
    if args.command=='summary': return execute_summary(args,backend=backend)
    if args.command=='ingest':
        from .ingest import production
        return production.migrate_config(args.config,apply=args.apply)
    if args.command not in ('broker','recall','remember','memory','stats','tasks','task','review','notes'):
        return execute_phase4(args,backend,console,input_fn)
    if args.command!='broker':
        client=BrokerClient.from_file(args.agent_key)
        try:
            if args.command=='memory':
                if args.action=='build': return build_pages(client,args)
                request=dict(op=args.action,path=args.path)
                if args.action=='ls': request['depth']=args.depth
                if args.action=='grep': request.update(query=args.query,limit=args.limit,**({'mode':args.mode} if args.mode else {}))
                return client.request(request)
            if args.command=='stats': return client.request(dict(op='stats'))
            if args.command=='review':
                from .broker.client import sign_prepared
                response=client.request(dict(op='review',target=args.target,verdict=args.verdict))
                return response if 'error' in response else sign_prepared(client,response,None)
            if args.command=='tasks':
                return client.request(dict(op='tasks',**{k:v for k,v in dict(scope=args.scope,task=args.task,status=args.status).items() if v},
                                           **({'mine':True} if args.mine else {})))
            if args.command=='notes':
                return client.request(dict(op='notes',task=args.task,limit=args.limit,**({'scope':args.scope} if args.scope else {}),
                                           **({'include_pending':True} if args.include_pending else {})))
            if args.command=='task' and args.action=='note':
                from .broker.client import sign_prepared
                response=client.request(dict(op='note',task=args.task,scope=args.scope,text=args.text,to=args.to))
                return response if 'error' in response else sign_prepared(client,response,None)
            if args.command=='task':
                fields={k:v for k,v in dict(project=args.project,title=args.title,next=args.next,handoff=args.handoff,
                        status=args.status,label=args.label,blocked_on=args.blocked_on).items() if v is not None}
                if args.claim: fields['claim']=True
                if args.lease_minutes is not None: fields['lease_ms']=args.lease_minutes*60000
                return client.task(args.action,args.task,scope=args.scope,**fields)
            if args.command=='recall':
                request=dict(op='recall',query=args.query,project=args.project,task=args.task,
                    limit=args.limit,profiles=args.profile,scopes=args.scope,compat=args.compat,
                    **({'mode':args.mode} if args.mode else {}),**({'include_pending':True} if args.include_pending else {}))
                if not args.per_profile: return client.request(request)
                if not args.profile or len(set(args.profile))!=len(args.profile) or args.scope: raise ValueError('invalid_request')
                return merge_profiles([client.request({**request,'profiles':[p]}) for p in args.profile],args.limit)
            return client.write(dict(kind=args.kind,content=args.content),scope=args.scope,tags=args.tag,task=args.task)
        finally: client.close()
    home=BrokerHome(args.home); store=select_store(home,backend)
    if args.action in ('run','sync'): home_remote(args,home)
    if args.action=='sync' and not (args.filesystem or args.s3_bucket): raise ValueError('invalid_request')
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
                status.synced(status.base_for(home.root),row)
            except Exception: pass
        def on_overview(sections):
            from . import status, settings
            status.write(status.base_for(home.root),**sections)
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
            from .registry_store import device_class,RegistryStore
            from .storage.filesystem import FilesystemStorage
            local_registry=RegistryStore(FilesystemStorage(home.root/'replica'),state.account.public_record()).load()
            if args.allow_secret and device_class(local_registry,state.device.device_id)!='workstation':
                raise ValueError('denied')   # secret windows need a person at the device
            from .admin import create_agent
            path,grant=create_agent(home,state,args.name,args.read,args.write,args.default_write,
                args.allow_secret,args.allow_cross_profile,args.allow_import,args.allow_pages,args.allow_summarize)
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



def execute_summary(args, *, backend=None):
    """One summarizer step for the transcript summary (agentmesh_log.ingest); the request is JSON on stdin, never on the
    command line. related and write serve personal transcripts only, so a workspace home's key is refused."""
    from .migrate.mapping import Mapping
    from . import summarize
    if args.action=='delete':
        from .presence import ConsolePresence
        console=ConsolePresence(output=lambda text:print(text,file=sys.stderr))
        if not console.confirm('delete','Hide memory %s in %s (a revision with status deleted; history stays).'%(args.memory_key,args.scope)):
            return {'error':'presence_required'}
        client=BrokerClient.from_file(args.agent_key)
        try: return summarize.delete(client,args.scope,args.memory_key)
        finally: client.close()
    from .ingest import personal
    try: personal.check(agent_key=args.agent_key)
    except ValueError: raise ValueError('workspace_refused') from None
    raw=sys.stdin.buffer.read(8*1024*1024+1)
    if len(raw)>8*1024*1024: raise ValueError('invalid_request')
    try: request=parse(raw)
    except Exception: raise ValueError('invalid_request') from None
    if not summarize.valid_request(request,args.action): raise ValueError('invalid_request')
    if args.settings:
        from .settings import load,root
        from .apply import open_home
        from .projects import effective_mapping
        _,store=open_home(root(),backend)
        mapping=effective_mapping(load(),store.load(),args.project_dir)
    else: mapping=Mapping.load(args.mapping)
    client=BrokerClient.from_file(args.agent_key)
    try:
        if args.action=='related':
            context=summarize.related(client,mapping,request['project'],request['sessions'])
            return dict(related=context,digest=summarize.digest(context))
        try: return summarize.write(client,mapping,request)
        except summarize.Stale: return dict(status='stale')
    finally: client.close()


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


def execute_workspace(args,*,backend=None,console=None,input_fn=input):
    """Workspace registry administration on this device. Every change requires presence (class `workspace`)."""
    from . import workspace
    from .storage.filesystem import FilesystemStorage
    if args.action=='join-link':
        from .joinflow import join_link
        return join_link(args,backend=backend,console=console)
    home=BrokerHome(args.home); store=select_store(home,backend); presence=PresenceManager(home,store)
    local=FilesystemStorage(home.root/'replica')
    def profile_id(state,name):
        sid=resolve(state,name)
        if next(s for t in state.trees for s in t.scopes.values() if s.scope_id==sid).kind!='profile': raise ValueError('invalid_request')
        return sid
    def current(state,ws_id):
        registry=registry_of(local,state)
        owner=registry.profile_owner(ws_id) if hasattr(registry,'profile_owner') else state.account.account_id
        return workspace.load(local,ws_id,registry,owner)
    def finish(entry,out):
        if out:
            Path(out).write_text(json.dumps(entry,sort_keys=True),encoding='utf-8'); return {'ok':True,'written':out,'hash':workspace.entry_hash(entry)}
        ident=workspace.publish(local,entry); return {'ok':True,'hash':ident,'note':'published locally; the broker sync uploads it'}
    if args.action=='attest':
        if args.attest_action in ('admin','oidc'): refuse_unattended(registry_of(local,store.load()),store.load())
        return execute_attest(args,home,store,presence,local,profile_id,current,input_fn,console)
    with home.lock():
        state=store.load()
        if args.action not in ('show','account','join'): refuse_unattended(registry_of(local,state),state)
        if args.action in ('cosign','publish'):
            entry=json.loads(Path(args.input).read_text(encoding='utf-8')); workspace.shape(entry)
            ws=current(state,entry['workspace'])
            if entry['previous']!=ws.head: raise ValueError('stale')  # the workspace changed since this entry was signed
            what=(f"set role of {entry['subject']} to {entry['role'] or 'none'}" if entry['op']=='member'
                  else f"settings {json.dumps(entry['settings'],sort_keys=True)}")
            if args.action=='cosign':
                presence.require('workspace','approve',console=console,one_shot=True,summary='Co-sign workspace change: '+what+'.')
                return finish(workspace.cosign(entry,state.device),args.out)
            check=workspace.verified(registry_of(local,state),entry)
            if check is None or not workspace.allowed(ws.state,entry,*check): raise ValueError('denied')
            presence.require('workspace','apply',console=console,one_shot=True,summary='Publish workspace change: '+what+'.')
            return finish(entry,None)
        if args.action in ('account','join','grant'): return execute_federation(args,home,store,presence,local,state,console)
        if args.action=='invite': return execute_invite(args,home,store,presence,local,state,console,profile_id,current)
        if args.action=='chat-link':
            from .adapters import links
            from .registry_store import now
            ws_id=profile_id(state,args.profile); ws=current(state,ws_id)
            if not ws.exists: raise ValueError('not_found')
            if ws.role(state.account.account_id,now()) not in workspace.MANAGERS: raise ValueError('denied')
            account=None if args.account=='none' else args.account
            if account is not None and account not in ws.state.members: raise ValueError('denied')   # members only
            presence.require('workspace','grant',console=console,one_shot=True,
                             summary=f'Link {args.platform} user {args.user} to account {args.account}.')
            links.publish(local,links.create(state.device,ws_id,args.platform,args.user,account))
            return {'ok':True,'note':'published locally; the broker sync uploads it'}
        ws_id=profile_id(state,args.profile); ws=current(state,ws_id)
        if args.action=='show': return workspace.describe(ws)
        if args.action=='init':
            if ws.exists: raise ValueError('denied')
            presence.require('workspace','create',console=console,one_shot=True,summary='Create a workspace for profile '+args.profile+' with this account as owner.')
            from . import federation
            federation.publish_account(local,state.account.public_record())   # lets members pin this owner
            return finish(workspace.create(state.device,ws_id,'genesis',subject=state.account.account_id,role='owner'),None)
        if not ws.exists: raise ValueError('not_found')
        if args.action=='member':
            role=None if args.role=='none' else args.role
            presence.require('workspace','grant' if role else 'remove',console=console,one_shot=True,
                             summary=f'Set workspace role of {args.account} to {args.role}.')
            root=None
            if args.root_fingerprint:
                from . import federation
                # Pin the other account's root key, checked against the fingerprint its owner gave out of band.
                try: published=federation.read_account(local,args.account)
                except (ValueError,KeyError): raise ValueError('not_found') from None
                if federation.fingerprint(published['root_public_key'])!=args.root_fingerprint.strip().lower(): raise ValueError('denied')
                if role is None: raise ValueError('invalid_request')
                root=published['root_public_key']
            entry=workspace.create(state.device,ws_id,'member',previous=ws.head,subject=args.account,role=role,account_root=root)
        else:
            settings={}
            if args.lease_default_minutes is not None: settings['task_lease.default_ms']=args.lease_default_minutes*60000
            if args.lease_max_minutes is not None: settings['task_lease.max_ms']=args.lease_max_minutes*60000
            if args.review_staged is not None: settings['review.staged']=1 if args.review_staged=='on' else 0
            if args.quota_agent is not None: settings['quota.agent_events_per_day']=args.quota_agent
            if args.quota_member is not None: settings['quota.member_events_per_day']=args.quota_member
            settings={**ws.state.settings,**settings}
            if args.no_direct_agents: settings.pop('review.direct_agents',None)
            if args.no_inbox_scopes: settings.pop('inbox.scopes',None)
            if args.inbox_scope:
                ids=[resolve(state,n) for n in args.inbox_scope]
                if any(next(x for t in state.trees for x in t.scopes.values() if x.scope_id==i).profile_id!=ws_id for i in ids):
                    raise ValueError('invalid_request')   # inbox scopes must belong to this workspace
                settings['inbox.scopes']=sorted(set(settings.get('inbox.scopes',[]))|set(ids))
            if args.direct_agent: settings['review.direct_agents']=sorted(set(settings.get('review.direct_agents',[]))|set(args.direct_agent))
            if not workspace.settings_valid(settings): raise ValueError('invalid_request')
            presence.require('workspace','apply',console=console,one_shot=True,summary='Change workspace settings.')
            entry=workspace.create(state.device,ws_id,'settings',previous=ws.head,settings=settings)
        # Refuse locally what replay would ignore, so a mistake is caught before it is shared.
        check=workspace.verified(registry_of(local,state),entry)
        if check is None: raise ValueError('denied')
        if not args.out and not workspace.allowed(ws.state,entry,*check): raise ValueError('denied')
        result=finish(entry,args.out)
        if args.action=='member' and not args.out:
            result.update(rotate_after_removal(store,state,local,ws_id,ws.state.members.get(args.account),role))
        return result


def execute_attest(args,home,store,presence,local,profile_id,current,input_fn,console):
    """Email attestations. The email is read from --email or a prompt and is never stored or printed."""
    from . import attest
    import os, secrets
    def email():
        value=args.email if getattr(args,'email',None) else input_fn('Email address: ')
        return attest.normalize(value)
    action=args.attest_action
    if action=='oidc-login':
        state=store.load(); ws_id=profile_id(state,args.profile)
        secret=Path(args.client_secret_file).read_text(encoding='utf-8').strip() if args.client_secret_file else None
        seed=attest._b64(secrets.token_bytes(24)); account=state.account.account_id
        token=attest.login(args.issuer,args.client_id,attest.nonce(ws_id,account,seed),client_secret=secret,
                           output=lambda text:print(text,file=sys.stderr))
        out=Path(args.out); fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(attest.token_file(ws_id,account,args.issuer,args.client_id,seed,token),stream)
        return {'ok':True,'written':str(out),'note':'contains your email; give it only to a workspace admin, then delete it'}
    with home.lock():
        state=store.load(); registry=registry_of(local,state)
        if action=='oidc':
            record=attest.read_token_file(json.loads(Path(args.input).read_text(encoding='utf-8')))
            if (record['issuer'],record['client_id'])!=(args.issuer,args.client_id): raise ValueError('denied')
            ws_id=record['workspace']
        else: ws_id=profile_id(state,args.profile)
        ws=current(state,ws_id)
        if not ws.exists: raise ValueError('not_found')
        if action in ('list','check'):
            records,ignored=attest.load(local,ws_id,registry,ws)
            if action=='list': return {'attestations':[attest.summary(r) for _,r in sorted(records.items())],'ignored':ignored}
            wanted=attest.email_hash(ws_id,email())
            return {'accounts':sorted(a for a,r in records.items() if r['email_hash']==wanted)}
        from .registry_store import now
        if ws.role(state.account.account_id,now()) not in attest.MANAGERS: raise ValueError('denied')
        if action=='admin':
            address=email()
            presence.require('workspace','approve',console=console,one_shot=True,
                             summary=f'Attest that account {args.account} belongs to {address} (checked: {args.channel}).')
            entry=attest.create(state.device,ws_id,args.account,address,'admin',channel=args.channel)
        else:
            meta=attest.discover(args.issuer); jwks=attest.fetch_json(meta['jwks_uri'])
            address=attest.verify_id_token(record['id_token'],jwks,issuer=args.issuer,audience=args.client_id,
                expected_nonce=attest.nonce(ws_id,record['account'],record['seed']))
            presence.require('workspace','approve',console=console,one_shot=True,
                             summary=f'Attest that account {record["account"]} belongs to {address} (verified by {args.issuer}).')
            entry=attest.create(state.device,ws_id,record['account'],address,'oidc',issuer=args.issuer,token=record['id_token'])
        if attest.valid(entry,registry,ws) is None: raise ValueError('denied')
        return {'ok':True,'hash':attest.publish(local,entry),'note':'published locally; the broker sync uploads it'}


def rotate_after_removal(store,state,local,ws_id,old,new):
    """When an account loses read access (removed, or made a contributor), give the workspace scopes new key epochs
    wrapped only to devices that may still hold them. Scopes whose keys this device lacks are reported, not rotated."""
    from .federation import HOLDERS
    from .lifecycle import rotate_scopes
    if old not in HOLDERS or new in HOLDERS: return {}
    tree=next((t for t in state.trees if t.root.scope_id==ws_id),None)
    if tree is None: return {'rotated':[],'not_rotated':'no local keys for this workspace'}
    held=sorted(sid for sid in tree.scopes if sid in tree.keys); missing=sorted(set(tree.scopes)-set(held))
    rotate_scopes(state,local,set(held),reason='revoke'); store.save(state)
    from .broker.grants import names
    ns=names(state)
    return {'rotated':[ns[s] for s in held],**({'not_rotated':[ns[s] for s in missing]} if missing else {}),
            'note':'run broker sync so members receive the new epochs; the removed account keeps what it already read'}


def refuse_unattended(registry,state):
    """Servers and write-only devices approve nothing: no devices, members, key grants or classes."""
    from .registry_store import device_class
    if device_class(registry,state.device.device_id)!='workstation': raise ValueError('denied')


def execute_adapter(args,*,backend=None):
    """Run a chat adapter until interrupted. The sender's role is resolved from chat links on every message."""
    import os, threading
    from . import workspace
    from .adapters import links
    from .adapters.core import Adapter
    from .adapters.slack import SlackSocket
    from .registry_store import now
    from .storage.filesystem import FilesystemStorage
    home=BrokerHome(args.home); store=select_store(home,backend); local=FilesystemStorage(home.root/'replica')
    config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    if type(config) is not dict or set(config)-{'profile','channels','allow_shared_channels'} or type(config.get('profile')) is not str:
        raise ValueError('invalid_request')
    def context():
        state=store.load(); registry=registry_of(local,state); ws_id=resolve(state,config['profile'])
        owner=registry.profile_owner(ws_id) if hasattr(registry,'profile_owner') else state.account.account_id
        return state,registry,ws_id,workspace.load(local,ws_id,registry,owner)
    def identify(user):
        state,registry,ws_id,ws=context()
        account=links.resolve(local,ws_id,registry,ws,'slack',user)
        return account,(ws.role(account,now()) if account else None)
    def inbox_scopes():
        state,registry,ws_id,ws=context(); ns=names(state)
        return {ns[s] for s in ws.at(now()).settings.get('inbox.scopes',[]) if s in ns}
    client=BrokerClient.from_file(args.agent_key)
    try:
        socket=SlackSocket(Adapter(client,config,identify,inbox_scopes,'slack'),
                           os.environ.get('AGENTMESH_SLACK_APP_TOKEN'),os.environ.get('AGENTMESH_SLACK_BOT_TOKEN'))
        stop=threading.Event()
        try: socket.run(stop)
        except KeyboardInterrupt: stop.set()
        return {'ok':True}
    finally: client.close()


def registry_of(local,state):
    """This device's registry over its local replica: one account, or a Federation for shared workspaces."""
    from . import federation
    return federation.load(local,state.account.public_record(),state.control.get('federation_pins'))


def execute_federation(args,home,store,presence,local,state,console):
    """W2: show this account for others to pin, join another account's workspace, grant keys to a member device."""
    from . import federation
    from .keyring import publish_recipient, regrant
    from .encoding import hex_bytes
    me=state.account.public_record()
    if args.action=='account':
        federation.publish_account(local,me)
        return {'account':me['account_id'],'fingerprint':federation.fingerprint(me['root_public_key']),'device':state.device.device_id}
    if args.action=='join':
        token=getattr(args,'invite_record',None)
        if args.invite or token:
            from . import invites
            token=invites.read_token(token or json.loads(Path(args.invite).read_text(encoding='utf-8')))
            args.profile_id,args.owner_account,args.owner_fingerprint=token['workspace'],token['owner_account'],token['owner_fingerprint']
        if not (args.profile_id and args.owner_account and args.owner_fingerprint): raise ValueError('invalid_request')
        hex_bytes(args.profile_id,16); hex_bytes(args.owner_account,16)
        if args.owner_account==me['account_id']: raise ValueError('invalid_request')
        try: owner=federation.read_account(local,args.owner_account)
        except (ValueError,KeyError): raise ValueError('not_found') from None   # run broker sync first
        if federation.fingerprint(owner['root_public_key'])!=args.owner_fingerprint.strip().lower(): raise ValueError('denied')
        presence.require('workspace','trust',console=console,one_shot=True,
                         summary=f'Trust account {args.owner_account} as owner of workspace {args.profile_id}.')
        pins=state.control.setdefault('federation_pins',{})
        old=pins.get(args.profile_id)
        if old is not None and old!={'owner':args.owner_account,'root':owner['root_public_key']}: raise ValueError('denied')
        pins[args.profile_id]={'owner':args.owner_account,'root':owner['root_public_key']}; store.save(state)
        federation.publish_account(local,me)
    else:
        hex_bytes(args.device,16)
        registry=registry_of(local,state)
        cert=registry.certificates.get(args.device)
        if cert is None: raise ValueError('not_found')   # the member's device records arrive with broker sync
        presence.require('workspace','grant',console=console,one_shot=True,
                         summary=f'Give device {args.device} of account {cert["account_id"]} keys for: {", ".join(args.scope)}.')
        scopes=[resolve(state,name) for name in args.scope]
        from .registry_store import now
        if write_only(registry,state,scopes,cert['account_id'],now()):
            from .keyring import grant_write
            grant_write(state,local,args.device,scopes)
            return {'ok':True,'write_only':True,'note':'contributor: public write keys and topology published; run broker sync'}
        regrant(state,local,args.device,scopes)
        return {'ok':True,'note':'keys published locally; the broker sync uploads them'}
    publish_recipient(home,store,local,args.profile_id)
    if token is not None:
        from . import invites
        invites.publish_use(local,invites.use_record(state.device,me['account_id'],me['root_public_key'],token))
        return {'ok':True,'account':me['account_id'],'device':state.device.device_id,'invite':token['invite'],
                'note':'run broker sync; an admin accepts the invite use, then sync again to receive the keys'}
    return {'ok':True,'account':me['account_id'],'fingerprint':federation.fingerprint(me['root_public_key']),
            'device':store.load().device.device_id,'note':'run broker sync, then give the admin your account and fingerprint'}


def execute_invite(args,home,store,presence,local,state,console,profile_id,current):
    """Create, list, revoke and accept signed workspace invites (W3)."""
    import os, secrets
    from . import invites, workspace, federation
    from .keyring import regrant
    from .registry_store import now
    ws_id=profile_id(state,args.profile); ws=current(state,ws_id)
    if not ws.exists: raise ValueError('not_found')
    ns=names(state); action=args.invite_action
    if action=='list':
        return {'invites':[dict(id=i,role=v['role'],scopes=[ns.get(s,s) for s in v['scopes']],max_uses=v['max_uses'],used=v['used'],
                                expires_at=v['expires_at'],revoked=v['revoked']) for i,v in sorted(ws.state.invites.items())]}
    if ws.role(state.account.account_id,now()) not in workspace.MANAGERS: raise ValueError('denied')
    if action=='create':
        scopes=[resolve(state,n) for n in args.scope]
        if any(next(s for t in state.trees for s in t.scopes.values() if s.scope_id==sid).profile_id!=ws_id for sid in scopes):
            raise ValueError('invalid_request')
        if not 1<=args.days<=7 or not 1<=args.max_uses<=100: raise ValueError('invalid_request')
        secret=invites.new_secret(); invite_id=secrets.token_hex(16); at=now(); expires=at+args.days*86_400_000
        entry=workspace.create(state.device,ws_id,'invite',previous=ws.head,role=args.role,issued_at=at,invite=dict(
            id=invite_id,scopes=sorted(scopes),max_uses=args.max_uses,expires_at=expires,invite_key=invites.invite_key(secret),revoked=False))
        check=workspace.verified(registry_of(local,state),entry)
        if check is None or not workspace.allowed(ws.state,entry,*check): raise ValueError('denied')
        presence.require('workspace','grant',console=console,one_shot=True,
                         summary=f'Invite up to {args.max_uses} {args.role}(s) to {", ".join(args.scope)} for {args.days} day(s).')
        # The token carries the workspace owner's fingerprint (this account, or the owner this device pinned).
        owner=ws_owner(local,state,ws_id)
        root=state.account.public_record()['root_public_key'] if owner==state.account.account_id else state.control['federation_pins'][ws_id]['root']
        base=invites.token(ws_id,invite_id,secret,owner,federation.fingerprint(root),args.role,expires)
        if args.link:
            # One link, one slot, one person: a presigned PUT cannot refuse an existing object.
            if args.max_uses!=1: raise ValueError('invalid_request')
            from . import joinlink
            store_=remote_storage(args)
            if store_ is None: raise ValueError('invalid_request')   # the link names the workspace store
            slot=joinlink.new_slot(); channels=[]
            if getattr(store_,'backend','filesystem')=='s3':
                seconds=min(args.days*86_400,604_800)
                channels.append(dict(kind='storage',request_url=joinlink.presign(store_,joinlink.request_key(slot),'PUT',seconds),
                                     answer_url=joinlink.presign(store_,joinlink.answer_key(slot),'GET',seconds)))
            else: channels.append(dict(kind='folder'))
            channels.append(dict(kind='file'))
            remote_=joinlink.remote_of(store_)
            if getattr(args,'s3_credentials_kind',None) and remote_['kind']=='s3': remote_['credentials_kind']=args.s3_credentials_kind
            if getattr(args,'s3_path_style',False) and remote_['kind']=='s3': remote_['path_style']=True
            token=invites.token_v2(base,federation.account_record(owner,root),remote_,channels,slot,args.label)
            text=invites.encode_link(token)
        workspace.publish(local,entry)
        out=Path(args.out or args.link); fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            if args.link: stream.write(text+'\n')
            else: json.dump(base,stream)
        if args.link:
            return {'ok':True,'invite':invite_id,'slot':slot,'written':str(out),
                    'note':'send the link only to the person invited (it lets them ask to join, nothing more); run broker sync'}
        return {'ok':True,'invite':invite_id,'written':str(out),'note':'give the token file only to the people invited; run broker sync'}
    if action=='revoke':
        old=ws.state.invites.get(args.id)
        if old is None: raise ValueError('not_found')
        entry=workspace.create(state.device,ws_id,'invite',previous=ws.head,role=old['role'],invite=dict(
            id=args.id,scopes=old['scopes'],max_uses=old['max_uses'],expires_at=old['expires_at'],invite_key=old['invite_key'],revoked=True))
        check=workspace.verified(registry_of(local,state),entry)
        if check is None or not workspace.allowed(ws.state,entry,*check): raise ValueError('denied')
        presence.require('workspace','revoke',console=console,one_shot=True,summary=f'Revoke invite {args.id}.')
        workspace.publish(local,entry); return {'ok':True}
    if action in ('requests','reject') or getattr(args,'request',None) or getattr(args,'request_file',None):
        return execute_join_request(args,action,home,store,presence,local,state,console,ws_id,current)
    accepted,skipped=[],[]
    uses=[u for u in invites.pending_uses(local,ws_id) if u['account'] not in ws.state.members]
    if not uses: return {'accepted':[],'skipped':[]}
    presence.require('workspace','grant',console=console,one_shot=True,summary=f'Accept up to {len(uses)} invite use(s) after checking them.')
    for use in uses:
        ws=current(state,ws_id)
        if use['account'] in ws.state.members: continue
        reason=invites.check_use(use,local,ws.state.invites.get(use['invite']))
        if reason is not None: skipped.append(dict(account=use['account'],device=use['device_id'],reason=reason)); continue
        invite=ws.state.invites[use['invite']]
        entry=workspace.create(state.device,ws_id,'member',previous=ws.head,subject=use['account'],role=invite['role'],
                               account_root=use['account_root'],invite={'id':use['invite']})
        check=workspace.verified(registry_of(local,state),entry)
        if check is None or not workspace.allowed(ws.state,entry,*check):
            skipped.append(dict(account=use['account'],device=use['device_id'],reason='not allowed')); continue
        workspace.publish(local,entry)
        if invite['role'] in federation.HOLDERS: regrant(state,local,use['device_id'],invite['scopes'])
        else:
            from .keyring import grant_write
            grant_write(state,local,use['device_id'],invite['scopes'])   # contributors: write-only
        accepted.append(dict(account=use['account'],device=use['device_id'],role=invite['role']))
    return {'accepted':accepted,'skipped':skipped,'note':'run broker sync so joiners receive their keys'}


def admit(state,local,ws_id,use,current):
    """Add the use's account with the invite's role and grant the device the invite's scopes (the body of accept)."""
    from . import workspace, federation
    from .keyring import regrant, grant_write
    ws=current(state,ws_id); invite=ws.state.invites[use['invite']]
    entry=workspace.create(state.device,ws_id,'member',previous=ws.head,subject=use['account'],role=invite['role'],
                           account_root=use['account_root'],invite={'id':use['invite']})
    check=workspace.verified(registry_of(local,state),entry)
    if check is None or not workspace.allowed(ws.state,entry,*check): return 'not allowed'
    workspace.publish(local,entry)
    if invite['role'] in federation.HOLDERS: regrant(state,local,use['device_id'],invite['scopes'])
    else: grant_write(state,local,use['device_id'],invite['scopes'])
    return None


def member_credentials(spec,kind,input_fn=None):
    """The storage credential an admin hands to one member (the ``manual`` issuer): typed, from a file, or none."""
    from .storage.r2creds import check_values, S3_KINDS
    if spec in (None,'none'): return None
    if spec=='prompt':
        import getpass
        values=(getpass.getpass('Member access key ID: '),getpass.getpass('Member secret access key: '))
    elif spec.startswith('file:'):
        value=json.loads(Path(spec[5:]).read_text(encoding='utf-8'))
        if type(value) is not dict or set(value)!={'kind','access_key_id','secret_access_key'} or value['kind'] not in S3_KINDS:
            raise ValueError('invalid_request')
        kind=value['kind']; values=(value['access_key_id'],value['secret_access_key'])
    else: raise ValueError('invalid_request')
    check_values(kind,values)
    return dict(kind=kind,access_key_id=values[0],secret_access_key=values[1])


def execute_join_request(args,action,home,store,presence,local,state,console,ws_id,current):
    """One-click join, admin side: list requests, approve one (members entry, keys, sealed storage credential) or
    reject it. Requests come from the workspace store (storage and folder channels) or from a file."""
    import os
    from . import joinlink
    from .storage import ObjectNotFound
    remote_=remote_storage(args)
    def load(slot=None,path=None):
        data=Path(path).read_bytes() if path else remote_.get(joinlink.request_key(slot))
        record=joinlink.read_request(data)
        if record['workspace']!=ws_id or (slot and record['slot']!=slot): raise ValueError('invalid_request')
        return record
    def answered(slot):
        if remote_ is None: return False
        try: remote_.get(joinlink.answer_key(slot)); return True
        except ObjectNotFound: return False
    def describe(record):
        ws=current(state,ws_id); _,reason=joinlink.check_request(record,local,ws.state.invites.get(record['invite']))
        inv=ws.state.invites.get(record['invite'],{})
        return dict(slot=record['slot'],account=record['account'],device=record['device_id'],invite=record['invite'],
                    role=inv.get('role'),requested_at=record['issued_at'],ready=reason is None,reason=reason)
    if action=='requests':
        found=[]
        if remote_ is not None:
            for key in remote_.list(joinlink.REQ_PREFIX):
                slot=key[len(joinlink.REQ_PREFIX):].removesuffix('.json')
                try:
                    if not answered(slot): found.append(describe(load(slot)))
                except (ValueError,KeyError,TypeError): found.append(dict(slot=slot,ready=False,reason='invalid'))
        for path in args.request_file:
            try: found.append(describe(load(path=path)))
            except (ValueError,KeyError,TypeError): found.append(dict(file=path,ready=False,reason='invalid'))
        return {'requests':found}
    if remote_ is None and not args.answer_out: raise ValueError('invalid_request')   # nowhere to put the answer
    record=load(args.request,None) if args.request else load(path=args.request_file)
    if args.request and answered(record['slot']): return {'error':'already_answered'}
    def deliver(data):
        if args.answer_out:
            fd=os.open(args.answer_out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream: stream.write(data)
        else: remote_.put(joinlink.answer_key(record['slot']),data)
    if action=='reject':
        presence.require('workspace','revoke',console=console,one_shot=True,summary=f"Refuse join request {record['slot']} from account {record['account']}.")
        deliver(joinlink.build_answer(ws_id,record['slot'],'rejected',args.reason,record['answer_recipient'],None))
        return {'ok':True,'rejected':record['slot']}
    ws=current(state,ws_id)
    if record['account'] in ws.state.members: return {'error':'already_member'}
    use,reason=joinlink.check_request(record,local,ws.state.invites.get(record['invite']))
    if reason is not None: return {'error':'denied','reason':reason}
    creds=member_credentials(args.member_credentials,getattr(args,'s3_credentials_kind',None) or 's3')
    if creds is None and getattr(remote_,'backend','filesystem')=='s3': raise ValueError('invalid_request')   # an S3 member needs one
    invite=ws.state.invites[record['invite']]
    presence.require('workspace','grant',console=console,one_shot=True,
                     summary=f"Admit account {record['account']} (device {record['device_id']}) as {invite['role']}, "
                             f"give it keys for {len(invite['scopes'])} scope(s)"+(' and a storage credential' if creds else '')+'.')
    answer=joinlink.build_answer(ws_id,record['slot'],'approved',None,record['answer_recipient'],creds)   # fails before any write
    joinlink.apply_request(record,local)
    why=admit(state,local,ws_id,use,current)
    if why is not None: return {'error':'denied','reason':why}
    deliver(answer)
    return {'ok':True,'accepted':dict(account=record['account'],device=record['device_id'],role=invite['role']),
            'note':'run broker sync so the member receives the keys'}


def write_only(registry,state,scopes,account,at):
    """Whether ``account`` is a contributor in the workspaces of ``scopes`` (gets write-only keys, never read keys)."""
    if not hasattr(registry,'role'): return False
    profiles={next(s for t in state.trees for s in t.scopes.values() if s.scope_id==sid).profile_id for sid in scopes}
    return any(registry.role(p,account,at)=='contributor' for p in profiles)


def ws_owner(local,state,ws_id):
    registry=registry_of(local,state)
    return registry.profile_owner(ws_id) if hasattr(registry,'profile_owner') else state.account.account_id


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
        if args.action=='class':
            from .registry_store import RegistryStore,device_class_record
            from .encoding import hex_bytes
            hex_bytes(args.identifier,16); state=store.load(); local=FilesystemStorage(home.root/'replica')
            registry=RegistryStore(local,state.account.public_record()).load()
            refuse_unattended(registry,state)
            cert=registry.certificates.get(args.identifier)
            if cert is None: raise ValueError('not_found')
            if args.identifier==state.device.device_id and cert['issuer'] not in ('account','recovery'): raise ValueError('denied')
            presence.require('device','approve',console=console,one_shot=True,
                             summary=f'Mark device {args.identifier} as {args.device_class}.')
            RegistryStore(local,state.account.public_record()).publish_device_class(
                device_class_record(args.identifier,state.device,args.device_class))
            return {'ok':True,'note':'published locally; the broker sync uploads it'}
        from .registry_store import device_class,RegistryStore
        state=store.load(); registry=RegistryStore(FilesystemStorage(home.root/'replica'),state.account.public_record()).load()
        return {'devices':[{'device_id':d,'revoked':d in registry.revocations,'class':device_class(registry,d)}
                           for d in sorted(registry.device_records)]}
    if args.command=='profile':
        state=store.load(); profile=resolve(state,args.name)
        confirmation=args.confirm_name
        if args.action=='delete' and confirmation is None and sys.stdin is not None and sys.stdin.isatty(): confirmation=input_fn('Type profile name: ')
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
            refuse_unattended(registry,state)
            from .keyring import regrant
            regrant(state,storage,args.identifier,[resolve(state,name) for name in args.scope])
            return {'ok':True}
        refuse_unattended(registry,state)
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

def build_pages(client, args):
    """`memory build`: settings memory_tree gives the writer device, labels, model and caps."""
    from .settings import load
    from .pages import build, ClaudeSummarizer, Reconnecting
    tree=load().get('memory_tree',{})
    me=client.request(dict(op='whoami'))
    if 'error' in me: return me
    if tree.get('writer_device') not in (None,me['device_id']): return {'error':'not_writer_device'}
    if args.max_pages is not None and not 1<=args.max_pages<=200: raise ValueError('invalid_request')
    summarize=ClaudeSummarizer(model=tree.get('model','claude-sonnet-5-5'),budget=tree.get('budget_usd',0.5))
    writer=Reconnecting(lambda:BrokerClient.from_file(args.agent_key))
    try:
        return build(writer,summarize=summarize,labels=tuple(tree.get('summarize_labels',('general','internal'))),
            max_pages=args.max_pages or tree.get('max_pages',20),scopes=args.scope,dry_run=args.dry_run)
    finally: writer.close()


def job_main(argv):
    """`job run` and `ingest scan|summary` keep the exit code and output protocol of the scheduled jobs they replace
    (memory-sync's job_notify.py and jobs/agentmesh_*.py): no JSON result line of the CLI's own."""
    if argv[:2]==['job','run']:
        from .control.jobrun import main as run
        return run(argv[2:])
    if argv[:1]==['ingest'] and argv[1:2] in (['scan'],['summary']):
        args=parser().parse_args(argv)
        from .ingest import production
        return production.execute('ingest' if args.action=='scan' else 'summary',config=args.config,force=getattr(args,'force',False))
    return None


def main(argv=None,*,backend=None,console=None,input_fn=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    code=job_main(argv)
    if code is not None: return code
    p=parser(); args=p.parse_args(argv)
    try:
        result=execute(args,backend=backend,console=console,input_fn=input_fn)
        # `memory cat` prints the page itself so it can be piped into sed/grep.
        if args.command=='memory' and args.action=='cat' and not args.json and type(result.get('markdown')) is str:
            emit(result['markdown'].rstrip('\n')); return 0
        emit(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 1 if 'error' in result or result.get('errors',0) or (args.command=='r2-credentials' and result.get('ok') is False) else 0
    except KeyboardInterrupt: return 0
    except Exception as exc:
        code={'invalid_request':'invalid_request','S3 credentials missing':'credentials_missing','S3 credentials refused':'credentials_refused',
              'staging binding missing or invalid; run apply':'staging_binding_missing',
              'staging binding device differs from broker':'staging_binding_device_mismatch',
              'staging node differs from binding':'staging_binding_node_mismatch',
              'staging identity missing; initialize ingest before apply':'staging_identity_missing',
              'unmapped project':'unmapped_project','truncated':'truncated','denied':'denied','not_found':'not_found',
              'invalid staging identity':'staging_identity_invalid',
              'invalid token':'token_rejected','token expired':'token_expired','email not verified':'email_not_verified',
              'issuer mismatch':'issuer_mismatch','https required':'https_required'}.get(str(exc))
        from .connector_cli import CODES as CONNECTOR_CODES
        if code is None and str(exc) in CONNECTOR_CODES:
            emit(json.dumps({'error':str(exc),'hint':CONNECTOR_CODES[str(exc)]})); return 1
        if code is None: code=str(exc) if str(exc) in ('unknown_app','ambiguous_app','invalid_settings','presence_required','pairing_denied','pairing_expired','pairing_mismatch','pairing_reused','stale','workspace_refused') else 'unavailable'
        if isinstance(exc,BrokerUnreachable): code='broker_unreachable'
        if code=='unavailable' and getattr(exc,'status_code',None): code=exc.status_code  # e.g. broker_busy
        emit(json.dumps({'error':code})); return 1


if __name__=='__main__': sys.exit(main())
