"""What a native job runs (read from its launchd plist) and when it is due. No side effects beyond reading the plist.

Missed slots after sleep give exactly one run: an interval job is due once `interval` has passed since its last start;
a calendar job is due when its most recent matching minute is later than its last start.

Three shapes come out of a plist (see `mode`): a `service` (KeepAlive true: kept running, restarted with backoff), a
`timer` (StartInterval or StartCalendarInterval) and a `once` (RunAtLoad only: one run per supervisor start).
"""
import os
import plistlib
from datetime import datetime, timedelta
from pathlib import Path

ENV_BASE=('HOME','USER','LOGNAME','SHELL','TMPDIR')
DEFAULT_PATH='/usr/bin:/bin:/usr/sbin:/sbin'
# The user environment a Task Scheduler action ran with, less anything a shell or app added. PATH stays the user's:
# Windows has no fixed default, and the summary job finds claude.exe through it. SYSTEMROOT is required by Python itself.
WINDOWS_ENV=('SYSTEMROOT','WINDIR','SYSTEMDRIVE','USERPROFILE','HOMEDRIVE','HOMEPATH','USERNAME','USERDOMAIN',
    'COMPUTERNAME','APPDATA','LOCALAPPDATA','PROGRAMDATA','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMW6432',
    'COMMONPROGRAMFILES','COMMONPROGRAMFILES(X86)','TEMP','TMP','PATH','PATHEXT','COMSPEC','OS',
    'NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE','PSMODULEPATH','PUBLIC','ALLUSERSPROFILE')
RANGES={'Minute':(0,59),'Hour':(0,23),'Weekday':(0,7)}
LOOKBACK_MINUTES=8*24*60
THROTTLE_S=10  # launchd's default ThrottleInterval
NO_LOG='/dev/null'


def plist_path(unit, agents=None):
    return Path(agents or Path.home()/'Library'/'LaunchAgents')/(unit.label+'.plist')


def spec_from_plist(data):
    """The launchd keys the native backend reproduces; ValueError for anything it cannot reproduce exactly."""
    argv,env=data.get('ProgramArguments'),data.get('EnvironmentVariables',{})
    if type(argv) is not list or not argv or not all(type(a) is str and a for a in argv) or not argv[0].startswith('/'):
        raise ValueError('unsupported_plist')
    if type(env) is not dict or not all(type(k) is str and type(v) is str for k,v in env.items()): raise ValueError('unsupported_plist')
    for key in ('WorkingDirectory','StandardOutPath','StandardErrorPath'):
        if key in data and (type(data[key]) is not str or not data[key].startswith('/')): raise ValueError('unsupported_plist')
    keep_alive=data.get('KeepAlive')
    if keep_alive not in (None,False,True): raise ValueError('unsupported_plist')  # the dictionary forms are not reproduced
    umask=data.get('Umask',0o22)
    if type(umask) is not int or not 0<=umask<=0o777: raise ValueError('unsupported_plist')
    throttle=data.get('ThrottleInterval',THROTTLE_S)
    if type(throttle) is not int or not 0<=throttle<=86400: raise ValueError('unsupported_plist')
    interval,calendar=data.get('StartInterval'),data.get('StartCalendarInterval')
    if interval is not None and calendar is not None: raise ValueError('unsupported_schedule')
    if keep_alive is True and (interval is not None or calendar is not None): raise ValueError('unsupported_schedule')
    if keep_alive is not True and interval is None and calendar is None and data.get('RunAtLoad') is not True:
        raise ValueError('unsupported_schedule')
    if interval is not None and (type(interval) is not int or interval<=0): raise ValueError('unsupported_schedule')
    if calendar is not None and (type(calendar) is not dict or not calendar or not set(calendar)<=set(RANGES)
            or not all(type(v) is int and RANGES[k][0]<=v<=RANGES[k][1] for k,v in calendar.items())):
        raise ValueError('unsupported_schedule')
    return dict(argv=argv,env=env,cwd=data.get('WorkingDirectory'),stdout=data.get('StandardOutPath',NO_LOG),
        stderr=data.get('StandardErrorPath',NO_LOG),umask=umask,interval=interval,calendar=calendar,
        run_at_load=data.get('RunAtLoad') is True,keep_alive=keep_alive is True,throttle=throttle)


def mode(spec):
    """service, timer or once. Specs recorded before services existed have no keep_alive key: they are timers."""
    if spec.get('keep_alive'): return 'service'
    return 'timer' if spec.get('interval') is not None or spec.get('calendar') is not None else 'once'


def read_spec(unit, agents=None, path=None):
    with open(path or plist_path(unit,agents),'rb') as stream: return spec_from_plist(plistlib.load(stream))


def child_env(spec, parent, windows=os.name=='nt'):
    """launchd's base environment (on Windows, the user environment a task got), then the spec's own variables.
    Nothing else from the supervisor leaks: not AGENTMESH_HOME, not a desktop app's ANTHROPIC_* host auth."""
    if windows:
        upper={key.upper():value for key,value in parent.items()}
        env={key:upper[key] for key in WINDOWS_ENV if key in upper}
    else:
        env={key:parent[key] for key in ENV_BASE if key in parent}
        env['PATH']=DEFAULT_PATH
    env.update(spec['env'])
    return env


def home(parent, windows=os.name=='nt'):
    """A child's working directory when its spec names none: the user's home, as the OS scheduler used."""
    if windows: return parent.get('USERPROFILE') or parent.get('SYSTEMROOT') or 'C:\\'
    return parent.get('HOME') or '/'


def matches(calendar, moment):
    weekday=(moment.weekday()+1)%7  # launchd: 0 and 7 are Sunday
    return (calendar.get('Minute',moment.minute)==moment.minute and calendar.get('Hour',moment.hour)==moment.hour
        and calendar.get('Weekday',weekday)%7==weekday)


def last_fire(calendar, now):
    moment=now.replace(second=0,microsecond=0)
    for _ in range(LOOKBACK_MINUTES):
        if matches(calendar,moment): return moment
        moment-=timedelta(minutes=1)
    return None


def due(spec, reference, now):
    """reference: epoch seconds of the last start, or of adoption when RunAtLoad is false; None means run now."""
    if reference is None: return True
    if spec['interval'] is not None: return now-reference>=spec['interval']
    fire=last_fire(spec['calendar'],datetime.fromtimestamp(now))
    return fire is not None and fire.timestamp()>reference
