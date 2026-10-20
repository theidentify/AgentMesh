"""Mutual proof of possession bound to fresh per-connection nonces."""
import base64
import secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from ..encoding import typed, fields, hex_bytes
from ..errors import IdentityError

CHALLENGE = b'AgentMesh/broker-challenge/Ed25519/v1\x00'
AUTH = b'AgentMesh/broker-auth/Ed25519/v1\x00'


class BrokerIdentityError(IdentityError): pass


def b64(data): return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def nonce(value):
    try:
        raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        if len(raw) != 32 or b64(raw) != value: raise ValueError()
    except Exception: raise IdentityError('unauthenticated') from None
    return value


def hello():
    return {'format': 'agentmesh-broker-hello-v1', 'client_nonce': b64(secrets.token_bytes(32))}


def challenge_body(h, c):
    return dict(device_id=c['device_id'], client_nonce=nonce(h['client_nonce']), server_nonce=nonce(c['server_nonce']))


def auth_body(agent_id, h, c):
    return {**challenge_body(h, c), 'agent_id': agent_id}


def check_challenge(h, c, pinned):
    try:
        fields(c, {'format', 'device_id', 'server_nonce', 'signature'}, 'agentmesh-broker-challenge-v1')
        hex_bytes(c['device_id'], 16)
        Ed25519PublicKey.from_public_bytes(pinned).verify(hex_bytes(c['signature'], 64), CHALLENGE + typed(challenge_body(h, c)))
    except Exception: raise BrokerIdentityError('broker identity mismatch') from None


def authenticate(agent, h, c):
    return {'format': 'agentmesh-broker-auth-v1', 'agent_cert': agent.certificate(),
        'signature': agent._private.sign(AUTH + typed(auth_body(agent.agent_id, h, c))).hex()}


class Challenge:
    def __init__(self, state, h):
        fields(h, {'format', 'client_nonce'}, 'agentmesh-broker-hello-v1'); nonce(h['client_nonce'])
        self.hello = h
        self.message = {'format': 'agentmesh-broker-challenge-v1', 'device_id': state.device.device_id,
            'server_nonce': b64(secrets.token_bytes(32))}
        self.message['signature'] = state.device._private.sign(CHALLENGE + typed(challenge_body(h, self.message))).hex()

    def verify(self, auth, state):
        try:
            fields(auth, {'format', 'agent_cert', 'signature'}, 'agentmesh-broker-auth-v1')
            cert = auth['agent_cert']; public = state.registry().verify_agent(cert)
            grant = state.grants.get(cert['agent_id'])
            if cert['device_id'] != state.device.device_id or grant is None or grant.agent_public_key != public.hex():
                raise ValueError()
            Ed25519PublicKey.from_public_bytes(public).verify(hex_bytes(auth['signature'], 64),
                AUTH + typed(auth_body(cert['agent_id'], self.hello, self.message)))
            return cert['agent_id']
        except Exception: raise IdentityError('unauthenticated') from None
