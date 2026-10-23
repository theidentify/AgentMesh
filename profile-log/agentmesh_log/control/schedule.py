"""What a native job runs (read from its launchd plist) and when it is due. No side effects beyond reading the plist.

Missed slots after sleep give exactly one run: an interval job is due once `interval` has passed since its last start;
a calendar job is due when its most recent matching minute is later than its last start.
"""
import plistlib
from datetime import datetime, timedelta
from pathlib import Path

ENV_BASE=('HOME','USER','LOGNAME','SHELL','TMPDIR')
DEFAULT_PATH='/usr/bin:/bin:/usr/sbin:/sbin'
RANGES={'Minute':(0,59),'Hour':(0,23),'Weekday':(0,7)}
LOOKBACK_MINUTES=8*24*60


def plist_path(unit, agents=None):
    return Path(agents or Path.home()/'Library'/'LaunchAgents')/(unit.label+'.plist')


def spec_from_plist(data):
    """The launchd keys the native backend reproduces; ValueError for anything it cannot reproduce exactly."""
    argv,env=data.get('ProgramArguments'),data.get('EnvironmentVariables',{})
    if type(argv) is not list or not argv or not all(type(a) is str and a for a in argv) or not argv[0].startswith('/'):
        raise ValueError('unsupported_plist')
    if type(env) is not dict or not all(type(k) is str and type(v) is str for k,v in env.items()): raise ValueError('unsupported_plist')
    if 'StandardOutPath' not in data or 'StandardErrorPath' not in data: raise ValueError('unsupported_plist')
    for key in ('WorkingDirectory','StandardOutPath','StandardErrorPath'):
        if key in data and (type(data[key]) is not str or not data[key].startswith('/')): raise ValueError('unsupported_plist')
    if data.get('KeepAlive') not in (None,False): raise ValueError('unsupported_plist')
    umask=data.get('Umask',0o22)
    if type(umask) is not int or not 0<=umask<=0o777: raise ValueError('unsupported_plist')
    interval,calendar=data.get('StartInterval'),data.get('StartCalendarInterval')
    if (interval is None)==(calendar is None): raise ValueError('unsupported_schedule')
    if interval is not None and (type(interval) is not int or interval<=0): raise ValueError('unsupported_schedule')
    if calendar is not None and (type(calendar) is not dict or not calendar or not set(calendar)<=set(RANGES)
            or not all(type(v) is int and RANGES[k][0]<=v<=RANGES[k][1] for k,v in calendar.items())):
        raise ValueError('unsupported_schedule')
    return dict(argv=argv,env=env,cwd=data.get('WorkingDirectory'),stdout=data['StandardOutPath'],
        stderr=data['StandardErrorPath'],umask=umask,interval=interval,calendar=calendar,run_at_load=data.get('RunAtLoad') is True)


def read_spec(unit, agents=None):
    with open(plist_path(unit,agents),'rb') as stream: return spec_from_plist(plistlib.load(stream))


def child_env(spec, parent):
    """launchd's base environment, then the plist's EnvironmentVariables. Nothing else from the supervisor leaks."""
    env={key:parent[key] for key in ENV_BASE if key in parent}
    env['PATH']=DEFAULT_PATH
    env.update(spec['env'])
    return env


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
