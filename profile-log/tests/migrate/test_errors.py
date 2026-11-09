"""Broker connection errors as the CLI reports them. The migrate commands these once also covered went with R5b."""
import json
import pytest
from agentmesh_log import cli
from agentmesh_log.broker import client as clients


@pytest.mark.parametrize('command',['recall','remember'])
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
    argv=[command,'hello']
    assert cli.main([*argv,'--agent-key',str(key)])==1
    output=capsys.readouterr()
    # A busy pipe means the broker is running, so it stays 'unavailable'.
    assert json.loads(output.out)==dict(error='unavailable' if kind in ('other','busy') else 'broker_unreachable')
    assert not output.err
