"""`agentmesh-log control ...`: run the supervisor, write its plist, or call it over the socket."""
import argparse
import hashlib
import os
import sys
from pathlib import Path

WINDOWS=os.name=='nt'  # tests of the launchd paths set this False on a Windows runner
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
    sub.choices['serve'].add_argument('--root',help='the AgentMesh home (a Windows task has no environment to set it in)')
    for action in ('start','stop','restart','reset','adopt','release','reload'): sub.add_parser(action).add_argument('unit')
    consolidate=sub.add_parser('consolidate',help='run every unit under the supervisor: one background item (dry run unless --apply)')
    consolidate.add_argument('--apply',action='store_true')
    rewire=sub.add_parser('rewire-ingest',help="point the ingest, summary and retention jobs at agentmesh_log instead of memory-sync (dry run unless --apply)")
    rewire.add_argument('--apply',action='store_true'); rewire.add_argument('--back',action='store_true')
    handover=sub.add_parser('handover',help='hand the supervisor to AgentMesh Control as its login item (--back to undo)')
    handover.add_argument('--back',action='store_true')
    handover.add_argument('--app',default=None,help='the AgentMesh Control bundle (default ~/Applications)')
    handover.add_argument('--label',default=None,help='another label, for a test supervisor beside the real one')
    for name in ('consolidate','adopt','release','reload','panic-revert','handover'):
        sub.choices[name].add_argument('--agents',help='the LaunchAgents folder (required with a non-default AGENTMESH_HOME)')
        sub.choices[name].add_argument('--task-folder',help='Windows: only tasks in this Task Scheduler folder '
            '(a non-default AGENTMESH_HOME gets its own folder when this is not given)')
    sub.choices['serve'].add_argument('--task-folder',help=argparse.SUPPRESS)
    sub.choices['panic-revert'].add_argument('--label',default=None,help='the supervisor label (Windows: task) to stop')
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
    task=sub.add_parser('task',help='Windows: write the supervisor\'s logon task XML (import it with schtasks /Create /XML)')
    task.add_argument('--out',required=True)
    task.add_argument('--python',default=None,help='pythonw.exe of the AgentMesh venv (default: beside this python)')


def default_home(layout): return layout.root==Path.home()/'.agentmesh'


def task_folder(layout, given=None):
    """The Task Scheduler folder a command may touch: every folder for the real home, else the given one or a folder of
    the test home's own, so a test never moves, stops or deletes the real install's tasks."""
    if given: return '\\'+given.strip('\\')+'\\'
    if default_home(layout): return None
    return '\\AgentMeshTest-'+hashlib.sha256(str(layout.root).encode('utf-8')).hexdigest()[:12]+'\\'


def serve_windows(layout, folder=None):
    """The Windows supervisor: the same native backend and control ops over a named pipe; units not moved yet stay
    with Task Scheduler. Moves over the pipe need presence the supervisor cannot give on Windows (no helper), so
    they are refused; the owner runs consolidate and release in a terminal."""
    import json
    import signal
    from . import winmigrate
    from .connector import Connector
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
    from .taskscheduler import TaskSchedulerBackend
    from .units import UNITS
    layout.ensure(); store=Store(layout.state); tasks=TaskSchedulerBackend(folder=task_folder(layout,folder))
    try: winmigrate.evict_reloaded(layout,store,tasks)
    except Exception as exc: print(json.dumps({'evict_error':type(exc).__name__}),file=sys.stderr,flush=True)
    native=NativeBackend(store,UNITS,notify=notifier(layout))
    native.reconcile()
    presence=HelperPresence(layout.presence_helper)
    moves=Moves(layout,store,presence,launchd=tasks)
    control=Control(layout,Router(store,tasks,native),moves=moves,mappings=Mappings(layout,presence,moves),
        memory=Memory(layout,presence,moves),connector=Connector(layout,presence,moves))
    def tick():
        native.tick(); write_snapshot(layout,native)
    supervisor=Supervisor(control,layout,ticker=tick,on_close=native.shutdown)
    for name in ('SIGTERM','SIGBREAK','SIGINT'):
        if hasattr(signal,name): signal.signal(getattr(signal,name),lambda signum,frame:supervisor.close())
    try: supervisor.serve()
    finally: supervisor.close(); store.close()
    return {'ok':True}


def serve(layout, folder=None):
    if WINDOWS: return serve_windows(layout,folder)
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
    from .connector import Connector
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
        memory=Memory(layout,presence,moves),connector=Connector(layout,presence,moves))
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


def write_task(layout, out, python=None):
    from .wintasks import TASK_NAME, render_supervisor
    path=Path(out)
    if path.exists() or path.is_symlink(): return {'error':'exists'}
    if python is None:
        here=Path(sys.executable); python=str(here.with_name('pythonw.exe')) if here.name.lower()=='python.exe' else str(here)
    path.write_bytes(render_supervisor(layout,python))
    return {'ok':True,'path':str(path),'task':TASK_NAME}


def write_plist(layout, out, python, label=None):
    from .plist import render
    path=Path(out)
    if path.exists() or path.is_symlink(): return {'error':'exists'}
    path.write_bytes(render(layout,python,label=label))
    return {'ok':True,'path':str(path)}


def execute(args, *, layout=None, call=None, console=None, launchd=None, agents=None, helper=None, tasks=None, windows=False):
    layout=layout or Layout()
    if args.action=='serve':
        if getattr(args,'root',None): layout=Layout(Path(args.root).expanduser().absolute())
        return serve(layout,getattr(args,'task_folder',None))
    if args.action=='task': return write_task(layout,args.out,args.python)
    if args.action=='plist': return write_plist(layout,args.out,args.python,args.label)
    if call is None: from .client import call
    if args.action in ('adopt','release','panic-revert','consolidate','reload','handover'):
        return local(args,layout,call,console,launchd,agents,tasks,windows)
    if args.action=='rewire-ingest': return rewire_ingest(args,layout,console,windows)
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


def rewire_ingest(args, layout, console, windows=False, check=None):
    """memory-sync's retirement: owner-run against state.db with typed presence, like the other moves."""
    from . import rewire
    from .migrate import audit
    from .store import Store
    if console is None:
        from ..presence import ConsolePresence
        console=ConsolePresence(output=lambda text:print(text,file=sys.stderr))
    layout.ensure(); store=Store(layout.state)
    try:
        return rewire.rewire(layout,store,console=console,apply=args.apply,back=args.back,windows=WINDOWS or windows,
            check=check or rewire.importable,audit=audit)
    finally: store.close()


def local(args, layout, call, console, launchd, agents, tasks=None, windows=False):
    """Backend moves run here, in the owner's terminal, against state.db; never over the socket.

    A test home (AGENTMESH_HOME other than ~/.agentmesh) must name its LaunchAgents folder: the default is the real one,
    whose plists belong to the real supervisor."""
    if WINDOWS or windows: return local_windows(args,layout,call,console,tasks)
    from . import migrate
    from .store import Store
    if agents is None and getattr(args,'agents',None): agents=Path(args.agents).expanduser().absolute()
    if agents is None and layout.root!=Path.home()/'.agentmesh': return {'error':'agents_required'}
    if console is None:
        from ..presence import ConsolePresence
        console=ConsolePresence(output=lambda text:print(text,file=sys.stderr))
    layout.ensure(); store=Store(layout.state)
    try:
        if args.action=='handover':
            from . import handover
            label=args.label or handover.LABEL
            if args.back: return handover.hand_back(layout,console=console,agents=agents,label=label,launchd=launchd)
            return handover.handover(layout,console=console,agents=agents,app=args.app,label=label,launchd=launchd)
        if args.action=='panic-revert':
            return migrate.panic_revert(layout,store,launchd=launchd,agents=agents,label=getattr(args,'label',None))
        if args.action=='adopt':
            return migrate.adopt(layout,store,args.unit,console=console,call=call,launchd=launchd,agents=agents)
        if args.action=='consolidate':
            return migrate.consolidate(layout,store,console=console,call=call,apply=args.apply,launchd=launchd,agents=agents)
        if args.action=='reload': return migrate.reload(layout,store,args.unit,console=console,call=call,agents=agents)
        return migrate.release(layout,store,args.unit,console=console,launchd=launchd,agents=agents)
    finally: store.close()


def local_windows(args, layout, call, console, tasks):
    """The Windows moves: Task Scheduler instead of launchd, the same typed presence, against the same state.db."""
    from . import winmigrate
    from .store import Store
    # consolidate moves every unit at once; handover is the macOS login item
    if args.action in ('adopt','reload','handover'): return {'error':'unsupported_platform'}
    if tasks is None:
        from .taskscheduler import TaskSchedulerBackend
        tasks=TaskSchedulerBackend(folder=task_folder(layout,getattr(args,'task_folder',None)))
    if console is None:
        from ..presence import ConsolePresence
        console=ConsolePresence(output=lambda text:print(text,file=sys.stderr))
    layout.ensure(); store=Store(layout.state)
    try:
        if args.action=='consolidate':
            return winmigrate.consolidate(layout,store,console=console,call=call,tasks=tasks,apply=args.apply)
        if args.action=='release': return winmigrate.release(layout,store,args.unit,console=console,call=call,tasks=tasks)
        return winmigrate.panic_revert(layout,store,tasks=tasks,supervisor=getattr(args,'label',None))
    finally: store.close()
