"""Opt-in semantic Ed25519 adapter; identity backend is operator-injected.

No pairing, key creation, ownership discovery, encryption or SQL packet signing.
"""
import importlib.util
from pathlib import Path
import re
from .core import AccessDenied, KnowledgeError, profile_id

FORMAT = 'agentmesh-knowledge-bundle-v2'
DOMAIN = b'AgentMesh/semantic-knowledge/Ed25519/v2\x00'
FIELDS = {'format', 'encoding', 'issuer_identity', 'recipient_identity', 'value'}
IDENTITY = ('group', 'node', 'sender', 'key_id')


def load_backend(path):
    """Execute only an explicitly selected trusted local Python module."""
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise KnowledgeError('signed backend requires an absolute trusted local file')
    try:
        spec = importlib.util.spec_from_file_location('_agentmesh_semantic_identity_backend', path)
        if spec is None or spec.loader is None:
            raise ValueError('invalid backend module file')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in ('Security', 'read_trust', 'typed', 'crypto'):
            if not callable(getattr(module, name, None)):
                raise ValueError('missing identity primitive')
        module.crypto()
        return module
    except (ImportError, AttributeError, ValueError, OSError) as exc:
        raise KnowledgeError('signed identity backend unavailable or incompatible') from exc


class SignedChannel:
    format = FORMAT

    def __init__(self, *, backend, security_dir, principal, peer_profile,
                 peer_fingerprint, peer_group, peer_node, peer_sender):
        self.backend = backend
        self.principal = profile_id(principal)
        self.peer_profile = profile_id(peer_profile)
        if self.principal == self.peer_profile:
            raise KnowledgeError('signed semantic channel requires distinct profiles')
        if not isinstance(peer_fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', peer_fingerprint):
            raise KnowledgeError('full lowercase peer fingerprint required')
        self.peer = dict(group=peer_group, node=peer_node, sender=peer_sender, key_id=peer_fingerprint)
        try:
            backend.crypto()
            self.security = backend.Security(security_dir)
            self.local = {k: self.security.public[k] for k in IDENTITY}
            self.reauthenticate()
        except (ValueError, OSError, ImportError, AttributeError, TypeError, KeyError) as exc:
            raise AccessDenied('signed channel identity or trust unavailable') from exc

    @property
    def scope(self):
        return dict(principal=self.principal, peer_profile=self.peer_profile,
                    local=dict(self.local), peer=dict(self.peer))

    def reauthenticate(self):
        """Reload JSON trust each time; SQLite does not lock external trust updates."""
        try:
            self.security.check_self()
            entry = self.backend.read_trust(self.security.directory)['peers'].get(self.peer['key_id'])
            if (not entry or entry['revoked'] or
                    {k: entry[k] for k in IDENTITY} != self.peer or
                    entry['group'] != self.local['group']):
                raise ValueError('peer not approved for pinned scope')
            return entry
        except (ValueError, OSError, ImportError, TypeError, KeyError) as exc:
            raise AccessDenied('signed channel trust rejected') from exc

    def _metadata(self, outgoing):
        return dict(format='agentmesh-semantic-signature-v2', encoding='agentmesh-typed-v1',
                    issuer_identity=dict(self.local if outgoing else self.peer),
                    recipient_identity=dict(self.peer if outgoing else self.local))

    def _binding(self, unsigned, outgoing):
        issuer, recipient = (self.principal, self.peer_profile) if outgoing else (self.peer_profile, self.principal)
        if (unsigned.get('format'), unsigned.get('issuer'), unsigned.get('recipient')) != (FORMAT, issuer, recipient):
            raise AccessDenied('signed semantic profile binding mismatch')

    def _message(self, unsigned, metadata):
        try:
            return DOMAIN + self.backend.typed(dict(envelope=unsigned, authentication=metadata))
        except (ValueError, TypeError, RecursionError) as exc:
            raise KnowledgeError('invalid bounded signed semantic encoding') from exc

    def sign(self, unsigned):
        self.reauthenticate()
        self._binding(unsigned, True)
        metadata = self._metadata(True)
        return {**metadata, 'value': self.security.key.sign(self._message(unsigned, metadata)).hex()}

    def verify(self, unsigned, signature):
        entry = self.reauthenticate()
        self._binding(unsigned, False)
        if (not isinstance(signature, dict) or set(signature) != FIELDS or
                {k: v for k, v in signature.items() if k != 'value'} != self._metadata(False) or
                not isinstance(signature['value'], str) or not re.fullmatch('[0-9a-f]{128}', signature['value'])):
            raise AccessDenied('invalid signed semantic authentication metadata')
        _, Public, Invalid = self.backend.crypto()
        try:
            Public.from_public_bytes(bytes.fromhex(entry['public_key'])).verify(
                bytes.fromhex(signature['value']), self._message(unsigned, self._metadata(False)))
        except (Invalid, ValueError) as exc:
            raise AccessDenied('invalid semantic signature') from exc
