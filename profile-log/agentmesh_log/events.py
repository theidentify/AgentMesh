"""Signed encrypted append-only events; verification needs no data keys."""
import time
from . import ulid
from .encoding import parse, wire, typed, fields, hex_bytes, milliseconds
from .errors import ValidationError, IdentityError, KeyAccessError
from .identity import sign, verify_signature, validate_agent_certificate
from .keys import ScopeWriteKey, ScopeReadKey, encrypt, decrypt, decode_ciphertext, WRAP_FIELDS
from .model import Label, change_label

EVENT_DOMAIN = b'AgentMesh/event/Ed25519/v1\x00'
INFO_DOMAIN = b'AgentMesh/event/v1\x00'
EVENT_FIELDS = {'format', 'id', 'account', 'profile', 'scope', 'scope_key_id', 'device', 'agent', 'agent_cert', 'ciphertext', 'signature'}
PAYLOAD_FIELDS = {'body', 'label', 'created_at', 'revises', 'tags', 'task'}


def _header(envelope):
    return {k: v for k, v in envelope.items() if k not in ('ciphertext', 'signature')}


def validate_payload(payload):
    if type(payload) is not dict or set(payload) != PAYLOAD_FIELDS:
        raise ValidationError('invalid event payload fields')
    if type(payload['body']) not in (str, dict): raise ValidationError('invalid body type')
    if type(payload['label']) is not str or payload['label'] not in Label.__members__:
        raise ValidationError('invalid payload label')
    milliseconds(payload['created_at'])
    if payload['revises'] is not None: ulid.parse(payload['revises'])
    if type(payload['tags']) is not list or any(type(t) is not str for t in payload['tags']):
        raise ValidationError('invalid tags')
    if payload['task'] is not None and type(payload['task']) is not str:
        raise ValidationError('invalid task')
    typed(payload)
    return payload


def prepare_event(write_key: ScopeWriteKey, agent, body, *, label=Label.general, created_at=None,
               revises=None, tags=None, task=None, previous_label=None, user_confirmed=False) -> dict:
    if not isinstance(write_key, ScopeWriteKey): raise KeyAccessError('write key required')
    if not isinstance(label, Label): raise ValidationError('invalid label')
    if previous_label is not None:
        if revises is None: raise ValidationError('previous label requires revision')
        change_label(previous_label, label, user_confirmed=user_confirmed)
    payload = validate_payload({'body': body, 'label': label.name,
        'created_at': time.time_ns() // 1_000_000 if created_at is None else created_at,
        'revises': revises, 'tags': [] if tags is None else tags, 'task': task})
    header = {'format': 'agentmesh-event-v1', 'id': ulid.generate(), 'account': agent.account_id,
        'profile': write_key.profile_id, 'scope': write_key.scope_id, 'scope_key_id': write_key.key_id,
        'device': agent.device_id, 'agent': agent.agent_id, 'agent_cert': agent.certificate()}
    ciphertext = encrypt(wire(payload), write_key, INFO_DOMAIN + typed(header))
    return {**header, 'ciphertext': ciphertext}


def attach_signature(unsigned, signature):
    fields(unsigned, EVENT_FIELDS - {'signature'}, 'agentmesh-event-v1')
    hex_bytes(signature, 64)
    return wire({**unsigned, 'signature': signature})


def seal_event(write_key, agent, body, **kwargs):
    unsigned = prepare_event(write_key, agent, body, **kwargs)
    return attach_signature(unsigned, agent._private.sign(EVENT_DOMAIN + typed(unsigned)).hex())


def verify_event(data: bytes, registry) -> dict:
    envelope = parse(data)
    fields(envelope, EVENT_FIELDS, 'agentmesh-event-v1')
    ulid.parse(envelope['id'])
    for name in ('account', 'profile', 'scope', 'device', 'agent'): hex_bytes(envelope[name], 16)
    hex_bytes(envelope['scope_key_id'], 32)
    decode_ciphertext(envelope['ciphertext']); hex_bytes(envelope['signature'], 64)
    cert = envelope['agent_cert']
    validate_agent_certificate(cert)
    if envelope['account'] != registry.account_id: raise IdentityError('wrong account')
    public = registry.verify_historical_agent(cert,ulid.timestamp(envelope['id']))
    if (cert['account_id'], cert['device_id'], cert['agent_id']) != (envelope['account'], envelope['device'], envelope['agent']):
        raise IdentityError('event certificate binding mismatch')
    verify_signature(envelope, public, EVENT_DOMAIN)
    return envelope


def open_event(data: bytes, registry, read_key: ScopeReadKey) -> dict:
    envelope = verify_event(data, registry)
    if not isinstance(read_key, ScopeReadKey): raise KeyAccessError('read key required')
    if (envelope['scope_key_id'], envelope['scope'], envelope['profile']) != (read_key.key_id, read_key.scope_id, read_key.profile_id):
        raise KeyAccessError('event scope key mismatch')
    plaintext = decrypt(envelope['ciphertext'], read_key, INFO_DOMAIN + typed(_header(envelope)))
    return validate_payload(parse(plaintext))


def event_path(envelope):
    for name in ('profile', 'scope'): hex_bytes(envelope[name], 16)
    ulid.parse(envelope['id'])
    return f"events/{envelope['profile']}/{envelope['scope']}/{envelope['id']}.json"


def wrap_path(record):
    fields(record, WRAP_FIELDS | {'format', 'ciphertext', 'auth'}, 'agentmesh-scope-wrap-v1')
    for name in WRAP_FIELDS: hex_bytes(record[name], 32 if name.endswith('key_id') else 16)
    return f"keys/{record['profile']}/{record['child_scope']}/{record['child_key_id']}.{record['parent_key_id']}.json"
