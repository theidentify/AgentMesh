import os
import subprocess
import sys
import time
import pytest
from agentmesh_log.control.native import NativeBackend, started_token
from agentmesh_log.control.store import Store
from agentmesh_log.control.units import UNITS, Unit, get

pytestmark=pytest.mark.skipif(os.name=='nt',reason='POSIX process groups')
SLOW=Unit('slow','org.agentmesh.test.slow','job',False,1,True)


def make(tmp_path, units=UNITS, **kw):
    store=Store(tmp_path/'state.db'); clock=[1000.0]
    native=NativeBackend(store,units,clock=lambda:clock[0],
        environ={'HOME':str(tmp_path),'AGENTMESH_HOME':'/leak'},**kw)
    return store,native,clock


def spec(tmp_path, code, **kw):
    return dict(dict(argv=[sys.executable,'-c',code],env={},cwd=str(tmp_path),stdout=str(tmp_path/'out.log'),
        stderr=str(tmp_path/'out.log'),umask=0o77,interval=60,calendar=None,run_at_load=True),**kw)


def adopt(store, name, value, at=900000): store.set_backend(name,'native',value,at)


def settle(native, name):
    """Wait for the run (if a tick has not reaped it already), then reap it."""
    run=native.running.get(name)
    if run is not None: run['proc'].wait(10)
    native.tick()


def outcomes(store, unit):
    return [row[0] for row in store._all('SELECT outcome FROM runs WHERE unit=? ORDER BY id',(unit,))]


def test_runs_like_launchd(tmp_path):
    store,native,_=make(tmp_path)
    code='import os,sys; print("AGENTMESH_HOME" in os.environ, os.environ["PATH"], os.getcwd()); open("made","w").close(); sys.exit(3)'
    adopt(store,'ingest',spec(tmp_path,code)); native.tick()
    assert native.status(get('ingest'))['running'] is True
    settle(native,'ingest')
    assert (tmp_path/'out.log').read_text().split()==['False','/usr/bin:/bin:/usr/sbin:/sbin',os.path.realpath(tmp_path)]
    assert oct((tmp_path/'made').stat().st_mode&0o777)=='0o600' and oct((tmp_path/'out.log').stat().st_mode&0o777)=='0o600'
    assert store.last_exit('ingest')==3 and outcomes(store,'ingest')==['failed']
    assert native.status(get('ingest'))==dict(loaded=True,running=False,pid=None,last_exit=3,runtime_s=None,failed=False)


def test_schedule_no_overlap(tmp_path):
    store,native,clock=make(tmp_path)
    adopt(store,'ingest',spec(tmp_path,'pass')); native.tick(); native.tick()
    settle(native,'ingest'); assert len(outcomes(store,'ingest'))==1
    clock[0]=1059.0; native.tick(); assert 'ingest' not in native.running
    clock[0]=1060.0; native.tick(); assert 'ingest' in native.running
    settle(native,'ingest'); clock[0]=100000.0; native.tick(); native.tick()
    settle(native,'ingest'); assert outcomes(store,'ingest')==['ok','ok','ok']


def test_waits_from_adoption(tmp_path):
    store,native,clock=make(tmp_path)
    adopt(store,'ingest',spec(tmp_path,'pass',run_at_load=False),at=1000000); native.tick()
    assert 'ingest' not in native.running
    clock[0]=1060.0; native.tick(); assert 'ingest' in native.running
    settle(native,'ingest')


def test_watchdog_kills_stuck_run(tmp_path):
    store,native,clock=make(tmp_path,units=UNITS+(SLOW,))
    adopt(store,'slow',spec(tmp_path,'import time; time.sleep(30)')); native.tick()
    clock[0]=1001.0; native.tick(); assert native.running['slow']['term_at'] is None
    clock[0]=1002.0; native.tick(); proc=native.running['slow']['proc']
    assert proc.wait(5)==-15
    native.tick(); assert outcomes(store,'slow')==['killed_stuck'] and store.kills_since('slow',0)==1
    assert store.failed('slow') is None


def test_watchdog_escalates(tmp_path):
    store,native,clock=make(tmp_path,units=UNITS+(SLOW,))
    code='import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path("ready").touch(); time.sleep(30)'
    adopt(store,'slow',spec(tmp_path,code)); native.tick()
    for _ in range(100):
        if (tmp_path/'ready').exists(): break
        time.sleep(0.05)
    clock[0]=1002.0; native.tick(); proc=native.running['slow']['proc']
    time.sleep(0.3); assert proc.poll() is None
    clock[0]=1012.0; native.tick(); assert proc.wait(5)==-9
    native.tick(); assert outcomes(store,'slow')==['killed_stuck']


@pytest.mark.parametrize('before,failed',[(2,False),(3,True)],ids=['three','four'])
def test_storm_guard(tmp_path, before, failed):
    alerts=[]
    store,native,clock=make(tmp_path,units=UNITS+(SLOW,),notify=alerts.append)
    for _ in range(before): store.add_kill('slow',999000)
    adopt(store,'slow',spec(tmp_path,'import time; time.sleep(30)')); native.tick()
    clock[0]=1002.0; native.tick(); native.running['slow']['proc'].wait(5); native.tick()
    for _ in range(100):
        if alerts or not failed: break
        time.sleep(0.02)
    assert (store.failed('slow') is not None)==failed and bool(alerts)==failed
    clock[0]=5000.0; native.tick()
    assert ('slow' in native.running)!=failed
    if failed:
        assert '`slow`' in alerts[0] and 'control reset slow' in alerts[0]
        with pytest.raises(RuntimeError): native.start(SLOW)
        assert native.reset(SLOW) is True; native.tick(); assert 'slow' in native.running
    native.stop(SLOW); native.running['slow']['proc'].wait(5); native.tick()


def test_stop_and_restart(tmp_path):
    store,native,_=make(tmp_path); unit=get('ingest')
    adopt(store,'ingest',spec(tmp_path,'import time; time.sleep(30)')); native.tick()
    first=native.running['ingest']['proc']
    native.restart(unit); first.wait(5); native.tick()
    second=native.running['ingest']['proc']; assert second.pid!=first.pid
    native.stop(unit); second.wait(5); native.tick()
    assert outcomes(store,'ingest')==['stopped','stopped'] and 'ingest' not in native.running


def test_launch_error_recorded(tmp_path):
    store,native,_=make(tmp_path)
    adopt(store,'ingest',dict(spec(tmp_path,'pass'),argv=['/nonexistent/agentmesh-job'])); native.tick(); native.tick()
    assert 'ingest' not in native.running and outcomes(store,'ingest')==['launch_error'] and store.last_exit('ingest')==127


def test_reconcile_same_process_only(tmp_path):
    store,native,_=make(tmp_path)
    same=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    other=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    try:
        time.sleep(0.2)
        store.start_run('ingest',same.pid,started_token(same.pid),1)
        store.start_run('summary',other.pid,'Thu Jan  1 00:00:00 1970',1)
        native.reconcile()
        assert same.wait(5)==-15 and other.poll() is None
        assert store.open_runs()==[] and outcomes(store,'ingest')==['orphaned']==outcomes(store,'summary')
    finally:
        for proc in (same,other):
            if proc.poll() is None: proc.kill(); proc.wait(5)


def test_shutdown_stops_runs(tmp_path):
    store,native,_=make(tmp_path)
    adopt(store,'ingest',spec(tmp_path,'import time; time.sleep(30)')); native.tick()
    proc=native.running['ingest']['proc']
    native.shutdown()
    assert proc.returncode==-15 and native.running=={} and outcomes(store,'ingest')==['stopped']


def runs(tmp_path, store, native, clock, codes, name='ingest'):
    """One scheduled run per code ('fail', 'ok' or 'stop'), each a schedule interval after the last."""
    for code in codes:
        body={'fail':'raise SystemExit(4)','ok':'pass','stop':'import time; time.sleep(30)'}[code]
        adopt(store,name,spec(tmp_path,body)); clock[0]+=61; native.tick()
        if code=='stop': native.stop(get(name))
        settle(native,name)
    time.sleep(0.1)  # alerts go out on daemon threads


def test_streak_alerts_once(tmp_path):
    alerts=[]; store,native,clock=make(tmp_path,notify=alerts.append)
    runs(tmp_path,store,native,clock,['fail','fail'])
    assert alerts==[] and store.failure_streak('ingest')==2
    runs(tmp_path,store,native,clock,['fail'])
    assert len(alerts)==1 and '`ingest` failed 3 runs in a row' in alerts[0] and 'exit 4' in alerts[0]
    assert str(tmp_path/'out.log') in alerts[0]
    runs(tmp_path,store,native,clock,['fail','fail'])
    assert len(alerts)==1


def test_streak_recovery(tmp_path):
    alerts=[]; store,native,clock=make(tmp_path,notify=alerts.append)
    runs(tmp_path,store,native,clock,['fail']*4+['ok','ok'])
    assert len(alerts)==2 and 'ran ok again after 4 failed runs' in alerts[1]


def test_short_streak_quiet(tmp_path):
    alerts=[]; store,native,clock=make(tmp_path,notify=alerts.append)
    runs(tmp_path,store,native,clock,['fail','fail','ok','fail','fail'])
    assert alerts==[] and store.failure_streak('ingest')==2


def test_stop_neither_breaks_nor_counts(tmp_path):
    alerts=[]; store,native,clock=make(tmp_path,notify=alerts.append)
    runs(tmp_path,store,native,clock,['fail','fail','stop'])
    assert outcomes(store,'ingest')[-1]=='stopped' and store.failure_streak('ingest')==2 and alerts==[]
    runs(tmp_path,store,native,clock,['fail'])
    assert len(alerts)==1


def test_streak_survives_restart(tmp_path):
    alerts=[]; store,native,clock=make(tmp_path,notify=alerts.append)
    runs(tmp_path,store,native,clock,['fail','fail'])
    again=NativeBackend(store,UNITS,clock=lambda:clock[0],environ={'HOME':str(tmp_path)},notify=alerts.append)
    runs(tmp_path,store,again,clock,['fail'])
    assert len(alerts)==1


def test_launch_errors_streak(tmp_path):
    alerts=[]; store,native,clock=make(tmp_path,notify=alerts.append)
    for _ in range(3):
        adopt(store,'ingest',dict(spec(tmp_path,'pass'),argv=['/nonexistent/agentmesh-job'])); clock[0]+=61; native.tick()
    native.tick(); time.sleep(0.1)
    assert len(alerts)==1 and 'launch_error' in alerts[0] and 'exit 127' in alerts[0]
