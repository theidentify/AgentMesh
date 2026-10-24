import plistlib
from datetime import datetime
import pytest
from agentmesh_log.control.schedule import DEFAULT_PATH, child_env, due, read_spec, spec_from_plist
from agentmesh_log.control.units import get

INGEST=dict(Label='org.agentmesh.ingest',ProgramArguments=['/opt/am/python','/opt/am/job_notify.py','--name','ingest'],
    EnvironmentVariables={'PATH':'/opt/am/bin:/usr/bin:/bin'},WorkingDirectory='/opt/am/jobs',
    StandardOutPath='/opt/am/status/launchd-ingest.log',StandardErrorPath='/opt/am/status/launchd-ingest.log',
    StartInterval=60,RunAtLoad=True,Umask=63,ProcessType='Background')
RETENTION=dict(Label='org.agentmesh.retention',ProgramArguments=['/opt/am/python','/opt/am/job_notify.py'],
    StandardOutPath='/opt/am/r.log',StandardErrorPath='/opt/am/r.log',StartCalendarInterval={'Hour':4,'Minute':0,'Weekday':0},
    RunAtLoad=False,Umask=63)


def ts(*parts): return datetime(*parts).timestamp()


def test_spec_from_plist():
    assert spec_from_plist(INGEST)==dict(argv=INGEST['ProgramArguments'],env={'PATH':'/opt/am/bin:/usr/bin:/bin'},
        cwd='/opt/am/jobs',stdout='/opt/am/status/launchd-ingest.log',stderr='/opt/am/status/launchd-ingest.log',
        umask=63,interval=60,calendar=None,run_at_load=True)
    spec=spec_from_plist(RETENTION)
    assert spec['interval'] is None and spec['calendar']=={'Hour':4,'Minute':0,'Weekday':0} and spec['run_at_load'] is False
    assert spec['cwd'] is None and spec['env']=={}


BAD=[('no-argv',dict(INGEST,ProgramArguments=[])),('relative',dict(INGEST,ProgramArguments=['python'])),
    ('both',dict(INGEST,StartCalendarInterval={'Hour':1})),('neither',{k:v for k,v in INGEST.items() if k!='StartInterval'}),
    ('zero',dict(INGEST,StartInterval=0)),('day-key',dict(RETENTION,StartCalendarInterval={'Day':1})),
    ('hour-24',dict(RETENTION,StartCalendarInterval={'Hour':24})),('keepalive',dict(INGEST,KeepAlive=True)),
    ('no-stdout',{k:v for k,v in INGEST.items() if k!='StandardOutPath'}),('env',dict(INGEST,EnvironmentVariables={'A':1})),
    ('cwd',dict(INGEST,WorkingDirectory='jobs'))]


@pytest.mark.parametrize('data',[d for _,d in BAD],ids=[i for i,_ in BAD])
def test_unsupported(data):
    with pytest.raises(ValueError): spec_from_plist(data)


def test_read_spec(tmp_path):
    with open(tmp_path/'org.agentmesh.ingest.plist','wb') as f: plistlib.dump(INGEST,f)
    assert read_spec(get('ingest'),tmp_path)['interval']==60


def test_child_env():
    parent={'HOME':'/h','USER':'u','TMPDIR':'/t/','AGENTMESH_HOME':'/x','PATH':'/evil','SECRET':'s'}
    assert child_env(dict(env={'PATH':'/p'}),parent)=={'HOME':'/h','USER':'u','TMPDIR':'/t/','PATH':'/p'}
    assert child_env(dict(env={}),parent)['PATH']==DEFAULT_PATH


def test_due_interval():
    spec=dict(interval=60,calendar=None)
    assert due(spec,None,100) and not due(spec,100,159) and due(spec,100,160) and due(spec,100,100000)


def test_due_calendar():
    spec=dict(interval=None,calendar={'Hour':4,'Minute':0,'Weekday':0})
    saturday=ts(2026,10,24,12,0)
    assert not due(spec,saturday,ts(2026,10,25,3,59)) and due(spec,saturday,ts(2026,10,25,4,0))
    assert not due(spec,ts(2026,10,25,4,0,30),ts(2026,10,25,5,0))
    assert not due(spec,ts(2026,10,25,4,0),ts(2026,10,25,4,30))
    assert due(spec,saturday,ts(2026,10,26,9,0))
    assert due(dict(spec,calendar={'Hour':4,'Minute':0,'Weekday':7}),saturday,ts(2026,10,25,4,0))
    assert due(spec,None,saturday)
