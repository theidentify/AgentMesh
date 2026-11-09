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
    'memory-export':lambda a:('memory.export',{'path':a.path,'folder':str(Path(a.folder).expanduser().absolute()),
        'include_internal':a.include_internal,'allow_synced':a.allow_synced}),
}


def add_parsers(commands):
    control=commands.add_parser('control'); sub=control.add_subparsers(dest='action',required=True)
    for action in ('serve','status','units','quota','diagnose','panic-revert'): sub.add_parser(action)
    for action in ('start','stop','restart','reset','adopt','release','reload'): sub.add_parser(action).add_argument('unit')
    consolidate=sub.add_parser('consolidate',help='run every unit under the supervisor: one background item (dry run unless --apply)')
    consolidate.add_argument('--apply',action='store_true')
    for name in ('consolidate','adopt','release','reload','panic-revert'):
        sub.choices[name].add_argument('--agents',help='the LaunchAgents folder (required with a non-default AGENTMESH_HOME)')
    sub.choices['panic-revert'].add_argument('--label',default=None,help='the supervisor label to boot out')
    maintenance=sub.add_parser('maintenance'); maintenance.add_argument('state',choices=['on','off'])
    maintenance.add_argument('--reason',default='maintenance')
    sub.add_parser('audit').add_argument('--limit',type=int,default=20)
    archive=sub.add_parser('sqlite-archive',help='list or delete the archives the SQLite retirement left')
    archive.add_argument('step',choices=['list','delete']); archive.add_argument('--archive')
    export=sub.add_parser('memory-export',help='write a memory subtree as plaintext markdown files (Touch ID each time)')
    export.add_argument('path'); export.add_argument('folder')
    export.add_argument('--include-internal',action='store_true'); export.add_argument('--allow-synced',action='store_true')
    plist=sub.add_parser('plist'); plist.add_argument('--out',required=True); plist.add_argument('--python',default=sys.executable)
    plist.add_argument('--label',default=None,help='another label, for a test supervisor beside the real one')


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
    from .memory import Memory
    from .projects import Mappings
    from .router import Router
    from .server import Supervisor
    from .store import Store
    from .units import UNITS
    layout.ensure(); store=Store(layout.state); launchd=LaunchdBackend()
    # Unit labels are global to the login session: a test supervisor (any other AGENTMESH_HOME) never boots out the
    # real home's launchd jobs.
    if layout.root==Path.home()/'.agentmesh':
        try: migrate.evict_reloaded(layout,store,launchd)
        except Exception as exc: print(json.dumps({'evict_error':type(exc).__name__}),file=sys.stderr,flush=True)
    native=NativeBackend(store,UNITS,notify=notifier(layout))
    native.reconcile()
    # unit.backend: the supervisor's own Touch ID / Mac password helper; a client never vouches for presence.
    presence=HelperPresence(layout.presence_helper)
    moves=Moves(layout,store,presence,launchd=launchd)
    control=Control(layout,Router(store,launchd,native),moves=moves,mappings=Mappings(layout,presence,moves),
        memory=Memory(layout,presence,moves))
    def tick():
        native.tick(); write_snapshot(layout,native)
    supervisor=Supervisor(control,layout,ticker=tick,on_close=native.shutdown)
    signal.signal(signal.SIGTERM,lambda signum,frame:supervisor.close())
    try: supervisor.serve()
    finally: supervisor.close(); store.close()
    return {'ok':True}


SNAPSHOT={}


def write_snapshot(layout, native):
    """<home>/status/supervisor.json, rewritten only when it changes. Insight reads it for units launchd no longer runs."""
    from .layout import write_json
    value=native.snapshot()
    if SNAPSHOT.get(str(layout.root))==value: return
    try: layout.status.mkdir(mode=0o700,parents=True,exist_ok=True); write_json(layout.status/'supervisor.json',value)
    except OSError: return
    SNAPSHOT[str(layout.root)]=value


def write_plist(layout, out, python, label=None):
    from .plist import render
    path=Path(out)
    if path.exists() or path.is_symlink(): return {'error':'exists'}
    path.write_bytes(render(layout,python,label=label))
    return {'ok':True,'path':str(path)}


def execute(args, *, layout=None, call=None, console=None, launchd=None, agents=None, helper=None):
    layout=layout or Layout()
    if args.action=='serve': return serve(layout)
    if args.action=='plist': return write_plist(layout,args.out,args.python,args.label)
    if call is None: from .client import call
    if args.action in ('adopt','release','panic-revert','consolidate','reload'): return local(args,layout,call,console,launchd,agents)
    if args.action.startswith('sqlite-'): return sqlite(args,layout,console,helper)
    op,params=CALLS[args.action](args)
    try: response=call(layout,op,params)
    except OSError: return {'error':'supervisor_unavailable'}
    if not response.get('ok'): return {'error':response.get('error',{}).get('code','failed')}
    result=response.get('result')
    return result if isinstance(result,dict) else {'items':result}


def sqlite(args, layout, console, helper):
    """SQLite archives: owner-run here, never over the socket; delete is tier 3."""
    from . import sqlite_archive
    if args.step=='list': return {'archives':sqlite_archive.archives(layout.root)}
    if not args.archive: return {'error':'archive_required'}
    if console is None:
        from ..presence import ConsolePresence
        console=ConsolePresence(output=lambda text:print(text,file=sys.stderr))
    if helper is None and sys.platform=='darwin':
        from .presence_helper import HelperPresence
        helper=HelperPresence(layout.presence_helper)
    return sqlite_archive.delete(layout,layout.root,args.archive,console=console,helper=helper)


def local(args, layout, call, console, launchd, agents):
    """Backend moves run here, in the owner's terminal, against state.db; never over the socket.

    A test home (AGENTMESH_HOME other than ~/.agentmesh) must name its LaunchAgents folder: the default is the real one,
    whose plists belong to the real supervisor."""
    from . import migrate
    from .store import Store
    if agents is None and getattr(args,'agents',None): agents=Path(args.agents).expanduser().absolute()
    if agents is None and layout.root!=Path.home()/'.agentmesh': return {'error':'agents_required'}
    if console is None:
        from ..presence import ConsolePresence
        console=ConsolePresence(output=lambda text:print(text,file=sys.stderr))
    layout.ensure(); store=Store(layout.state)
    try:
        if args.action=='panic-revert':
            return migrate.panic_revert(layout,store,launchd=launchd,agents=agents,label=getattr(args,'label',None))
        if args.action=='adopt':
            return migrate.adopt(layout,store,args.unit,console=console,call=call,launchd=launchd,agents=agents)
        if args.action=='consolidate':
            return migrate.consolidate(layout,store,console=console,call=call,apply=args.apply,launchd=launchd,agents=agents)
        if args.action=='reload': return migrate.reload(layout,store,args.unit,console=console,call=call,agents=agents)
        return migrate.release(layout,store,args.unit,console=console,launchd=launchd,agents=agents)
    finally: store.close()
