import copy
import pytest
from agentmesh_log import ulid
from agentmesh_log.encoding import parse, wire, typed, MAX_BYTES
from agentmesh_log.errors import LogError, LabelLoweringRequiresConfirmation, KeyAccessError
from agentmesh_log.model import Account, Profile, ScopeTree, Label
from agentmesh_log.identity import DeviceIdentity, AgentIdentity, Registry, sign
from agentmesh_log.events import seal_event, verify_event, open_event, event_path, wrap_path, EVENT_DOMAIN, INFO_DOMAIN
from agentmesh_log.keys import encrypt


@pytest.fixture
def context():
    account = Account('Private.Email@Example.test')
    device = DeviceIdentity()
    registry = Registry(account.public_record()); registry.add_device(device.certificate(account))
    agent = AgentIdentity(device, account.account_id)
    tree = ScopeTree(Profile('work'))
    a = tree.add(tree.root.scope_id, 'project', 'client-a', Label.internal)
    b = tree.add(tree.root.scope_id, 'project', 'client-b')
    key = tree.keys[a.scope_id]
    return account, device, registry, agent, tree, key, tree.keys[b.scope_id]


def sealed(context, **kwargs):
    return seal_event(context[5].write_key, context[3], 'unique private body', label=Label.secret,
                      tags=['unique private tag'], task='unique private task', **kwargs)


def test_event_roundtrip_write_only_and_paths(context):
    data = sealed(context)
    envelope = verify_event(data, context[2])
    payload = open_event(data, context[2], context[5])
    assert payload['body'] == 'unique private body' and payload['label'] == 'secret'
    assert payload['revises'] is None and payload['tags'] == ['unique private tag']
    assert event_path(envelope) == f"events/{envelope['profile']}/{envelope['scope']}/{envelope['id']}.json"
    record = context[4].wraps[context[5].scope_id]
    assert wrap_path(record) == f"keys/{record['profile']}/{record['child_scope']}/{record['child_key_id']}.{record['parent_key_id']}.json"
    with pytest.raises(KeyAccessError): open_event(data, context[2], context[5].write_key)
    with pytest.raises(KeyAccessError): seal_event(context[5], context[3], 'body')


@pytest.mark.parametrize('field', ['format', 'id', 'account', 'profile', 'scope', 'scope_key_id',
    'device', 'agent', 'agent_cert', 'ciphertext', 'signature'],
    ids=['format', 'id', 'account', 'profile', 'scope', 'key', 'device', 'agent', 'cert', 'cipher', 'sig'])
def test_every_envelope_field_tamper(context, field):
    p = parse(sealed(context))
    if field == 'agent_cert': p[field]['issued_at'] += 1
    elif field == 'id': p[field] = ulid.generate()
    else: p[field] = ('0' if p[field][0] != '0' else '1') + p[field][1:]
    with pytest.raises(LogError): open_event(wire(p), context[2], context[5])


def test_resign_attack_header_binding(context):
    account, _, registry, _, _, key, _ = context
    attacker_device = DeviceIdentity(); registry.add_device(attacker_device.certificate(account))
    attacker = AgentIdentity(attacker_device, account.account_id)
    p = parse(sealed(context))
    p.update(agent=attacker.agent_id, device=attacker.device_id, agent_cert=attacker.certificate())
    p = sign(p, attacker._private, EVENT_DOMAIN)
    assert verify_event(wire(p), registry) == p
    with pytest.raises(LogError): open_event(wire(p), registry, key)


@pytest.mark.parametrize('wrong', ['sibling', 'profile', 'epoch'],
    ids=['sibling', 'profile', 'epoch'])
def test_wrong_key(context, wrong):
    tree, old = context[4:6]
    if wrong == 'sibling': key = context[6]
    elif wrong == 'profile':
        other = ScopeTree(Profile('personal')); key = other.keys[other.root.scope_id]
    else:
        key = old
        new = tree.rotate(old.scope_id)
        context = (*context[:5], new, context[6])
    with pytest.raises(LogError): open_event(sealed(context), context[2], key)


def test_revision_label_lowering(context):
    prior = ulid.generate()
    with pytest.raises(LabelLoweringRequiresConfirmation):
        seal_event(context[5].write_key, context[3], {}, label=Label.general,
                   revises=prior, previous_label=Label.secret)
    data = seal_event(context[5].write_key, context[3], {}, label=Label.general,
                      revises=prior, previous_label=Label.secret, user_confirmed=True)
    assert open_event(data, context[2], context[5])['revises'] == prior


def test_envelope_metadata_and_email_privacy(context):
    account, device, _, agent, tree, *_ = context
    public = wire([account.public_record(), device.certificate(account), agent.certificate(), *tree.wraps.values()])
    data = sealed(context)
    for secret in (account.email, account.email.lower(), 'unique private body', 'unique private tag', 'unique private task'):
        assert secret.encode() not in data + public
    for field in ('label', 'tags', 'task', 'body', 'created_at', 'revises'):
        assert field not in parse(data)
    assert b'"secret"' not in data


@pytest.mark.parametrize('attack', ['duplicate', 'extra', 'missing', 'wrong-type', 'oversize', 'ulid',
    'cert-type', 'ciphertext-type', 'signature-type', 'nonfinite', 'bool-time'],
    ids=['dup', 'extra', 'missing', 'type', 'size', 'ulid', 'cert', 'cipher', 'sig', 'nan', 'time'])
def test_strict_before_signature(context, monkeypatch, attack):
    p = parse(sealed(context))
    if attack == 'duplicate': data = b'{"id":"a","id":"b"}'
    elif attack == 'oversize': data = b' ' * (MAX_BYTES + 1)
    elif attack == 'nonfinite': data = b'{"id":NaN}'
    else:
        if attack == 'extra': p['extra'] = 'bad'
        elif attack == 'missing': del p['scope']
        elif attack == 'wrong-type': p['account'] = []
        elif attack == 'ulid': p['id'] = '8' + '0'*25
        elif attack == 'cert-type': p['agent_cert'] = []
        elif attack == 'ciphertext-type': p['ciphertext'] = 1
        elif attack == 'signature-type': p['signature'] = None
        elif attack == 'bool-time': p['agent_cert']['issued_at'] = True
        data = wire(p)
    def forbidden(*args): pytest.fail('signature work reached for malformed envelope')
    monkeypatch.setattr(context[2], 'verify_agent', forbidden)
    with pytest.raises(LogError): verify_event(data, context[2])


@pytest.mark.parametrize('field,value', [('body', []), ('label', 'unknown'), ('created_at', True),
    ('revises', 'bad'), ('tags', 'not-list'), ('tags', [1]), ('task', 1), ('extra', None)],
    ids=['body', 'label', 'time', 'revises', 'tags', 'item', 'task', 'extra'])
def test_decrypted_payload_validation(context, field, value):
    p = parse(sealed(context))
    payload = open_event(wire(p), context[2], context[5]); payload[field] = value
    header = {k: v for k, v in p.items() if k not in ('signature', 'ciphertext')}
    p['ciphertext'] = encrypt(wire(payload), context[5].write_key, INFO_DOMAIN + typed(header))
    p = sign(p, context[3]._private, EVENT_DOMAIN)
    with pytest.raises(LogError): open_event(wire(p), context[2], context[5])


def test_cert_envelope_binding(context):
    p = parse(sealed(context)); p['device'] = '0'*32
    p = sign(p, context[3]._private, EVENT_DOMAIN)
    with pytest.raises(LogError): verify_event(wire(p), context[2])


@pytest.mark.parametrize('isolate_envelope_check', [False, True],
    ids=['chain', 'envelope'])
def test_event_account_binding(context, monkeypatch, isolate_envelope_check):
    _, device, registry, _, _, key, _ = context
    foreign_agent = AgentIdentity(device, Account().account_id)
    data = seal_event(key.write_key, foreign_agent, 'private body')
    if isolate_envelope_check:
        # Isolate the envelope guard from the registry's independent certificate guard.
        monkeypatch.setattr(registry, 'verify_agent', lambda cert: foreign_agent.public_bytes)
    with pytest.raises(LogError, match='wrong account'): verify_event(data, registry)
