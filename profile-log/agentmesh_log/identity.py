"""Root/device/agent signing chain; data keys never belong to agents."""
import time
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from .encoding import typed, fields, hex_bytes, milliseconds
from .errors import IdentityError
from .model import new_id

DEVICE_DOMAIN = b'AgentMesh/device-cert/Ed25519/v1\x00'
AGENT_DOMAIN = b'AgentMesh/agent-cert/Ed25519/v1\x00'
DEVICE_FIELDS = {'format', 'account_id', 'device_id', 'device_public_key', 'issuer', 'issued_at', 'signature'}
AGENT_FIELDS = {'format', 'account_id', 'device_id', 'agent_id', 'agent_public_key', 'issued_at', 'signature'}


def sign(record, private, domain):
    unsigned = {k: v for k, v in record.items() if k != 'signature'}
    return {**unsigned, 'signature': private.sign(domain + typed(unsigned)).hex()}


def verify_signature(record, public, domain):
    signature = hex_bytes(record['signature'], 64)
    unsigned = {k: v for k, v in record.items() if k != 'signature'}
    try: Ed25519PublicKey.from_public_bytes(public).verify(signature, domain + typed(unsigned))
    except (InvalidSignature, ValueError): raise IdentityError('invalid signature') from None


def validate_agent_certificate(cert):
    fields(cert, AGENT_FIELDS, 'agentmesh-agent-cert-v1')
    for name in ('account_id', 'device_id', 'agent_id'): hex_bytes(cert[name], 16)
    hex_bytes(cert['agent_public_key'], 32); hex_bytes(cert['signature'], 64)
    milliseconds(cert['issued_at'])


class DeviceIdentity:
    def __init__(self):
        self.device_id = new_id()
        self._private = Ed25519PrivateKey.generate()

    @property
    def public_bytes(self):
        return self._private.public_key().public_bytes_raw()

    def certificate(self, account, *, issuer=None, issued_at=None):
        record = {'format': 'agentmesh-device-cert-v1', 'account_id': account.account_id,
                  'device_id': self.device_id, 'device_public_key': self.public_bytes.hex(),
                  'issuer': 'account' if issuer is None else issuer.device_id,
                  'issued_at': milliseconds(time.time_ns() // 1_000_000 if issued_at is None else issued_at)}
        return sign(record, account._root if issuer is None else issuer._private, DEVICE_DOMAIN)


class AgentIdentity:
    def __init__(self, device, account_id, *, issued_at=None):
        hex_bytes(account_id, 16)
        self.agent_id, self.device_id, self.account_id = new_id(), device.device_id, account_id
        self._private = Ed25519PrivateKey.generate()
        self._certificate = sign({'format': 'agentmesh-agent-cert-v1', 'account_id': account_id,
            'device_id': self.device_id, 'agent_id': self.agent_id, 'agent_public_key': self.public_bytes.hex(),
            'issued_at': milliseconds(time.time_ns() // 1_000_000 if issued_at is None else issued_at)}, device._private, AGENT_DOMAIN)

    @property
    def public_bytes(self):
        return self._private.public_key().public_bytes_raw()

    def certificate(self):
        return dict(self._certificate)


class Registry:
    def __init__(self, account_public_record):
        fields(account_public_record, {'format', 'account_id', 'root_public_key'}, 'agentmesh-account-v1')
        hex_bytes(account_public_record['account_id'], 16)
        self.account_id = account_public_record['account_id']
        self._root_public = hex_bytes(account_public_record['root_public_key'], 32)
        self._devices = {}

    def add_device(self, cert):
        fields(cert, DEVICE_FIELDS, 'agentmesh-device-cert-v1')
        for name in ('account_id', 'device_id'): hex_bytes(cert[name], 16)
        public = hex_bytes(cert['device_public_key'], 32)
        hex_bytes(cert['signature'], 64); milliseconds(cert['issued_at'])
        if cert['issuer'] != 'account': hex_bytes(cert['issuer'], 16)
        if cert['account_id'] != self.account_id: raise IdentityError('wrong account')
        if cert['issuer'] == cert['device_id']: raise IdentityError('self-issued device certificate')
        issuer = self._root_public if cert['issuer'] == 'account' else self._devices.get(cert['issuer'])
        if issuer is None: raise IdentityError('unknown device issuer')
        verify_signature(cert, issuer, DEVICE_DOMAIN)
        old = self._devices.get(cert['device_id'])
        if old is not None and old != public: raise IdentityError('device identifier already registered')
        self._devices[cert['device_id']] = public

    def verify_agent(self, cert):
        validate_agent_certificate(cert)
        if cert['account_id'] != self.account_id: raise IdentityError('wrong account')
        # TODO(phase4): check device/agent revocation before accepting the chain.
        device = self._devices.get(cert['device_id'])
        if device is None: raise IdentityError('unknown device')
        verify_signature(cert, device, AGENT_DOMAIN)
        return hex_bytes(cert['agent_public_key'], 32)
