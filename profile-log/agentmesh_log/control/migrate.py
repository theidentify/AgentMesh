"""Owner-run moves of a unit between launchd and the supervisor, and the panic revert.

These run in the owner's terminal, never over the socket. adopt, release, consolidate and reload need typed console
presence (the same `Type migrate:` confirmation `apply` uses), so an agent's non-interactive shell cannot move units.
panic-revert needs no presence: it only returns units to their original launchd plists.

adopt leaves the plist in ~/Library/LaunchAgents (disabled). consolidate adopts every native-capable unit at once and
parks each plist under <home>/control/units/, so macOS lists one background item (the supervisor) instead of one per
unit. release and panic-revert put a parked plist back before they bootstrap it.
"""
import os
import shutil
import signal
import sys
import time
from ..broker.audit import append_row
from . import schedule
from .launchd import LaunchdBackend
from .native import signal_group, started_token
from .plist import LABEL
from .units import UNITS, get

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
    """launchd -> supervisor: wait for the launchd job to be idle, disable and boot it out, record the plist's spec as
    native. Disable first: bootout alone leaves the plist to be loaded again at the next reboot or login."""
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
    # A service is never idle: it stops when launchd boots it out and the supervisor starts it on its next tick.
    while unit.kind=='job' and launchd.status(unit)['running']:
        if waited>=wait_s:
            audit(layout,'unit.adopt',{'unit':name},'busy'); return {'error':'busy'}
        sleep(POLL_S); waited+=POLL_S
    try: launchd.disable(unit)
    except RuntimeError:
        audit(layout,'unit.adopt',{'unit':name},'launchctl_failed'); return {'error':'launchctl_failed'}
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
    try:
        launchd.enable(unit)
        if not launchd.status(unit)['loaded']: launchd.bootstrap(unit,restore_plist(layout,unit,agents))
    except (RuntimeError,OSError):
        audit(layout,'unit.release',{'unit':name},'launchctl_failed'); return {'error':'launchctl_failed'}
    audit(layout,'unit.release',{'unit':name},'ok')
    return {'ok':True,'unit':name,'backend':'launchd','waited_s':waited}


def evict_reloaded(layout, store, launchd):
    """Supervisor start backstop: launchd loaded a native unit again (a reboot or login after an adopt without disable).
    Disable and boot it out, so only the supervisor schedules it, and record `launchd_reloaded`."""
    evicted=[]
    for name in sorted(store.native_units()):
        unit=native_unit(name)
        if unit is None or not launchd.status(unit)['loaded']: continue
        try:
            launchd.disable(unit)
            try: launchd.bootout(unit)
            except RuntimeError:
                if launchd.status(unit)['loaded']: raise
            outcome='ok'; evicted.append(name)
        except RuntimeError: outcome='launchctl_failed'
        try: audit(layout,'launchd_reloaded',{'unit':name},outcome)
        except Exception: pass
    return evicted


def panic_revert(layout, store, *, launchd=None, agents=None, token=started_token, sleep=time.sleep, label=None):
    """Emergency return to launchd without the supervisor: stop it, stop its runs, re-bootstrap the original plists."""
    launchd=launchd or LaunchdBackend()
    launchd.runner(['/bin/launchctl','bootout',launchd.domain+'/'+(label or LABEL)])
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
            launchd.enable(unit)
            if not launchd.status(unit)['loaded']: launchd.bootstrap(unit,restore_plist(layout,unit,agents))
            reverted.append(name)
        except (RuntimeError,OSError): errors.append(name)
    try: audit(layout,'panic_revert',{},'ok' if not errors else 'partial')
    except Exception: pass
    return {'ok':not errors,'reverted':reverted,'stopped':stopped,'errors':errors}


def parked(layout, unit): return layout.units/(unit.label+'.plist')


def restore_plist(layout, unit, agents=None):
    """The unit's plist in LaunchAgents, moved back from <home>/control/units/ when consolidate parked it there."""
    home,kept=schedule.plist_path(unit,agents),parked(layout,unit)
    if not os.path.lexists(home) and os.path.lexists(kept):
        home.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(kept),str(home))
    return home


def park(layout, unit, agents=None):
    layout.units.mkdir(mode=0o700,exist_ok=True)
    target=parked(layout,unit)
    if os.path.lexists(target): raise FileExistsError(str(target))
    shutil.move(str(schedule.plist_path(unit,agents)),str(target)); os.chmod(target,0o600)
    return target


def consolidate_plan(layout, store, agents=None):
    """What consolidate would do: `move` a launchd unit under the supervisor, `park` the plist of a unit that is already
    native (adopted earlier), `skip` a plist the native backend cannot reproduce. Jobs first, services last."""
    rows=[]
    for unit in UNITS:
        path=schedule.plist_path(unit,agents)
        if not unit.native_ok or not path.is_file(): continue
        try: spec=schedule.read_spec(unit,path=path)
        except Exception:
            rows.append(dict(unit=unit.name,label=unit.label,action='skip',reason='unsupported_plist')); continue
        action='park' if store.backend(unit.name)=='native' else 'move'
        rows.append(dict(unit=unit.name,label=unit.label,action=action,mode=schedule.mode(spec),argv=spec['argv'],spec=spec))
    rows.sort(key=lambda r:(r['action']=='skip',r.get('mode')=='service'))
    return rows


def consolidate(layout, store, *, console, call, apply=False, launchd=None, agents=None, sleep=time.sleep, wait_s=WAIT_S):
    """Every native-capable launchd unit -> the supervisor, plists parked out of LaunchAgents. Dry run unless apply.

    Per unit, as adopt: wait for a job to be idle, disable, boot out, then record the spec as native. A service is down
    from its bootout until the supervisor's next tick (about a second). A unit that fails stays on launchd, plist in place.
    """
    plan=consolidate_plan(layout,store,agents)
    shown=[{k:v for k,v in row.items() if k!='spec'} for row in plan]
    if not apply: return {'apply':False,'plan':shown}
    work=[row for row in plan if row['action'] in ('move','park')]
    skipped=[row['unit'] for row in plan if row['action']=='skip']
    if not work: return {'ok':True,'moved':[],'parked':[],'skipped':skipped,'errors':[]}
    try: alive=call(layout,'status',{}).get('ok') is True
    except OSError: alive=False
    if not alive: return {'error':'supervisor_unavailable'}
    summary='Run these under the AgentMesh supervisor (org.agentmesh.control), one background item instead of %d:\n%s\n' \
        'Plists move to %s. Undo one: agentmesh-log control release UNIT; undo all: agentmesh-log control panic-revert' \
        %(len(work)+1,'\n'.join('  %s (%s): %s'%(r['unit'],r['mode'],' '.join(r['argv'])) for r in work),layout.units)
    if not console.confirm('migrate',summary):
        audit(layout,'unit.consolidate',{},'presence_denied'); return {'error':'presence_required'}
    audit(layout,'unit.consolidate',{},'requested')
    launchd=launchd or LaunchdBackend(); moved,parked_,errors=[],[],[]
    for row in work:
        name=row['unit']; unit=get(name)
        try:
            if row['action']=='move':
                waited=0
                while unit.kind=='job' and launchd.status(unit)['running']:
                    if waited>=wait_s: raise RuntimeError('busy')
                    sleep(POLL_S); waited+=POLL_S
                launchd.disable(unit)
                try: launchd.bootout(unit)
                except RuntimeError:
                    if launchd.status(unit)['loaded']: raise
            park(layout,unit,agents)
            if row['action']=='move': store.set_backend(name,'native',row['spec'],now_ms()); moved.append(name)
            else: parked_.append(name)
            outcome='ok'
        except (RuntimeError,OSError) as exc:
            outcome=str(exc) if str(exc) in ('busy','launchctl_failed') else type(exc).__name__
            errors.append(dict(unit=name,error=outcome))
            if row['action']=='move' and store.backend(name)!='native':
                try: launchd.enable(unit)  # leave it as it was: launchd's, loadable at the next login
                except RuntimeError: pass
        audit(layout,'unit.consolidate',{'unit':name},outcome)
    return {'ok':not errors,'moved':moved,'parked':parked_,'skipped':skipped,'errors':errors}


def reload(layout, store, name, *, console, call, agents=None):
    """Re-read a native unit's plist (parked, or still in LaunchAgents after adopt) and restart it with the new spec.
    For tools that edit a unit's plist, such as insight/deploy_insight.py."""
    unit=native_unit(name)
    if unit is None or store.backend(name)!='native': return {'error':'not_native'}
    path=parked(layout,unit)
    if not path.is_file(): path=schedule.plist_path(unit,agents)
    try: spec=schedule.read_spec(unit,path=path)
    except Exception: return {'error':'unsupported_plist'}
    if not console.confirm('migrate','Restart %s with %s:\n%s'%(name,path,' '.join(spec['argv']))):
        audit(layout,'unit.reload',{'unit':name},'presence_denied'); return {'error':'presence_required'}
    store.set_backend(name,'native',spec,now_ms())
    try: response=call(layout,'unit.restart',{'unit':name})
    except OSError: response={'ok':False}
    outcome='ok' if response.get('ok') else 'restart_failed'
    audit(layout,'unit.reload',{'unit':name},outcome)
    return {'ok':True,'unit':name,'path':str(path)} if outcome=='ok' else {'error':'supervisor_unavailable'}
