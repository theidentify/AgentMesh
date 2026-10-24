"""Native backend: the supervisor runs scheduled jobs itself, exactly as their launchd plists did.

Enforcing watchdog: a run past its unit's max_runtime_s gets SIGTERM to its process group, then SIGKILL after
KILL_GRACE_S. More than STORM_KILLS watchdog kills within STORM_WINDOW_S puts the unit in `failed`; a failed unit is
never started again (by schedule, start or restart) until `control reset`.
"""
import os
import signal
import subprocess
import threading
import time
from . import schedule

KILL_GRACE_S=10
STORM_KILLS=3
STORM_WINDOW_S=3600


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
    def __init__(self, store, units, *, clock=time.time, notify=None, environ=None, token=started_token):
        self.store,self.units,self.clock,self.token=store,{u.name:u for u in units},clock,token
        self.notify=notify or (lambda text:None)
        self.environ=dict(os.environ) if environ is None else environ
        self.lock=threading.RLock(); self.running={}

    def now_ms(self): return int(self.clock()*1000)

    def status(self, unit):
        with self.lock: run=self.running.get(unit.name)
        last,failed=self.store.last_exit(unit.name),self.store.failed(unit.name) is not None
        if run is None: return dict(loaded=True,running=False,pid=None,last_exit=last,runtime_s=None,failed=failed)
        return dict(loaded=True,running=True,pid=run['proc'].pid,last_exit=last,runtime_s=int(self.clock()-run['started']),failed=failed)

    def start(self, unit):
        with self.lock:
            if self.store.failed(unit.name): raise RuntimeError('unit_failed')
            if unit.name not in self.running: self.spawn(unit,self.store.spec(unit.name))
        return True

    def restart(self, unit):
        with self.lock:
            run=self.running.get(unit.name)
            if run is None: return self.start(unit)
            run['restart']=True; self.terminate(run,'stop')
        return True

    def stop(self, unit):
        with self.lock:
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
            self.store.end_run(self.store.start_run(unit.name,-1,None,at),127,'launch_error',at)
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
                last=self.store.last_start(name)
                if last is not None: reference=last/1000
                elif spec['run_at_load']: reference=None
                else: reference=(self.store.changed_at(name) or 0)/1000
                if schedule.due(spec,reference,now): self.spawn(unit,spec)
        for text in alerts: threading.Thread(target=self.notify,args=(text,),daemon=True).start()

    def finish(self, name, run, code):
        del self.running[name]
        at=self.now_ms()
        outcome={'watchdog':'killed_stuck','stop':'stopped'}.get(run['why']) or ('ok' if code==0 else 'failed')
        self.store.end_run(run['run_id'],code,outcome,at)
        alerts=[]
        if run['why']=='watchdog':
            self.store.add_kill(name,at)
            if self.store.kills_since(name,at-STORM_WINDOW_S*1000)>STORM_KILLS:
                self.store.set_failed(name,'restart_storm',at)
                alerts.append('🔴 AgentMesh control: `%s` stopped after more than %d watchdog kills in %d min. '
                    'Fix it, then run `agentmesh-log control reset %s`.'%(name,STORM_KILLS,STORM_WINDOW_S//60,name))
        if run['restart'] and not self.store.failed(name) and name in self.store.native_units():
            self.spawn(self.units[name],self.store.spec(name))
        return alerts

    def reconcile(self):
        """Runs a previous supervisor left open: SIGTERM the orphan only if it is still that same process."""
        for run in self.store.open_runs():
            if run['pid']>0 and run['pid_started'] is not None and self.token(run['pid'])==run['pid_started']:
                signal_group(run['pid'],signal.SIGTERM)
            self.store.end_run(run['id'],None,'orphaned',self.now_ms())

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
