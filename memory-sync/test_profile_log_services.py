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
    assert {k:v.decode('utf-8') for k,v in files.items()}==expected[platform+'/'+unit]


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
