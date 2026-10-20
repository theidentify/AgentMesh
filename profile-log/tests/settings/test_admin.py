from agentmesh_log.admin import create_agent, create_scope
from agentmesh_log.broker.keystore import BrokerHome, BrokerState
from agentmesh_log.broker.grants import resolve
from agentmesh_log.model import Label


def test_shared_admin(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path))
    home = BrokerHome(tmp_path / 'broker'); state = BrokerState.create(['work'])
    create_scope(home, state, 'work/test', Label.internal)
    path, grant = create_agent(home, state, 'tester', ['work/test'], ['work/test'])
    assert grant.read == grant.write == [resolve(state, 'work/test')]
    assert path.exists() and state.grants[grant.agent_id] == grant
