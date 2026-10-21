"""Generate user-service files only; never register or start services."""
from pathlib import Path
import plistlib
import json
import math
import re
from urllib.parse import urlsplit
import unicodedata


def _validate(python, app_dir, database, exchange, interval):
    for value in [python, app_dir, database, exchange]:
        value = str(value)
        if not Path(value).is_absolute() or any(unicodedata.category(c) == 'Cc' for c in value):
            raise ValueError('paths must be absolute and contain no control characters')
    if not math.isfinite(float(interval)) or float(interval) <= 0:
        raise ValueError('interval must be finite and positive')


def render_systemd(python, app_dir, database, exchange, interval=60):
    _validate(python, app_dir, database, exchange, interval)
    args = [str(python), str(Path(app_dir) / 'sync_worker.py'), str(database), str(exchange), '--interval', str(interval)]
    command = ' '.join(json.dumps(arg, ensure_ascii=False).replace('%', '%%') for arg in args)
    return ('[Unit]\nDescription=AgentMesh memory sync\n\n[Service]\n'
            'Type=simple\nExecStart=:' + command + '\nRestart=always\nRestartSec=10\n'
            'UMask=0077\n\n[Install]\nWantedBy=default.target\n')


def render_launchagent(python, app_dir, database, exchange, interval=60, postgres_dsn=None):
    _validate(python, app_dir, database, exchange, interval)
    logs = Path(database).parent
    config = {
        'Label': 'org.agentmesh.sync',
        'ProgramArguments': [str(python), str(Path(app_dir) / 'sync_worker.py'), str(database), str(exchange), '--interval', str(interval)],
        'RunAtLoad': True, 'KeepAlive': True, 'Umask': 0o077,
        'StandardOutPath': str(logs / 'agentmesh.stdout.log'),
        'StandardErrorPath': str(logs / 'agentmesh.stderr.log'),
    }
    if postgres_dsn:
        config['ProgramArguments'].append('--postgres')
        config['EnvironmentVariables'] = {'OMP_MEMORY_DSN': postgres_dsn}
    return plistlib.dumps(config)


def main(argv=None):
    import sys
    actual = sys.argv[1:] if argv is None else argv
    if any(v == '--unit' or v.startswith('--unit=') for v in actual):
        return generate_profile_units(actual)
    import argparse
    import os
    parser = argparse.ArgumentParser(description=__import__('brand').description(__doc__),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['generate'])
    parser.add_argument('--platform', required=True, choices=['macos', 'linux'])
    for name in ['python', 'app-dir', 'database', 'exchange', 'output']:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--postgres', action='store_true', help='macOS only: read OMP_MEMORY_DSN from generation environment')
    args = parser.parse_args(argv)
    dsn = os.environ.get('OMP_MEMORY_DSN') if args.postgres else None
    if args.postgres and (args.platform != 'macos' or not dsn):
        parser.error('--postgres requires macos and OMP_MEMORY_DSN')
    try:
        values = (args.python, args.app_dir, args.database, args.exchange, args.interval)
        data = render_launchagent(*values, postgres_dsn=dsn) if args.platform == 'macos' else render_systemd(*values).encode('utf-8')
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exclusive creation avoids overwriting existing services or following symlinks.
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
    except (ValueError, OSError):
        parser.error('cannot generate service: invalid arguments or output already exists/unwritable')
    return 0




def render_profile_units(platform,unit,cli_argv,*,home,database=None,mapping=None,agent_key=None,interval=300,authority=True,remote=None,logs=None):
    """Render opt-in broker/catch-up units without registering or starting them."""
    import subprocess
    import xml.etree.ElementTree as ET
    if platform not in ('macos','linux','windows') or unit not in ('broker','catch-up'): raise ValueError('invalid unit')
    if not isinstance(cli_argv,list) or not cli_argv or any(type(a) is not str or not a or any(unicodedata.category(c)=='Cc' for c in a) for a in cli_argv): raise ValueError('invalid argv')
    _validate(cli_argv[0],home,home,home,interval)
    if len(cli_argv)==3 and cli_argv[1:]==['-m','agentmesh_log.cli']: pass
    elif len(cli_argv)==2 and Path(cli_argv[1]).is_absolute(): pass
    else: raise ValueError('invalid argv')
    stem='org.agentmesh.profile-log.'+unit
    if unit=='broker': argv=[*cli_argv,'broker','run','--home',home]
    else:
        if not authority: return {}
        _validate(cli_argv[0],database,mapping,agent_key,interval)
        argv=[*cli_argv,'migrate','catch-up','--db',database,'--mapping',mapping,'--agent-key',agent_key]
    if remote is not None:
        if unit!='broker' or type(remote) is not dict or set(remote)!={'bucket','prefix','endpoint','credentials','sync_interval'}:
            raise ValueError('invalid remote')
        bucket,prefix,endpoint,credentials,seconds=(remote[k] for k in ('bucket','prefix','endpoint','credentials','sync_interval'))
        if any(type(v) is not str or any(unicodedata.category(c)=='Cc' for c in v) for v in (bucket,prefix,endpoint,credentials)):
            raise ValueError('invalid remote')
        if not re.fullmatch('[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',bucket): raise ValueError('invalid bucket')
        parsed=urlsplit(endpoint)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in endpoint):
            raise ValueError('invalid endpoint')
        # Same grammar as profile-log.storage.validate_key(prefix=True), without a package dependency.
        value=prefix[:-1] if prefix.endswith('/') else prefix
        if len(prefix)>1024 or (prefix and (not value or any(not re.fullmatch('[A-Za-z0-9_-][A-Za-z0-9._-]*',p) for p in value.split('/')))):
            raise ValueError('invalid prefix')
        if not re.fullmatch(r'keychain:[A-Za-z0-9.-]+',credentials): raise ValueError('invalid credentials reference')
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<10: raise ValueError('invalid sync interval')
        argv+=['--s3-bucket',bucket,'--s3-prefix',prefix,'--s3-endpoint',endpoint,'--s3-credentials',credentials,'--sync-interval',format(seconds,'g')]
    if logs is not None:
        if unit!='broker': raise ValueError('invalid logs')
        _validate(cli_argv[0],logs,logs,logs,interval)
    if platform=='macos':
        value=dict(Label=stem,ProgramArguments=argv,RunAtLoad=True,Umask=0o077)
        if logs is not None:
            value.update(StandardOutPath=str(Path(logs)/'broker.out.log'),StandardErrorPath=str(Path(logs)/'broker.err.log'))
        if unit=='broker': value['KeepAlive']=True
        else: value['StartInterval']=int(interval)
        return {stem+'.plist':plistlib.dumps(value)}
    if platform=='linux':
        command=' '.join(json.dumps(a,ensure_ascii=False).replace('%','%%') for a in argv)
        value='[Unit]\nDescription=AgentMesh profile log '+unit+'\n\n[Service]\nType='+('simple' if unit=='broker' else 'oneshot')+'\nExecStart=:'+command+'\nUMask=0077\n'
        if unit=='broker': value+='Restart=on-failure\nRestartSec=10\n\n[Install]\nWantedBy=default.target\n'
        result={stem+'.service':value.encode('utf-8')}
        if unit=='catch-up': result[stem+'.timer']=('[Unit]\nDescription=AgentMesh migration catch-up\n\n[Timer]\nOnBootSec=30\nOnUnitActiveSec='+str(interval)+'\nUnit='+stem+'.service\n\n[Install]\nWantedBy=timers.target\n').encode('utf-8')
        return result
    task=ET.Element('Task',version='1.4',xmlns='http://schemas.microsoft.com/windows/2004/02/mit/task')
    triggers=ET.SubElement(task,'Triggers')
    trigger=ET.SubElement(triggers,'LogonTrigger' if unit=='broker' else 'TimeTrigger')
    if unit=='catch-up':
        ET.SubElement(trigger,'StartBoundary').text='2020-01-01T00:00:00'
        repeat=ET.SubElement(trigger,'Repetition'); ET.SubElement(repeat,'Interval').text='PT'+str(int(interval))+'S'
    ET.SubElement(trigger,'Enabled').text='true'
    settings=ET.SubElement(task,'Settings'); restart=ET.SubElement(settings,'RestartOnFailure')
    ET.SubElement(restart,'Interval').text='PT1M'; ET.SubElement(restart,'Count').text='3'
    ET.SubElement(settings,'MultipleInstancesPolicy').text='IgnoreNew'
    ET.SubElement(settings,'ExecutionTimeLimit').text='PT0S'
    action=ET.SubElement(ET.SubElement(task,'Actions'),'Exec')
    ET.SubElement(action,'Command').text=argv[0]; ET.SubElement(action,'Arguments').text=subprocess.list2cmdline(argv[1:])
    return {stem+'.xml':ET.tostring(task,encoding='utf-8',xml_declaration=True)}


def generate_profile_units(argv):
    import argparse
    import os
    from profile_log_bridge import load_config
    class SafeParser(argparse.ArgumentParser):
        def error(self,message): self.exit(2,'invalid service arguments\n')
    p=SafeParser(description='Render profile-log service files only')
    p.add_argument('command',choices=['generate']); p.add_argument('--unit',choices=['broker','catch-up'],required=True)
    p.add_argument('--platform',choices=['macos','linux','windows'],required=True)
    p.add_argument('--database',required=True); p.add_argument('--home',required=True)
    p.add_argument('--output',required=True,help='new directory for generated files')
    p.add_argument('--interval',type=float,default=300)
    p.add_argument('--s3-bucket'); p.add_argument('--s3-prefix',default=''); p.add_argument('--s3-endpoint')
    p.add_argument('--s3-credentials'); p.add_argument('--sync-interval',type=float,default=120); p.add_argument('--logs')
    args=p.parse_args(argv)
    remote=None
    if args.s3_bucket is not None:
        remote=dict(bucket=args.s3_bucket,prefix=args.s3_prefix,endpoint=args.s3_endpoint,
            credentials=args.s3_credentials,sync_interval=args.sync_interval)
    elif args.s3_prefix or args.s3_endpoint or args.s3_credentials: raise ValueError('invalid remote')
    config=load_config(args.database)
    if not config or not config['enabled']: raise ValueError('bridge not enabled')
    files=render_profile_units(args.platform,args.unit,config['cli_argv'],home=args.home,database=args.database,
        mapping=config['mapping'],agent_key=config['import_agent_key'],interval=args.interval,authority=config['authority'],remote=remote,logs=args.logs)
    if not files: return 0
    root=Path(args.output)
    if not root.is_absolute() or any(unicodedata.category(c)=='Cc' for c in str(root)) or any(p.is_symlink() for p in (root,*root.parents)): raise ValueError('invalid output')
    root.mkdir(mode=0o700)
    for name,data in files.items():
        fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream: stream.write(data)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
