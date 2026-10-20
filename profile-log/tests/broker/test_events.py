from types import SimpleNamespace
from agentmesh_log import events
from agentmesh_log.encoding import wire, typed
from agentmesh_log.identity import sign, DeviceIdentity, AgentIdentity
from agentmesh_log.model import Account
from agentmesh_log.keys import ScopeReadKey


def test_two_step(taxonomy, monkeypatch):
    account, registry, devices, agents, tree, *_ = taxonomy
    agent = agents[0]; key = tree.keys[tree.root.scope_id]
    identifier = events.ulid.generate()
    monkeypatch.setattr(events.ulid, 'generate', lambda: identifier)
    original_encrypt = events.encrypt
    ciphertexts = []
    def fixed_encrypt(data, key, info):
        if not ciphertexts: ciphertexts.append(original_encrypt(data, key, info))
        return ciphertexts[0]
    monkeypatch.setattr(events, 'encrypt', fixed_encrypt)
    public_agent = SimpleNamespace(account_id=agent.account_id, device_id=agent.device_id,
        agent_id=agent.agent_id, certificate=agent.certificate)
    unsigned = events.prepare_event(key.write_key, public_agent, 'fixture', created_at=123)
    assert 'signature' not in unsigned
    data = events.attach_signature(unsigned, agent._private.sign(events.EVENT_DOMAIN + typed(unsigned)).hex())
    assert data == events.seal_event(key.write_key, agent, 'fixture', created_at=123)
    header = {'format': 'agentmesh-event-v1', 'id': identifier, 'account': account.account_id,
        'profile': key.profile_id, 'scope': key.scope_id, 'scope_key_id': key.key_id,
        'device': agent.device_id, 'agent': agent.agent_id, 'agent_cert': agent.certificate()}
    payload = dict(body='fixture', label='general', created_at=123, revises=None, tags=[], task=None)
    old = wire(sign({**header, 'ciphertext': fixed_encrypt(wire(payload), key.write_key,
        events.INFO_DOMAIN + typed(header))}, agent._private, events.EVENT_DOMAIN))
    assert data == old
    assert events.open_event(data, registry, key)['body'] == 'fixture'


def test_private_roundtrip(taxonomy):
    account, registry, devices, agents, tree, *_ = taxonomy
    restored = Account.from_private(account.account_id, account.private_bytes())
    assert restored.public_record() == account.public_record()
    device = devices[0][0]
    assert DeviceIdentity.from_private(device.device_id, device.private_bytes()).public_bytes == device.public_bytes
    agent = agents[0]
    assert AgentIdentity.from_private(agent.certificate(), agent.private_bytes()).certificate() == agent.certificate()
    key = tree.keys[tree.root.scope_id]
    assert ScopeReadKey.from_private(key.profile_id, key.scope_id, key.private_bytes()).key_id == key.key_id
