"""Passphrase-protected private identity keys (issue #6).

Threat model (see KEY-PROTECTION.md): protects the Ed25519 device key at rest
against copies of the identity directory (backups, disk images, synced or
stolen files). It does not protect against code already running as the same
user while the key is unlocked, nor against a weak passphrase. Unattended
unlock (OS keychain / DPAPI) is issue #7; until then a protected key can only
be unlocked through an interactive console and the worker refuses it.

Format: identity.json keeps the public fields unchanged (same key_id,
sender, group, node) and replaces `private_key` with `protection`:
scrypt-derived key, AES-256-GCM, public fields bound as associated data.
"""
import os
import sys

import signed_packets as signed

FORMAT = 'agentmesh-key-protection-v1'
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 17, 8, 1  # ~128 MiB, about half a second to unlock
MIN_N, MAX_N = 2 ** 14, 2 ** 20
MIN_PASSPHRASE = 12
FIELDS = {'format', 'kdf', 'n', 'r', 'p', 'salt', 'cipher', 'nonce', 'ciphertext'}
LOCKED = ('passphrase-protected identity is locked; unattended unlock is not available yet '
          '(requires OS-protected key access)')


def _primitives():
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as exc:
        raise ValueError('key protection requires cryptography; install requirements-security.txt') from exc
    return AESGCM, Scrypt, InvalidTag


def associated_data(public):
    """Binds the ciphertext to the exact public identity it belongs to."""
    return b'agentmesh-key-protection-v1\0' + signed.typed({k: public[k] for k in sorted(signed.PUBLIC_FIELDS)})


def _derive(passphrase, salt, n, r, p):
    _, Scrypt, _ = _primitives()
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode('utf-8'))


def seal(public, private_bytes, passphrase, *, n=SCRYPT_N):
    AESGCM, _, _ = _primitives()
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _derive(passphrase, salt, n, SCRYPT_R, SCRYPT_P)
    ciphertext = AESGCM(key).encrypt(nonce, private_bytes, associated_data(public))
    return {'format': FORMAT, 'kdf': 'scrypt', 'n': n, 'r': SCRYPT_R, 'p': SCRYPT_P, 'salt': salt.hex(),
            'cipher': 'aes-256-gcm', 'nonce': nonce.hex(), 'ciphertext': ciphertext.hex()}


def check(protection):
    """Validate parameters before any key derivation (corruption cannot force huge KDF work)."""
    if (type(protection) is not dict or set(protection) != FIELDS or protection['format'] != FORMAT
            or protection['kdf'] != 'scrypt' or protection['cipher'] != 'aes-256-gcm'
            or type(protection['n']) is not int or not MIN_N <= protection['n'] <= MAX_N
            or protection['n'] & (protection['n'] - 1) or protection['r'] != SCRYPT_R or protection['p'] != SCRYPT_P):
        raise ValueError('invalid protected identity parameters')
    try:
        signed.hex_bytes(protection['salt'], 16)
        signed.hex_bytes(protection['nonce'], 12)
        signed.hex_bytes(protection['ciphertext'], 48)  # 32-byte key + 16-byte GCM tag
    except (ValueError, TypeError) as exc:
        raise ValueError('invalid protected identity parameters') from exc
    return protection


def open_sealed(public, protection, passphrase):
    AESGCM, _, InvalidTag = _primitives()
    check(protection)
    key = _derive(passphrase, bytes.fromhex(protection['salt']), protection['n'], protection['r'], protection['p'])
    try:
        return AESGCM(key).decrypt(bytes.fromhex(protection['nonce']), bytes.fromhex(protection['ciphertext']),
                                   associated_data(public))
    except InvalidTag as exc:
        # Wrong passphrase and tampering are indistinguishable by design.
        raise ValueError('protected identity could not be unlocked: wrong passphrase or damaged file') from exc


def console_passphrase(prompt):
    """No-echo passphrase from an interactive console only; never args, env or files."""
    try:
        interactive = sys.stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        raise ValueError('passphrase entry requires an interactive console')
    import getpass
    return getpass.getpass(prompt, stream=sys.stderr)


def protect_identity(directory, ask=console_passphrase, *, n=SCRYPT_N):
    """Explicit migration of a raw identity to passphrase protection.

    The public identity (key_id, sender, group, node) is unchanged. The new
    file is verified by unlocking it before an atomic replace, and again after;
    an interruption leaves either the original or the protected file.
    """
    current = signed.Security(directory)
    if current.locked:
        raise ValueError('identity is already passphrase-protected')
    passphrase = ask('New identity passphrase (at least %d characters): ' % MIN_PASSPHRASE)
    if len(passphrase) < MIN_PASSPHRASE:
        raise ValueError('passphrase must be at least %d characters' % MIN_PASSPHRASE)
    if ask('Repeat the passphrase: ') != passphrase:
        raise ValueError('passphrases do not match; nothing was changed')
    private_bytes = current.key.private_bytes_raw()
    protected = {**current.public, 'protection': seal(current.public, private_bytes, passphrase, n=n)}
    if open_sealed(current.public, protected['protection'], passphrase) != private_bytes:
        raise ValueError('protected identity verification failed; nothing was changed')
    path = current.directory / 'identity.json'
    signed.write_local(path, protected)
    reread = signed.Security(directory, unlock=lambda: passphrase)
    if reread.public != current.public or reread.key.private_bytes_raw() != private_bytes:
        raise ValueError('protected identity readback failed')
    return {'status': 'protected', 'key_id': current.public['key_id']}
