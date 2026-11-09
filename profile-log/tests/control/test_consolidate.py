"""control consolidate: every unit under the supervisor, plists parked out of LaunchAgents; release and panic-revert put
them back. A fake launchd and a temporary LaunchAgents folder: the real ones are never touched."""
import json
import plistlib
import pytest
from agentmesh_log.cli import parser
from agentmesh_log.control import migrate
from agentmesh_log.control.cli import execute
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.store import Store
from .support import FakeLaunchd, PLISTS, Presence, ok_call, write_plists

NOSLEEP=lambda seconds:None
BROKER=dict(Label='org.agentmesh.profile-log.broker',ProgramArguments=['/opt/am/python','-m','agentmesh_log.cli','broker','run'],
    StandardOutPath='/opt/am/logs/broker.out.log',StandardErrorPath='/opt/am/logs/broker.err.log',KeepAlive=True,RunAtLoad=True)
OLLAMA=dict(Label='org.agentmesh.ollama-env',ProgramArguments=['/opt/am/AgentMesh-Ollama'],RunAtLoad=True)
ODD=dict(Label='org.agentmesh.bridge-tunnel',ProgramArguments=['/opt/am/tunnel'],KeepAlive={'SuccessfulExit':False})
CONTROL=dict(Label='org.agentmesh.control',ProgramArguments=['/opt/am/python','-m','agentmesh_log.cli','control','serve'])


def setup(tmp_path, extra=(BROKER,OLLAMA,ODD,CONTROL), **kw):
    layout=Layout(tmp_path/'home').ensure(); agents=tmp_path/'agents'; agents.mkdir(); write_plists(agents)
    for data in extra:
        with open(agents/(data['Label']+'.plist'),'wb') as stream: plistlib.dump(data,stream)
    return layout,Store(layout.state),FakeLaunchd(**kw),agents


def run(layout, store, launchd, agents, apply=True, answer=True, call=ok_call):
    return migrate.consolidate(layout,store,console=Presence(answer),call=call,apply=apply,launchd=launchd,agents=agents,
        sleep=NOSLEEP,wait_s=4)


def listing(folder): return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def test_dry_run_changes_nothing(tmp_path):
    layout,store,launchd,agents=setup(tmp_path)
    result=run(layout,store,launchd,agents,apply=False)
    assert result['apply'] is False
    assert [(r['unit'],r['action'],r.get('mode')) for r in result['plan']]==[('ingest','move','timer'),
        ('retention','move','timer'),('ollama-env','move','once'),('broker','move','service'),('bridge-tunnel','skip',None)]
    assert launchd.calls==[] and store.native_units()=={} and not layout.units.exists()


def test_apply_leaves_one_plist(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); console=Presence(True)
    result=migrate.consolidate(layout,store,console=console,call=ok_call,apply=True,launchd=launchd,agents=agents,sleep=NOSLEEP)
    assert result=={'ok':True,'moved':['ingest','retention','ollama-env','broker'],'parked':[],'skipped':['bridge-tunnel'],'errors':[]}
    # The supervisor's own plist, and the one plist it cannot reproduce, stay in LaunchAgents.
    assert listing(agents)==['org.agentmesh.bridge-tunnel.plist','org.agentmesh.control.plist']
    assert listing(layout.units)==['org.agentmesh.ingest.plist','org.agentmesh.ollama-env.plist',
        'org.agentmesh.profile-log.broker.plist','org.agentmesh.retention.plist']
    assert oct(layout.units.stat().st_mode&0o777)=='0o700'
    assert set(store.native_units())=={'ingest','retention','ollama-env','broker'}
    assert store.spec('broker')['keep_alive'] is True and store.spec('ingest')['interval']==60
    assert launchd.disabled=={'ingest','retention','ollama-env','broker'} and not launchd.loaded&launchd.disabled
    assert console.seen[0][0]=='migrate' and 'broker (service)' in console.seen[0][1]
    launchd.reboot(); assert not launchd.loaded&launchd.disabled  # a login loads none of them again
    outcomes=[json.loads(line)['outcome'] for line in layout.audit.read_bytes().splitlines()]
    assert outcomes==['requested','ok','ok','ok','ok']
    assert run(layout,store,launchd,agents)=={'ok':True,'moved':[],'parked':[],'skipped':['bridge-tunnel'],'errors':[]}


def test_parks_units_adopted_earlier(tmp_path):
    layout,store,launchd,agents=setup(tmp_path,extra=())
    migrate.adopt(layout,store,'ingest',console=Presence(True),call=ok_call,launchd=launchd,agents=agents,sleep=NOSLEEP)
    launchd.calls.clear()
    result=run(layout,store,launchd,agents)
    assert result['moved']==['retention'] and result['parked']==['ingest']
    assert ('bootout','ingest') not in launchd.calls and listing(agents)==[]


@pytest.mark.parametrize('answer,call,error',[(False,ok_call,'presence_required'),
    (True,lambda l,o,a:dict(ok=False),'supervisor_unavailable')],ids=['denied','no-supervisor'])
def test_refusals(tmp_path, answer, call, error):
    layout,store,launchd,agents=setup(tmp_path)
    assert run(layout,store,launchd,agents,answer=answer,call=call)=={'error':error}
    assert launchd.calls==[] and store.native_units()=={} and len(listing(agents))==6


def test_failures_stay_launchd(tmp_path):
    layout,store,launchd,agents=setup(tmp_path,busy={'ingest':99})
    real=launchd.bootout
    def bootout(unit):
        if unit.name=='broker': raise RuntimeError('launchctl_failed')
        return real(unit)
    launchd.bootout=bootout
    result=run(layout,store,launchd,agents)
    assert result['ok'] is False and result['moved']==['retention','ollama-env']
    assert result['errors']==[dict(unit='ingest',error='busy'),dict(unit='broker',error='launchctl_failed')]
    assert (agents/'org.agentmesh.ingest.plist').exists() and (agents/'org.agentmesh.profile-log.broker.plist').exists()
    assert store.backend('ingest')==store.backend('broker')=='launchd' and 'broker' not in launchd.disabled


def test_undo_restores_plists(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); run(layout,store,launchd,agents)
    result=migrate.release(layout,store,'broker',console=Presence(True),launchd=launchd,agents=agents,sleep=NOSLEEP)
    assert result['ok'] and (agents/'org.agentmesh.profile-log.broker.plist').exists()
    assert ('bootstrap','broker',str(agents/'org.agentmesh.profile-log.broker.plist')) in launchd.calls
    result=migrate.panic_revert(layout,store,launchd=launchd,agents=agents,sleep=NOSLEEP,label='org.agentmesh.test-control')
    assert result['ok'] and result['reverted']==['ingest','ollama-env','retention']
    assert ('/bin/launchctl','bootout','gui/501/org.agentmesh.test-control') in launchd.calls
    assert listing(layout.units)==[] and len(listing(agents))==6 and launchd.disabled==set()


def test_reload_edited_plist(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); run(layout,store,launchd,agents)
    path=layout.units/'org.agentmesh.profile-log.broker.plist'
    path.write_bytes(plistlib.dumps(dict(BROKER,ProgramArguments=['/opt/am/python','-m','agentmesh_log.cli','broker','run','--new'])))
    seen=[]
    def call(layout, op, args): seen.append((op,args)); return dict(ok=True,result={})
    result=migrate.reload(layout,store,'broker',console=Presence(True),call=call,agents=agents)
    assert result=={'ok':True,'unit':'broker','path':str(path)} and seen==[('unit.restart',{'unit':'broker'})]
    assert store.spec('broker')['argv'][-1]=='--new'
    assert migrate.reload(layout,store,'summary',console=Presence(True),call=call,agents=agents)=={'error':'not_native'}


def test_cli_needs_agents(tmp_path, monkeypatch):
    layout=Layout(tmp_path/'home')
    for argv in (['control','consolidate','--apply'],['control','panic-revert'],['control','release','ingest']):
        assert execute(parser().parse_args(argv),layout=layout,call=ok_call)=={'error':'agents_required'}
    agents=tmp_path/'agents'; agents.mkdir(); write_plists(agents)
    result=execute(parser().parse_args(['control','consolidate','--agents',str(agents)]),layout=layout,call=ok_call)
    assert [r['unit'] for r in result['plan']]==list(PLISTS)
