"""Signed workspace invites (team workspace W3): onboarding many people without pairing each device by hand.

1. An owner or admin creates an ``invite`` workspace entry: role (member or contributor), scopes, ``max_uses``,
   ``expires_at`` (at most 7 days) and ``invite_key``, the public half of an Ed25519 key made from a random secret.
   The secret goes only into the **invite token**, a file handed out of band with the owner's account fingerprint.
2. The joiner runs ``workspace join --invite TOKEN``: their device pins the owner (fingerprint from the token),
   publishes its account record and profile recipient, and an **invite use** naming its account and root key,
   signed twice: by the joining device, and by the invite key (``proof``). Anyone can verify both, but nobody
   without the token (the storage provider included) can make a proof.
3. Any owner or admin runs ``workspace invite accept``. Their device checks each use (both signatures, invite not
   revoked or expired at the use's time, uses left), then publishes a member entry naming the invite (which counts
   the use on the chain) and grants the device the invite's scopes.

The role and scopes come from the signed invite, never from the joiner. Attestation is separate (``attest``).
"""
import hashlib
import secrets
from .encoding import fields, hex_bytes, milliseconds, parse, typed, wire
from .identity import sign, verify_signature
from .errors import IdentityError

TOKEN = 'agentmesh-workspace-invite-token-v1'
USE = 'agentmesh-workspace-invite-use-v1'
USE_FIELDS = {'format', 'workspace', 'invite', 'account', 'account_root', 'device_id', 'proof', 'issued_at', 'signature'}
DOMAIN = b'AgentMesh/workspace-invite-use/Ed25519/v1\x00'
PREFIX = 'registry/invite-uses/'


PROOF_DOMAIN = b'AgentMesh/workspace-invite-proof/Ed25519/v1\x00'


def _invite_private(secret):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.from_private_bytes(hex_bytes(secret, 32))


def invite_key(secret): return _invite_private(secret).public_key().public_bytes_raw().hex()


def new_secret(): return secrets.token_bytes(32).hex()


def token(workspace, invite_id, secret, owner_account, owner_fingerprint, role, expires_at):
    return dict(format=TOKEN, workspace=workspace, invite=invite_id, secret=secret, owner_account=owner_account,
                owner_fingerprint=owner_fingerprint, role=role, expires_at=expires_at)


V1_FIELDS = {'format', 'workspace', 'invite', 'secret', 'owner_account', 'owner_fingerprint', 'role', 'expires_at'}
# v2 (one-click join): the link also carries what a joiner needs before it has storage access. None of it is a
# storage credential: ``remote`` names the store, ``channels`` say how the request and answer travel.
TOKEN_V2 = 'agentmesh-workspace-invite-token-v2'
V2_FIELDS = (V1_FIELDS - {'format'}) | {'format', 'owner_record', 'remote', 'channels', 'slot', 'label'}
LINK = 'agentmesh://join#'


def read_token(record):
    if type(record) is dict and record.get('format') == TOKEN_V2:
        fields(record, V2_FIELDS, TOKEN_V2)
        from .joinlink import shape_remote, shape_channels
        from .federation import account_record, fingerprint
        hex_bytes(record['slot'], 16)
        owner = record['owner_record']
        if type(owner) is not dict or owner != account_record(owner.get('account_id'), owner.get('root_public_key')):
            raise ValueError('invalid owner record')
        hex_bytes(owner['account_id'], 16); hex_bytes(owner['root_public_key'], 32)
        if owner['account_id'] != record['owner_account'] or fingerprint(owner['root_public_key']) != record['owner_fingerprint']:
            raise ValueError('owner record mismatch')
        shape_remote(record['remote']); shape_channels(record['channels'])
        if record['label'] is not None and not (type(record['label']) is str and 1 <= len(record['label']) <= 100
                                                and not any(ord(c) < 32 for c in record['label'])):
            raise ValueError('invalid label')
    else: fields(record, V1_FIELDS, TOKEN)
    for name in ('workspace', 'invite', 'owner_account'): hex_bytes(record[name], 16)
    hex_bytes(record['secret'], 32); milliseconds(record['expires_at'])
    if record['role'] not in ('member', 'contributor'): raise ValueError('invalid role')
    return record


def token_v2(base, owner_record, remote, channels, slot, label):
    return dict(base, format=TOKEN_V2, owner_record=owner_record, remote=remote, channels=channels, slot=slot, label=label)


def encode_link(record):
    import base64
    return LINK + base64.urlsafe_b64encode(wire(read_token(record))).rstrip(b'=').decode('ascii')


def decode_link(text):
    """A token from a link (``agentmesh://join#…``) or from token JSON, as a file or pasted text holds it."""
    import base64, json, re
    text = text.strip()
    if text.startswith(LINK):
        body = text[len(LINK):]
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,16384}', body): raise ValueError('invalid link')
        return read_token(parse(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))))
    try: return read_token(json.loads(text))
    except json.JSONDecodeError: raise ValueError('invalid link') from None


def _message(workspace, invite_id, account, root, device_id):
    return PROOF_DOMAIN + typed(dict(workspace=workspace, invite=invite_id, account=account, account_root=root, device_id=device_id))


def proof(secret, workspace, invite_id, account, root, device_id):
    return _invite_private(secret).sign(_message(workspace, invite_id, account, root, device_id)).hex()


def use_record(device, account, root, tok, *, issued_at=None):
    from .registry_store import now
    record = dict(format=USE, workspace=tok['workspace'], invite=tok['invite'], account=account, account_root=root,
                  device_id=device.device_id, proof=proof(tok['secret'], tok['workspace'], tok['invite'], account, root, device.device_id),
                  issued_at=now() if issued_at is None else issued_at)
    return sign(record, device._private, DOMAIN)


def use_path(record):
    return PREFIX + record['workspace'] + '/' + hashlib.sha256(wire(record)).hexdigest() + '.json'


def shape_use(record):
    fields(record, USE_FIELDS, USE)
    for name in ('workspace', 'invite', 'account', 'device_id'): hex_bytes(record[name], 16)
    hex_bytes(record['account_root'], 32); hex_bytes(record['proof'], 64); hex_bytes(record['signature'], 64)
    milliseconds(record['issued_at'])
    return record


def publish_use(storage, record):
    shape_use(record); storage.put(use_path(record), wire(record))


def pending_uses(storage, workspace):
    out = []
    for key in storage.list(PREFIX + workspace + '/'):
        try:
            record = shape_use(parse(storage.get(key)))
            if key == use_path(record): out.append(record)
        except (ValueError, KeyError, TypeError): continue
    return sorted(out, key=lambda r: (r['issued_at'], r['device_id']))


def check_use(record, storage, invite):
    """Why a use cannot be accepted (a short reason), or None when it can. ``invite`` is its replayed state."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from .registry_store import RegistryStore
    from .federation import account_record
    if invite is None: return 'unknown invite'
    if invite['revoked']: return 'revoked'
    if record['issued_at'] > invite['expires_at']: return 'expired'
    if invite['used'] >= invite['max_uses']: return 'no uses left'
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(invite['invite_key'])).verify(bytes.fromhex(record['proof']),
            _message(record['workspace'], record['invite'], record['account'], record['account_root'], record['device_id']))
    except (InvalidSignature, ValueError): return 'bad proof'
    try:
        registry = RegistryStore(storage, account_record(record['account'], record['account_root'])).load()
        verify_signature(record, registry.device_public(record['device_id'], at=record['issued_at']), DOMAIN)
    except (IdentityError, ValueError, KeyError): return 'device not verified (sync first?)'
    return None
