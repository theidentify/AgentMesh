"""The native backend, control pipe and Task Scheduler moves on real Windows. Tasks go in a throwaway folder only."""
import os
import subprocess
import sys
import threading
import time
import uuid
import pytest
from agentmesh_log.control import native as native_module, procs, winmigrate
from agentmesh_log.control.client import call
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.native import NativeBackend
from agentmesh_log.control.server import Supervisor
from agentmesh_log.control.store import Store
from agentmesh_log.control.taskscheduler import TaskSchedulerBackend
from agentmesh_log.control.units import UNITS, get
from .wintask_support import rendered_job

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows only')


def make(tmp_path):
    store=Store(tmp_path/'state.db')
    environ=dict(os.environ,AGENTMESH_HOME='C:\\leak',ANTHROPIC_API_KEY='host-auth')
    return store,NativeBackend(store,UNITS,environ=environ)


def spec(tmp_path, code, **kw):
    return dict(dict(argv=[sys.executable,'-c',code],env={},cwd=str(tmp_path),stdout=str(tmp_path/'out.log'),
        stderr=str(tmp_path/'out.log'),umask=0o77,interval=60,calendar=None,run_at_load=True),**kw)


def wait_for(check, seconds=20):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if check(): return True
        time.sleep(0.2)
    return False


def test_job_env(tmp_path):
    store,native=make(tmp_path)
    code='import os,sys; print("AGENTMESH_HOME" in os.environ, "ANTHROPIC_API_KEY" in os.environ, os.getcwd()); sys.exit(3)'
    store.set_backend('ingest','native',spec(tmp_path,code),1)
    native.tick(); native.running['ingest']['proc'].wait(30); native.tick()
    assert (tmp_path/'out.log').read_text().split()==['False','False',str(tmp_path)]
    assert store.last_exit('ingest')==3


def test_stop_tree(tmp_path):
    store,native=make(tmp_path)
    child=tmp_path/'child.pid'
    code=('import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(300)"]); '
        'open(sys.argv[1],"w").write(str(p.pid)); time.sleep(300)')
    store.set_backend('broker','native',dict(spec(tmp_path,code,interval=None,keep_alive=True,throttle=1),
        argv=[sys.executable,'-c',code,str(child)]),1)
    native.tick()
    assert wait_for(lambda:child.exists() and child.read_text())
    grandchild=int(child.read_text()); assert procs.started_token(grandchild)
    native.stop(get('broker'))
    assert wait_for(lambda:native.tick() or 'broker' not in native.running)
    assert wait_for(lambda:procs.started_token(grandchild) is None)
    native.tick(); assert 'broker' not in native.running  # held down after stop


def test_reconcile(tmp_path, monkeypatch):
    monkeypatch.setattr(native_module,'KILL_GRACE_S',2)
    store,native=make(tmp_path)
    orphan=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)'],**procs.spawn_options({}))
    try:
        store.start_run('broker',orphan.pid,procs.started_token(orphan.pid),1)
        native.reconcile()
        assert orphan.wait(20) is not None and store.open_runs()==[]
    finally:
        if orphan.poll() is None: orphan.kill()


def test_pipe(tmp_path):
    layout=Layout(tmp_path/'home')

    class Control:
        def handle(self, request, peer): return {'ok':True,'result':{'op':request['op']}}

    supervisor=Supervisor(Control(),layout)
    thread=threading.Thread(target=supervisor.serve,daemon=True); thread.start()
    assert wait_for(lambda:supervisor.server is not None)
    assert call(layout,'status',{})=={'ok':True,'result':{'op':'status'}}
    supervisor.close(); thread.join(10)
    assert not thread.is_alive()


class Console:
    def confirm(self, action, summary): return True


def test_real_task(tmp_path):
    folder='\\AgentMeshTest-'+uuid.uuid4().hex[:12]+'\\'
    tasks=TaskSchedulerBackend(folder=folder)
    task=folder+'ingest'
    argv=[sys.executable,str(tmp_path/'agentmesh'/'job_notify.py'),'--name','ingest','--',sys.executable,'-c','pass']
    layout=Layout(tmp_path/'home'); layout.ensure(); store=Store(layout.state)
    try:
        tasks.act('register',task,rendered_job(argv,300,cwd=str(tmp_path)))
        assert [(r['task'],r['unit']) for r in tasks.tasks(fresh=True)]==[(task,'ingest')]
        result=winmigrate.consolidate(layout,store,console=Console(),call=lambda *a:{'ok':True},tasks=tasks,apply=True)
        assert result['moved']==['ingest'] and tasks.tasks(fresh=True)==[]
        assert store.native_units()['ingest']['argv']==argv
        result=winmigrate.release(layout,store,'ingest',console=Console(),call=lambda *a:{'ok':True},tasks=tasks)
        assert result['ok'] is True
        assert [(r['task'],r['state']) for r in tasks.tasks(fresh=True)]==[(task,'Ready')]
    finally:
        for row in tasks.tasks(fresh=True): tasks.act('delete',row['task'])
        try: tasks.powershell('$s=New-Object -ComObject Schedule.Service; $s.Connect(); '
            '$s.GetFolder("\\").DeleteFolder($env:AGENTMESH_TASK_FOLDER.Trim("\\"),0)')
        except RuntimeError: pass  # an empty test folder left behind is harmless
        store.close()
