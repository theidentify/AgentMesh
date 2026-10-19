import copy
import pytest
from agentmesh_log.model import Account
from agentmesh_log.identity import DeviceIdentity, AgentIdentity, Registry, DEVICE_DOMAIN, AGENT_DOMAIN, sign
from agentmesh_log.errors import LogError


@pytest.fixture
def identities():
    account = Account('Private.Email@Example.test')
    first, second = DeviceIdentity(), DeviceIdentity()
    registry = Registry(account.public_record())
    first_cert = first.certificate(account)
    registry.add_device(first_cert)
    second_cert = second.certificate(account, issuer=first)
    registry.add_device(second_cert)
    agent = AgentIdentity(second, account.account_id)
    return account, first, second, registry, agent, first_cert, second_cert


def test_identity_chain(identities):
    account, first, second, registry, agent, first_cert, second_cert = identities
    assert registry.verify_agent(agent.certificate()) == agent.public_bytes
    registry.add_device(copy.deepcopy(second_cert))
    assert not any('scope' in name or 'data_key' in name for name in vars(agent))
    assert registry.account_id == account.account_id


@pytest.mark.parametrize('attack', ['wrong-device', 'wrong-account', 'unknown-issuer', 'self-issued',
    'duplicate-device', 'bad-signature', 'cross-device-to-agent', 'cross-agent-to-device'])
def test_identity_rejections(identities, attack):
    account, first, second, registry, agent, first_cert, second_cert = identities
    if attack == 'wrong-device':
        cert = agent.certificate(); cert['device_id'] = first.device_id
        cert = sign(cert, second._private, AGENT_DOMAIN)
    elif attack == 'cross-device-to-agent':
        cert = agent.certificate(); cert['signature'] = second_cert['signature']
    elif attack == 'wrong-account':
        cert = DeviceIdentity().certificate(Account())
    elif attack == 'unknown-issuer':
        cert = DeviceIdentity().certificate(account, issuer=DeviceIdentity())
    elif attack == 'self-issued':
        device = DeviceIdentity(); cert = device.certificate(account, issuer=device)
    elif attack == 'duplicate-device':
        device = DeviceIdentity(); device.device_id = first.device_id; cert = device.certificate(account)
    else:
        cert = copy.deepcopy(second_cert)
        cert['signature'] = '0'*128 if attack == 'bad-signature' else agent.certificate()['signature']
    with pytest.raises(LogError):
        if 'agent_public_key' in cert: registry.verify_agent(cert)
        else: registry.add_device(cert)


@pytest.mark.parametrize('field,value', [('issued_at', True), ('device_id', []), ('signature', None),
    ('agent_public_key', 'bad'), ('format', 'bad'), ('extra', 1)])
def test_agent_certificate_strict_shape(identities, field, value):
    registry, agent = identities[3:5]
    cert = agent.certificate(); cert[field] = value
    with pytest.raises(LogError): registry.verify_agent(cert)


def test_registry_does_not_keep_callers_mutable_records(identities):
    account, first, second, registry, agent, first_cert, second_cert = identities
    second_cert['device_public_key'] = first.public_bytes.hex()
    assert registry.verify_agent(agent.certificate()) == agent.public_bytes


def test_signature_domains(identities):
    account, first, second, registry, agent, *_ = identities
    cert = sign(agent.certificate(), second._private, DEVICE_DOMAIN)
    with pytest.raises(LogError): registry.verify_agent(cert)
    cert = sign(DeviceIdentity().certificate(account), account._root, AGENT_DOMAIN)
    with pytest.raises(LogError): registry.add_device(cert)


def test_device_account_binding_with_valid_root_signature(identities):
    account, _, _, registry, *_ = identities
    cert = DeviceIdentity().certificate(account)
    cert['account_id'] = Account().account_id
    cert = sign(cert, account._root, DEVICE_DOMAIN)
    with pytest.raises(LogError, match='wrong account'): registry.add_device(cert)


def test_agent_account_binding_with_valid_device_signature(identities):
    _, _, device, registry, *_ = identities
    agent = AgentIdentity(device, Account().account_id)
    with pytest.raises(LogError, match='wrong account'): registry.verify_agent(agent.certificate())
