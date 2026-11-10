"""Task Scheduler definitions, read and written as XML. Pure functions: nothing here registers or queries a task.

On Windows the units start as separate scheduled tasks, imported by hand from rendered XML (the broker at logon, ingest
every minute, summary every five, and before R5b catch-up). `control consolidate --apply` moves them under one logon
task, the supervisor (TASK_NAME), the same way it moves launchd plists on a Mac. This module turns an exported task into
the spec the native backend runs, says which unit a task is from what it runs (task names were the owner's choice), and
renders the supervisor's own task.

What a spec keeps from a task: the one Exec action (command, arguments split as CommandLineToArgvW does, working
directory) and its one trigger. A logon trigger with RestartOnFailure is a service; a logon trigger alone runs once per
supervisor start; a time trigger repeating every N is a timer; a daily or weekly calendar trigger is a calendar timer.
Anything else (several actions or triggers, a bounded repetition, an environment variable in the command) is refused,
so a task the backend cannot reproduce stays in Task Scheduler.
"""
import ntpath
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime

NS='http://schemas.microsoft.com/windows/2004/02/mit/task'
TASK_NAME=r'\AgentMesh\Control'
RETIRED=frozenset(('catch-up',))  # the SQLite import, removed at R5b: its task is deleted, never supervised
DAYS=('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')  # launchd Weekday numbering
DURATION=re.compile(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?')
JOBS=('ingest','summary','retention')


def q(name): return '{%s}%s'%(NS,name)


def text(node, path, default=None):
    found=node.find(path) if node is not None else None
    return found.text.strip() if found is not None and found.text is not None else default


def seconds(value):
    """An XML duration (P1D, PT5M, PT90S) in whole seconds, or None."""
    match=DURATION.fullmatch(value or '')
    if not match or not any(match.groups()) or value.endswith('T'): return None
    d,h,m,s=(int(g or 0) for g in match.groups())
    return ((d*24+h)*60+m)*60+s


def split_args(line):
    """Split a command line the way CommandLineToArgvW and the MS C runtime do; inverse of subprocess.list2cmdline."""
    args,current,quoted,started,i=[],[],False,False,0
    while i<len(line):
        c=line[i]
        if c=='\\':
            j=i
            while j<len(line) and line[j]=='\\': j+=1
            if j<len(line) and line[j]=='"':
                current.append('\\'*((j-i)//2)); started=True
                if (j-i)%2: current.append('"'); i=j+1
                else: i=j
            else: current.append('\\'*(j-i)); started=True; i=j
            continue
        if c=='"':
            if quoted and i+1<len(line) and line[i+1]=='"': current.append('"'); i+=2; continue
            quoted=not quoted; started=True; i+=1; continue
        if c in ' \t' and not quoted:
            if started: args.append(''.join(current)); current,started=[],False
            i+=1; continue
        current.append(c); started=True; i+=1
    if started: args.append(''.join(current))
    return args


def parse(data):
    """The Task element of an exported task (bytes in any encoding its prolog declares, or str)."""
    if isinstance(data,str): data=re.sub(r'^\s*<\?xml[^>]*\?>','',data.lstrip('\ufeff'))
    root=ET.fromstring(data)
    if root.tag!=q('Task'): raise ValueError('unsupported_task')
    return root


def command(root):
    """argv and working directory of the task's one Exec action."""
    actions=root.find(q('Actions'))
    items=list(actions) if actions is not None else []
    if len(items)!=1 or items[0].tag!=q('Exec'): raise ValueError('unsupported_task')
    program=(text(items[0],q('Command')) or '').strip('"')
    arguments=text(items[0],q('Arguments'),'')
    cwd=text(items[0],q('WorkingDirectory'))
    if not program or '%' in program+arguments+(cwd or '') or not ntpath.isabs(program): raise ValueError('unsupported_task')
    if cwd is not None and not ntpath.isabs(cwd.strip('"')): raise ValueError('unsupported_task')
    return [program,*split_args(arguments)],(cwd.strip('"') if cwd else None)


def job_name(options):
    """The --name of a job wrapper's own options; they end at `--`, and the rest is the job."""
    if '--' in options: options=options[:options.index('--')]
    name=options[options.index('--name')+1] if '--name' in options[:-1] else None
    return name if name in JOBS else None


def unit_of(argv):
    """Which AgentMesh unit an argv runs, or None for anything else. A scheduled job runs through the job wrapper:
    memory-sync's job_notify.py before its retirement, `-m agentmesh_log.control.jobrun` or `agentmesh-log job run` after."""
    rest=argv[1:]
    if rest[:2]==['-m','agentmesh_log.control.jobrun']: return job_name(rest[2:])
    if len(rest)>=2 and rest[:2]==['-m','agentmesh_log.cli']: rest=rest[2:]
    elif ntpath.basename(argv[0]).lower() in ('agentmesh-log','agentmesh-log.exe','agentmesh-log.cmd'): pass
    elif rest and ntpath.basename(rest[0]).lower()=='job_notify.py': return job_name(rest[1:])
    else: return None
    if rest[:2]==['job','run']: return job_name(rest[2:])
    head=[a for a in rest if not a.startswith('-')][:2]
    return {('broker','run'):'broker',('bridge','serve'):'bridge',('migrate','catch-up'):'catch-up'}.get(tuple(head))


def schedule_of(root):
    """interval, calendar, run_at_load, keep_alive and throttle from the task's one trigger and its settings."""
    triggers=root.find(q('Triggers'))
    items=[t for t in (list(triggers) if triggers is not None else []) if text(t,q('Enabled'),'true')!='false']
    if len(items)!=1: raise ValueError('unsupported_schedule')
    trigger,settings=items[0],root.find(q('Settings'))
    repeat=trigger.find(q('Repetition'))
    every=seconds(text(repeat,q('Interval'))) if repeat is not None else None
    if repeat is not None and (every is None or text(repeat,q('Duration')) not in (None,'')): raise ValueError('unsupported_schedule')
    base=dict(interval=None,calendar=None,run_at_load=False,keep_alive=False,throttle=60)
    if trigger.tag in (q('LogonTrigger'),q('BootTrigger')):
        if every is not None: return dict(base,interval=every,run_at_load=True)
        restart=settings.find(q('RestartOnFailure')) if settings is not None else None
        if restart is None: return dict(base,run_at_load=True)
        return dict(base,keep_alive=True,throttle=seconds(text(restart,q('Interval'))) or 60)
    if trigger.tag==q('TimeTrigger'):
        if every is None: raise ValueError('unsupported_schedule')  # a one-shot time trigger
        return dict(base,interval=every)
    if trigger.tag==q('CalendarTrigger') and every is None:
        start=text(trigger,q('StartBoundary'))
        try: moment=datetime.fromisoformat(start[:19])
        except (TypeError,ValueError): raise ValueError('unsupported_schedule') from None
        calendar=dict(Hour=moment.hour,Minute=moment.minute)
        daily,weekly=trigger.find(q('ScheduleByDay')),trigger.find(q('ScheduleByWeek'))
        if daily is not None and text(daily,q('DaysInterval'),'1')=='1': return dict(base,calendar=calendar)
        days=weekly.find(q('DaysOfWeek')) if weekly is not None else None
        if days is not None and text(weekly,q('WeeksInterval'),'1')=='1' and len(days)==1:
            day=days[0].tag.split('}')[-1]
            if day in DAYS: return dict(base,calendar=dict(calendar,Weekday=DAYS.index(day)))
    raise ValueError('unsupported_schedule')


def spec_from_task(data, log):
    """The native spec for an exported task; `log` takes the output Task Scheduler never kept. ValueError otherwise."""
    root=parse(data); argv,cwd=command(root)
    return dict(argv=argv,env={},cwd=cwd,stdout=str(log),stderr=str(log),umask=0o77,**schedule_of(root))


def render_supervisor(layout, python):
    """The logon task that keeps `control serve` running: the only AgentMesh task after consolidate.
    UTF-16 with a BOM, which `schtasks /Create /XML` requires."""
    root=ET.Element('Task',{'version':'1.4','xmlns':NS})
    info=ET.SubElement(root,'RegistrationInfo')
    ET.SubElement(info,'Description').text='AgentMesh supervisor: runs the broker, ingest and summary as its children.'
    ET.SubElement(ET.SubElement(ET.SubElement(root,'Triggers'),'LogonTrigger'),'Enabled').text='true'
    principal=ET.SubElement(ET.SubElement(root,'Principals'),'Principal',{'id':'Author'})
    ET.SubElement(principal,'LogonType').text='InteractiveToken'; ET.SubElement(principal,'RunLevel').text='LeastPrivilege'
    settings=ET.SubElement(root,'Settings')
    for key,value in (('MultipleInstancesPolicy','IgnoreNew'),('DisallowStartIfOnBatteries','false'),
            ('StopIfGoingOnBatteries','false'),('StartWhenAvailable','true'),('ExecutionTimeLimit','PT0S')):
        ET.SubElement(settings,key).text=value
    restart=ET.SubElement(settings,'RestartOnFailure')
    ET.SubElement(restart,'Interval').text='PT1M'; ET.SubElement(restart,'Count').text='999'
    action=ET.SubElement(ET.SubElement(root,'Actions',{'Context':'Author'}),'Exec')
    ET.SubElement(action,'Command').text=str(python)
    ET.SubElement(action,'Arguments').text=subprocess.list2cmdline(
        ['-m','agentmesh_log.cli','control','serve','--root',str(layout.root)])
    ET.SubElement(action,'WorkingDirectory').text=str(layout.root)
    return ('<?xml version="1.0" encoding="UTF-16"?>\n'+ET.tostring(root,encoding='unicode')).encode('utf-16')
