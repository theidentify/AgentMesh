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

    @classmethod
    def from_private(cls, device_id, raw):
        hex_bytes(device_id, 16)
        if type(raw) is not bytes or len(raw) != 32: raise IdentityError('invalid private key')
        obj = cls.__new__(cls)
        obj.device_id = device_id
        obj._private = Ed25519PrivateKey.from_private_bytes(raw)
        return obj

    def private_bytes(self):
        return self._private.private_bytes_raw()

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

    @classmethod
    def from_private(cls, cert, raw):
        validate_agent_certificate(cert)
        if type(raw) is not bytes or len(raw) != 32: raise IdentityError('invalid private key')
        obj = cls.__new__(cls)
        obj._private = Ed25519PrivateKey.from_private_bytes(raw)
        obj.account_id, obj.device_id, obj.agent_id = (cert[k] for k in ('account_id', 'device_id', 'agent_id'))
        if obj.public_bytes.hex() != cert['agent_public_key']: raise IdentityError('agent key mismatch')
        obj._certificate = dict(cert)
        return obj

    def private_bytes(self):
        return self._private.private_bytes_raw()

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
        self.certificates = {}
        self.device_records = {}
        self.revocations = {}
        self.recoveries = {}

    def add_device(self, cert):
        fields(cert, DEVICE_FIELDS, 'agentmesh-device-cert-v1')
        for name in ('account_id', 'device_id'): hex_bytes(cert[name], 16)
        public = hex_bytes(cert['device_public_key'], 32)
        hex_bytes(cert['signature'], 64); milliseconds(cert['issued_at'])
        if cert['issuer'] not in ('account','recovery'): hex_bytes(cert['issuer'], 16)
        if cert['account_id'] != self.account_id: raise IdentityError('wrong account')
        if cert['issuer'] == cert['device_id']: raise IdentityError('self-issued device certificate')
        if cert['issuer'] == 'recovery':
            issuers = [bytes.fromhex(r['recovery_signing_public']) for r in self.recoveries.values() if r['issued_at'] <= cert['issued_at']]
            for issuer in issuers:
                try: verify_signature(cert,issuer,DEVICE_DOMAIN); break
                except IdentityError: continue
            else: raise IdentityError('unknown recovery issuer')
        else:
            issuer = self._root_public if cert['issuer'] == 'account' else self.device_public(cert['issuer'],at=cert['issued_at'])
        if issuer is None: raise IdentityError('unknown device issuer')
        verify_signature(cert, issuer, DEVICE_DOMAIN)
        old = self._devices.get(cert['device_id'])
        if old is not None and old != public: raise IdentityError('device identifier already registered')
        self._devices[cert['device_id']] = public
        self.certificates[cert['device_id']] = dict(cert)

    def verify_agent(self, cert):
        validate_agent_certificate(cert)
        if cert['account_id'] != self.account_id: raise IdentityError('wrong account')
        device = self.device_public(cert['device_id'])
        if device is None: raise IdentityError('unknown device')
        verify_signature(cert, device, AGENT_DOMAIN)
        return hex_bytes(cert['agent_public_key'], 32)

    def device_public(self, device_id, *, at=None, seen=None):
        seen = set() if seen is None else seen
        if device_id in seen: raise IdentityError('device chain cycle')
        seen.add(device_id)
        cert = self.certificates.get(device_id)
        if cert is None: raise IdentityError('unknown device')
        revoked = self.revocations.get(device_id)
        if revoked is not None and (at is None or at >= revoked['revoked_at']):
            raise IdentityError('revoked device')
        if at is not None and at < cert['issued_at']: raise IdentityError('device not yet issued')
        if cert['issuer'] not in ('account','recovery'):
            self.device_public(cert['issuer'],at=cert['issued_at'],seen=seen)
        return self._devices[device_id]

    def add_recovery(self, record):
        from .registry_store import shape, domain, recovery_id
        from .agekeys import validate_recipient
        shape(record,'recovery-record')
        if record['account_id'] != self.account_id: raise IdentityError('wrong account')
        hex_bytes(record['recovery_signing_public'],32)
        validate_recipient(record['recovery_age_recipient'])
        previous = record['previous']
        if previous is None: public = self._root_public
        else:
            parent = self.recoveries.get(previous)
            if parent is None or record['issued_at'] < parent['issued_at']: raise IdentityError('unknown recovery parent')
            public = bytes.fromhex(parent['recovery_signing_public'])
        verify_signature(record,public,domain('recovery-record'))
        self.recoveries[recovery_id(record)] = record

    def current_recovery(self):
        parents = {r['previous'] for r in self.recoveries.values()}
        leaves = [(r['issued_at'],k,r) for k,r in self.recoveries.items() if k not in parents]
        if not leaves: raise IdentityError('missing recovery record')
        return max(leaves,key=lambda item:item[:2])[2]

    def add_revocation(self, record):
        from .registry_store import shape, domain
        shape(record,'device-revocation')
        subject, signer = record['device_id'],record['revoked_by']
        if subject not in self._devices or subject == signer or record['reason_code'] not in ('removed','recovery','compromised'):
            raise IdentityError('invalid revocation')
        if signer == 'recovery':
            public = bytes.fromhex(self.current_recovery()['recovery_signing_public'])
        else: public = self.device_public(signer)
        verify_signature(record,public,domain('device-revocation'))
        self.revocations[subject] = record

    def verify_historical_agent(self, cert, event_time):
        """Historical event verification; live handshakes still reject revoked devices."""
        validate_agent_certificate(cert)
        if cert['account_id'] != self.account_id or cert['issued_at'] > event_time:
            raise IdentityError('invalid historical certificate')
        public=self.device_public(cert['device_id'],at=event_time)
        verify_signature(cert,public,AGENT_DOMAIN)
        return hex_bytes(cert['agent_public_key'],32)
