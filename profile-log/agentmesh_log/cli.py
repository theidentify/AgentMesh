"""JSON command-line interface for explicit broker administration and agent IPC."""
import argparse
import json
import sys
from .encoding import wire
from .identity import AgentIdentity
from .model import Label
from .broker.keystore import BrokerHome, BrokerState, FileKeyStore, atomic_write
from .broker.grants import Grant, resolve, names
from .broker.service import Broker
from .broker.client import BrokerClient


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
        cmd.add_argument('--s3-prefix',default=''); cmd.add_argument('--s3-endpoint')
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
    return p


def remote_storage(args):
    if args.filesystem:
        from .storage.filesystem import FilesystemStorage
        return FilesystemStorage(args.filesystem)
    if args.s3_bucket:
        from .storage.s3 import S3Storage
        return S3Storage(args.s3_bucket,prefix=args.s3_prefix,endpoint_url=args.s3_endpoint)
    return None


def execute(args):
    if args.command!='broker':
        client=BrokerClient.from_file(args.agent_key)
        try:
            if args.command=='recall':
                return client.request(dict(op='recall',query=args.query,project=args.project,task=args.task,
                    limit=args.limit,profiles=args.profile,scopes=args.scope))
            return client.write(dict(kind=args.kind,content=args.content),scope=args.scope,tags=args.tag,task=args.task)
        finally: client.close()
    home=BrokerHome(args.home); store=FileKeyStore(home)
    if args.action=='run':
        remote=remote_storage(args)
        if args.sync_interval is not None and (remote is None or args.sync_interval<=0): raise ValueError('invalid_request')
        broker=Broker(home)
        try: broker.serve(remote=remote,sync_interval=args.sync_interval)
        finally: broker.close()
        return {'ok':True}
    with home.lock():
        if args.action=='init':
            if store.path.exists(): raise ValueError('already initialized')
            state=BrokerState.create(args.profile); store.save(state)
            return {'ok':True}
        state=store.load()
        if args.action=='scope':
            state.add(args.scope,Label[args.label]); store.save(state)
            return {'ok':True}
        if args.action=='grant':
            if not args.name: raise ValueError('invalid name')
            reads=[resolve(state,n) for n in args.read]; writes=[resolve(state,n) for n in args.write]
            # Equally narrow candidates use lexical local-name order, never common implicitly.
            scope_names=names(state)
            candidates=sorted((s for s in writes if not scope_names[s].endswith('/common')),
                key=lambda s:(-scope_names[s].count('/'),scope_names[s]))
            default=resolve(state,args.default_write) if args.default_write else (candidates[0] if candidates else None)
            if default not in writes: raise ValueError('invalid default write')
            profile=next(t.root.scope_id for t in state.trees if default in t.scopes)
            agent=AgentIdentity(state.device,state.account.account_id)
            grant=Grant(agent.agent_id,agent.public_bytes.hex(),reads,writes,default,profile,args.allow_secret,args.allow_cross_profile)
            path=home.root/'agents'/(agent.agent_id+'.key')
            atomic_write(path,wire(dict(format='agentmesh-agent-key-v1',agent_cert=agent.certificate(),
                agent_private_key=agent.private_bytes().hex(),device_public_key=state.device.public_bytes.hex(),address=home.address(state.device.device_id))))
            state.grants[agent.agent_id]=grant; store.save(state)
            return {'agent_key':str(path)}
        if args.action=='revoke':
            state.revoke(args.agent_id); store.save(state); return {'ok':True}
        if args.action=='sync':
            broker=Broker(home)
            try: return broker.sync_once(remote_storage(args))
            finally: broker.close()
    raise ValueError('invalid_request')


def main(argv=None):
    p=parser(); args=p.parse_args(argv)
    try:
        result=execute(args)
        emit(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 1 if 'error' in result or result.get('errors',0) else 0
    except KeyboardInterrupt: return 0
    except Exception:
        emit('{"error":"unavailable"}'); return 1


if __name__=='__main__': sys.exit(main())
