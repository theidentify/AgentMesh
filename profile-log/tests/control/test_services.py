"""The supervisor's service runner: KeepAlive services restart with backoff, the owner can hold one down, a RunAtLoad-only
job runs once per supervisor start, and shutdown stops everything. Real child processes; a fake clock."""
import json
import os
import subprocess
import sys
import threading
import time
import pytest
from agentmesh_log.control import cli, native as native_module
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.native import BACKOFF_MAX_S, NativeBackend, STABLE_S, started_token
from agentmesh_log.control.store import Store
from agentmesh_log.control.units import UNITS, get

pytestmark=pytest.mark.skipif(os.name=='nt',reason='POSIX process groups')
FOREVER='import time\nwhile True: time.sleep(1)'


def make(tmp_path, **kw):
    store=Store(tmp_path/'state.db'); clock=[1000.0]
    native=NativeBackend(store,UNITS,clock=lambda:clock[0],environ={'HOME':str(tmp_path)},**kw)
    return store,native,clock


def service(tmp_path, code, **kw):
    return dict(dict(argv=[sys.executable,'-c',code],env={},cwd=str(tmp_path),stdout=str(tmp_path/'svc.out.log'),
        stderr=str(tmp_path/'svc.err.log'),umask=0o77,interval=None,calendar=None,run_at_load=True,keep_alive=True,
        throttle=10),**kw)


def reap(native, name):
    run=native.running.get(name)
    if run is not None: run['proc'].wait(10)
    native.tick()


def outcomes(store, unit):
    return [row[0] for row in store._all('SELECT outcome FROM runs WHERE unit=? ORDER BY id',(unit,))]


@pytest.fixture
def cleanup():
    natives=[]
    yield natives.append
    for native in natives: native.shutdown()


def test_service_runs_and_logs(tmp_path, cleanup):
    store,native,_=make(tmp_path); cleanup(native)
    store.set_backend('broker','native',service(tmp_path,'import sys,time; print("up",flush=True); time.sleep(30)'),1)
    native.tick()
    status=native.status(get('broker'))
    assert status['running'] is True and status['pid']>0
    for _ in range(50):
        if (tmp_path/'svc.out.log').exists() and (tmp_path/'svc.out.log').read_text(): break
        time.sleep(0.1)
    assert (tmp_path/'svc.out.log').read_text()=='up\n'
    assert native.snapshot()['units']['broker']==dict(mode='service',running=True,pid=status['pid'],started_at=1000,
        held=False,failed=False,next_start_at=None)


def test_crash_backoff(tmp_path, cleanup):
    store,native,clock=make(tmp_path); cleanup(native)
    store.set_backend('bridge','native',service(tmp_path,'import sys; sys.exit(2)',throttle=0),1)
    delays=[]
    for _ in range(12):
        native.tick(); assert 'bridge' in native.running
        reap(native,'bridge'); assert 'bridge' not in native.running
        delays.append(native.backoff['bridge']['next']-clock[0])
        native.tick(); assert 'bridge' not in native.running or delays[-1]==0  # no restart before the backoff ends
        clock[0]=native.backoff['bridge']['next']
    assert delays[:6]==[1,2,4,8,16,32] and delays[-1]==BACKOFF_MAX_S and max(delays)==BACKOFF_MAX_S
    assert set(outcomes(store,'bridge'))=={'failed'}


def test_throttle_and_reset(tmp_path, cleanup):
    store,native,clock=make(tmp_path); cleanup(native)
    store.set_backend('broker','native',service(tmp_path,'import sys; sys.exit(1)',throttle=30),1)
    native.tick(); reap(native,'broker')
    assert native.backoff['broker']['next']-clock[0]==30  # ThrottleInterval, as launchd would
    clock[0]+=30; native.tick(); reap(native,'broker')
    assert native.backoff['broker']=={'quick':2,'next':clock[0]+2}
    clock[0]+=2; native.tick(); clock[0]+=STABLE_S; reap(native,'broker')  # this run lasted a minute
    assert native.backoff['broker']=={'quick':0,'next':clock[0]+30}


def test_stop_holds(tmp_path, cleanup):
    store,native,clock=make(tmp_path); cleanup(native)
    store.set_backend('insight','native',service(tmp_path,FOREVER),1)
    native.tick(); proc=native.running['insight']['proc']
    native.stop(get('insight')); assert proc.wait(5)==-15
    for step in range(3): clock[0]+=BACKOFF_MAX_S; native.tick()
    assert 'insight' not in native.running and outcomes(store,'insight')==['stopped']
    assert native.snapshot()['units']['insight']['held'] is True
    native.start(get('insight')); assert 'insight' in native.running
    assert native.snapshot()['units']['insight']['held'] is False


def test_restart_skips_backoff(tmp_path, cleanup):
    store,native,clock=make(tmp_path); cleanup(native)
    store.set_backend('broker','native',service(tmp_path,FOREVER),1)
    native.tick(); first=native.running['broker']['proc']
    native.restart(get('broker')); first.wait(5); native.tick()
    assert native.running['broker']['proc'] is not first and outcomes(store,'broker')==['stopped',None]


def test_launch_error_backoff(tmp_path, cleanup):
    store,native,clock=make(tmp_path); cleanup(native)
    store.set_backend('broker','native',service(tmp_path,'',argv=[str(tmp_path/'missing')]),1)
    native.tick(); native.tick(); native.tick()
    assert outcomes(store,'broker')==['launch_error'] and native.backoff['broker']['next']==clock[0]+10


def test_once_per_start(tmp_path):
    store,native,clock=make(tmp_path)
    once=dict(service(tmp_path,'pass'),keep_alive=False)
    store.set_backend('ollama-env','native',once,1)
    native.tick(); reap(native,'ollama-env')
    for _ in range(3): clock[0]+=86400; native.tick()
    assert outcomes(store,'ollama-env')==['ok'] and 'ollama-env' not in native.running
    _,again,_=make(tmp_path); again.tick(); reap(again,'ollama-env')  # the next supervisor start runs it again
    assert outcomes(store,'ollama-env')==['ok','ok']


def test_timers_beside_services(tmp_path, cleanup):
    store,native,clock=make(tmp_path); cleanup(native)
    store.set_backend('broker','native',service(tmp_path,FOREVER),1)
    timer=dict(service(tmp_path,'pass'),keep_alive=False,interval=60)
    del timer['keep_alive'],timer['throttle']  # as an M2-era adopt recorded it
    store.set_backend('ingest','native',timer,1)
    native.tick(); assert {'broker','ingest'}<=set(native.running)
    reap(native,'ingest'); clock[0]+=59; native.tick(); assert 'ingest' not in native.running
    clock[0]+=1; native.tick(); assert 'ingest' in native.running and 'broker' in native.running
    reap(native,'ingest')


def test_shutdown_services(tmp_path, monkeypatch):
    monkeypatch.setattr(native_module,'KILL_GRACE_S',1)
    store,native,clock=make(tmp_path)
    store.set_backend('broker','native',service(tmp_path,FOREVER),1)
    stubborn='import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nopen("ready","w").close()\nwhile True: time.sleep(1)'
    store.set_backend('bridge','native',service(tmp_path,stubborn),1)
    native.tick(); procs={n:r['proc'] for n,r in native.running.items()}
    for _ in range(50):
        if (tmp_path/'ready').exists(): break
        time.sleep(0.1)
    native.shutdown()
    assert procs['broker'].returncode==-15 and procs['bridge'].returncode==-9
    assert native.running=={} and store.open_runs()==[]
    clock[0]+=BACKOFF_MAX_S; assert all(native.backoff.get(n) is None for n in procs)  # a stop is not a crash


def test_reconcile_waits(tmp_path, monkeypatch):
    """A crashed supervisor leaves its service running; the next one stops it before starting a new one."""
    monkeypatch.setattr(native_module,'KILL_GRACE_S',1)
    stubborn=subprocess.Popen([sys.executable,'-c','import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\ntime.sleep(30)'],
        start_new_session=True)
    polite=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    try:
        time.sleep(0.3)
        store,native,_=make(tmp_path)
        store.start_run('broker',stubborn.pid,started_token(stubborn.pid),1)
        store.start_run('bridge',polite.pid,started_token(polite.pid),1)
        for proc in (stubborn,polite): threading.Thread(target=proc.wait,daemon=True).start()  # as launchd reaps orphans
        assert native.reconcile()==1
        assert polite.wait(5)==-15 and stubborn.wait(5)==-9 and store.open_runs()==[]
    finally:
        for proc in (stubborn,polite):
            if proc.poll() is None: proc.kill(); proc.wait(5)


def test_snapshot_on_change(tmp_path, cleanup):
    layout=Layout(tmp_path/'home'); store,native,clock=make(tmp_path); cleanup(native)
    store.set_backend('broker','native',service(tmp_path,FOREVER),1)
    native.tick(); cli.write_snapshot(layout,native)
    path=layout.status/'supervisor.json'; first=path.stat().st_mtime_ns
    value=json.loads(path.read_text())
    assert value['units']['broker']['running'] is True and value['supervisor_pid']==os.getpid()
    assert oct(path.stat().st_mode&0o777)=='0o600'
    time.sleep(0.01); cli.write_snapshot(layout,native); assert path.stat().st_mtime_ns==first
    native.stop(get('broker')); reap(native,'broker'); cli.write_snapshot(layout,native)
    assert json.loads(path.read_text())['units']['broker']['held'] is True
