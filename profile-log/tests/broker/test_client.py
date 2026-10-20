import pytest
from agentmesh_log.broker.client import BrokerClient
from agentmesh_log.broker.handshake import Challenge, BrokerIdentityError
from agentmesh_log.identity import DeviceIdentity
from .support import setup_home


def test_impostor_no_auth(tmp_path):
    home,state,store,agents=setup_home(tmp_path)
    class Fake:
        sent=[]; closed=False
        def send(self,value): self.sent.append(value)
        def receive(self): return Challenge(state,self.sent[0]).message
        def close(self): self.closed=True
    channel=Fake()
    with pytest.raises(BrokerIdentityError): BrokerClient(agents[0],DeviceIdentity().public_bytes,channel)
    assert channel.closed and len(channel.sent)==1


def test_author_scope_checks(tmp_path):
    from agentmesh_log.events import prepare_event
    from agentmesh_log.broker.service import Session
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    key=state.trees[0].keys[state.grants[agent.agent_id].default_write]
    unsigned=prepare_event(key.write_key,agent,'fixture')
    client=BrokerClient.__new__(BrokerClient); client.agent=agent
    sent=[]
    def response(req):
        sent.append(req); return {'stage':'sign','unsigned':unsigned}
    client.request=response
    with pytest.raises(ValueError,match='scope'): client.write('fixture',expected_scope='0'*32)
    assert len(sent)==1
    unsigned['agent']=agents[1].agent_id
    with pytest.raises(ValueError,match='author'): client.write('fixture')
    assert len(sent)==2


def test_close_bad_frame():
    class Bad:
        closed=False
        def send(self,obj): pass
        def receive(self): raise ValueError('invalid frame')
        def close(self): self.closed=True
    client=BrokerClient.__new__(BrokerClient); client.channel=Bad()
    with pytest.raises(ValueError): client.request({'op':'whoami'})
    assert client.channel.closed
