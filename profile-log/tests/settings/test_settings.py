import json
import os
import pytest
from agentmesh_log import settings


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / 'home'; root.mkdir(mode=0o700)
    monkeypatch.setenv('AGENTMESH_HOME', str(root))
    return root


def put(path, value):
    path.write_text(json.dumps(value), encoding='utf-8'); path.chmod(0o600)


@pytest.mark.parametrize('bad', ['symlink', 'mode', 'root', 'unknown', 'duplicate', 'local', 'material', 'nested', 'type'])
def test_s1_refusal(root, bad):
    path = root / 'settings.json'; put(path, {})
    if bad == 'symlink':
        path.rename(root / 'target'); path.symlink_to(root / 'target')
    elif bad == 'mode': path.chmod(0o644)
    elif bad == 'root': root.chmod(0o755)
    elif bad == 'duplicate': path.write_text('{"agents":{},"agents":{}}', encoding='utf-8')
    else:
        put(path, {'unknown': {'wat': 1}, 'local': {'bridge': {'database': '/tmp/db'}},
                   'material': {'migration': {'projects': {'x': 'AGE-SECRET-KEY-test'}}},
                   'nested': {'agents': {'a': {'extra': True}}},
                   'type': {'bridge': {'enabled': 1}}}[bad])
    if os.name == 'nt' and bad in ('mode', 'root'): return
    with pytest.raises(ValueError, match='^invalid_settings$'): settings.load()


def test_merge_and_local(root):
    put(root / 'settings.json', {'agents': {'a': {'read': ['work'], 'write': []}}, 'bridge': {'enabled': False}})
    put(root / 'settings.local.json', {'agents': {'a': {'read': ['work/common']}}, 'bridge': {'database': str(root / 'db')}})
    value = settings.load()
    assert value['agents']['a'] == {'read': ['work/common'], 'write': []}
    assert value['bridge']['database'] == str(root / 'db')
    assert value['$schema'] == 'agentmesh-settings-v1'


def test_settings_cli(root):
    from agentmesh_log.cli import parser, execute
    put(root / 'settings.json', {'routing': {'allowed_scopes': ['work']}})
    assert execute(parser().parse_args(['settings', 'show']))['routing']['allowed_scopes'] == ['work']
    assert execute(parser().parse_args(['settings', 'validate'])) == {'ok': True}


def test_key_material_names(root):
    put(root/'settings.json',{'profiles':{'AGE-SECRET-KEY-test':{}}})
    with pytest.raises(ValueError,match='invalid_settings'): settings.load()


def test_file_owner_and_type(root,monkeypatch):
    import stat
    put(root/'settings.json',{})
    real=os.getuid if os.name!='nt' else None
    if real is not None:
        monkeypatch.setattr(os,'getuid',lambda:real()+1)
        with pytest.raises(ValueError,match='invalid_settings'): settings.load()
        monkeypatch.undo()
    (root/'settings.json').unlink(); (root/'settings.json').mkdir(mode=0o700)
    with pytest.raises(ValueError,match='invalid_settings'): settings.read(root/'settings.json')


def test_parent_symlink(root,monkeypatch):
    put(root/'settings.json',{})
    alias=root.parent/'alias'; alias.symlink_to(root.parent,target_is_directory=True)
    monkeypatch.setenv('AGENTMESH_HOME',str(alias/root.name))
    with pytest.raises(ValueError,match='invalid_settings'): settings.load()
