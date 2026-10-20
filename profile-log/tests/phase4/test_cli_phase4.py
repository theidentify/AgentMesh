import pytest
from agentmesh_log.cli import parser,execute
from agentmesh_log.broker.keystore import BrokerHome
from agentmesh_log.oskeystore import initialize,OsKeyStore
from agentmesh_log.presence import ConsolePresence
from .test_oskeystore import FakeBackend


@pytest.mark.parametrize('command',[
 ['broker','grant','--name','agent','--write','work'],
 ['broker','revoke','0'*32],
 ['pair','approve','0'*32,'--filesystem','unused'],
 ['pair','recipient','0'*32,'--filesystem','unused'],
 ['pair','regrant','0'*32,'--scope','work','--filesystem','unused'],
 ['device','remove','0'*32,'--filesystem','unused'],
 ['recovery','create'],['recovery','test'],['recovery','use'],
 ['profile','archive','work'],['profile','delete','work'],
 ['keystore','migrate','--to','os'],
],ids=['grant','revoke','approve','recipient','regrant','remove','create','test','use','archive','delete','migrate'])
@pytest.mark.parametrize('tty',[True,False],ids=['tty','pipe'])
def test_p4_7_cli(tmp_path,command,tty):
    home=BrokerHome(tmp_path/'home'); backend=FakeBackend(); initialize(home,['work'],backend=backend)
    no=ConsolePresence(input_fn=lambda _:'no',isatty=lambda:tty,output=lambda _:None)
    args=parser().parse_args([*command,'--home',str(home.root)])
    before=OsKeyStore(home,backend).path.read_bytes()
    with pytest.raises(ValueError,match='presence_required'): execute(args,backend=backend,console=no)
    assert OsKeyStore(home,backend).path.read_bytes()==before
    assert len((home.root/'presence.jsonl').read_bytes().splitlines())==1


def test_p4_8_delete_cli(tmp_path):
    from .test_lifecycle import yes
    from agentmesh_log.recovery import test_recovery as check
    from agentmesh_log.storage.filesystem import FilesystemStorage
    backend=FakeBackend(); home=BrokerHome(tmp_path/'home'); shares=initialize(home,['work','personal'],backend=backend)
    files=[]
    for i,share in enumerate(shares[:2]):
        path=tmp_path/f'share{i}'; path.write_text(share,encoding='utf-8'); files.extend(['--share-file',str(path)])
    args=parser().parse_args(['profile','delete','work','--home',str(home.root),'--confirm-name','work',*files])
    prompts=[]; console=ConsolePresence(input_fn=lambda p:p.split()[1].rstrip(':'),isatty=lambda:True,output=prompts.append)
    result=execute(args,backend=backend,console=console)
    assert len(result['recovery_shares'])==3
    store=OsKeyStore(home,backend); storage=FilesystemStorage(home.root/'replica')
    assert check(home,store,storage,result['recovery_shares'][:2],console=yes())
    assert not check(home,store,storage,shares[:2],console=yes())
    assert any('Old recovery shares' in text for text in prompts)
