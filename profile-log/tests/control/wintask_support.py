"""Task XML as the pre-R5b renderers wrote it (memory-sync/platform_service.py) and as Task Scheduler exports it."""
import subprocess

NS='http://schemas.microsoft.com/windows/2004/02/mit/task'
PY=r'C:\Users\o\.agentmesh\venv\Scripts\python.exe'
PYW=r'C:\Users\o\.agentmesh\venv\Scripts\pythonw.exe'
APP=r'C:\Users\o\.agentmesh\app\AgentMesh\memory-sync'
JOBS=r'C:\Users\o\.agentmesh\jobs'
HOME=r'C:\Users\o\.agentmesh\profile-log'

BROKER_ARGV=[PYW,'-m','agentmesh_log.cli','broker','run','--home',HOME,'--s3-bucket','agentmesh','--s3-prefix','v1/',
    '--s3-endpoint','https://x.r2.cloudflarestorage.com','--s3-credentials','wincred:agentmesh.r2','--sync-interval','120',
    '--log-dir',r'C:\Users\o\.agentmesh\logs']


def job_argv(name):
    return [PY,APP+r'\job_notify.py','--name',name,'--state-dir',r'C:\Users\o\.agentmesh\production-status',
        '--webhook-file',r'C:\Users\o\.config\agentmesh\discord webhook','--',PY,JOBS+'\\agentmesh_'+name+'.py']


CATCH_UP_ARGV=[PY,'-m','agentmesh_log.cli','migrate','catch-up','--db',r'C:\x\windows-staging.db','--mapping',r'C:\x\m.json',
    '--agent-key',r'C:\x\importer.key']


def rendered_broker():
    """render_profile_units('windows','broker',...) before R5b."""
    return ('<Task version="1.4" xmlns="%s"><Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>'
        '<Principals><Principal id="AgentMesh"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>'
        '</Principal></Principals><Settings><RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>'
        '<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings>'
        '<Actions Context="AgentMesh"><Exec><Command>%s</Command><Arguments>%s</Arguments></Exec></Actions></Task>'
        %(NS,BROKER_ARGV[0],subprocess.list2cmdline(BROKER_ARGV[1:])))


def rendered_job(argv, interval, cwd=JOBS):
    """render_job_unit('windows',...) (and catch-up's TimeTrigger) before R5b."""
    return ('<Task version="1.4" xmlns="%s"><Triggers><TimeTrigger><StartBoundary>2020-01-01T00:00:00</StartBoundary>'
        '<Repetition><Interval>PT%dS</Interval></Repetition><Enabled>true</Enabled></TimeTrigger></Triggers>'
        '<Principals><Principal id="AgentMesh"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>'
        '</Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>'
        '<ExecutionTimeLimit>PT1H</ExecutionTimeLimit></Settings><Actions Context="AgentMesh"><Exec><Command>%s</Command>'
        '<Arguments>%s</Arguments><WorkingDirectory>%s</WorkingDirectory></Exec></Actions></Task>'
        %(NS,interval,argv[0],subprocess.list2cmdline(argv[1:]),cwd))


def exported_job(argv, interval='PT5M'):
    """What Export-ScheduledTask returns once Task Scheduler has normalized an imported task."""
    return ('<?xml version="1.0" encoding="UTF-16"?>\r\n<Task version="1.4" xmlns="%s">\r\n  <RegistrationInfo>\r\n'
        '    <URI>\\org.agentmesh.summary</URI>\r\n  </RegistrationInfo>\r\n  <Triggers>\r\n    <TimeTrigger>\r\n'
        '      <Repetition>\r\n        <Interval>%s</Interval>\r\n        <StopAtDurationEnd>false</StopAtDurationEnd>\r\n'
        '      </Repetition>\r\n      <StartBoundary>2020-01-01T00:00:00</StartBoundary>\r\n      <Enabled>true</Enabled>\r\n'
        '    </TimeTrigger>\r\n  </Triggers>\r\n  <Principals>\r\n    <Principal id="AgentMesh">\r\n'
        '      <UserId>S-1-5-21-1-2-3-1001</UserId>\r\n      <LogonType>InteractiveToken</LogonType>\r\n'
        '      <RunLevel>LeastPrivilege</RunLevel>\r\n    </Principal>\r\n  </Principals>\r\n  <Settings>\r\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\r\n'
        '    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>\r\n    <IdleSettings>\r\n'
        '      <StopOnIdleEnd>true</StopOnIdleEnd>\r\n    </IdleSettings>\r\n    <Enabled>true</Enabled>\r\n'
        '    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>\r\n  </Settings>\r\n  <Actions Context="AgentMesh">\r\n'
        '    <Exec>\r\n      <Command>"%s"</Command>\r\n      <Arguments>%s</Arguments>\r\n'
        '      <WorkingDirectory>%s</WorkingDirectory>\r\n    </Exec>\r\n  </Actions>\r\n</Task>'
        %(NS,interval,argv[0],subprocess.list2cmdline(argv[1:]),JOBS))


class FakeTasks:
    """An in-memory Task Scheduler with the TaskSchedulerBackend interface the migration uses."""

    def __init__(self, rows, fail=None):
        from agentmesh_log.control import wintasks
        self.wintasks=wintasks
        self.rows={task:dict(task=task,state=state,xml=xml) for task,state,xml in rows}
        self.calls,self.fail=[],fail or {}

    def unit(self, xml):
        try: return self.wintasks.unit_of(self.wintasks.command(self.wintasks.parse(xml))[0])
        except ValueError: return None

    def tasks(self, fresh=False):
        return [dict(row,unit=self.unit(row['xml'])) for row in self.rows.values()]

    def act(self, action, task, xml=None):
        self.calls.append((action,task))
        if self.fail.get((action,task)):
            raise RuntimeError(self.fail[(action,task)] if isinstance(self.fail[(action,task)],str) else 'task_scheduler_failed')
        if action=='register': self.rows[task]=dict(task=task,state='Ready',xml=xml); return True
        if task not in self.rows: raise RuntimeError('task_scheduler_failed')
        row=self.rows[task]
        if action=='delete': del self.rows[task]
        elif action=='disable': row['state']='Disabled'
        elif action=='enable': row['state']='Ready'
        elif action=='stop' and row['state']=='Running': row['state']='Ready'
        elif action=='start': row['state']='Running'
        return True

    def find(self, unit):
        rows=[r for r in self.tasks() if r['unit']==unit.name]
        return rows[0] if len(rows)==1 else None

    def status(self, unit):
        row=self.find(unit)
        return dict(loaded=row is not None and row['state']!='Disabled',running=bool(row and row['state']=='Running'),
            pid=None,last_exit=None,runtime_s=None)
