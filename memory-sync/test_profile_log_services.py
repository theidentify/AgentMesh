import plistlib
import pytest
import platform_service as service


def test_p5_11_units(tmp_path):
    cli=[str(tmp_path/'python'),'-m','agentmesh_log.cli']
    for platform in ('macos','linux','windows'):
        for unit in ('broker','catch-up'):
            files=service.render_profile_units(platform,unit,cli,home=str(tmp_path/'home'),database=str(tmp_path/'db'),
                mapping=str(tmp_path/'mapping'),agent_key=str(tmp_path/'agent'),authority=True)
            assert files and all(b'--sync-interval' not in data for data in files.values())
            if platform=='macos':
                config=plistlib.loads(next(iter(files.values())))
                assert config['Umask']==0o077
                assert config['ProgramArguments'][:3]==cli
            if platform=='linux' and unit=='catch-up': assert len(files)==2
    with pytest.raises(ValueError): service.render_profile_units('linux','broker',['relative'],home=str(tmp_path/'home'))


@pytest.mark.parametrize('platform',['macos','linux','windows'])
@pytest.mark.parametrize('unit',['broker','catch-up'])
def test_snapshots(platform,unit):
    import json
    from pathlib import Path
    root=Path(Path.cwd().anchor)/'synthetic'
    cli=[str(root/'python'),'-m','agentmesh_log.cli']
    files=service.render_profile_units(platform,unit,cli,home=str(root/'home'),database=str(root/'db'),mapping=str(root/'mapping'),agent_key=str(root/'agent'))
    # Fixed POSIX inputs keep the cross-platform renderer snapshots portable.
    if str(root)!='/synthetic':
        from unittest.mock import patch
        with patch.object(service.Path,'is_absolute',return_value=True):
            files=service.render_profile_units(platform,unit,['/synthetic/python','-m','agentmesh_log.cli'],home='/synthetic/home',database='/synthetic/db',mapping='/synthetic/mapping',agent_key='/synthetic/agent')
    expected=json.loads((Path(__file__).parent/'tests/fixtures/profile_log_units.json').read_text(encoding='utf-8'))
    snapshot=expected[platform+'/'+unit]
    if platform=='windows' and unit=='broker':
        snapshot={k:v.replace('<Settings>', '<Principals><Principal id="AgentMesh"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals><Settings>').replace('<Count>3</Count>','<Count>999</Count>').replace('<Actions>','<Actions Context="AgentMesh">') for k,v in snapshot.items()}
    if platform=='windows':
        # Task Scheduler rejects a utf-8 declaration; it needs UTF-16 with a BOM.
        assert all(v.startswith(b'\xff\xfe') for v in files.values())
        snapshot={k:v.replace("<?xml version='1.0' encoding='utf-8'?>\n",'<?xml version="1.0" encoding="UTF-16"?>\n') for k,v in snapshot.items()}
    assert {k:v.decode('utf-16' if platform=='windows' else 'utf-8') for k,v in files.items()}==snapshot


@pytest.mark.parametrize('platform',['macos','linux'])
def test_sync_unchanged(tmp_path,platform):
    import json
    from pathlib import Path
    expected=json.loads((Path(__file__).parent/'tests/fixtures/profile_log_units.json').read_text(encoding='utf-8'))
    from unittest.mock import patch
    with patch.object(service.Path,'is_absolute',return_value=True):
        out=tmp_path/'sync.unit'
        assert service.main(['generate','--platform',platform,'--python','/synthetic/python',
            '--app-dir','/synthetic/app','--database','/synthetic/db','--exchange','/synthetic/exchange','--output',str(out)])==0
        actual=out.read_bytes()
    # Path joining is native in the unchanged renderer.
    if __import__('os').name=='nt': actual=actual.replace(bytes([92,92]),b'/').replace(bytes([92]),b'/')
    assert actual==expected['sync/'+platform].encode('utf-8')


@pytest.mark.parametrize('platform',['macos','linux','windows'])
@pytest.mark.parametrize('unit',['broker','catch-up'])
def test_exclusive(tmp_path,platform,unit):
    from test_profile_log_bridge import config
    db,path,value=config(tmp_path)
    out=tmp_path/'units'
    argv=['generate','--unit',unit,'--platform',platform,'--database',str(db),'--home',str(tmp_path/'home'),'--output',str(out)]
    assert service.main(argv)==0
    before={p.name:p.read_bytes() for p in out.iterdir()}
    with pytest.raises(FileExistsError): service.main(argv)
    assert before=={p.name:p.read_bytes() for p in out.iterdir()}
    if __import__('os').name!='nt': assert all(p.stat().st_mode&0o777==0o600 for p in out.iterdir())


def remote_config(**kw):
    return dict(dict(bucket='profile-r2',prefix='v1/',endpoint='https://example.r2.cloudflarestorage.com',
        credentials='keychain:org.agentmesh.profile-log.r2',sync_interval=60),**kw)


def test_r2_plist(tmp_path):
    cli=[str(tmp_path/'python'),'-m','agentmesh_log.cli']; home=str(tmp_path/'home'); logs=str(tmp_path/'logs')
    base=plistlib.loads(next(iter(service.render_profile_units('macos','broker',cli,home=home).values())))
    data=next(iter(service.render_profile_units('macos','broker',cli,home=home,remote=remote_config(),logs=logs).values()))
    value=plistlib.loads(data)
    expected=base['ProgramArguments']+['--s3-bucket','profile-r2','--s3-prefix','v1/','--s3-endpoint',
        'https://example.r2.cloudflarestorage.com','--s3-credentials','keychain:org.agentmesh.profile-log.r2','--sync-interval','60']
    assert value==dict(base,ProgramArguments=expected,StandardOutPath=str(tmp_path/'logs/broker.out.log'),StandardErrorPath=str(tmp_path/'logs/broker.err.log'))
    import re
    assert not any(re.fullmatch('[0-9a-f]{32}|[0-9a-f]{64}',a) for a in value['ProgramArguments'])
    assert 'EnvironmentVariables' not in value


@pytest.mark.parametrize('field,bad',[('bucket','UPPER'),('bucket','a'),('bucket','ab_'),('bucket','a'*64),
    ('endpoint','http://example.test'),('endpoint','https://example.test\n'),('endpoint','https://u:p@example.test'),
    ('prefix','../private/'),('prefix','v1/\x00'),('credentials','secret-value'),('credentials','keychain:x\n'),
    ('sync_interval',9),('sync_interval',float('nan')),('sync_interval',float('inf'))])
def test_r2_invalid(tmp_path,field,bad):
    with pytest.raises(ValueError): service.render_profile_units('macos','broker',[str(tmp_path/'python'),'-m','agentmesh_log.cli'],
        home=str(tmp_path/'home'),remote=remote_config(**{field:bad}))


def test_r2_generate(tmp_path,monkeypatch):
    from test_profile_log_bridge import config
    db,path,value=config(tmp_path); out=tmp_path/'units'
    import subprocess
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:pytest.fail('renderer executed a command'))
    argv=['generate','--unit','broker','--platform','macos','--database',str(db),'--home',str(tmp_path/'home'),
        '--output',str(out),'--s3-bucket','profile-r2','--s3-prefix','v1/','--s3-endpoint','https://example.test',
        '--s3-credentials','keychain:org.agentmesh.profile-log.r2','--logs',str(tmp_path/'logs')]
    assert service.main(argv)==0
    data=next(out.iterdir()).read_bytes(); assert b'--s3-credentials' in data
    args=plistlib.loads(data)['ProgramArguments']; assert args[args.index('--sync-interval')+1]=='120'
    with pytest.raises(FileExistsError): service.main(argv)
    assert next(out.iterdir()).read_bytes()==data
    assert not (tmp_path/'logs').exists()


def test_r2_generate_missing_credentials(tmp_path):
    from test_profile_log_bridge import config
    db,path,value=config(tmp_path); out=tmp_path/'units'
    with pytest.raises(ValueError): service.main(['generate','--unit','broker','--platform','macos','--database',str(db),
        '--home',str(tmp_path/'home'),'--output',str(out),'--s3-bucket','profile-r2'])
    assert not out.exists()


def test_r2_parser_does_not_echo_secrets(tmp_path,capsys):
    secret='fedc9876'*8
    with pytest.raises(SystemExit): service.main(['generate','--unit','broker','--platform','macos',
        '--database',str(tmp_path/'db'),'--home',str(tmp_path/'home'),'--output',str(tmp_path/'out'),'--sync-interval',secret])
    captured=capsys.readouterr(); assert secret not in captured.out+captured.err


@pytest.mark.parametrize('logs',['relative','/bad\nlogs','/bad\x85logs'])
def test_r2_logs_validation(tmp_path,logs):
    with pytest.raises(ValueError): service.render_profile_units('macos','broker',[str(tmp_path/'python'),'-m','agentmesh_log.cli'],
        home=str(tmp_path/'home'),remote=remote_config(),logs=logs)
