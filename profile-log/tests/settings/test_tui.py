import json
import pytest
from test_apply import env, console, desired
from agentmesh_log.settings import save_user
from agentmesh_log.tui.model import Model


def model():
    return Model({'agents':{'reader':{'read':[],'write':[]}}}, scopes=['work/common','work/new'], profiles=['work'], devices=[{'device_id':'device','revoked':False}], projects=[{'project':'demo','items':2,'summaries':1,'target':'work/common'}])


def test_model_grid_mapping_pair():
    m=model(); m.toggle('reader','work/common','read')
    assert m.edits()['agents']['reader']['read']==['work/common']
    m.new_scope('work/added','internal'); assert m.edits()['profiles']['work']['scopes']['added']['label']=='internal'
    m.set_target('demo','work/new'); assert m.edits()['migration']['projects']['demo']=='work/new'
    m.pair_scopes={'work/new'}; seen=[]
    m.approve('request',lambda request,scopes:seen.append((request,scopes)))
    assert seen==[('request',['work/new'])]
    m.pair_scopes=set()
    with pytest.raises(ValueError,match='pairing_empty'): m.approve('request',lambda *_:pytest.fail('must not approve'))


def test_mapping_preview():
    m=model(); m.sources=[dict(table='memory_items',id=1,project='demo',target='work/common',reason=None),dict(table='memory_items',id=2,project='demo',target='work/common',reason=None)]
    m.ledger=[dict(source={'table':'memory_items','id':1},scope='work/common',status='created')]
    m.set_target('demo','work/new')
    assert m.preview()=={'retired':1,'recreated':1,'created':1}


def test_s10_save_only_via_apply(env,monkeypatch):
    from agentmesh_log.tui import Controller
    root,home,store=env; save_user({'agents':{'reader':{'read':[],'write':[]}}})
    m=model(); before=store.path.read_bytes(); calls=[]
    monkeypatch.setattr(type(store),'save',lambda *_:pytest.fail('TUI directly saved state'))
    def apply(**kwargs):
        calls.append(json.loads((root/'settings.json').read_text(encoding='utf-8')))
        assert store.path.read_bytes()==before
        return {'ok':True}
    c=Controller(m,apply_fn=apply,console=console()); m.toggle('reader','work/common','read')
    assert store.path.read_bytes()==before; assert c.save()['ok']; assert calls[0]['agents']['reader']['read']==['work/common']


def test_menu_scripted_save(env):
    from agentmesh_log.tui import run_admin
    root,home,store=env; save_user({}); output=[]; prompts=[]
    # New scope, new agent, read/write toggles, save, quit.
    answers=iter(['2','2','work/new','general','3','reader','1','reader','work/new','read','1','reader','work/new','write','0','5','0'])
    result=run_admin(menu=True,input_fn=lambda _:next(answers),output=output.append,console=console(calls=prompts))
    assert result['ok'] and prompts
    state=store.load(); assert 'reader' in state.control['agent_names']


def test_s11_fake_curses_content_free():
    from agentmesh_log.tui.curses_ui import run
    m=model(); lines=[]
    class Screen:
        def erase(self): pass
        def getmaxyx(self): return (40,160)
        def addstr(self,y,x,text): lines.append(text)
        def refresh(self): pass
        def getch(self): return ord('q')
    class Controller:
        model=m
    run(Screen(),Controller())
    assert 'demo' in '\n'.join(lines) and 'ROW_CONTENT_MARKER' not in '\n'.join(lines)


@pytest.mark.parametrize('forced,tty,available',[(True,True,True),(False,False,True),(False,True,False)])
def test_s13_fallback(env,monkeypatch,forced,tty,available):
    from agentmesh_log import tui
    from agentmesh_log.tui import curses_ui, menu_ui
    root,home,store=env; save_user({}); seen=[]
    monkeypatch.setattr(curses_ui,'available',lambda:available)
    monkeypatch.setattr(menu_ui,'run',lambda c,**kwargs:seen.append('menu'))
    monkeypatch.setattr(curses_ui,'start',lambda c:pytest.fail('curses should not run'))
    tui.run_admin(menu=forced,isatty=lambda:tty)
    assert seen==['menu']


def test_admin_cli(env,monkeypatch):
    from agentmesh_log import cli,tui
    seen=[]; monkeypatch.setattr(tui,'run_admin',lambda **kwargs:seen.append(kwargs) or {'ok':True})
    assert cli.execute(cli.parser().parse_args(['admin','--menu']))=={'ok':True}
    assert seen[0]['menu'] is True


def test_s11_db_markers(env):
    import sqlite3
    from pathlib import Path
    from agentmesh_log.tui import load_model,Controller,menu_ui,curses_ui
    root,home,store=env; db=root/'source.db'; marker='ROW_CONTENT_MARKER'
    with sqlite3.connect(db) as conn:
        conn.executescript((Path(__file__).resolve().parents[3]/'memory-sync/schema.sql').read_text(encoding='utf-8'))
        conn.execute("INSERT INTO memory_items(kind,scope,project,content,confidence) VALUES('fact','project','demo',?,1)",(marker,))
    save_user({'migration':{'global':'work/common','null_project':'work/common','projects':{'demo':'work/common'},'prefixes':[]}})
    p=root/'settings.local.json'; p.write_text(json.dumps({'bridge':{'database':str(db)}}),encoding='utf-8'); p.chmod(0o600)
    m=load_model(); c=Controller(m); rendered=[]
    answers=iter(['1','3','','0']); menu_ui.run(c,input_fn=lambda _:next(answers),output=rendered.append)
    class Screen:
        keys=iter([ord('2'),ord('3'),ord('4'),ord('q')])
        def erase(self): pass
        def getmaxyx(self): return (50,180)
        def addstr(self,y,x,text): rendered.append(text)
        def refresh(self): pass
        def getch(self): return next(self.keys)
    curses_ui.run(Screen(),c)
    assert 'demo' in '\n'.join(rendered) and marker not in '\n'.join(rendered)
    assert all(marker not in repr(v) for v in vars(m).values())


def test_s12_pair_delegation(env,monkeypatch):
    from agentmesh_log import cli
    from agentmesh_log.tui import run_admin,menu_ui
    seen=[]
    def execute(args,**kwargs): seen.append(args); return {'ok':True}
    monkeypatch.setattr(cli,'execute',execute)
    def menu(c,**kwargs):
        c.model.request_id='a'*32; c.model.pair_scopes={'work/common'}; c.pair()
        c.model.pair_scopes.clear()
        with pytest.raises(ValueError,match='pairing_empty'): c.pair()
    monkeypatch.setattr(menu_ui,'run',menu)
    run_admin(menu=True)
    assert len(seen)==1 and seen[0].scope==['work/common'] and seen[0].command=='pair' and seen[0].action=='approve'


def test_fake_curses_grid_save():
    from agentmesh_log.tui.curses_ui import run
    m=model(); calls=[]
    class Controller:
        model=m
        def save(self): calls.append(m.edits()); return {'ok':True}
    class Screen:
        keys=iter([ord('2'),258,ord(' '),ord('s'),ord('q')])
        def erase(self): pass
        def getmaxyx(self): return (30,160)
        def addstr(self,*args): pass
        def refresh(self): pass
        def getch(self): return next(self.keys)
    run(Screen(),Controller())
    assert calls[0]['agents']['reader']['read']==['work/common']


def test_preview_global():
    m=model(); m.sources=[dict(table='memory_items',id=1,scope='global',project='demo',target='work/common',reason=None)]
    m.ledger=[dict(source={'table':'memory_items','id':1},scope='work/common',status='created')]
    m.set_target('demo','work/new')
    assert m.preview()=={'retired':0,'recreated':0,'created':0}


def test_preview_retired_same_scope():
    m=model(); m.sources=[dict(table='memory_items',id=1,project='demo',target='work/common',reason=None)]
    m.ledger=[dict(source={'table':'memory_items','id':1},scope='work/common',status='retired')]
    assert m.preview()=={'retired':0,'recreated':1,'created':0}


def test_s10_exact_apply_diff(env):
    from agentmesh_log.apply import apply_settings
    from agentmesh_log.tui import Controller,load_model
    root,home,store=env; save_user({'agents':{'reader':{'read':[],'write':[]}}})
    assert apply_settings(console=console(),output=lambda _:None)['ok']
    c=Controller(load_model(),console=console(),output=lambda _:None)
    before=store.path.read_bytes(); c.model.toggle('reader','work/common','read')
    assert store.path.read_bytes()==before
    changes=c.save()['diff']; assert len(changes)==1
    assert changes[0]['kind']=='agent_widen' and changes[0]['name']=='reader'
    assert changes[0]['before']['read']==[] and changes[0]['after']['read']==['work/common']
    assert {k:v for k,v in changes[0]['before'].items() if k!='read'}=={k:v for k,v in changes[0]['after'].items() if k!='read'}


def test_s12_real_pair(env,monkeypatch):
    import re
    from agentmesh_log import cli
    from agentmesh_log.tui import run_admin,menu_ui
    from agentmesh_log.oskeystore import initialize
    from agentmesh_log.storage.filesystem import FilesystemStorage
    from agentmesh_log.pairing import JoinRequest,Approver
    from agentmesh_log.encoding import parse
    from agentmesh_log.broker.grants import resolve
    from agentmesh_log.presence import ConsolePresence
    root,home,store=env; store.path.unlink(); initialize(home,['work'],file_keystore=True)
    storage=FilesystemStorage(home.root/'replica'); join=JoinRequest(storage); seen=[]; prompts=[]
    def reveal(remote,path,deadline):
        approval=parse(storage.get(join.prefix+'approver.json'))
        return join.reveal(approval,store.load().registry())
    monkeypatch.setattr(cli,'wait_object',reveal)
    original=Approver.grant
    def grant(self,state,scopes): seen.append(scopes); return original(self,state,scopes)
    monkeypatch.setattr(Approver,'grant',grant)
    def menu(c,**kwargs):
        c.model.request_id=join.request_id; c.model.pair_scopes={'work/common'}; c.pair()
    monkeypatch.setattr(menu_ui,'run',menu)
    yes=ConsolePresence(input_fn=lambda prompt:prompt[5:-2],isatty=lambda:True,output=lambda _:None)
    run_admin(menu=True,console=yes,input_fn=lambda prompt:prompts.append(prompt) or 'yes')
    assert seen==[[resolve(store.load(),'work/common')]]
    assert len(prompts)==1 and re.search(r'Code \d{6} matches',prompts[0])


def test_curses_import_failure(env,monkeypatch):
    import builtins,importlib
    from agentmesh_log.tui import curses_ui,menu_ui,run_admin
    original=builtins.__import__; seen=[]
    def guarded(name,*args,**kwargs):
        if name=='curses': raise ImportError('synthetic unavailable')
        return original(name,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(builtins,'__import__',guarded); importlib.reload(curses_ui)
        patch.setattr(menu_ui,'run',lambda c,**kwargs:seen.append('menu'))
        run_admin(isatty=lambda:True)
    importlib.reload(curses_ui)
    assert seen==['menu']


def test_overview_all_agents(env):
    from agentmesh_log.admin import create_agent
    from agentmesh_log.tui import load_model
    root,home,store=env; state=store.load()
    _,grant=create_agent(home,state,'legacy',['work/common'],['work/common'],'work/common')
    store.save(state)
    assert any(grant.agent_id in line and 'work/common' in line for line in load_model().lines(0))
