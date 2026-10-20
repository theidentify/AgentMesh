import pytest
from agentmesh_log.broker.handshake import Challenge, hello, authenticate, check_challenge, BrokerIdentityError
from agentmesh_log.broker.keystore import BrokerState
from agentmesh_log.broker.grants import Grant
from agentmesh_log.identity import AgentIdentity, DeviceIdentity


def setup():
    s = BrokerState.create(['work']); a = AgentIdentity(s.device, s.account.account_id)
    sid = s.trees[0].root.scope_id
    s.grants[a.agent_id] = Grant(a.agent_id, a.public_bytes.hex(), [sid], [sid], sid, sid)
    return s, a


def test_p3_2_binding():
    s, a = setup(); h = hello(); c = Challenge(s, h)
    check_challenge(h, c.message, s.device.public_bytes)
    auth = authenticate(a, h, c.message)
    assert c.verify(auth, s) == a.agent_id
    with pytest.raises(ValueError): Challenge(s, h).verify(auth, s)
    other = AgentIdentity(s.device, s.account.account_id)
    forged = authenticate(other, h, c.message); forged['agent_cert'] = a.certificate()
    with pytest.raises(ValueError): c.verify(forged, s)
    foreign = DeviceIdentity(); s.certificates.append(foreign.certificate(s.account))
    agent = AgentIdentity(foreign, s.account.account_id)
    sid=s.trees[0].root.scope_id
    s.grants[agent.agent_id] = Grant(agent.agent_id,agent.public_bytes.hex(),[sid],[sid],sid,sid)
    with pytest.raises(ValueError): c.verify(authenticate(agent, h, c.message), s)
    different, agent = setup()
    with pytest.raises(ValueError): c.verify(authenticate(agent, h, c.message), s)
    s.revoke(a.agent_id)
    with pytest.raises(ValueError): c.verify(auth, s)
    assert Challenge(s, h).message['server_nonce'] != c.message['server_nonce']


def test_p3_3_impostor():
    s, a = setup(); h = hello(); c = Challenge(s, h)
    with pytest.raises(BrokerIdentityError): check_challenge(h, c.message, DeviceIdentity().public_bytes)
