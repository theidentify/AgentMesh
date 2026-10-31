"""`agentmesh-log control ...`: run the supervisor, write its plist, or call it over the socket."""
import sys
from pathlib import Path
from .layout import Layout

CALLS={
    'status':lambda a:('status',{}),'units':lambda a:('units',{}),'quota':lambda a:('quota.status',{}),
    'diagnose':lambda a:('diagnose',{}),'audit':lambda a:('audit.tail',{'limit':a.limit}),
    'start':lambda a:('unit.start',{'unit':a.unit}),'stop':lambda a:('unit.stop',{'unit':a.unit}),
    'restart':lambda a:('unit.restart',{'unit':a.unit}),
    'reset':lambda a:('unit.reset',{'unit':a.unit}),
    'maintenance':lambda a:('maintenance.on',{'reason':a.reason}) if a.state=='on' else ('maintenance.off',{}),
}


def add_parsers(commands):
    control=commands.add_parser('control'); sub=control.add_subparsers(dest='action',required=True)
    for action in ('serve','status','units','quota','diagnose'): sub.add_parser(action)
    for action in ('start','stop','restart','reset','adopt','release'): sub.add_parser(action).add_argument('unit')
    maintenance=sub.add_parser('maintenance'); maintenance.add_argument('state',choices=['on','off'])
    maintenance.add_argument('--reason',default='maintenance')
    sub.add_parser('audit').add_argument('--limit',type=int,default=20)
    sub.add_parser('panic-revert')
    plist=sub.add_parser('plist'); plist.add_argument('--out',required=True); plist.add_argument('--python',default=sys.executable)


def serve(layout):
    if sys.platform!='darwin': return {'error':'unsupported_platform'}
    import json
    import signal
    from . import migrate
    from .launchd import LaunchdBackend
    from .moves import Moves
    from .native import NativeBackend
    from .notify import notifier
    from .ops import Control
    from .presence_helper import HelperPresence
    from .projects import Mappings
    from .router import Router
    from .server import Supervisor
    from .store import Store
    from .units import UNITS
    layout.ensure(); store=Store(layout.state); launchd=LaunchdBackend()
    try: migrate.evict_reloaded(layout,store,launchd)
    except Exception as exc: print(json.dumps({'evict_error':type(exc).__name__}),file=sys.stderr,flush=True)
    native=NativeBackend(store,UNITS,notify=notifier(layout))
    native.reconcile()
    # unit.backend: the supervisor's own Touch ID / Mac password helper; a client never vouches for presence.
    presence=HelperPresence(layout.presence_helper)
    moves=Moves(layout,store,presence,launchd=launchd)
    control=Control(layout,Router(store,launchd,native),moves=moves,mappings=Mappings(layout,presence,moves))
    supervisor=Supervisor(control,layout,ticker=native.tick,on_close=native.shutdown)
    signal.signal(signal.SIGTERM,lambda signum,frame:supervisor.close())
    try: supervisor.serve()
    finally: supervisor.close(); store.close()
    return {'ok':True}


def write_plist(layout, out, python):
    from .plist import render
    path=Path(out)
    if path.exists() or path.is_symlink(): return {'error':'exists'}
    path.write_bytes(render(layout,python))
    return {'ok':True,'path':str(path)}


def execute(args, *, layout=None, call=None, console=None, launchd=None, agents=None):
    layout=layout or Layout()
    if args.action=='serve': return serve(layout)
    if args.action=='plist': return write_plist(layout,args.out,args.python)
    if call is None: from .client import call
    if args.action in ('adopt','release','panic-revert'): return local(args,layout,call,console,launchd,agents)
    op,params=CALLS[args.action](args)
    try: response=call(layout,op,params)
    except OSError: return {'error':'supervisor_unavailable'}
    if not response.get('ok'): return {'error':response.get('error',{}).get('code','failed')}
    result=response.get('result')
    return result if isinstance(result,dict) else {'items':result}


def local(args, layout, call, console, launchd, agents):
    """Backend moves run here, in the owner's terminal, against state.db; never over the socket."""
    from . import migrate
    from .store import Store
    if console is None:
        from ..presence import ConsolePresence
        console=ConsolePresence(output=lambda text:print(text,file=sys.stderr))
    layout.ensure(); store=Store(layout.state)
    try:
        if args.action=='panic-revert': return migrate.panic_revert(layout,store,launchd=launchd,agents=agents)
        if args.action=='adopt':
            return migrate.adopt(layout,store,args.unit,console=console,call=call,launchd=launchd,agents=agents)
        return migrate.release(layout,store,args.unit,console=console,launchd=launchd,agents=agents)
    finally: store.close()
