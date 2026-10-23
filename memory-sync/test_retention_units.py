import json
import plistlib
import re
import subprocess
import xml.etree.ElementTree as ET

import pytest
import platform_service as service

BASE = dict(python='/opt/Python 3/python', app_dir='/apps/Agent Mesh', state_dir='/state/jobs')
STEM = 'org.agentmesh.retention'
NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}


def expected(webhook=None):
    args = [BASE['python'], BASE['app_dir']+'/job_notify.py', '--name', 'retention', '--state-dir', BASE['state_dir']]
    if webhook: args += ['--webhook-file', webhook]
    return args + ['--', BASE['python'], BASE['app_dir']+'/agentmesh.py', 'staging', 'retention', '--apply', '--json']


@pytest.mark.parametrize('webhook', [None, '/private/hook & file'])
@pytest.mark.parametrize('weekday,hour,minute', [(0,4,0), (6,23,59), (1,0,1)])
def test_platform_schedules_and_argv(webhook, weekday, hour, minute):
    opts = dict(BASE, webhook_file=webhook, weekday=weekday, hour=hour, minute=minute)
    mac = plistlib.loads(service.render_retention_unit('macos', **opts)[STEM+'.plist'])
    assert mac['ProgramArguments'] == expected(webhook)
    assert mac['StartCalendarInterval'] == dict(Weekday=weekday, Hour=hour, Minute=minute)
    assert mac['RunAtLoad'] is False and 'KeepAlive' not in mac and 'StartInterval' not in mac
    assert mac['Umask'] == 0o077 and mac['ProcessType'] == 'Background'
    assert mac['StandardOutPath'] == mac['StandardErrorPath'] == '/state/jobs/launchd-retention.log'
    assert 'EnvironmentVariables' not in mac
    linux = service.render_retention_unit('linux', **opts)
    text = linux[STEM+'.service'].decode()
    line = next(line.removeprefix('ExecStart=:') for line in text.splitlines() if line.startswith('ExecStart=:'))
    assert [json.loads(a).replace('%%', '%') for a in re.findall(r'"(?:\\.|[^"\\])*"', line)] == expected(webhook)
    assert 'Type=oneshot' in text and 'Environment=' not in text
    timer = linux[STEM+'.timer'].decode()
    day = ('Sun','Mon','Tue','Wed','Thu','Fri','Sat')[weekday]
    assert f'OnCalendar={day} *-*-* {hour:02d}:{minute:02d}:00' in timer
    assert 'Persistent=true' in timer
    xml = service.render_retention_unit('windows', **opts)[STEM+'.xml']
    task = ET.fromstring(xml)
    get = lambda path: task.find('t:'+path.replace('/', '/t:'), NS)
    assert get('Actions/Exec/Command').text == BASE['python']
    assert get('Actions/Exec/Arguments').text == subprocess.list2cmdline(expected(webhook)[1:])
    assert get('Triggers/CalendarTrigger/StartBoundary').text.endswith(f'T{hour:02d}:{minute:02d}:00')
    weekly = get('Triggers/CalendarTrigger/ScheduleByWeek')
    assert weekly.find('t:WeeksInterval', NS).text == '1'
    day = ('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')[weekday]
    assert weekly.find('t:DaysOfWeek/t:'+day, NS) is not None
    assert get('Settings/MultipleInstancesPolicy').text == 'IgnoreNew'
    assert get('Settings/ExecutionTimeLimit').text == 'PT2H'
    for platform in ('macos','linux','windows'):
        assert all(b'--older-than-days' not in data and b'AGENTMESH_HOME' not in data
                   for data in service.render_retention_unit(platform, **opts).values())


@pytest.mark.parametrize('platform', ['macos','linux','windows'])
def test_home(platform):
    home = '/home/100% "quoted" $HOME'
    files = service.render_retention_unit(platform, **BASE, home=home)
    if platform == 'macos':
        assert plistlib.loads(files[STEM+'.plist'])['EnvironmentVariables'] == {'AGENTMESH_HOME': home}
    elif platform == 'linux':
        line = next(x for x in files[STEM+'.service'].decode().splitlines() if x.startswith('Environment='))
        assert json.loads(line.removeprefix('Environment=')).replace('%%','%') == 'AGENTMESH_HOME='+home
    else:
        task = ET.fromstring(files[STEM+'.xml'])
        launcher = 'import os,sys; os.environ["AGENTMESH_HOME"]=sys.argv[1]; os.execv(sys.argv[2],sys.argv[2:])'
        assert task.find('t:Actions/t:Exec/t:Arguments', NS).text == subprocess.list2cmdline(['-c', launcher, home, *expected()])


@pytest.mark.parametrize('platform', ['macos','linux','windows'])
def test_invalid_arguments(platform):
    for key in ('python','app_dir','state_dir','webhook_file','home'):
        for bad in ('relative/path','/bad\npath','/bad\x00path','/bad\tpath','/bad\x7fpath','/bad\x85path'):
            with pytest.raises(ValueError): service.render_retention_unit(platform, **dict(BASE, **{key:bad}))
    for key, maximum in (('weekday',6), ('hour',23), ('minute',59)):
        for bad in (-1, maximum+1, 1.5, True, '1'):
            with pytest.raises(ValueError): service.render_retention_unit(platform, **BASE, **{key:bad})


@pytest.mark.parametrize('platform', ['macos','linux','windows'])
def test_cli_new_private_directory(tmp_path, capsys, platform):
    root = tmp_path/'new'
    argv = ['generate-retention', '--platform', platform, '--output', str(root)]
    for key, value in dict(BASE, webhook_file='/hook', home='/home/mesh', weekday='2', hour='7', minute='8').items():
        argv += ['--'+key.replace('_','-'), value]
    assert service.main(argv) == 0
    expected_files = service.render_retention_unit(platform, **BASE, webhook_file='/hook', home='/home/mesh', weekday=2, hour=7, minute=8)
    assert {p.name:p.read_bytes() for p in root.iterdir()} == expected_files
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in root.iterdir())
    assert set(capsys.readouterr().out.splitlines()) == {str(root/name) for name in expected_files}
    with pytest.raises(SystemExit) as error: service.main(argv)
    assert error.value.code == 2
    assert {p.name:p.read_bytes() for p in root.iterdir()} == expected_files


def test_cli_rejects_invalid_output(tmp_path):
    existing = tmp_path/'existing'
    existing.mkdir()
    link = tmp_path/'link'
    link.symlink_to(existing, target_is_directory=True)
    for output in ('relative', str(tmp_path/'bad\npath'), str(link/'new')):
        argv = ['generate-retention', '--platform', 'macos', '--python', '/python',
                '--app-dir', '/app', '--state-dir', '/state', '--output', output]
        with pytest.raises(SystemExit) as error: service.main(argv)
        assert error.value.code == 2
    assert list(existing.iterdir()) == []
