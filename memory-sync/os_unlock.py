"""OS-protected unattended unlock of a passphrase-protected identity (issue #7).

A random 32-byte wrapping key K lives in the OS key store of the worker's user
(Mac login keychain via /usr/bin/security, Windows Credential Manager). The
device key is sealed under K in identity.json `os_unlock`; the passphrase
envelope (`protection`) stays the portable path. Any same-user process can read
K. See KEY-PROTECTION.md and docs/superpowers/specs/2026-10-12-os-unattended-unlock-design.md.
"""
import os
import re
import sys

import signed_packets as signed

FORMAT = 'agentmesh-os-unlock-v1'
SERVICE = 'agentmesh.identity-unlock'
BACKENDS = ('macos-keychain-file', 'windows-credential')
FIELDS = {'format', 'backend', 'nonce', 'ciphertext'}
TIMEOUT = 15

UNSUPPORTED = 'OS unlock is not supported on this platform'
ENABLED = 'OS unlock is already enabled'
MISSING = 'OS unlock item is not available for this account'
REFUSED = 'OS key store refused access'
NO_RESPONSE = 'OS key store did not respond'
DAMAGED = 'OS unlock could not open the identity: wrong key or damaged file'
NOT_PROTECTED = 'identity must be passphrase-protected before enabling OS unlock'
NOT_REMOVED = 'OS unlock item could not be removed'
BUSY = 'OS unlock is already in progress'
REASONS = frozenset({UNSUPPORTED, ENABLED, MISSING, REFUSED, NO_RESPONSE, DAMAGED, NOT_PROTECTED, NOT_REMOVED, BUSY})


class OSUnlockError(ValueError):
    """Carries only a fixed reason; always raised `from None` so no subprocess output is chained."""
    def __init__(self, reason):
        if reason not in REASONS:
            reason = REFUSED
        super().__init__(reason)


def check_key_id(key_id):
    if type(key_id) is not str or not re.fullmatch('[0-9a-f]{64}', key_id):
        raise OSUnlockError(REFUSED) from None
    return key_id


def associated_data(backend, public):
    return (FORMAT.encode() + b'\0' + backend.encode() + b'\0'
            + signed.typed({k: public[k] for k in sorted(signed.PUBLIC_FIELDS)}))


def check(envelope):
    if (type(envelope) is not dict or set(envelope) != FIELDS or envelope['format'] != FORMAT
            or envelope['backend'] not in BACKENDS):
        raise ValueError('invalid OS unlock envelope')
    try:
        signed.hex_bytes(envelope['nonce'], 12)
        signed.hex_bytes(envelope['ciphertext'], 48)
    except (ValueError, TypeError):
        raise ValueError('invalid OS unlock envelope') from None
    return envelope


def _aesgcm():
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM, InvalidTag


def seal(public, private_bytes, backend, key):
    AESGCM, _ = _aesgcm()
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, private_bytes, associated_data(backend, public))
    return {'format': FORMAT, 'backend': backend, 'nonce': nonce.hex(), 'ciphertext': ciphertext.hex()}


def open_envelope(public, envelope, key):
    AESGCM, InvalidTag = _aesgcm()
    check(envelope)
    try:
        return AESGCM(key).decrypt(bytes.fromhex(envelope['nonce']), bytes.fromhex(envelope['ciphertext']),
                                   associated_data(envelope['backend'], public))
    except (InvalidTag, ValueError, TypeError):
        pass
    raise OSUnlockError(DAMAGED) from None
