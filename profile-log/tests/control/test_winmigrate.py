import argparse
import json
import os
import pytest
from agentmesh_log.control import cli, winacl, winmigrate, wintasks
from agentmesh_log.control.layout import Layout, read_json
from agentmesh_log.control.store import Store
from .wintask_support import (BROKER_ARGV, CATCH_UP_ARGV, PY, PYW, FakeTasks, exported_job, job_argv, rendered_broker,
    rendered_job)


REAL_ELEVATED=winacl.elevated


@pytest.fixture(autouse=True)
def not_elevated(monkeypatch):
    """A Windows CI runner is elevated: never touch real ACLs from the move tests."""
    monkeypatch.setattr(winacl,'elevated',lambda:False)


class Console:
    def __init__(self, ok=True): self.ok,self.summaries=ok,[]
    def confirm(self, action, summary): self.summaries.append((action,summary)); return self.ok


def alive(layout, op, args): return {'ok':True,'result':{}}


def dead(layout, op, args): raise OSError('no supervisor')


def device(tmp_path, extra=()):
    """The Windows device as the rollout left it: broker at logon, ingest, summary, catch-up, and the supervisor."""
    layout=Layout(tmp_path/'home'); layout.ensure(); store=Store(layout.state)
    tasks=FakeTasks([('\\org.agentmesh.profile-log.broker','Running',rendered_broker()),
        ('\\org.agentmesh.ingest','Ready',rendered_job(job_argv('ingest'),60)),
        ('\\AgentMesh Summary','Ready',exported_job(job_argv('summary'))),
        ('\\org.agentmesh.profile-log.catch-up','Ready',rendered_job(CATCH_UP_ARGV,300)),
        (wintasks.TASK_NAME,'Running',wintasks.render_supervisor(layout,PYW).decode('utf-16')),*extra])
    return layout,store,tasks


def test_dry_run(tmp_path):
    other=('\\AgentMesh-9b6b3962c37aa75426290216','Ready',rendered_job([PY,r'C:\o\.omp_memory\agentmesh.py','sync'],60))
    layout,store,tasks=device(tmp_path,[other])
    result=winmigrate.consolidate(layout,store,console=Console(),call=dead,tasks=tasks)
    assert result['apply'] is False
    assert [(r['unit'],r['action']) for r in result['plan']]==[('catch-up','retire'),('ingest','move'),('summary','move'),
        ('broker','move'),(None,'skip')]
    assert result['plan'][-1]['reason']=='not_a_unit' and all('xml' not in r and 'spec' not in r for r in result['plan'])
    assert tasks.calls==[] and store.native_units()=={}


def test_apply(tmp_path):
    layout,store,tasks=device(tmp_path)
    console=Console()
    result=winmigrate.consolidate(layout,store,console=console,call=alive,tasks=tasks,apply=True)
    assert result=={'ok':True,'moved':['ingest','summary','broker'],'parked':[],'retired':['catch-up'],'skipped':[],'errors':[]}
    assert list(tasks.rows)==[wintasks.TASK_NAME]  # one AgentMesh task left
    native=store.native_units()
    assert native['broker']['argv']==BROKER_ARGV and native['broker']['keep_alive'] is True
    assert native['ingest']['interval']==60 and native['summary']['interval']==300
    assert native['summary']['stdout']==str(layout.root/'logs'/'summary.log')
    assert ('stop','\\org.agentmesh.profile-log.broker') in tasks.calls
    assert ('stop','\\org.agentmesh.ingest') not in tasks.calls  # an idle job is not ended
    assert tasks.calls.index(('disable','\\org.agentmesh.ingest'))<tasks.calls.index(('delete','\\org.agentmesh.ingest'))
    saved=read_json(winmigrate.saved(layout,'broker'))
    assert saved['task']=='\\org.agentmesh.profile-log.broker' and saved['xml']==rendered_broker()
    assert read_json(winmigrate.saved(layout,'catch-up',retired=True))['task']=='\\org.agentmesh.profile-log.catch-up'
    assert 'catch-up' not in native
    assert console.summaries[0][0]=='migrate' and 'one scheduled task instead of 5' in console.summaries[0][1]
    rows=[json.loads(line) for line in layout.audit.read_text().splitlines()]
    assert [r['outcome'] for r in rows]==['requested','ok','ok','ok','ok']


def test_apply_refusals(tmp_path):
    layout,store,tasks=device(tmp_path)
    assert winmigrate.consolidate(layout,store,console=Console(),call=dead,tasks=tasks,apply=True)=={'error':'supervisor_unavailable'}
    assert winmigrate.consolidate(layout,store,console=Console(False),call=alive,tasks=tasks,apply=True)=={'error':'presence_required'}
    assert tasks.calls==[] and store.native_units()=={}


def test_busy_job(tmp_path):
    layout,store,tasks=device(tmp_path)
    tasks.rows['\\org.agentmesh.ingest']['state']='Running'
    naps=[]
    result=winmigrate.consolidate(layout,store,console=Console(),call=alive,tasks=tasks,apply=True,sleep=naps.append,wait_s=4)
    assert result['errors']==[dict(unit='ingest',task='\\org.agentmesh.ingest',error='busy')] and naps==[2,2]
    assert tasks.rows['\\org.agentmesh.ingest']['state']=='Running' and 'ingest' not in store.native_units()
    assert result['moved']==['summary','broker']


def test_failed_save(tmp_path):
    layout,store,tasks=device(tmp_path)
    winmigrate.save(layout,'broker',dict(task='\\old',xml='<x/>'))  # a file from an earlier run is never overwritten
    result=winmigrate.consolidate(layout,store,console=Console(),call=alive,tasks=tasks,apply=True)
    assert result['errors']==[dict(unit='broker',task='\\org.agentmesh.profile-log.broker',error='FileExistsError')]
    assert tasks.rows['\\org.agentmesh.profile-log.broker']['state']=='Running'  # enabled and started again
    assert 'broker' not in store.native_units() and result['moved']==['ingest','summary']


def test_skips(tmp_path):
    twin=('\\copy of ingest','Ready',rendered_job(job_argv('ingest'),60))
    odd=('\\bridge','Ready',rendered_job([PY,'-m','agentmesh_log.cli','bridge','serve'],60).replace('</Interval>','</Interval><Duration>P1D</Duration>'))
    layout,store,tasks=device(tmp_path,[twin,odd])
    plan={r['task']:r for r in winmigrate.plan(layout,store,tasks)}
    assert plan['\\copy of ingest']['reason']==plan['\\org.agentmesh.ingest']['reason']=='duplicate'
    assert plan['\\bridge']['reason']=='unsupported_task'


def test_release(tmp_path):
    layout,store,tasks=device(tmp_path)
    winmigrate.consolidate(layout,store,console=Console(),call=alive,tasks=tasks,apply=True)
    calls=[]
    def call(layout_, op, args): calls.append(op); return {'ok':True}
    result=winmigrate.release(layout,store,'broker',console=Console(),call=call,tasks=tasks,sleep=lambda s:None)
    assert result=={'ok':True,'unit':'broker','backend':'task_scheduler','waited_s':0}
    assert calls==['status','unit.stop'] and store.backend('broker')=='launchd'
    assert tasks.rows['\\org.agentmesh.profile-log.broker']=={'task':'\\org.agentmesh.profile-log.broker','state':'Running',
        'xml':rendered_broker()}
    assert not winmigrate.saved(layout,'broker').exists()
    assert winmigrate.release(layout,store,'broker',console=Console(),call=call,tasks=tasks)=={'error':'not_native'}


def test_panic_revert(tmp_path):
    layout,store,tasks=device(tmp_path)
    winmigrate.consolidate(layout,store,console=Console(),call=alive,tasks=tasks,apply=True)
    store.start_run('broker',999999,'gone',1)
    result=winmigrate.panic_revert(layout,store,tasks=tasks,token=lambda pid:None,sleep=lambda s:None)
    assert result=={'ok':True,'reverted':['broker','ingest','summary'],'stopped':[],'errors':[]}
    assert tasks.rows[wintasks.TASK_NAME]['state']=='Disabled'
    assert tasks.rows['\\org.agentmesh.profile-log.broker']['state']=='Running'
    assert tasks.rows['\\AgentMesh Summary']['state']=='Ready'
    assert '\\org.agentmesh.profile-log.catch-up' not in tasks.rows  # retired stays retired
    assert store.native_units()=={} and store.open_runs()==[]


def test_evict(tmp_path):
    layout,store,tasks=device(tmp_path)
    assert winmigrate.evict_reloaded(layout,store,tasks)==[]
    store.set_backend('ingest','native',{},1)
    tasks.rows['\\org.agentmesh.ingest']['state']='Running'
    assert winmigrate.evict_reloaded(layout,store,tasks)==['ingest']
    assert tasks.rows['\\org.agentmesh.ingest']['state']=='Disabled'
    assert ('stop','\\org.agentmesh.ingest') in tasks.calls


def args(action, **kw):
    return argparse.Namespace(action=action,**dict(dict(apply=False,unit=None,agents=None,task_folder=None,label=None),**kw))


def test_cli_moves(tmp_path):
    layout,store,tasks=device(tmp_path); store.close()
    common=dict(layout=layout,call=alive,console=Console(),tasks=tasks,windows=True)
    assert cli.execute(args('consolidate'),**common)['apply'] is False
    assert cli.execute(args('consolidate',apply=True),**common)['moved']==['ingest','summary','broker']
    assert cli.execute(args('adopt',unit='ingest'),**common)=={'error':'unsupported_platform'}
    assert cli.execute(args('release',unit='ingest'),**common)['ok'] is True
    assert cli.execute(args('panic-revert'),**common)['reverted']==['broker','summary']


def test_task_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.Path,'home',classmethod(lambda cls:tmp_path))
    assert cli.task_folder(Layout(tmp_path/'.agentmesh')) is None
    assert cli.task_folder(Layout(tmp_path/'.agentmesh'),'AgentMeshTest-x')=='\\AgentMeshTest-x\\'
    folder=cli.task_folder(Layout(tmp_path/'other'))
    assert folder.startswith('\\AgentMeshTest-') and folder.endswith('\\') and folder!=cli.task_folder(Layout(tmp_path/'o2'))


def test_cli_task(tmp_path):
    layout=Layout(tmp_path/'home'); out=tmp_path/'control.xml'
    result=cli.execute(argparse.Namespace(action='task',out=str(out),python=PYW),layout=layout)
    assert result=={'ok':True,'path':str(out),'task':wintasks.TASK_NAME}
    assert wintasks.command(wintasks.parse(out.read_bytes()))[0][0]==PYW
    assert cli.execute(argparse.Namespace(action='task',out=str(out),python=PYW),layout=layout)=={'error':'exists'}


BROKER='\\org.agentmesh.profile-log.broker'


def test_elevated_task(tmp_path):
    """The broker task was registered from an elevated window: Disable is denied to the normal token, Stop/Start work."""
    layout,store,tasks=device(tmp_path)
    tasks.fail={('disable',BROKER):'needs_elevation'}
    result=winmigrate.consolidate(layout,store,console=Console(),call=alive,tasks=tasks,apply=True)
    assert result['errors']==[dict(unit='broker',task=BROKER,error='needs_elevation')]
    assert 'elevated PowerShell' in result['advice'] and result['moved']==['ingest','summary']
    assert tasks.rows[BROKER]['state']=='Running' and 'broker' not in store.native_units()
    assert not winmigrate.saved(layout,'broker').exists()
    rows=[json.loads(line) for line in layout.audit.read_text().splitlines()]
    assert rows[-1]['outcome']=='needs_elevation'
    layout,store,tasks=device(tmp_path/'clean')
    assert 'advice' not in winmigrate.consolidate(layout,store,console=Console(),call=alive,tasks=tasks,apply=True)


def test_elevated_release_and_revert(tmp_path):
    layout,store,tasks=device(tmp_path)
    winmigrate.consolidate(layout,store,console=Console(),call=alive,tasks=tasks,apply=True)
    tasks.fail={('register',BROKER):'needs_elevation'}
    result=winmigrate.release(layout,store,'broker',console=Console(),call=alive,tasks=tasks,sleep=lambda s:None)
    assert result['error']=='needs_elevation' and 'elevated PowerShell' in result['advice']
    store.set_backend('broker','native',store.spec('broker'),1)
    result=winmigrate.panic_revert(layout,store,tasks=tasks,token=lambda pid:None,sleep=lambda s:None)
    assert result['errors']==['broker'] and 'elevated PowerShell' in result['advice']
    tasks.fail={('register',BROKER):True}
    store.set_backend('broker','native',store.spec('broker'),1)
    assert 'advice' not in winmigrate.panic_revert(layout,store,tasks=tasks,token=lambda pid:None,sleep=lambda s:None)


def test_elevated_files_go_to_user(tmp_path, monkeypatch):
    """An elevated consolidate hands control\\units and each saved file to the user, so release works from a normal window."""
    calls=[]
    def give(paths, **kw):
        calls.append([str(p) for p in paths]); return []
    monkeypatch.setattr(winacl,'give_to_user',give)
    layout,store,tasks=device(tmp_path)
    result=winmigrate.consolidate(layout,store,console=Console(),call=alive,tasks=tasks,apply=True)
    assert 'warning' not in result and len(calls)==1
    assert calls[0][:2]==[str(layout.units),str(layout.units/'retired')]
    assert set(calls[0][2:])=={str(winmigrate.saved(layout,n)) for n in ('ingest','summary','broker')}|{
        str(winmigrate.saved(layout,'catch-up',retired=True))}
    monkeypatch.setattr(winacl,'give_to_user',lambda paths,**kw:[str(layout.units)])
    monkeypatch.setattr(winacl,'user',lambda:'PC\\o')
    layout,store,tasks=device(tmp_path/'again')
    result=winmigrate.consolidate(layout,store,console=Console(),call=alive,tasks=tasks,apply=True)
    assert result['ok'] is True and result['acl_unfixed']==[str(layout.units)] and 'PC\\o' in result['warning']


def test_give_to_user(tmp_path):
    folder=tmp_path/'units'; folder.mkdir(); saved=folder/'broker.task.json'; saved.write_text('{}')
    calls=[]
    def runner(argv):
        calls.append(argv); return argparse.Namespace(returncode=1 if 'bad' in argv[1] else 0)
    paths=[folder,saved,tmp_path/'missing']
    assert winacl.give_to_user(paths,is_elevated=lambda:False,runner=runner)==[] and calls==[]
    assert winacl.give_to_user(paths,is_elevated=lambda:True,account=lambda:'PC\\o',runner=runner)==[]
    assert calls==[['icacls',str(folder),'/setowner','PC\\o'],['icacls',str(folder),'/grant','PC\\o:(OI)(CI)F'],
        ['icacls',str(saved),'/setowner','PC\\o'],['icacls',str(saved),'/grant','PC\\o:F']]
    bad=tmp_path/'bad.json'; bad.write_text('{}')
    assert winacl.give_to_user([bad,saved],is_elevated=lambda:True,account=lambda:'PC\\o',runner=runner)==[str(bad)]
    assert winacl.give_to_user([saved],is_elevated=lambda:True,account=lambda:None,runner=runner)==[str(saved)]
    def broken(argv): raise OSError('no icacls')
    assert winacl.give_to_user([saved],is_elevated=lambda:True,account=lambda:'o',runner=broken)==[str(saved)]
    if os.name!='nt': assert REAL_ELEVATED() is False  # never elevated off Windows


def test_unreadable_saved_task(tmp_path, monkeypatch):
    """A saved file an earlier elevated run left unreadable: release and panic-revert say why instead of crashing."""
    layout,store,tasks=device(tmp_path)
    winmigrate.consolidate(layout,store,console=Console(),call=alive,tasks=tasks,apply=True)
    def denied(path): raise PermissionError(13,'denied')
    monkeypatch.setattr(winmigrate,'read_json',denied)
    result=winmigrate.release(layout,store,'broker',console=Console(),call=alive,tasks=tasks)
    assert result['error']=='saved_task_unreadable' and 'icacls' in result['advice']
    assert store.backend('broker')=='native'  # nothing changed
    result=winmigrate.panic_revert(layout,store,tasks=tasks,token=lambda pid:None,sleep=lambda s:None)
    assert result['errors']==['broker','ingest','summary'] and 'icacls' in result['advice']
