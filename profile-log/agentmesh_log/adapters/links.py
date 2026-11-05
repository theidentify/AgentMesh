"""Chat identity links: an owner or admin states that a chat user (platform + user ID) is a workspace account.

Records live at ``registry/chat-links/<workspace>/<hash>.json`` and hold only ``user_hash`` (SHA-256 over a domain
tag, the workspace, the platform and the user ID), never the user ID itself. A link counts when its signing device was
valid then and its account was an owner or admin of the workspace at that time; the newest valid link per chat user
wins, and ``account: null`` unlinks. The adapter resolves links on every message, so a removal or role change applies
to the next message, not the next restart.
"""
import hashlib
from ..encoding import fields, hex_bytes, milliseconds, parse, typed, wire
from ..identity import sign, verify_signature
from ..errors import IdentityError

FORMAT = 'agentmesh-chat-link-v1'
FIELDS = {'format', 'workspace', 'platform', 'user_hash', 'account', 'issued_at', 'issuer_device', 'signature'}
DOMAIN = b'AgentMesh/chat-link/Ed25519/v1\x00'
PREFIX = 'registry/chat-links/'
PLATFORMS = ('slack', 'discord', 'teams', 'line')
MANAGERS = ('owner', 'admin')


def user_hash(workspace, platform, user):
    if platform not in PLATFORMS or type(user) is not str or not 1 <= len(user) <= 128: raise ValueError('invalid_request')
    return hashlib.sha256(b'AgentMesh/chat-user/v1\x00' + typed(dict(workspace=workspace, platform=platform, user=user))).hexdigest()


def record_path(record):
    return PREFIX + record['workspace'] + '/' + hashlib.sha256(wire(record)).hexdigest() + '.json'


def shape(record):
    fields(record, FIELDS, FORMAT)
    hex_bytes(record['workspace'], 16); hex_bytes(record['issuer_device'], 16); hex_bytes(record['user_hash'], 32)
    hex_bytes(record['signature'], 64); milliseconds(record['issued_at'])
    if record['platform'] not in PLATFORMS: raise ValueError('invalid platform')
    if record['account'] is not None: hex_bytes(record['account'], 16)
    return record


def create(device, workspace, platform, user, account, *, issued_at=None):
    from ..registry_store import now
    record = dict(format=FORMAT, workspace=workspace, platform=platform, user_hash=user_hash(workspace, platform, user),
                  account=account, issued_at=now() if issued_at is None else issued_at, issuer_device=device.device_id)
    shape(dict(record, signature='00' * 64))
    return sign(record, device._private, DOMAIN)


def publish(storage, record):
    shape(record); storage.put(record_path(record), wire(record))


def resolve(storage, workspace, registry, ws, platform, user):
    """The linked account of a chat user, or None (unlinked, unlinked again, or only invalid links)."""
    wanted = user_hash(workspace, platform, user); best = None
    for key in storage.list(PREFIX + workspace + '/'):
        try:
            record = shape(parse(storage.get(key)))
            if key != record_path(record) or record['user_hash'] != wanted or record['platform'] != platform: continue
            verify_signature(record, registry.device_public(record['issuer_device'], at=record['issued_at']), DOMAIN)
            signer = registry.certificates[record['issuer_device']]['account_id']
            if ws.role(signer, record['issued_at']) not in MANAGERS: continue
            if getattr(registry, 'device_classes', {}).get(record['issuer_device'], 'workstation') != 'workstation': continue
        except (ValueError, KeyError, TypeError, IdentityError): continue
        if best is None or (record['issued_at'], key) > (best[0]['issued_at'], best[1]): best = (record, key)
    return best[0]['account'] if best else None
