import json
from types import SimpleNamespace
import pytest
from agentmesh_log import cli
from agentmesh_log.broker import client as clients
from agentmesh_log.migrate import run, source


@pytest.mark.parametrize('action,op',[(a,o) for a in ('catch-up','rows','verify','report')
    for o in ('whoami','import_ledger','import') if o!='import' or a in ('catch-up','rows')],ids=[str(i) for i in range(10)])
def test_presence(tmp_path,monkeypatch,capsys,action,op):
    key=tmp_path/'home'/'agents'/'key'; monkeypatch.setenv('AGENTMESH_HOME',str(tmp_path/'mesh'))
    monkeypatch.setattr(source,'binding_for',lambda db: dict(staging_node='test'))
    monkeypatch.setattr(run,'check_binding',lambda *a: None)
    monkeypatch.setattr(source,'check_binding',lambda *a: None)
    from agentmesh_log.migrate import verify
    monkeypatch.setattr(verify,'check_binding',lambda *a: None)
    monkeypatch.setattr(verify,'rows',lambda *a,**k: [])
    monkeypatch.setattr(run,'rows',lambda *a,**k: [dict(table='memory_items',id=1,reason=None)])
    monkeypatch.setattr(source,'rows',lambda *a,**k: [])
    monkeypatch.setattr(run,'body_for',lambda *a: (dict(metadata=dict(source={},row_hash='x')),1,False))
    class Fake:
        def request(self,req):
            if req['op']==op: return dict(error='presence_required',**{'class':'first_access'})
            return dict(device_id='d',write=[],entries=[])
        def import_row(self,**kwargs): return self.request(dict(op='import'))
        def close(self): pass
    monkeypatch.setattr(clients.BrokerClient,'from_file',lambda path: Fake())
    from agentmesh_log.migrate.mapping import Mapping
    monkeypatch.setattr(Mapping,'load',lambda path: SimpleNamespace(targets=set(),target=lambda row:'work'))
    argv=['migrate',action,'--db',str(tmp_path/'db'),'--agent-key',str(key),'--mapping','unused']
    if action=='rows': argv+=['--ref','memory_items:1']
    if action=='report': argv+=['--out',str(tmp_path/'report')]
    assert cli.main(argv)==1
    output=capsys.readouterr()
    assert json.loads(output.out)==dict(error='presence_required')
    # The broker home under AGENTMESH_HOME, not the agent key's folder.
    assert output.err==f'run: agentmesh-log presence open --home {tmp_path/"mesh"/"profile-log"} --class first_access\n'


@pytest.mark.parametrize('code',['denied','busy','invalid_request','unavailable'])
def test_other(code):
    with pytest.raises(ValueError,match='broker unavailable'): run.checked(dict(error=code))


@pytest.mark.parametrize('command',['recall','remember','migrate'])
@pytest.mark.parametrize('kind',['missing','refused','busy','other'])
def test_connect(tmp_path,monkeypatch,capsys,command,kind):
    from broker.support import setup_home
    from broker.test_isolation import keyfile
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE','1')
    home,state,store,agents=setup_home(tmp_path); key=keyfile(home,state,agents[0])
    exc=dict(missing=FileNotFoundError(),refused=ConnectionRefusedError(),busy=OSError(),other=PermissionError())[kind]
    if kind=='busy': exc.winerror=231
    def fail(address): raise exc
    monkeypatch.setattr(clients,'UnixClient',fail); monkeypatch.setattr(clients,'PipeClient',fail)
    monkeypatch.setattr(source,'binding_for',lambda db: dict(staging_node='test'))
    from agentmesh_log.migrate.mapping import Mapping
    monkeypatch.setattr(Mapping,'load',lambda path: None)
    argv=[command,'hello'] if command!='migrate' else ['migrate','catch-up','--db',str(tmp_path/'db'),'--mapping','unused']
    assert cli.main([*argv,'--agent-key',str(key)])==1
    output=capsys.readouterr()
    # A busy pipe means the broker is running, so it stays 'unavailable'.
    assert json.loads(output.out)==dict(error='unavailable' if kind in ('other','busy') else 'broker_unreachable')
    assert not output.err


@pytest.mark.parametrize('kind',[None,'export'])
def test_hint(tmp_path,monkeypatch,capsys,kind):
    monkeypatch.setenv('AGENTMESH_HOME',str(tmp_path))
    def fail(*a,**k): run.checked(dict(error='presence_required',**({} if kind is None else {'class':kind})))
    monkeypatch.setattr(cli,'execute',fail)
    key=tmp_path/'agents'/'key'
    assert cli.main(['migrate','report','--agent-key',str(key),'--out','unused'])==1
    output=capsys.readouterr()
    assert json.loads(output.out)==dict(error='presence_required')
    assert output.err==f'run: agentmesh-log presence open --home {tmp_path/"profile-log"} --class {kind or "first_access"}\n'
