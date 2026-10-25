import plistlib
import subprocess
import xml.etree.ElementTree as ET

import pytest
import platform_service as service

BASE=dict(python='/opt/Python 3/pythonw.exe',jobs_dir='/mesh/jobs folder',app_dir='/mesh/app',state_dir='/mesh/state')
NS={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}


def expected(name,webhook):
    argv=[BASE['python'],'/mesh/app/job_notify.py','--name',name,'--state-dir','/mesh/state']
    if webhook is not None: argv+=['--webhook-file',webhook]
    return argv+['--',BASE['python'],'/mesh/jobs folder/agentmesh_'+name+'.py']


@pytest.mark.parametrize('name,interval',[('ingest',60),('summary',300)])
@pytest.mark.parametrize('webhook',[None,'/private/hook & file'])
def test_job_units(name,interval,webhook):
    opts=dict(BASE,interval=interval,webhook_file=webhook)
    stem='org.agentmesh.'+name
    mac=plistlib.loads(service.render_job_unit('macos',name,**opts,path_env='/u/.local/bin:/usr/bin')[stem+'.plist'])
    log='/mesh/state/launchd-'+name+'.log'
    # Same keys as the live Mac org.agentmesh.ingest/summary plists.
    assert mac == dict(Label=stem,ProgramArguments=expected(name,webhook),StartInterval=interval,
                      Umask=0o077,WorkingDirectory=BASE['jobs_dir'],RunAtLoad=True,ProcessType='Background',
                      StandardOutPath=log,StandardErrorPath=log,EnvironmentVariables={'PATH':'/u/.local/bin:/usr/bin'})
    assert 'EnvironmentVariables' not in plistlib.loads(service.render_job_unit('macos',name,**opts)[stem+'.plist'])
    xml=service.render_job_unit('windows',name,**opts)[stem+'.xml']
    assert xml.startswith((b'\xff\xfe',b'\xfe\xff'))
    task=ET.fromstring(xml.decode('utf-16'))
    get=lambda path: task.find('t:'+path.replace('/','/t:'),NS)
    assert get('Triggers/TimeTrigger/Repetition/Interval').text == 'PT'+str(interval)+'S'
    assert get('Triggers/TimeTrigger/StartBoundary').text == '2020-01-01T00:00:00'
    assert get('Triggers/TimeTrigger/Enabled').text == 'true'
    assert get('Principals/Principal').attrib == {'id':'AgentMesh'}
    assert get('Principals/Principal/RunLevel').text == 'LeastPrivilege'
    assert get('Principals/Principal/LogonType').text == 'InteractiveToken'
    assert get('Settings/MultipleInstancesPolicy').text == 'IgnoreNew'
    assert get('Settings/ExecutionTimeLimit').text == 'PT1H'
    assert get('Actions').attrib == {'Context':'AgentMesh'}
    assert get('Actions/Exec/Command').text == BASE['python']
    assert get('Actions/Exec/Arguments').text == subprocess.list2cmdline(expected(name,webhook)[1:])
    assert get('Actions/Exec/WorkingDirectory').text == BASE['jobs_dir']


@pytest.mark.parametrize('platform',['macos','windows'])
def test_invalid_paths_and_intervals(platform):
    for key in (*BASE,'webhook_file'):
        for bad in ('relative/path','/bad\npath','/bad\x00path'):
            with pytest.raises(ValueError): service.render_job_unit(platform,'ingest',**dict(BASE,interval=60,**{key:bad}))
    for interval in (0,-1,float('inf'),float('nan'),0.5):
        with pytest.raises(ValueError): service.render_job_unit(platform,'ingest',**BASE,interval=interval)
    with pytest.raises(ValueError,match='invalid job'): service.render_job_unit(platform,'other',**BASE,interval=60)
    with pytest.raises(ValueError,match='invalid platform'): service.render_job_unit('linux','ingest',**BASE,interval=60)


def cli(root,platform='windows'):
    argv=['generate-jobs','--platform',platform,'--output',str(root)]
    for key,value in BASE.items(): argv+=['--'+key.replace('_','-'),value]
    return argv


@pytest.mark.parametrize('platform',['macos','windows'])
@pytest.mark.parametrize('custom',[False,True])
def test_generate_jobs_private_new_directory(tmp_path,platform,custom):
    root=tmp_path/'new'; argv=cli(root,platform)
    intervals=(72,360) if custom else (60,300)
    if custom: argv+=['--ingest-interval','72','--summary-interval','360','--webhook-file','/hook']
    assert service.main(argv) == 0
    files={}
    for name,interval in zip(('ingest','summary'),intervals):
        files.update(service.render_job_unit(platform,name,**BASE,interval=interval,webhook_file='/hook' if custom else None))
    assert {p.name:p.read_bytes() for p in root.iterdir()} == files
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in root.iterdir())
    with pytest.raises(SystemExit) as error: service.main(argv)
    assert error.value.code == 2
    assert {p.name:p.read_bytes() for p in root.iterdir()} == files


def test_generate_jobs_rejects_symlinks_and_relative_output(tmp_path):
    existing=tmp_path/'existing'; existing.mkdir()
    link=tmp_path/'link'; link.symlink_to(existing,target_is_directory=True)
    for output in ('relative',tmp_path/'bad\npath',link/'new',link,existing):
        with pytest.raises(SystemExit) as error: service.main(cli(output))
        assert error.value.code == 2
    assert list(existing.iterdir()) == []


def test_sqlite_private_permissions_skip_windows(monkeypatch):
    from types import SimpleNamespace
    import sqlite_memory
    calls=[]
    monkeypatch.setattr(sqlite_memory,'os',SimpleNamespace(name='nt',chmod=lambda *args:calls.append(args)))
    sqlite_memory._private('/db')
    assert calls == []
    monkeypatch.setattr(sqlite_memory,'os',SimpleNamespace(name='posix',chmod=lambda *args:calls.append(args)))
    sqlite_memory._private('/db')
    assert calls == [('/db',0o600),('/db-wal',0o600),('/db-shm',0o600)]
