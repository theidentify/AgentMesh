"""Native backend: the supervisor runs units itself, exactly as their launchd plists did.

Services (KeepAlive) are kept running. A service that exits is started again after a backoff: its ThrottleInterval
after a run that lasted STABLE_S or more, else BACKOFF_BASE_S doubling per quick exit in a row, capped at BACKOFF_MAX_S.
`control stop` holds a service down until `control start` or the next supervisor start. Timers run on their schedule;
a RunAtLoad-only job runs once per supervisor start.

Enforcing watchdog: a run past its unit's max_runtime_s gets SIGTERM to its process group, then SIGKILL after
KILL_GRACE_S. More than STORM_KILLS watchdog kills within STORM_WINDOW_S puts the unit in `failed`; a failed unit is
never started again (by schedule, start or restart) until `control reset`.

Failure streaks: the FAIL_STREAK-th failed run in a row alerts once, and the first ok run after such a streak alerts
that the unit recovered. Both come from the runs table, so a supervisor restart neither repeats nor loses them.
"""
import os
import signal
import subprocess
import threading
import time
from . import schedule
from .store import FAILURES

KILL_GRACE_S=10
STORM_KILLS=3
STORM_WINDOW_S=3600
FAIL_STREAK=3
STABLE_S=60
BACKOFF_BASE_S=1
BACKOFF_MAX_S=300


def started_token(pid, runner=subprocess.run):
    """`ps -o lstart` of pid. With the pid it names one process, so a reused pid is never signalled."""
    try: process=runner(['/bin/ps','-o','lstart=','-p',str(pid)],capture_output=True,text=True,timeout=5)
    except (OSError,subprocess.SubprocessError): return None
    if process.returncode!=0: return None
    return process.stdout.strip() or None


def open_log(path):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600)
    return os.fdopen(fd,'ab')


def signal_group(pid, sig):
    try: os.killpg(pid,sig)
    except (ProcessLookupError,PermissionError): pass


class NativeBackend:
    def __init__(self, store, units, *, clock=time.time, notify=None, environ=None, token=started_token, sleep=time.sleep):
        self.store,self.units,self.clock,self.token,self.sleep=store,{u.name:u for u in units},clock,token,sleep
        self.notify=notify or (lambda text:None)
        self.environ=dict(os.environ) if environ is None else environ
        self.lock=threading.RLock(); self.running={}; self.pending=[]
        self.held=set()        # services the owner stopped; not restarted until start
        self.loaded=set()      # once-at-load jobs already started by this supervisor
        self.backoff={}        # service -> dict(quick=exits in a row shorter than STABLE_S, next=earliest restart)

    def now_ms(self): return int(self.clock()*1000)

    def status(self, unit):
        with self.lock: run=self.running.get(unit.name)
        last,failed=self.store.last_exit(unit.name),self.store.failed(unit.name) is not None
        if run is None: return dict(loaded=True,running=False,pid=None,last_exit=last,runtime_s=None,failed=failed)
        return dict(loaded=True,running=True,pid=run['proc'].pid,last_exit=last,runtime_s=int(self.clock()-run['started']),failed=failed)

    def start(self, unit):
        with self.lock:
            if self.store.failed(unit.name): raise RuntimeError('unit_failed')
            self.held.discard(unit.name); self.backoff.pop(unit.name,None)
            if unit.name not in self.running: self.spawn(unit,self.store.spec(unit.name))
        return True

    def restart(self, unit):
        with self.lock:
            run=self.running.get(unit.name)
            if run is None: return self.start(unit)
            self.held.discard(unit.name); self.backoff.pop(unit.name,None)
            run['restart']=True; self.terminate(run,'stop')
        return True

    def stop(self, unit):
        with self.lock:
            spec=self.store.spec(unit.name)
            if spec is not None and schedule.mode(spec)=='service': self.held.add(unit.name)
            run=self.running.get(unit.name)
            if run is not None: self.terminate(run,'stop')
        return True

    def reset(self, unit): return self.store.clear_failed(unit.name)

    def spawn(self, unit, spec):
        at=self.now_ms(); out=err=None
        try:
            out=open_log(spec['stdout']); err=out if spec['stderr']==spec['stdout'] else open_log(spec['stderr'])
            proc=subprocess.Popen(spec['argv'],cwd=spec['cwd'] or self.environ.get('HOME') or '/',
                env=schedule.child_env(spec,self.environ),stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                start_new_session=True,umask=spec['umask'],close_fds=True)
        except OSError:
            run_id=self.store.start_run(unit.name,-1,None,at)
            self.store.end_run(run_id,127,'launch_error',at)
            self.pending+=self.streak_alerts(unit.name,run_id,'launch_error',127,spec)
            if schedule.mode(spec)=='service': self.schedule_restart(unit.name,dict(started=self.clock()),spec)
            return False
        finally:
            for stream in {id(s):s for s in (out,err) if s is not None}.values(): stream.close()
        run_id=self.store.start_run(unit.name,proc.pid,self.token(proc.pid),at)
        self.running[unit.name]=dict(proc=proc,run_id=run_id,started=self.clock(),term_at=None,why=None,restart=False)
        return True

    def terminate(self, run, why):
        if run['term_at'] is None:
            run['term_at'],run['why']=self.clock(),why
            signal_group(run['proc'].pid,signal.SIGTERM)

    def tick(self):
        """One supervisor second: reap finished runs, enforce the watchdog, then start due jobs."""
        alerts=[]
        with self.lock:
            now=self.clock()
            for name,run in list(self.running.items()):
                code=run['proc'].poll()
                if code is not None:
                    alerts+=self.finish(name,run,code); continue
                limit=self.units[name].max_runtime_s
                if run['term_at'] is None and limit and now-run['started']>limit: self.terminate(run,'watchdog')
                elif run['term_at'] is not None and now-run['term_at']>=KILL_GRACE_S: signal_group(run['proc'].pid,signal.SIGKILL)
            for name,spec in self.store.native_units().items():
                unit=self.units.get(name)
                if unit is None or name in self.running or self.store.failed(name): continue
                if self.due(name,spec,now): self.spawn(unit,spec)
            alerts+=self.pending; self.pending=[]
        for text in alerts: threading.Thread(target=self.notify,args=(text,),daemon=True).start()

    def due(self, name, spec, now):
        kind=schedule.mode(spec)
        if kind=='service': return name not in self.held and now>=self.backoff.get(name,{}).get('next',0)
        if kind=='once':
            if name in self.loaded: return False
            self.loaded.add(name); return True
        last=self.store.last_start(name)
        if last is not None: reference=last/1000
        elif spec['run_at_load']: reference=None
        else: reference=(self.store.changed_at(name) or 0)/1000
        return schedule.due(spec,reference,now)

    def schedule_restart(self, name, run, spec):
        """A service exited on its own: when it may start again."""
        now=self.clock(); state=self.backoff.setdefault(name,dict(quick=0,next=0))
        if now-run['started']>=STABLE_S: state['quick']=0; delay=spec.get('throttle',schedule.THROTTLE_S)
        else:
            state['quick']+=1
            delay=max(spec.get('throttle',schedule.THROTTLE_S) if state['quick']==1 else 0,
                min(BACKOFF_BASE_S*2**(state['quick']-1),BACKOFF_MAX_S))
        state['next']=now+delay

    def finish(self, name, run, code):
        del self.running[name]
        at=self.now_ms()
        outcome={'watchdog':'killed_stuck','stop':'stopped'}.get(run['why']) or ('ok' if code==0 else 'failed')
        self.store.end_run(run['run_id'],code,outcome,at)
        alerts=self.streak_alerts(name,run['run_id'],outcome,code,self.store.spec(name))
        if run['why']=='watchdog':
            self.store.add_kill(name,at)
            if self.store.kills_since(name,at-STORM_WINDOW_S*1000)>STORM_KILLS:
                self.store.set_failed(name,'restart_storm',at)
                alerts.append('🔴 AgentMesh control: `%s` stopped after more than %d watchdog kills in %d min. '
                    'Fix it, then run `agentmesh-log control reset %s`.'%(name,STORM_KILLS,STORM_WINDOW_S//60,name))
        spec=self.store.spec(name) if name in self.store.native_units() else None
        if run['restart'] and not self.store.failed(name) and spec is not None:
            self.spawn(self.units[name],spec)
        elif spec is not None and run['why'] is None and schedule.mode(spec)=='service': self.schedule_restart(name,run,spec)
        return alerts

    def streak_alerts(self, name, run_id, outcome, code, spec):
        """One alert when a streak reaches FAIL_STREAK, one when the first ok run ends it; nothing in between."""
        if outcome=='ok':
            before=self.store.failure_streak(name,before_id=run_id)
            return ['🟢 AgentMesh control: `%s` ran ok again after %d failed runs.'%(name,before)] if before>=FAIL_STREAK else []
        if outcome not in FAILURES or self.store.failure_streak(name)!=FAIL_STREAK: return []
        log=(spec or {}).get('stderr') or 'its log'
        return ['🟠 AgentMesh control: `%s` failed %d runs in a row (last: %s, exit %s). See %s.'%(name,FAIL_STREAK,outcome,code,log)]

    def snapshot(self):
        """Content-free run state for readers outside the socket (Insight): which native units run, as which pid."""
        with self.lock:
            units={}
            for name,spec in sorted(self.store.native_units().items()):
                run=self.running.get(name)
                units[name]=dict(mode=schedule.mode(spec),running=run is not None,pid=run['proc'].pid if run else None,
                    started_at=int(run['started']) if run else None,held=name in self.held,
                    failed=self.store.failed(name) is not None,next_start_at=self.backoff.get(name,{}).get('next'))
            return dict(v=1,supervisor_pid=os.getpid(),units=units)

    def reconcile(self):
        """Runs a previous supervisor left open: SIGTERM the orphan only if it is still that same process, and wait for
        it (SIGKILL after KILL_GRACE_S), so a restarted service does not race its orphan for a port or a lock."""
        alive=[]
        for run in self.store.open_runs():
            if run['pid']>0 and run['pid_started'] is not None and self.token(run['pid'])==run['pid_started']:
                signal_group(run['pid'],signal.SIGTERM); alive.append(run)
            self.store.end_run(run['id'],None,'orphaned',self.now_ms())
        waited=0.0
        while alive and waited<KILL_GRACE_S:
            self.sleep(0.2); waited+=0.2
            alive=[run for run in alive if self.token(run['pid'])==run['pid_started']]
        for run in alive: signal_group(run['pid'],signal.SIGKILL)
        return len(alive)

    def shutdown(self):
        """Supervisor exit: stop every native run, SIGKILL what ignores SIGTERM, and close the records."""
        with self.lock:
            runs=list(self.running.items())
            for _,run in runs: run['restart']=False; self.terminate(run,'stop')
        deadline=time.monotonic()+KILL_GRACE_S
        for _,run in runs:
            try: run['proc'].wait(max(0.0,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                signal_group(run['proc'].pid,signal.SIGKILL); run['proc'].wait(5)
        with self.lock:
            for name,run in runs:
                if self.running.get(name) is run: self.finish(name,run,run['proc'].returncode)
