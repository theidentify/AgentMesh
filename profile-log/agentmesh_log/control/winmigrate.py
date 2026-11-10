"""Windows: owner-run moves of units between Task Scheduler and the supervisor. The counterpart of migrate.py.

`control consolidate` lists every AgentMesh scheduled task, says which unit each runs (from its action, not its name),
and with --apply moves each native-capable one under the supervisor's logon task, so Task Scheduler holds one AgentMesh
task instead of one per unit. Per task: wait for a job to finish its run, disable it, end it (a service), save its exported
XML under <home>/control/units/, delete it, then record its spec as native; the supervisor starts it on its next tick.
The broker goes last, so recall is down for about a second.
A task registered from an elevated window can be disabled and deleted only from one: consolidate then reports
`needs_elevation` and says to re-run it elevated. Files an elevated run saves are handed back to the user (winacl), so
release and panic-revert still work from a normal window. A retired unit's task (catch-up, removed at R5b) is saved
under control/units/retired/ and deleted, never supervised. A task that fails is left as it was, enabled.

`control release UNIT` and `control panic-revert` register a saved task again from its XML. state.db's backend value
`launchd` means "the OS scheduler" on Windows too, so the shared store, router and status need no second vocabulary.

Like migrate.py, these run in the owner's terminal with typed console presence (`Type migrate:`), never over the socket.
"""
import os
import sys
import time
from ..broker.audit import append_row
from . import procs, schedule, winacl, wintasks
from .taskscheduler import ELEVATE
from .layout import read_json, write_json
from .native import started_token
from .units import UNITS, get

WAIT_S=600
POLL_S=2
REPORTED=('busy','task_scheduler_failed','needs_elevation')
ACL=('Saved task files could not be handed to %s; release and panic-revert from a normal window may not read them. '
    'Fix with: icacls <file> /setowner %%USERNAME%% and icacls <file> /grant %%USERNAME%%:F')


UNREADABLE=('A saved task file under control\\units cannot be read from this window (an elevated consolidate wrote it). '
    'Re-run this command from an elevated PowerShell, or give the file back from one: '
    'icacls <file> /setowner %USERNAME% and icacls <file> /grant %USERNAME%:F')


def failure(exc): return str(exc) if str(exc) in REPORTED else type(exc).__name__


def advise(result):
    """Add the elevated-PowerShell advice to a result whose error (or one of whose errors) is needs_elevation."""
    errors=[e.get('error') for e in result.get('errors') or [] if isinstance(e,dict)]
    if result.get('error')=='needs_elevation' or 'needs_elevation' in errors: result['advice']=ELEVATE
    return result


def now_ms(): return time.time_ns()//1000000


def audit(layout, op, args, outcome):
    layout.ensure()
    append_row(layout.audit,dict(ts=now_ms(),op=op,args=args,outcome=outcome,caller_pid=os.getpid(),caller_exe=sys.executable))


def saved(layout, name, retired=False):
    return (layout.units/'retired' if retired else layout.units)/(name+'.task.json')


def save(layout, name, row, retired=False):
    path=saved(layout,name,retired)
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if os.path.lexists(path): raise FileExistsError(str(path))
    write_json(path,dict(task=row['task'],xml=row['xml'],saved_at=now_ms()))
    return path


def log_for(layout, name): return layout.root/'logs'/(name+'.log')


def is_supervisor(row):
    try: argv=wintasks.command(wintasks.parse(row['xml']))[0]
    except (ValueError,SyntaxError): return False
    return argv[-4:-2]==['control','serve'] or argv[-2:]==['control','serve']


def plan(layout, store, tasks):
    """move a task's unit under the supervisor, park a task whose unit is already native, retire a removed unit's task,
    skip anything else (with why). The supervisor's own task is not listed. Jobs first, the broker last."""
    rows=[r for r in tasks.tasks(fresh=True) if not is_supervisor(r)]
    counts={}
    for row in rows: counts[row['unit']]=counts.get(row['unit'],0)+1
    known={u.name:u for u in UNITS}
    out=[]
    for row in rows:
        name,base=row['unit'],dict(task=row['task'],unit=row['unit'],state=row['state'])
        if name is None: out.append(dict(base,action='skip',reason='not_a_unit')); continue
        if counts[name]>1: out.append(dict(base,action='skip',reason='duplicate')); continue
        if name in wintasks.RETIRED: out.append(dict(base,action='retire',xml=row['xml'])); continue
        unit=known.get(name)
        if unit is None or not unit.native_ok: out.append(dict(base,action='skip',reason='not_native_capable')); continue
        try: spec=wintasks.spec_from_task(row['xml'],log_for(layout,name))
        except (ValueError,SyntaxError): out.append(dict(base,action='skip',reason='unsupported_task')); continue
        action='park' if store.backend(name)=='native' else 'move'
        out.append(dict(base,action=action,mode=schedule.mode(spec),argv=spec['argv'],spec=spec,xml=row['xml']))
    order={'retire':0,'move':1,'park':1,'skip':3}
    out.sort(key=lambda r:(order[r['action']],r.get('mode')=='service',r['unit']=='broker'))
    return out


def wait_idle(tasks, task, wait_s, sleep):
    waited=0
    while True:
        row=next((r for r in tasks.tasks(fresh=True) if r['task']==task),None)
        if row is None or row['state']!='Running': return
        if waited>=wait_s: raise RuntimeError('busy')
        sleep(POLL_S); waited+=POLL_S


def take(layout, tasks, row, *, retired=False, wait_s=WAIT_S, sleep=time.sleep):
    """Disable, end, save and delete one task. On failure before the delete the task is enabled again."""
    task,service=row['task'],row.get('mode')=='service'
    if not service: wait_idle(tasks,task,wait_s,sleep)
    tasks.act('disable',task)
    try:
        if service or row.get('state')=='Running': tasks.act('stop',task)
        save(layout,row['unit'],row,retired)
    except (RuntimeError,OSError):
        try:
            tasks.act('enable',task)
            if service: tasks.act('start',task)
        except RuntimeError: pass
        raise
    tasks.act('delete',task)


def evict_reloaded(layout, store, tasks):
    """Supervisor start backstop: a native unit's task is registered and enabled again (re-imported by hand, or a
    release that never finished). Disable and end it, so only the supervisor runs the unit; audit `task_reloaded`."""
    native=store.native_units()
    if not native: return []
    evicted=[]
    for row in tasks.tasks(fresh=True):
        if row['unit'] not in native or row['state'] in (None,'Disabled'): continue
        try:
            tasks.act('disable',row['task'])
            if row['state']=='Running': tasks.act('stop',row['task'])
            outcome='ok'; evicted.append(row['unit'])
        except RuntimeError as exc: outcome=failure(exc)
        try: audit(layout,'task_reloaded',{'unit':row['unit'],'task':row['task']},outcome)
        except Exception: pass
    return evicted


def alive(layout, call):
    try: return call(layout,'status',{}).get('ok') is True
    except OSError: return False


def consolidate(layout, store, *, console, call, tasks, apply=False, sleep=time.sleep, wait_s=WAIT_S):
    """Every native-capable AgentMesh task -> the supervisor; the retired catch-up task deleted. Dry run unless apply."""
    rows=plan(layout,store,tasks)
    shown=[{k:v for k,v in row.items() if k not in ('spec','xml')} for row in rows]
    if not apply: return {'apply':False,'plan':shown}
    work=[row for row in rows if row['action']!='skip']
    skipped=[dict(task=row['task'],reason=row['reason']) for row in rows if row['action']=='skip']
    if not work: return {'ok':True,'moved':[],'parked':[],'retired':[],'skipped':skipped,'errors':[]}
    if not alive(layout,call): return {'error':'supervisor_unavailable'}
    lines=['  %s (%s, task %s): %s'%(r['unit'],r.get('mode','retired'),r['task'],
        ' '.join(r['argv']) if 'argv' in r else 'deleted, not supervised') for r in work]
    summary=('Run these under the AgentMesh supervisor (%s), one scheduled task instead of %d:\n%s\nEach task\'s XML is '
        'saved in %s. Undo one: agentmesh-log control release UNIT; undo all: agentmesh-log control panic-revert'
        %(wintasks.TASK_NAME,len(work)+1,'\n'.join(lines),layout.units))
    if not console.confirm('migrate',summary):
        audit(layout,'unit.consolidate',{},'presence_denied'); return {'error':'presence_required'}
    audit(layout,'unit.consolidate',{},'requested')
    moved,parked,retired,errors,written=[],[],[],[],[]
    for row in work:
        name=row['unit']
        try:
            take(layout,tasks,row,retired=row['action']=='retire',wait_s=wait_s,sleep=sleep)
            written.append(saved(layout,name,row['action']=='retire'))
            if row['action']=='move': store.set_backend(name,'native',row['spec'],now_ms()); moved.append(name)
            elif row['action']=='park': parked.append(name)
            else: retired.append(name)
            outcome='ok'
        except (RuntimeError,OSError) as exc:
            outcome=failure(exc)
            errors.append(dict(unit=name,task=row['task'],error=outcome))
        audit(layout,'unit.consolidate',{'unit':name,'task':row['task']},outcome)
    result={'ok':not errors,'moved':moved,'parked':parked,'retired':retired,'skipped':skipped,'errors':errors}
    if written:
        # an elevated run (needed for a task registered elevated) must leave files the user's normal token can read
        unfixed=winacl.give_to_user([layout.units,layout.units/'retired',*written])
        if unfixed: result['acl_unfixed'],result['warning']=unfixed,ACL%(winacl.user() or 'the user')
    return advise(result)


def restore(layout, tasks, name):
    """Register a saved task again and enable it; the saved file is removed once the task exists."""
    path=saved(layout,name); record=read_json(path)
    if record is None or type(record.get('task')) is not str or type(record.get('xml')) is not str: raise OSError('no_saved_task')
    if not any(r['task'].lower()==record['task'].lower() for r in tasks.tasks(fresh=True)):
        tasks.act('register',record['task'],record['xml'])
    tasks.act('enable',record['task'])
    os.replace(path,str(path)+'.restored')
    return record


def release(layout, store, name, *, console, call, tasks, sleep=time.sleep, wait_s=WAIT_S):
    """supervisor -> Task Scheduler: stop the native run (a service) or let it finish (a job), then register the task."""
    unit=next((u for u in UNITS if u.name==name and u.native_ok),None)
    if unit is None or store.backend(name)!='native': return {'error':'not_native'}
    try: record=read_json(saved(layout,name))
    except PermissionError: return {'error':'saved_task_unreadable','path':str(saved(layout,name)),'advice':UNREADABLE}
    if record is None: return {'error':'no_saved_task'}
    if not console.confirm('migrate','Move %s back from the AgentMesh supervisor to its scheduled task.'%name):
        audit(layout,'unit.release',{'unit':name},'presence_denied'); return {'error':'presence_required'}
    audit(layout,'unit.release',{'unit':name},'requested')
    if unit.kind=='service' and alive(layout,call):
        try: call(layout,'unit.stop',{'unit':name})
        except OSError: pass
    store.set_backend(name,'launchd',store.spec(name),now_ms())
    waited=0
    while store.open_runs(name) and waited<wait_s: sleep(POLL_S); waited+=POLL_S
    try:
        record=restore(layout,tasks,name)
        if unit.kind=='service': tasks.act('start',record['task'])
    except (RuntimeError,OSError) as exc:
        code='needs_elevation' if str(exc)=='needs_elevation' else 'task_scheduler_failed'
        audit(layout,'unit.release',{'unit':name},code); return advise({'error':code})
    audit(layout,'unit.release',{'unit':name},'ok')
    return {'ok':True,'unit':name,'backend':'task_scheduler','waited_s':waited}


def panic_revert(layout, store, *, tasks, token=started_token, sleep=time.sleep, supervisor=None):
    """Emergency return to the separate tasks without the supervisor: end and disable its task, end its runs, register
    every saved task again. Needs no presence: it only puts back what consolidate saved."""
    supervisor=supervisor or wintasks.TASK_NAME
    for action in ('disable','stop'):
        try: tasks.act(action,supervisor)
        except RuntimeError: pass
    sleep(1)
    stopped=[]
    for run in store.open_runs():
        if run['pid']>0 and run['pid_started'] is not None and token(run['pid'])==run['pid_started']:
            procs.terminate(run['pid']); stopped.append(run['unit'])
        store.end_run(run['id'],None,'panic',now_ms())
    reverted,errors,elevate,unreadable=[],[],False,False
    for name in sorted(store.native_units()):
        store.set_backend(name,'launchd',store.spec(name),now_ms())
        try:
            record=restore(layout,tasks,name)
            if get(name).kind=='service': tasks.act('start',record['task'])
            reverted.append(name)
        except (RuntimeError,OSError,KeyError) as exc:
            errors.append(name); elevate=elevate or str(exc)=='needs_elevation'
            unreadable=unreadable or isinstance(exc,PermissionError)
    try: audit(layout,'panic_revert',{},'ok' if not errors else 'partial')
    except Exception: pass
    result={'ok':not errors,'reverted':reverted,'stopped':stopped,'errors':errors}
    if elevate or unreadable: result['advice']=ELEVATE if elevate else UNREADABLE
    return result

