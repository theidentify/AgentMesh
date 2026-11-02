import json
import os
import stat
import pytest
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.memory import Memory, IDLE_S
from agentmesh_log.control.moves import Moves, MoveError
from agentmesh_log.control.ops import Control
from .test_pages import setup, Local, page


class Presence:
    def __init__(self, answer=True, available=True): self.answer,self.ok,self.asked=answer,available,[]
    def available(self): return self.ok
    def confirm(self, action, summary): self.asked.append((action,summary)); return self.answer


class Clock:
    def __init__(self): self.now=1000.0
    def __call__(self): return self.now


def memory(tmp_path, presence=None, key='/k'):
    home, b, client, agents, ids = setup(tmp_path)
    client.write(page('decisions/INDEX', [ids['d2']]), scope='work/a', tags=['page:decisions/INDEX'])
    clock=Clock(); presence=presence or Presence()
    class Wrapped(Local):
        def close(self): pass
    m=Memory(Layout(tmp_path/'agentmesh'),presence,Moves(None,None,None,clock=clock),
        settings=lambda:{'memory_tree':{'agent_key':key}} if key else {},
        client_factory=lambda path:Wrapped(b,agents[0]),clock=clock,home=tmp_path/'home')
    return m,clock,presence,ids


def test_view_unlock_and_idle_lock(tmp_path):
    m,clock,presence,ids=memory(tmp_path)
    with pytest.raises(MoveError,match='locked'): m.ls('/',1)
    assert m.unlock()=={'unlocked':True,'idle_s':IDLE_S} and presence.asked[0][0]=='read'
    assert [c['name'] for c in m.ls('/',1)['children']]==['work/']
    assert 'Pages live' in m.cat('/work/a/decisions/'+ids['d2'])['markdown']
    clock.now+=IDLE_S-1; m.ls('/work',1)
    clock.now+=IDLE_S-1; m.ls('/work',1)  # each read slides the idle window
    clock.now+=IDLE_S
    with pytest.raises(MoveError,match='locked'): m.ls('/',1)
    m.unlock(); m.relock()
    with pytest.raises(MoveError,match='locked'): m.cat('/work/a/decisions/INDEX.md')
    with pytest.raises(MoveError,match='denied'):
        m.unlock(); m.ls('/personal',1)


def test_unlock_refusals(tmp_path):
    m,clock,presence,ids=memory(tmp_path,Presence(answer=False))
    with pytest.raises(MoveError,match='presence_required'): m.unlock()
    with pytest.raises(MoveError,match='locked'): m.ls('/',1)
    m2,_,_,_=memory(tmp_path/'two',Presence(available=False))
    with pytest.raises(MoveError,match='presence_unavailable'): m2.unlock()
    m3,_,p3,_=memory(tmp_path/'three',key=None)
    with pytest.raises(MoveError,match='not_configured'): m3.unlock()
    assert p3.asked==[]
    m4,_,p4,_=memory(tmp_path/'four')
    for _ in range(3):
        p4.answer=False
        with pytest.raises(MoveError): m4.unlock()
    with pytest.raises(MoveError,match='rate_limited'): m4.unlock()


def test_export(tmp_path):
    m,clock,presence,ids=memory(tmp_path)
    out=tmp_path/'out'
    result=m.export('/work/a',str(out),False,False)
    assert presence.asked[-1][0]=='export' and 'PLAINTEXT' in presence.asked[-1][1]
    files=sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file())
    assert result['files']==len(files)-1 and result['secret_skipped']==1 and result['labels']==['general']
    assert 'README.md' in files and 'work/a/decisions/INDEX.md' in files  # its only source is general
    assert not any('/projects/' in f for f in files)  # project views do not duplicate items
    text=''.join(p.read_text() for p in out.rglob('*.md'))
    assert 'Pages live' in text and 'Use central Ollama' not in text and 'sk-not-a-real-one' not in text
    assert stat.S_IMODE(os.stat(out).st_mode)==0o700
    assert all(stat.S_IMODE(os.stat(p).st_mode)==0o600 for p in out.rglob('*') if p.is_file())
    full=m.export('/work',str(tmp_path/'full'),True,False)
    text=''.join(p.read_text() for p in (tmp_path/'full').rglob('*.md'))
    assert 'Use central Ollama' in text and full['labels']==['general','internal'] and full['files']>result['files']
    assert (tmp_path/'full'/'work'/'a'/'decisions'/'INDEX.md').exists()


@pytest.mark.parametrize('folder,code',[('relative/out','bad_folder'),('{tmp}/full','folder_not_empty'),
    ('{home}/Library/CloudStorage/Drive/out','synced_folder'),('{home}/.agentmesh/out','synced_folder')],
    ids=['relative','nonempty','cloud','agentmesh'])
def test_export_targets(tmp_path,folder,code):
    m,clock,presence,ids=memory(tmp_path)
    (tmp_path/'full').mkdir(); (tmp_path/'full'/'x').write_text('x')
    for d in ('Library/CloudStorage/Drive','.agentmesh'): (tmp_path/'home'/d).mkdir(parents=True)
    with pytest.raises(MoveError,match=code): m.export('/work',folder.format(tmp=tmp_path,home=tmp_path/'home'),False,False)
    assert presence.asked==[]


def test_export_icloud_desktop(tmp_path):
    m,clock,presence,ids=memory(tmp_path)
    home=tmp_path/'home'; (home/'Library/Mobile Documents/com~apple~CloudDocs/Desktop').mkdir(parents=True); (home/'Desktop').mkdir()
    with pytest.raises(MoveError,match='synced_folder'): m.export('/work',str(home/'Desktop'/'out'),False,False)
    assert m.export('/work',str(home/'Desktop'/'out'),False,True)['files']>=1


def test_control_ops(tmp_path):
    m,clock,presence,ids=memory(tmp_path)
    layout=Layout(tmp_path/'agentmesh'); control=Control(layout,None,memory=m)
    call=lambda op,args:control.handle(dict(v=1,id='x',op=op,args=args),dict(pid=1,exe='/bin/test'))
    assert call('memory.ls',{'path':'/','depth':1})['error']['code']=='locked'
    assert call('memory.unlock',{})['ok']
    assert call('memory.ls',{'path':'/','depth':1})['result']['children'][0]['name']=='work/'
    assert call('memory.cat',{'path':'/x','extra':1})['error']['code']=='bad_args'
    out=tmp_path/'exported'
    assert call('memory.export',{'path':'/work','folder':str(out),'include_internal':False,'allow_synced':False})['result']['files']>=1
    rows=[json.loads(l) for l in layout.audit.read_text().splitlines()]
    assert [(r['op'],r['outcome']) for r in rows]==[('memory.unlock','requested'),('memory.unlock','ok'),('memory.export','requested'),('memory.export','ok')]
    assert Control(layout,None).handle(dict(v=1,id='x',op='memory.unlock',args={}),{})['error']['code']=='presence_unavailable'


def test_control_cli_export(tmp_path):
    from agentmesh_log.cli import parser
    from agentmesh_log.control.cli import execute
    seen=[]
    def call(layout,op,args): seen.append((op,args)); return dict(id='x',ok=True,result={'files':2})
    args=parser().parse_args(['control','memory-export','/work/a','out','--include-internal'])
    assert execute(args,layout=Layout(tmp_path),call=call)=={'files':2}
    op,params=seen[0]
    assert op=='memory.export' and params['path']=='/work/a' and params['include_internal'] is True and params['allow_synced'] is False
    assert os.path.isabs(params['folder'])
