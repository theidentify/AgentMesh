"""`agentmesh-log control ...`: run the supervisor, write its plist, or call it over the socket."""
import sys
from pathlib import Path
from .layout import Layout

CALLS={
    'status':lambda a:('status',{}),'units':lambda a:('units',{}),'quota':lambda a:('quota.status',{}),
    'diagnose':lambda a:('diagnose',{}),'audit':lambda a:('audit.tail',{'limit':a.limit}),
    'start':lambda a:('unit.start',{'unit':a.unit}),'stop':lambda a:('unit.stop',{'unit':a.unit}),
    'restart':lambda a:('unit.restart',{'unit':a.unit}),
    'maintenance':lambda a:('maintenance.on',{'reason':a.reason}) if a.state=='on' else ('maintenance.off',{}),
}


def add_parsers(commands):
    control=commands.add_parser('control'); sub=control.add_subparsers(dest='action',required=True)
    for action in ('serve','status','units','quota','diagnose'): sub.add_parser(action)
    for action in ('start','stop','restart'): sub.add_parser(action).add_argument('unit')
    maintenance=sub.add_parser('maintenance'); maintenance.add_argument('state',choices=['on','off'])
    maintenance.add_argument('--reason',default='maintenance')
    sub.add_parser('audit').add_argument('--limit',type=int,default=20)
    plist=sub.add_parser('plist'); plist.add_argument('--out',required=True); plist.add_argument('--python',default=sys.executable)


def serve(layout):
    if sys.platform!='darwin': return {'error':'unsupported_platform'}
    from .launchd import LaunchdBackend
    from .ops import Control
    from .server import Supervisor
    supervisor=Supervisor(Control(layout,LaunchdBackend()),layout)
    try: supervisor.serve()
    finally: supervisor.close()
    return {'ok':True}


def write_plist(layout, out, python):
    from .plist import render
    path=Path(out)
    if path.exists() or path.is_symlink(): return {'error':'exists'}
    path.write_bytes(render(layout,python))
    return {'ok':True,'path':str(path)}


def execute(args, *, layout=None, call=None):
    layout=layout or Layout()
    if args.action=='serve': return serve(layout)
    if args.action=='plist': return write_plist(layout,args.out,args.python)
    if call is None: from .client import call
    op,params=CALLS[args.action](args)
    try: response=call(layout,op,params)
    except OSError: return {'error':'supervisor_unavailable'}
    if not response.get('ok'): return {'error':response.get('error',{}).get('code','failed')}
    result=response.get('result')
    return result if isinstance(result,dict) else {'items':result}
