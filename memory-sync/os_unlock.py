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

import subprocess


def home():
    import pwd
    return pwd.getpwuid(os.getuid()).pw_dir


def login_keychain():
    return os.path.join(home(), 'Library', 'Keychains', 'login.keychain-db')


class MacKeychainFile:
    """Interim backend: the file-based login keychain via /usr/bin/security (TN3137: on the road to
    deprecation, not deprecated). The keychain is always named explicitly; the search list and default
    keychain are never used. The item ACL keeps its default creator (/usr/bin/security)."""
    name = 'macos-keychain-file'
    tool = '/usr/bin/security'

    def __init__(self, keychain=None, runner=subprocess.run):
        self.keychain, self.runner = str(keychain or login_keychain()), runner

    def _run(self, args, stdin=None):
        if not os.path.isabs(self.keychain) or any(c in self.keychain for c in '"\\\n\r'):
            raise OSUnlockError(REFUSED) from None
        try:
            return self.runner(args, input=stdin, capture_output=True, text=True, timeout=TIMEOUT,
                               env={'PATH': '/usr/bin:/bin', 'HOME': home(), 'LANG': 'C'})
        except subprocess.TimeoutExpired:
            reason = NO_RESPONSE
        except (OSError, subprocess.SubprocessError, UnicodeError):
            reason = REFUSED
        # Raise outside the handler: suppressed context still retains captured secrets.
        raise OSUnlockError(reason) from None

    def store(self, key_id, secret):
        check_key_id(key_id)
        if type(secret) is not bytes or len(secret) != 32:
            raise OSUnlockError(REFUSED) from None
        # One line over stdin; exit status of -i is advisory, the load-back decides.
        self._run([self.tool, '-i'], 'add-generic-password -U -s %s -a %s -w %s "%s"\n'
                  % (SERVICE, key_id, secret.hex(), self.keychain))
        if self.load(key_id) != secret:
            raise OSUnlockError(REFUSED) from None

    def load(self, key_id):
        check_key_id(key_id)
        result = self._run([self.tool, 'find-generic-password', '-s', SERVICE, '-a', key_id, '-w', self.keychain])
        if result.returncode == 44:
            raise OSUnlockError(MISSING) from None
        value = result.stdout.rstrip('\r\n') if result.returncode == 0 else ''
        if not re.fullmatch('[0-9a-f]{64}', value):
            raise OSUnlockError(REFUSED) from None
        return bytes.fromhex(value)

    def delete(self, key_id):
        check_key_id(key_id)
        result = self._run([self.tool, 'delete-generic-password', '-s', SERVICE, '-a', key_id, self.keychain])
        if result.returncode not in (0, 44):
            raise OSUnlockError(NOT_REMOVED) from None


def current_backend():
    if sys.platform == 'darwin':
        return MacKeychainFile()
    if os.name == 'nt' or sys.platform == 'win32':
        return WindowsCredential()
    return None

class WindowsCredential:
    """Credential Manager generic credential (DPAPI-protected per user), local-machine persistence."""
    name = 'windows-credential'
    ERROR_NOT_FOUND, ERROR_NO_SUCH_LOGON_SESSION = 1168, 1312

    def __init__(self, prefix=SERVICE + '/'):
        self.prefix = prefix

    def target(self, key_id):
        return self.prefix + check_key_id(key_id)

    @staticmethod
    def _api():
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [('dwLowDateTime', wintypes.DWORD), ('dwHighDateTime', wintypes.DWORD)]

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD), ('TargetName', wintypes.LPWSTR),
                        ('Comment', wintypes.LPWSTR), ('LastWritten', FILETIME),
                        ('CredentialBlobSize', wintypes.DWORD), ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
                        ('Persist', wintypes.DWORD), ('AttributeCount', wintypes.DWORD),
                        ('Attributes', ctypes.c_void_p), ('TargetAlias', wintypes.LPWSTR),
                        ('UserName', wintypes.LPWSTR)]
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
        advapi.CredWriteW.restype = wintypes.BOOL
        advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        advapi.CredReadW.restype = wintypes.BOOL
        advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        advapi.CredDeleteW.restype = wintypes.BOOL
        advapi.CredFree.argtypes = [ctypes.c_void_p]
        advapi.CredFree.restype = None
        return ctypes, CREDENTIAL, advapi

    def _error(self, ctypes, missing):
        code = ctypes.get_last_error()
        return OSUnlockError(missing if code == self.ERROR_NOT_FOUND else REFUSED)

    def store(self, key_id, secret):
        if type(secret) is not bytes or len(secret) != 32:
            raise OSUnlockError(REFUSED) from None
        check_key_id(key_id)
        ctypes, CREDENTIAL, advapi = self._api()
        blob = (ctypes.c_ubyte * 32).from_buffer_copy(secret)
        credential = CREDENTIAL(Type=1, TargetName=self.target(key_id), CredentialBlobSize=32,
                                CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
                                Persist=2, UserName='agentmesh')  # CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE
        if not advapi.CredWriteW(ctypes.byref(credential), 0):
            raise self._error(ctypes, REFUSED) from None
        if self.load(key_id) != secret:
            raise OSUnlockError(REFUSED) from None

    def load(self, key_id):
        check_key_id(key_id)
        ctypes, CREDENTIAL, advapi = self._api()
        pointer = ctypes.POINTER(CREDENTIAL)()
        if not advapi.CredReadW(self.target(key_id), 1, 0, ctypes.byref(pointer)):
            raise self._error(ctypes, MISSING) from None
        try:
            if pointer.contents.CredentialBlobSize != 32:
                raise OSUnlockError(REFUSED) from None
            return ctypes.string_at(pointer.contents.CredentialBlob, 32)
        finally:
            advapi.CredFree(pointer)

    def delete(self, key_id):
        check_key_id(key_id)
        ctypes, _, advapi = self._api()
        if not advapi.CredDeleteW(self.target(key_id), 1, 0) and ctypes.get_last_error() != self.ERROR_NOT_FOUND:
            raise OSUnlockError(NOT_REMOVED) from None


from threading import RLock

_RESULTS = {}
_CACHE_LOCK = RLock()


def reset_cache():
    with _CACHE_LOCK:
        _RESULTS.clear()


def unlock(directory, public, envelope, backend=None, *, cache=True):
    """Cache a result per identity; changed envelopes fail closed without another OS read."""
    check(envelope)
    backend = backend if backend is not None else current_backend()
    entry = (str(directory), public['key_id'])
    fingerprint = signed.typed({'public': public, 'envelope': envelope})
    with _CACHE_LOCK:
        if cache and entry in _RESULTS:
            previous, result = _RESULTS[entry]
            if not isinstance(result, str) and previous != fingerprint:
                result = DAMAGED
        elif backend is None or backend.name != envelope['backend']:
            result = UNSUPPORTED
        else:
            try:
                result = open_envelope(public, envelope, backend.load(public['key_id']))
            except OSUnlockError as exc:
                result = str(exc)
            if cache:
                _RESULTS[entry] = (fingerprint, result)
    if isinstance(result, str):
        raise OSUnlockError(result) from None
    return result

LOCK_NAME = '.os-unlock.lock'


from contextlib import contextmanager


@contextmanager
def _locked(directory):
    from runtime_lock import lock
    cm = lock(signed.private_directory(directory) / LOCK_NAME, timeout=0)
    acquired = False
    try:
        cm.__enter__()
        acquired = True
    except TimeoutError:
        pass
    if not acquired:
        raise OSUnlockError(BUSY) from None
    try:
        yield
    finally:
        cm.__exit__(None, None, None)


def _raw(directory):
    return signed.parse(signed.read_local(signed.private_directory(directory) / 'identity.json', private=True))


def enable(directory, ask=None, backend=None):
    import key_protection
    ask = ask or key_protection.console_passphrase
    backend = backend if backend is not None else current_backend()
    if backend is None:
        raise OSUnlockError(UNSUPPORTED) from None
    with _locked(directory):
        current = signed.Security(directory)
        raw = _raw(directory)
        if 'protection' not in raw:
            raise OSUnlockError(NOT_PROTECTED) from None
        if 'os_unlock' in raw:
            raise OSUnlockError(ENABLED) from None
        unlocked = signed.Security(directory, unlock=lambda: ask('Identity passphrase: '))
        private_bytes = unlocked.key.private_bytes_raw()
        key = os.urandom(32)
        envelope = seal(current.public, private_bytes, backend.name, key)
        path = current.directory / 'identity.json'
        try:
            backend.store(current.public['key_id'], key)
            if open_envelope(current.public, envelope, backend.load(current.public['key_id'])) != private_bytes:
                raise OSUnlockError(REFUSED) from None
            signed.write_local(path, {**raw, 'os_unlock': envelope})
        except BaseException:
            try:
                on_disk = _raw(directory).get('os_unlock')
            except Exception:
                # If the committed file cannot be read, retain K rather than strand it.
                on_disk = envelope
            if on_disk != envelope:
                backend.delete(current.public['key_id'])
            raise
        reset_cache()
        reread = signed.Security(directory, os_unlock=True, backend=backend)
        if reread.locked or reread.key.private_bytes_raw() != private_bytes:
            raise OSUnlockError(DAMAGED) from None
        return {'status': 'enabled', 'key_id': current.public['key_id'], 'backend': backend.name}


def disable(directory, backend=None):
    backend = backend if backend is not None else current_backend()
    with _locked(directory):
        current = signed.Security(directory)
        raw = _raw(directory)
        if 'os_unlock' in raw:
            signed.write_local(current.directory / 'identity.json', {k: v for k, v in raw.items() if k != 'os_unlock'})
            reset_cache()
        if backend is None:
            raise OSUnlockError(UNSUPPORTED) from None
        failed = False
        try:
            backend.delete(current.public['key_id'])
        except OSUnlockError:
            failed = True
        if failed:
            raise OSUnlockError(NOT_REMOVED) from None
        return {'status': 'disabled', 'key_id': current.public['key_id']}


def status(directory, backend=None):
    backend = backend if backend is not None else current_backend()
    current = signed.Security(directory)
    raw = _raw(directory)
    report = {'key_id': current.public['key_id'], 'protected': 'protection' in raw,
              'os_unlock': 'enabled' if 'os_unlock' in raw else 'absent',
              'backend': raw['os_unlock']['backend'] if 'os_unlock' in raw else None,
              'available': False, 'reason': None, 'stray_item': False}
    if 'os_unlock' in raw:
        try:
            unlock(current.directory, current.public, raw['os_unlock'], backend, cache=False)
            report['available'] = True
        except OSUnlockError as exc:
            report['reason'] = str(exc)
    elif backend is not None:
        try:
            backend.load(current.public['key_id'])
            report['stray_item'] = True
        except OSUnlockError as exc:
            report['reason'] = None if str(exc) == MISSING else str(exc)
    return report
