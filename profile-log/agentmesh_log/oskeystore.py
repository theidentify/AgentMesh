"""OS backends ported from memory-sync/os_unlock.py; no imports from that package."""
import hashlib
import os
import re
import secrets
import subprocess
import sys
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from .encoding import wire, parse, fields, hex_bytes, typed
from .broker.keystore import FileKeyStore, BrokerState, atomic_write
from .model import Account

SERVICE='agentmesh.profile-log'
TIMEOUT=15
REFUSED='OS key store refused access'
MISSING='OS key store item missing'
NO_RESPONSE='OS key store did not respond'
NOT_REMOVED='OS key store item could not be removed'
FORMAT='agentmesh-os-keystore-v1'

class OSUnlockError(ValueError):
    def __init__(self,reason):
        super().__init__(reason if reason in (REFUSED,MISSING,NO_RESPONSE,NOT_REMOVED) else REFUSED)

def check_key_id(key_id):
    hex_bytes(key_id,16)
    return key_id

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
            return self.runner(args, input=stdin, capture_output=True, text=True, encoding='utf-8', timeout=TIMEOUT,
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
            return None
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
    if sys.platform == 'win32':
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
        advapi = None
        try:
            advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        except OSError:
            pass
        if advapi is None:
            raise OSUnlockError(REFUSED) from None
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
            
            if ctypes.get_last_error() == self.ERROR_NOT_FOUND: return None
            raise OSUnlockError(REFUSED) from None
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



class OsKeyStore(FileKeyStore):
    def __init__(self,home,backend=None):
        super().__init__(home); self.path=home.root/'keystore.sealed'
        self.backend=current_backend() if backend is None else backend
        if self.backend is None: raise ValueError('OS key store unavailable')

    def load(self):
        try:
            record=parse(self._read()[1])
            fields(record,{'format','device_id','nonce','ciphertext'},FORMAT)
            device=record['device_id']; hex_bytes(device,16)
            key=self.backend.load(device)
            if type(key) is not bytes or len(key)!=32: raise ValueError()
            data=ChaCha20Poly1305(key).decrypt(hex_bytes(record['nonce'],12),bytes.fromhex(record['ciphertext']),
                typed(dict(format=FORMAT,device_id=device)))
            state=BrokerState.from_record(parse(data))
            if state.device.device_id!=device: raise ValueError()
            return state
        except Exception: pass
        raise ValueError('key store could not be opened') from None

    def save(self,state):
        self.home.check(); device=state.device.device_id
        key=self.backend.load(device)
        if key is None:
            if self.path.exists(): raise ValueError('key store unavailable')
            key=secrets.token_bytes(32); self.backend.store(device,key)
            if self.backend.load(device)!=key: raise ValueError('key store roundtrip failed')
        if type(key) is not bytes or len(key)!=32: raise ValueError('invalid wrapping key')
        nonce=secrets.token_bytes(12); aad=dict(format=FORMAT,device_id=device)
        ciphertext=ChaCha20Poly1305(key).encrypt(nonce,wire(state.record()),typed(aad))
        atomic_write(self.path,wire(dict(**aad,nonce=nonce.hex(),ciphertext=ciphertext.hex())))


def select_store(home,backend=None):
    if (home.root/'keystore.sealed').exists(): return OsKeyStore(home,backend)
    return FileKeyStore(home)


def initialize(home,profiles,*,backend=None):
    from .keyring import initialize_keyring
    from .recovery import create_recovery_material
    from .storage.filesystem import FilesystemStorage
    if (home.root/'keystore.json').exists() or (home.root/'keystore.sealed').exists(): raise ValueError('already initialized')
    store=OsKeyStore(home,backend); state=BrokerState.create(profiles)
    storage=FilesystemStorage(home.root/'replica')
    initialize_keyring(state,storage)
    shares=create_recovery_material(state,storage)
    state.account=Account.from_public(state.account.public_record())
    store.save(state)
    if store.load().record()!=state.record(): raise ValueError('key store roundtrip failed')
    return shares


def migrate(home,*,backend=None,console):
    from .keyring import initialize_keyring
    from .recovery import create_recovery_material
    from .presence import PresenceManager
    from .storage.filesystem import FilesystemStorage
    plain=FileKeyStore(home)
    PresenceManager(home,select_store(home,backend)).require('device','migrate',console=console)
    if (home.root/'keystore.sealed').exists(): raise ValueError('already migrated')
    state=plain.load(); storage=FilesystemStorage(home.root/'replica')
    initialize_keyring(state,storage)
    shares=create_recovery_material(state,storage)
    state.account=Account.from_public(state.account.public_record())
    sealed=OsKeyStore(home,backend); sealed.save(state)
    if sealed.load().record()!=state.record(): raise ValueError('key store roundtrip failed')
    plain.path.unlink()
    return shares
