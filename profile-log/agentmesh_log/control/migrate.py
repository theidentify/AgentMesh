"""Owner-run moves of a unit between launchd and the supervisor, and the panic revert.

These run in the owner's terminal, never over the socket. adopt and release need typed console presence (the same
`Type migrate:` confirmation `apply` uses), so an agent's non-interactive shell cannot move units. Plists stay on disk.
panic-revert needs no presence: it only returns units to their original launchd plists.
"""
import os
import signal
import sys
import time
from ..broker.audit import append_row
from . import schedule
from .launchd import LaunchdBackend
from .native import signal_group, started_token
from .plist import LABEL
from .units import get

WAIT_S=600
POLL_S=2


def now_ms(): return time.time_ns()//1000000


def audit(layout, op, args, outcome):
    layout.ensure()
    append_row(layout.audit,dict(ts=now_ms(),op=op,args=args,outcome=outcome,caller_pid=os.getpid(),caller_exe=sys.executable))


def native_unit(name):
    try: unit=get(name)
    except KeyError: return None
    return unit if unit.native_ok else None


def adopt(layout, store, name, *, console, call, launchd=None, agents=None, sleep=time.sleep, wait_s=WAIT_S):
    """launchd -> supervisor: wait for the launchd job to be idle, boot it out, record the plist's spec as native."""
    unit=native_unit(name)
    if unit is None: return {'error':'not_native_capable'}
    if store.backend(name)=='native': return {'error':'already_native'}
    try: spec=schedule.read_spec(unit,agents)
    except Exception: return {'error':'unsupported_plist'}
    try: alive=call(layout,'status',{}).get('ok') is True
    except OSError: alive=False
    if not alive: return {'error':'supervisor_unavailable'}
    summary=('Move %s (%s) from launchd to the AgentMesh supervisor.\nIt will run: %s\nUndo: agentmesh-log control release %s'
        %(name,unit.label,' '.join(spec['argv']),name))
    if not console.confirm('migrate',summary):
        audit(layout,'unit.adopt',{'unit':name},'presence_denied'); return {'error':'presence_required'}
    audit(layout,'unit.adopt',{'unit':name},'requested')
    launchd=launchd or LaunchdBackend(); waited=0
    while launchd.status(unit)['running']:
        if waited>=wait_s:
            audit(layout,'unit.adopt',{'unit':name},'busy'); return {'error':'busy'}
        sleep(POLL_S); waited+=POLL_S
    try: launchd.bootout(unit)
    except RuntimeError:
        if launchd.status(unit)['loaded']:
            audit(layout,'unit.adopt',{'unit':name},'launchctl_failed'); return {'error':'launchctl_failed'}
    store.set_backend(name,'native',spec,now_ms())
    audit(layout,'unit.adopt',{'unit':name},'ok')
    return {'ok':True,'unit':name,'backend':'native'}


def release(layout, store, name, *, console, launchd=None, agents=None, sleep=time.sleep, wait_s=WAIT_S):
    """supervisor -> launchd: no new native runs at once; a run in progress may finish (up to wait_s) first."""
    unit=native_unit(name)
    if unit is None or store.backend(name)!='native': return {'error':'not_native'}
    summary='Move %s back from the AgentMesh supervisor to launchd (%s).'%(name,unit.label)
    if not console.confirm('migrate',summary):
        audit(layout,'unit.release',{'unit':name},'presence_denied'); return {'error':'presence_required'}
    audit(layout,'unit.release',{'unit':name},'requested')
    store.set_backend(name,'launchd',store.spec(name),now_ms())
    waited=0
    while store.open_runs(name) and waited<wait_s: sleep(POLL_S); waited+=POLL_S
    launchd=launchd or LaunchdBackend()
    if not launchd.status(unit)['loaded']:
        try: launchd.bootstrap(unit,schedule.plist_path(unit,agents))
        except RuntimeError:
            audit(layout,'unit.release',{'unit':name},'launchctl_failed'); return {'error':'launchctl_failed'}
    audit(layout,'unit.release',{'unit':name},'ok')
    return {'ok':True,'unit':name,'backend':'launchd','waited_s':waited}


def panic_revert(layout, store, *, launchd=None, agents=None, token=started_token, sleep=time.sleep):
    """Emergency return to launchd without the supervisor: stop it, stop its runs, re-bootstrap the original plists."""
    launchd=launchd or LaunchdBackend()
    launchd.runner(['/bin/launchctl','bootout',launchd.domain+'/'+LABEL])
    sleep(1)
    stopped=[]
    for run in store.open_runs():
        if run['pid']>0 and run['pid_started'] is not None and token(run['pid'])==run['pid_started']:
            signal_group(run['pid'],signal.SIGTERM); stopped.append(run['unit'])
        store.end_run(run['id'],None,'panic',now_ms())
    reverted,errors=[],[]
    for name in sorted(store.native_units()):
        store.set_backend(name,'launchd',store.spec(name),now_ms())
        unit=get(name)
        try:
            if not launchd.status(unit)['loaded']: launchd.bootstrap(unit,schedule.plist_path(unit,agents))
            reverted.append(name)
        except RuntimeError: errors.append(name)
    try: audit(layout,'panic_revert',{},'ok' if not errors else 'partial')
    except Exception: pass
    return {'ok':not errors,'reverted':reverted,'stopped':stopped,'errors':errors}
