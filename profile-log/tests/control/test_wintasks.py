import json
import subprocess
import pytest
from agentmesh_log.control import procs, schedule, wintasks
from agentmesh_log.control.layout import Layout, pipe
from agentmesh_log.control.taskscheduler import TaskSchedulerBackend, split
from agentmesh_log.control.units import get
from .wintask_support import (BROKER_ARGV, CATCH_UP_ARGV, JOBS, PY, PYW, exported_job, job_argv, rendered_broker,
    rendered_job)

TRICKY=['plain','with space','tab\there','quote"in','trail\\','trail space\\ ','\\\\server\\share','"','','a\\\\"b',
    r'C:\Program Files\x\y.exe','--name=a b','%not-a-var']


def test_split_args():
    assert wintasks.split_args(subprocess.list2cmdline(TRICKY))==TRICKY
    for item in TRICKY: assert wintasks.split_args(subprocess.list2cmdline([item]))==[item]
    assert wintasks.split_args('a  "b c"  d')==['a','b c','d'] and wintasks.split_args('"a""b"')==['a"b']
    assert wintasks.split_args('')==[] and wintasks.split_args('  ')==[]


def test_durations():
    assert [wintasks.seconds(v) for v in ('PT60S','PT1M','PT5M','PT1H','P1D','P1DT1H1M1S')]==[60,60,300,3600,86400,90061]
    assert [wintasks.seconds(v) for v in ('','P','PT','5M','PT1.5S',None)]==[None]*6


def test_broker_service(tmp_path):
    spec=wintasks.spec_from_task(rendered_broker(),tmp_path/'broker.log')
    assert spec['argv']==BROKER_ARGV and spec['cwd'] is None and spec['env']=={}
    assert schedule.mode(spec)=='service' and spec['throttle']==60
    assert spec['stdout']==spec['stderr']==str(tmp_path/'broker.log')
    assert wintasks.unit_of(spec['argv'])=='broker'


@pytest.mark.parametrize('name,interval',[('ingest',60),('summary',300)],ids=['ingest','summary'])
def test_job_timers(tmp_path, name, interval):
    spec=wintasks.spec_from_task(rendered_job(job_argv(name),interval),tmp_path/'x.log')
    assert spec['argv']==job_argv(name) and spec['cwd']==JOBS
    assert schedule.mode(spec)=='timer' and spec['interval']==interval and spec['run_at_load'] is False
    assert wintasks.unit_of(spec['argv'])==name


def test_exported_task(tmp_path):
    """Export-ScheduledTask adds a UTF-16 prolog, CRLFs, defaults, a UserId and quotes the command."""
    for data in (exported_job(job_argv('summary')),exported_job(job_argv('summary')).encode('utf-16')):
        spec=wintasks.spec_from_task(data,tmp_path/'s.log')
        assert spec['argv']==job_argv('summary') and spec['interval']==300 and spec['cwd']==JOBS


def test_unit_of():
    assert wintasks.unit_of(CATCH_UP_ARGV)=='catch-up' and 'catch-up' in wintasks.RETIRED
    assert wintasks.unit_of([PY,'-m','agentmesh_log.cli','bridge','serve','--home','x'])=='bridge'
    assert wintasks.unit_of([r'C:\a\agentmesh-log.cmd','broker','run'])=='broker'
    assert wintasks.unit_of([PY,r'C:\a\job_notify.py','--name','retention','--',PY,'r.py'])=='retention'
    # Not a unit: an unknown job name, the old 0.3.1 worker, the supervisor itself, a --name only after `--`.
    assert wintasks.unit_of([PY,r'C:\a\job_notify.py','--name','sync'])is None
    assert wintasks.unit_of([PY,r'C:\o\.omp_memory\agentmesh.py','sync'])is None
    assert wintasks.unit_of([PYW,'-m','agentmesh_log.cli','control','serve','--root','x'])is None
    assert wintasks.unit_of([PY,r'C:\a\job_notify.py','--','x','--name','ingest'])is None


def test_calendar(tmp_path):
    weekly=('<Task xmlns="%s"><Triggers><CalendarTrigger><StartBoundary>2026-01-04T04:30:00</StartBoundary>'
        '<ScheduleByWeek><DaysOfWeek><Sunday/></DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>'
        '</CalendarTrigger></Triggers><Actions><Exec><Command>C:\\p.exe</Command></Exec></Actions></Task>')%wintasks.NS
    spec=wintasks.spec_from_task(weekly,tmp_path/'r.log')
    assert spec['calendar']==dict(Hour=4,Minute=30,Weekday=0) and schedule.mode(spec)=='timer'
    daily=weekly.replace('<ScheduleByWeek><DaysOfWeek><Sunday/></DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>',
        '<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>')
    assert wintasks.spec_from_task(daily,tmp_path/'r.log')['calendar']==dict(Hour=4,Minute=30)
    for other in (weekly.replace('<Sunday/>','<Sunday/><Monday/>'),daily.replace('>1</DaysInterval>','>2</DaysInterval>')):
        with pytest.raises(ValueError): wintasks.spec_from_task(other,tmp_path/'r.log')


def test_refusals(tmp_path):
    job=rendered_job(job_argv('ingest'),60)
    exec_=job[job.index('<Exec>'):job.index('</Exec>')+7]
    bad=[job.replace('</Exec>','</Exec>'+exec_),                                     # two actions
        job.replace('</TimeTrigger>','</TimeTrigger><LogonTrigger/>'),                # two triggers
        job.replace('</Interval>','</Interval><Duration>P1D</Duration>'),              # bounded repetition
        job.replace('<Repetition><Interval>PT60S</Interval></Repetition>',''),         # one-shot time trigger
        job.replace(PY,r'%USERPROFILE%\python.exe',1),                                 # environment variable
        job.replace('<Command>'+PY,'<Command>python.exe'),                             # relative program
        job.replace('<Exec>','<ComHandler><ClassId>x</ClassId></ComHandler><Exec>').replace(exec_,''),
        '<Other xmlns="%s"/>'%wintasks.NS]
    for data in bad:
        with pytest.raises(ValueError): wintasks.spec_from_task(data,tmp_path/'x.log')
    disabled=job.replace('</TimeTrigger>','</TimeTrigger><LogonTrigger><Enabled>false</Enabled></LogonTrigger>')
    assert wintasks.spec_from_task(disabled,tmp_path/'x.log')['interval']==60  # a disabled trigger does not count


def test_logon_once(tmp_path):
    once=rendered_broker().replace('<RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>','')
    spec=wintasks.spec_from_task(once,tmp_path/'x.log')
    assert schedule.mode(spec)=='once' and spec['run_at_load'] is True


def test_supervisor_task(tmp_path):
    layout=Layout(tmp_path/'home dir')
    data=wintasks.render_supervisor(layout,PYW)
    assert data[:2]==b'\xff\xfe' and data.decode('utf-16').startswith('<?xml version="1.0" encoding="UTF-16"?>')
    root=wintasks.parse(data)
    argv,cwd=wintasks.command(root)
    assert argv==[PYW,'-m','agentmesh_log.cli','control','serve','--root',str(layout.root)] and cwd==str(layout.root)
    assert wintasks.schedule_of(root)['keep_alive'] is True and wintasks.unit_of(argv) is None
    settings={c.tag.split('}')[1]:c.text for c in root.find(wintasks.q('Settings'))}
    assert settings['DisallowStartIfOnBatteries']=='false' and settings['ExecutionTimeLimit']=='PT0S'
    assert root.find('.//'+wintasks.q('LogonType')).text=='InteractiveToken'


def test_spawn_options():
    assert procs.spawn_options(dict(umask=0o77),windows=False)==dict(start_new_session=True,umask=0o77,close_fds=True)
    win=procs.spawn_options(dict(umask=0o77),windows=True)
    assert win==dict(creationflags=0x08000200,close_fds=True) and 'umask' not in win


def test_end_tree():
    calls=[]
    procs.terminate(42,windows=True,runner=lambda argv,**kw:calls.append(argv))
    procs.kill(43,windows=True,runner=lambda argv,**kw:calls.append(argv))
    assert [c[1:] for c in calls]==[['/PID','42','/T','/F'],['/PID','43','/T','/F']]
    assert calls[0][0].lower().endswith('taskkill.exe')
    procs.end_tree(1,runner=lambda argv,**kw:(_ for _ in ()).throw(OSError()))  # a missing taskkill is not fatal


def test_windows_env():
    parent={'SystemRoot':r'C:\Windows','PATH':r'C:\bin;C:\Users\o\.local\bin','USERPROFILE':r'C:\Users\o',
        'ANTHROPIC_API_KEY':'host-auth','CLAUDE_CODE_ENTRYPOINT':'desktop','AGENTMESH_HOME':r'C:\test','TEMP':r'C:\t'}
    env=schedule.child_env(dict(env={'PYTHONIOENCODING':'utf-8'}),parent,windows=True)
    assert env=={'SYSTEMROOT':r'C:\Windows','PATH':r'C:\bin;C:\Users\o\.local\bin','USERPROFILE':r'C:\Users\o',
        'TEMP':r'C:\t','PYTHONIOENCODING':'utf-8'}
    assert schedule.home(parent,windows=True)==r'C:\Users\o' and schedule.home({},windows=False)=='/'


def test_pipe_names(tmp_path):
    a,b=pipe(tmp_path/'a'),pipe(tmp_path/'b')
    assert a.startswith('\\\\.\\pipe\\agentmesh-control-') and len(a.rsplit('-',1)[1])==16 and a!=b
    assert pipe(str(tmp_path/'a').upper())==a  # Windows paths are case-insensitive


def test_names_out_of_band():
    seen=[]
    rows=[dict(path='\\',name='org.agentmesh.ingest',state='Running',xml=rendered_job(job_argv('ingest'),60)),
        dict(path='\\Odd; Remove-Item x\\',name='$(evil)',state='Ready',xml='<not xml'),
        dict(path='\\AgentMesh\\',name='Control',state='Running',xml=wintasks.render_supervisor(Layout('C:/h'),PYW).decode('utf-16'))]
    def runner(argv, env=None, input=None):
        seen.append((argv,env,input))
        return subprocess.CompletedProcess(argv,0,json.dumps(rows).encode('utf-8') if 'Export-ScheduledTask' in argv[-1] else b'',b'')
    backend=TaskSchedulerBackend(runner=runner,folder='\\AgentMeshTest-1\\',clock=lambda:0)
    found=backend.tasks()
    assert [(r['task'],r['unit']) for r in found]==[('\\org.agentmesh.ingest','ingest'),('\\Odd; Remove-Item x\\$(evil)',None),
        ('\\AgentMesh\\Control',None)]
    assert seen[0][1]['AGENTMESH_TASK_FOLDER']=='\\AgentMeshTest-1\\'
    assert backend.status(get('ingest'))==dict(loaded=True,running=True,pid=None,last_exit=None,runtime_s=None)
    assert backend.status(get('broker'))['loaded'] is False
    backend.act('register','\\Odd; Remove-Item x\\$(evil)','<Task/>')
    argv,env,data=seen[-1]
    assert argv[:2]==['powershell.exe','-NoProfile'] and '$(evil)' not in argv[-1] and 'Remove-Item x' not in argv[-1]
    assert (env['AGENTMESH_TASK_PATH'],env['AGENTMESH_TASK_NAME'])==('\\Odd; Remove-Item x\\','$(evil)') and data==b'<Task/>'
    assert backend.cached is None  # an action drops the cached listing
    failing=TaskSchedulerBackend(runner=lambda argv,**kw:subprocess.CompletedProcess(argv,1,b'',b'denied'))
    with pytest.raises(RuntimeError,match='task_scheduler_failed'): failing.act('stop','\\x')
    assert failing.status(get('broker'))['loaded'] is False


def test_split_paths():
    assert split('\\org.agentmesh.ingest')==('\\','org.agentmesh.ingest')
    assert split('\\AgentMesh\\Control')==('\\AgentMesh\\','Control') and split('Control')==('\\','Control')
