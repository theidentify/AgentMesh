import json
import os
import subprocess
import sys
import time
import pytest
from agentmesh_log.control import migrate
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.native import started_token
from agentmesh_log.control.store import Store
from .support import FakeLaunchd, PLISTS, Presence, ok_call, write_plists

NOSLEEP=lambda seconds:None


def setup(tmp_path, **kw):
    layout=Layout(tmp_path).ensure(); agents=tmp_path/'agents'; agents.mkdir(); write_plists(agents)
    return layout,Store(layout.state),FakeLaunchd(**kw),agents


def audit(layout): return [json.loads(line)['outcome'] for line in layout.audit.read_bytes().splitlines()]


def adopt(layout, store, launchd, agents, name='ingest', answer=True, call=ok_call, **kw):
    return migrate.adopt(layout,store,name,console=Presence(answer),call=call,launchd=launchd,agents=agents,sleep=NOSLEEP,**kw)


def test_adopt(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); console=Presence(True)
    result=migrate.adopt(layout,store,'ingest',console=console,call=ok_call,launchd=launchd,agents=agents,sleep=NOSLEEP)
    assert result=={'ok':True,'unit':'ingest','backend':'native'}
    assert launchd.calls==[('disable','ingest'),('bootout','ingest')] and store.backend('ingest')=='native'
    assert store.spec('ingest')['argv']==PLISTS['ingest']['ProgramArguments']
    assert console.seen[0][0]=='migrate' and 'Undo: agentmesh-log control release ingest' in console.seen[0][1]
    assert audit(layout)==['requested','ok']


def down(layout, op, args): raise ConnectionRefusedError()


REFUSALS=[('broker','broker',True,ok_call,'not_native_capable'),('unknown','nope',True,ok_call,'not_native_capable'),
    ('no-plist','summary',True,ok_call,'unsupported_plist'),('down','ingest',True,down,'supervisor_unavailable'),
    ('not-ok','ingest',True,lambda l,o,a:dict(ok=False),'supervisor_unavailable'),('denied','ingest',False,ok_call,'presence_required')]


@pytest.mark.parametrize('name,answer,call,error',[r[1:] for r in REFUSALS],ids=[r[0] for r in REFUSALS])
def test_adopt_refusals(tmp_path, name, answer, call, error):
    layout,store,launchd,agents=setup(tmp_path)
    assert adopt(layout,store,launchd,agents,name=name,answer=answer,call=call)=={'error':error}
    assert launchd.calls==[] and store.native_units()=={}


def test_adopt_twice(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); adopt(layout,store,launchd,agents)
    assert adopt(layout,store,launchd,agents)=={'error':'already_native'}


def test_adopt_waits_for_idle(tmp_path):
    layout,store,launchd,agents=setup(tmp_path,busy={'ingest':2})
    assert adopt(layout,store,launchd,agents)['ok'] is True
    layout,store,launchd,agents=setup(tmp_path/'b',busy={'ingest':1000})
    assert adopt(layout,store,launchd,agents,wait_s=4)=={'error':'busy'}
    assert launchd.calls==[] and store.backend('ingest')=='launchd' and audit(layout)==['requested','busy']


def test_release(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); adopt(layout,store,launchd,agents)
    run=store.start_run('ingest',123,'token',1)
    result=migrate.release(layout,store,'ingest',console=Presence(True),launchd=launchd,agents=agents,
        sleep=lambda s:store.end_run(run,0,'ok',2))
    assert result=={'ok':True,'unit':'ingest','backend':'launchd','waited_s':2}
    assert launchd.calls[-2:]==[('enable','ingest'),('bootstrap','ingest',str(agents/'org.agentmesh.ingest.plist'))]
    assert store.backend('ingest')=='launchd' and store.spec('ingest') is not None


def test_adopt_survives_reboot(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); adopt(layout,store,launchd,agents)
    launchd.reboot()
    assert 'ingest' not in launchd.loaded and 'summary' in launchd.loaded


def test_adopt_disable_failure(tmp_path):
    layout,store,launchd,agents=setup(tmp_path,fail={'disable'})
    assert adopt(layout,store,launchd,agents)=={'error':'launchctl_failed'}
    assert launchd.calls==[] and 'ingest' in launchd.loaded and store.backend('ingest')=='launchd'
    assert audit(layout)==['requested','launchctl_failed']


def test_release_enable_failure(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); adopt(layout,store,launchd,agents); launchd.fail.add('enable')
    result=migrate.release(layout,store,'ingest',console=Presence(True),launchd=launchd,agents=agents,sleep=NOSLEEP)
    assert result=={'error':'launchctl_failed'} and 'ingest' not in launchd.loaded
    assert audit(layout)[-1]=='launchctl_failed'


def test_release_survives_reboot(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); adopt(layout,store,launchd,agents)
    migrate.release(layout,store,'ingest',console=Presence(True),launchd=launchd,agents=agents,sleep=NOSLEEP)
    launchd.loaded.clear(); launchd.reboot()
    assert launchd.disabled==set() and 'ingest' in launchd.loaded


def test_release_refusals(tmp_path):
    layout,store,launchd,agents=setup(tmp_path)
    assert migrate.release(layout,store,'ingest',console=Presence(True),launchd=launchd,agents=agents)=={'error':'not_native'}
    adopt(layout,store,launchd,agents)
    denied=migrate.release(layout,store,'ingest',console=Presence(False),launchd=launchd,agents=agents)
    assert denied=={'error':'presence_required'} and store.backend('ingest')=='native'
    assert launchd.calls==[('disable','ingest'),('bootout','ingest')]


@pytest.mark.skipif(os.name=='nt',reason='POSIX process groups')
def test_panic_revert(tmp_path):
    layout,store,launchd,agents=setup(tmp_path)
    adopt(layout,store,launchd,agents); adopt(layout,store,launchd,agents,name='retention')
    same=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    other=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    try:
        time.sleep(0.2)
        store.start_run('ingest',same.pid,started_token(same.pid),1)
        store.start_run('retention',other.pid,'Thu Jan  1 00:00:00 1970',1)
        result=migrate.panic_revert(layout,store,launchd=launchd,agents=agents,sleep=NOSLEEP)
        assert result=={'ok':True,'reverted':['ingest','retention'],'stopped':['ingest'],'errors':[]}
        assert same.wait(5)==-15 and other.poll() is None
        assert launchd.calls[4]==('/bin/launchctl','bootout','gui/501/org.agentmesh.control')
        assert launchd.calls[5:]==[('enable','ingest'),('bootstrap','ingest',str(agents/'org.agentmesh.ingest.plist')),
            ('enable','retention'),('bootstrap','retention',str(agents/'org.agentmesh.retention.plist'))]
        assert launchd.disabled==set()
        assert store.native_units()=={} and store.open_runs()==[] and audit(layout)[-1]=='ok'
    finally:
        for proc in (same,other):
            if proc.poll() is None: proc.kill(); proc.wait(5)


def test_panic_revert_with_nothing_native(tmp_path):
    layout,store,launchd,agents=setup(tmp_path)
    assert migrate.panic_revert(layout,store,launchd=launchd,agents=agents,sleep=NOSLEEP)=={'ok':True,'reverted':[],'stopped':[],'errors':[]}
    assert launchd.calls==[('/bin/launchctl','bootout','gui/501/org.agentmesh.control')]


def test_panic_enables_loaded(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); adopt(layout,store,launchd,agents)
    launchd.loaded.add('ingest')  # still loaded (e.g. a manual bootstrap): enable anyway, never bootstrap twice
    result=migrate.panic_revert(layout,store,launchd=launchd,agents=agents,sleep=NOSLEEP)
    assert result['reverted']==['ingest'] and launchd.calls[-1]==('enable','ingest') and launchd.disabled==set()


def test_panic_revert_enable_failure(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); adopt(layout,store,launchd,agents); launchd.fail.add('enable')
    result=migrate.panic_revert(layout,store,launchd=launchd,agents=agents,sleep=NOSLEEP)
    assert result=={'ok':False,'reverted':[],'stopped':[],'errors':['ingest']} and audit(layout)[-1]=='partial'


def reload_after_m2_adopt(tmp_path):
    """An M2-era adopt (bootout only), then a reboot: launchd loads the native unit again."""
    layout,store,launchd,agents=setup(tmp_path); adopt(layout,store,launchd,agents)
    launchd.disabled.clear(); launchd.reboot(); launchd.calls.clear()
    return layout,store,launchd


def test_evict_clears_reboot_reload(tmp_path):
    layout,store,launchd=reload_after_m2_adopt(tmp_path)
    assert 'ingest' in launchd.loaded
    assert migrate.evict_reloaded(layout,store,launchd)==['ingest']
    assert launchd.calls==[('disable','ingest'),('bootout','ingest')] and store.backend('ingest')=='native'
    rows=[json.loads(line) for line in layout.audit.read_bytes().splitlines()]
    assert (rows[-1]['op'],rows[-1]['args'],rows[-1]['outcome'])==('launchd_reloaded',{'unit':'ingest'},'ok')
    launchd.reboot()
    assert 'ingest' not in launchd.loaded and migrate.evict_reloaded(layout,store,launchd)==[]


def test_evict_leaves_launchd_units(tmp_path):
    layout,store,launchd,agents=setup(tmp_path)
    assert migrate.evict_reloaded(layout,store,launchd)==[] and launchd.calls==[]


def test_evict_failure_is_audited(tmp_path):
    layout,store,launchd=reload_after_m2_adopt(tmp_path); launchd.fail.add('disable')
    assert migrate.evict_reloaded(layout,store,launchd)==[] and 'ingest' in launchd.loaded
    assert audit(layout)[-1]=='launchctl_failed'
