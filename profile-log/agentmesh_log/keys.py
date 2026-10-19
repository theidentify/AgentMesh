"""Independent scope epochs and HPKE base-mode wrapping (RFC 9180)."""
import base64
from dataclasses import dataclass
import hashlib
import hmac
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
import re
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.hpke import Suite, KEM, KDF, AEAD
from .encoding import hex_bytes, fields, typed
from .errors import ValidationError, CryptoError, KeyAccessError

SUITE = Suite(KEM.X25519, KDF.HKDF_SHA256, AEAD.CHACHA20_POLY1305)
KEY_DOMAIN = b'AgentMesh/scope-key-id/v1\x00'
WRAP_DOMAIN = b'AgentMesh/scope-wrap/v1\x00'
WRAP_AUTH_DOMAIN = b'AgentMesh/scope-wrap-auth/v1\x00'
WRAP_AUTH_KEY_DOMAIN = b'AgentMesh/scope-wrap-auth-key/v1\x00'
WRAP_FIELDS = {'profile', 'parent_scope', 'parent_key_id', 'child_scope', 'child_key_id'}


@dataclass(frozen=True, slots=True)
class ScopeWriteKey:
    profile_id: str
    scope_id: str
    public_bytes: bytes

    def __post_init__(self):
        hex_bytes(self.profile_id, 16); hex_bytes(self.scope_id, 16)
        if type(self.public_bytes) is not bytes or len(self.public_bytes) != 32:
            raise ValidationError('invalid scope public key')

    @property
    def key_id(self):
        return hashlib.sha256(KEY_DOMAIN + self.public_bytes).hexdigest()


class ScopeReadKey:
    __slots__ = ('profile_id', 'scope_id', '_private')

    def __init__(self, profile_id, scope_id, private):
        hex_bytes(profile_id, 16); hex_bytes(scope_id, 16)
        if not isinstance(private, X25519PrivateKey): raise KeyAccessError('private scope key required')
        self.profile_id, self.scope_id, self._private = profile_id, scope_id, private

    @classmethod
    def generate(cls, profile_id, scope_id):
        return cls(profile_id, scope_id, X25519PrivateKey.generate())

    @classmethod
    def from_private(cls, profile_id, scope_id, raw):
        if type(raw) is not bytes or len(raw) != 32: raise KeyAccessError('invalid private key')
        return cls(profile_id, scope_id, X25519PrivateKey.from_private_bytes(raw))

    def private_bytes(self):
        return self._private.private_bytes_raw()

    @property
    def write_key(self):
        return ScopeWriteKey(self.profile_id, self.scope_id, self._private.public_key().public_bytes_raw())

    @property
    def key_id(self):
        return self.write_key.key_id


def decode_ciphertext(value):
    if type(value) is not str or not re.fullmatch('[A-Za-z0-9_-]+', value):
        raise ValidationError('invalid ciphertext encoding')
    try: raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    except ValueError: raise ValidationError('invalid ciphertext encoding') from None
    if len(raw) < 48 or base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii') != value:
        raise ValidationError('invalid ciphertext encoding')
    return raw


def encrypt(data: bytes, key: ScopeWriteKey, info: bytes) -> str:
    if not isinstance(key, ScopeWriteKey): raise KeyAccessError('write key required')
    try: raw = SUITE.encrypt(data, X25519PublicKey.from_public_bytes(key.public_bytes), info)
    except (ValueError, InvalidTag): raise CryptoError('encryption failed') from None
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def decrypt(data: str, key: ScopeReadKey, info: bytes) -> bytes:
    if not isinstance(key, ScopeReadKey): raise KeyAccessError('read key required')
    raw = decode_ciphertext(data)
    try: return SUITE.decrypt(raw, key._private, info)
    except (ValueError, InvalidTag): raise CryptoError('decryption failed') from None


def wrap_key(child: ScopeReadKey, parent: ScopeReadKey) -> dict:
    if not isinstance(child, ScopeReadKey) or not isinstance(parent, ScopeReadKey):
        raise KeyAccessError('child and parent read keys required')
    if child.profile_id != parent.profile_id or child.scope_id == parent.scope_id:
        raise KeyAccessError('invalid wrapping scope')
    header = {'profile': child.profile_id, 'parent_scope': parent.scope_id,
              'parent_key_id': parent.key_id, 'child_scope': child.scope_id, 'child_key_id': child.key_id}
    record = {'format': 'agentmesh-scope-wrap-v1', **header,
              'ciphertext': encrypt(child._private.private_bytes_raw(), parent.write_key, WRAP_DOMAIN + typed(header))}
    return {**record, 'auth': _wrap_auth(record, parent).hex()}


def unwrap_key(record: dict, parent: ScopeReadKey) -> ScopeReadKey:
    if not isinstance(parent, ScopeReadKey): raise KeyAccessError('parent read key required')
    fields(record, WRAP_FIELDS | {'format', 'ciphertext', 'auth'}, 'agentmesh-scope-wrap-v1')
    for field in WRAP_FIELDS: hex_bytes(record[field], 32 if field.endswith('key_id') else 16)
    if (record['profile'], record['parent_scope'], record['parent_key_id']) != (parent.profile_id, parent.scope_id, parent.key_id):
        raise KeyAccessError('wrapping key mismatch')
    auth = hex_bytes(record['auth'], 32)
    if not hmac.compare_digest(auth, _wrap_auth(record, parent)):
        raise CryptoError('invalid wrap authentication')
    header = {k: record[k] for k in WRAP_FIELDS}
    raw = decrypt(record['ciphertext'], parent, WRAP_DOMAIN + typed(header))
    try: private = X25519PrivateKey.from_private_bytes(raw)
    except ValueError: raise CryptoError('invalid wrapped key') from None
    child = ScopeReadKey(record['profile'], record['child_scope'], private)
    if child.key_id != record['child_key_id']: raise CryptoError('wrapped key mismatch')
    return child


def _wrap_auth(record, parent):
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=WRAP_AUTH_KEY_DOMAIN + bytes.fromhex(parent.key_id)).derive(parent._private.private_bytes_raw())
    unsigned = {k: v for k, v in record.items() if k != 'auth'}
    return hmac.new(key, WRAP_AUTH_DOMAIN + typed(unsigned), hashlib.sha256).digest()
