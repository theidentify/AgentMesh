import json
import pytest
from test_apply import env, console
from agentmesh_log import projects
from agentmesh_log.settings import save_user, load


def put(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8'); path.chmod(0o600)


def config():
    return {'migration':{'global':'work/common','null_project':'work/common','projects':{'demo':'skip'},'prefixes':[]},'routing':{'allowed_scopes':['work/common']}}


def test_s9_trust_changes(env, tmp_path, capsys):
    root, home, store=env; save_user(config()); folder=tmp_path/'repo'
    put(folder/'.agentmesh/settings.json', {'project':{'name':'demo','scope':'work/common'}})
    assert projects.effective_mapping(load(),store.load(),[folder]).value['projects']['demo']=='skip'
    assert repr(str(folder)) in capsys.readouterr().err
    before=store.path.read_bytes()
    with pytest.raises(ValueError,match='presence_required'): projects.trust(folder,console=console('trust',False))
    assert store.path.read_bytes()==before
    projects.trust(folder,console=console('trust'))
    assert projects.effective_mapping(load(),store.load(),[folder]).value['projects']['demo']=='work/common'
    put(folder/'.agentmesh/settings.local.json', {'project':{'name':'demo2'}})
    assert 'demo2' not in projects.effective_mapping(load(),store.load(),[folder]).value['projects']
    projects.trust(folder,console=console('trust'))
    assert projects.effective_mapping(load(),store.load(),[folder]).value['projects']['demo2']=='work/common'
    value=config(); value['routing']['allowed_scopes']=[]; save_user(value)
    assert 'demo2' not in projects.effective_mapping(load(),store.load(),[folder]).value['projects']


def test_s9_project_grants_invalid(env,tmp_path,capsys):
    root,home,store=env; save_user(config()); folder=tmp_path/'repo'
    put(folder/'.agentmesh/settings.json',{'agents':{'evil':{'read':['work']}}})
    assert projects.effective_mapping(load(),store.load(),[folder]).value['projects']=={'demo':'skip'}
    assert len(capsys.readouterr().err.splitlines())==1
    with pytest.raises(ValueError,match='invalid_settings'): projects.trust(folder,console=console('trust'))


@pytest.mark.parametrize('low,high', [(0,1),(0,2),(0,3),(1,2),(1,3),(2,3)])
def test_s9_layer_pairs(env,tmp_path,low,high):
    root,home,store=env; save_user(config()); folder=tmp_path/'repo'
    paths=[root/'settings.json',root/'settings.local.json',folder/'.agentmesh/settings.json',folder/'.agentmesh/settings.local.json']
    for index,target in ((low,'skip'),(high,'work/common')):
        if index<2:
            value=config() if index==0 else {}; value.setdefault('migration',{}).setdefault('projects',{})['demo']=target
        else: value={'project':{'name':'demo','scope':'work/common' if index==high else 'work'}}
        put(paths[index],value)
    if high>=2: projects.trust(folder,console=console('trust'))
    assert projects.effective_mapping(load(),store.load(),[folder] if high>=2 else []).value['projects']['demo']=='work/common'


def test_migrate_parser():
    from agentmesh_log.cli import parser
    for action in ('plan','run','rows','catch-up','report'):
        argv=['migrate',action,'--settings','--project-dir','/tmp/repo']
        argv+=['--out','/tmp/report'] if action=='report' else ['--db','/tmp/synthetic.db']
        if action!='plan': argv+=['--agent-key','/tmp/synthetic.key']
        if action=='rows': argv+=['--ref','memory_items:1']
        assert parser().parse_args(argv).settings
        with pytest.raises(SystemExit): parser().parse_args(argv+['--mapping','/tmp/mapping'])


def test_s7_cli_parity(env, monkeypatch):
    import sqlite3
    from pathlib import Path
    from agentmesh_log.cli import parser, execute
    from agentmesh_log.apply import apply_settings
    from agentmesh_log.broker.client import BrokerClient
    from agentmesh_log.broker.service import Broker, Session
    from agentmesh_log.encoding import parse, typed
    from agentmesh_log.events import EVENT_DOMAIN
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    root, home, store=env; db=root/'synthetic.db'
    with sqlite3.connect(db) as conn:
        conn.executescript((Path(__file__).resolve().parents[3]/'memory-sync/schema.sql').read_text(encoding='utf-8'))
        conn.execute("INSERT INTO memory_items(kind,scope,content,confidence) VALUES('fact','global','synthetic marker',1)")
    value=config(); value['agents']={'importer':{'write':['work/common'],'allow_import':True}}
    save_user(value); assert apply_settings(console=console(),output=lambda _:None)['ok']
    mapping=root/'equivalent.json'; put(mapping,{'format':'agentmesh-migration-mapping-v1',**value['migration']})
    base=['migrate','plan','--db',str(db)]
    assert execute(parser().parse_args(base+['--settings']))==execute(parser().parse_args(base+['--mapping',str(mapping)]))
    record=parse((root/'agents/importer.key').read_bytes()); private=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(record['agent_private_key']))
    import shutil
    from agentmesh_log.broker.keystore import BrokerHome,FileKeyStore
    clone=root.parent/'equivalent-home'; shutil.copytree(root,clone)
    broker=Broker(home,store=store)
    class Client:
        def __init__(self): self.session=Session(record['agent_cert'])
        def request(self, req): return broker.request(self.session,req)
        def close(self): pass
        def import_row(self, **kwargs):
            result=self.request(dict(op='import',**kwargs))
            while result.get('stage')=='sign': result=self.request(dict(op='sign',signature=private.sign(EVENT_DOMAIN+typed(result['unsigned'])).hex()))
            return result
    monkeypatch.setattr(BrokerClient,'from_file',lambda _:Client())
    base=['migrate','run','--db',str(db),'--agent-key',str(root/'agents/importer.key')]
    try:
        first=execute(parser().parse_args(base+['--settings'])); assert first['outcomes']=={'created':1}
        unchanged=execute(parser().parse_args(base+['--settings']))
        broker.close(); other_home=BrokerHome(clone/'profile-log'); broker=Broker(other_home,store=FileKeyStore(other_home))
        assert first==execute(parser().parse_args(base+['--mapping',str(mapping)]))
        assert unchanged==execute(parser().parse_args(base+['--mapping',str(mapping)]))
    finally: broker.close()


def test_root_mapping(env):
    from agentmesh_log.apply import apply_settings
    from agentmesh_log.migrate.mapping import Mapping
    root,home,store=env; value=config(); value['migration']['global']='work'; save_user(value)
    # A mapping change can move rows between scopes, so it needs presence (review decision).
    assert apply_settings(output=lambda _:None)['error']=='presence_required'
    assert apply_settings(output=lambda _:None,console=console())['ok']
    assert Mapping.load(root/'migration-mapping.json').value['global']=='work'


def test_project_invalid_layer(env,tmp_path,capsys):
    root,home,store=env; save_user(config()); folder=tmp_path/'repo'
    put(folder/'.agentmesh/settings.json',{'project':{'name':'demo','scope':'work/common'}})
    put(folder/'.agentmesh/settings.local.json',{'agents':{}})
    projects.trust(folder,console=console('trust'),output=lambda _:None)
    capsys.readouterr()
    result=projects.effective_mapping(load(),store.load(),[folder])
    assert result.value['projects']['demo']=='work/common'
    assert len(capsys.readouterr().err.splitlines())==1


def test_trust_race(env,tmp_path):
    from agentmesh_log.presence import ConsolePresence
    root,home,store=env; folder=tmp_path/'repo'; path=folder/'.agentmesh/settings.json'
    put(path,{'project':{'name':'demo','scope':'work/common'}}); before=store.path.read_bytes()
    def confirm(_): put(path,{'project':{'name':'changed','scope':'work/common'}}); return 'trust'
    presence=ConsolePresence(input_fn=confirm,isatty=lambda:True,output=lambda _:None)
    with pytest.raises(ValueError,match='invalid_settings'): projects.trust(folder,console=presence,output=lambda _:None)
    assert store.path.read_bytes()==before


def test_empty_project_layer(env,tmp_path,capsys):
    root,home,store=env; save_user(config()); folder=tmp_path/'repo'
    put(folder/'.agentmesh/settings.json',{'project':{'name':'demo','scope':'work/common'}})
    path=folder/'.agentmesh/settings.local.json'; path.write_text('',encoding='utf-8'); path.chmod(0o600)
    projects.trust(folder,console=console('trust'),output=lambda _:None)
    assert len(capsys.readouterr().err.splitlines())==1



@pytest.mark.skipif(__import__('os').name=='nt', reason='POSIX modes')
def test_s9_git_checkout_modes(env,tmp_path,capsys):
    # git checks files out 0644 in 0755 directories; trust pins content, so those modes work.
    root, home, store=env; save_user(config()); folder=tmp_path/'repo'
    put(folder/'.agentmesh/settings.json', {'project':{'name':'demo','scope':'work/common'}})
    (folder/'.agentmesh').chmod(0o755); (folder/'.agentmesh/settings.json').chmod(0o644)
    projects.trust(folder,console=console('trust'))
    assert projects.effective_mapping(load(),store.load(),[folder]).value['projects']['demo']=='work/common'
    (folder/'.agentmesh/settings.json').chmod(0o666)
    assert projects.effective_mapping(load(),store.load(),[folder]).value['projects']['demo']=='skip'
    assert 'ignored' in capsys.readouterr().err
