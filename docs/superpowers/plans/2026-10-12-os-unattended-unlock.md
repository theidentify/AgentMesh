# OS-protected Unattended Unlock (#7) Implementation Plan

> **For agentic workers:** execute task by task, in order. Steps use checkbox (`- [ ]`) syntax. The spec is
> `docs/superpowers/specs/2026-10-12-os-unattended-unlock-design.md`; read it first. Where this plan and
> the spec disagree, the spec wins. Stop and report instead of guessing.

**Goal:** let the unattended worker unlock a passphrase-protected device key (#6) through an OS key store
(Mac Keychain via `/usr/bin/security`, Windows Credential Manager via ctypes). Fail closed on every error.

**Architecture:** a new module `memory-sync/os_unlock.py` owns the envelope (`os_unlock` field), the two
backends, a once-per-process result cache and the enable/disable/status operations.
`signed_packets.Security` gains an `os_unlock=True` opt-in that consults it. The worker and memory_sync opt
in, and the wizard opts in with a passphrase fallback. The CLI gets `identity-os-unlock`.

**Tech stack:** Python 3.11, the pinned `cryptography` (AESGCM), `subprocess` for `/usr/bin/security`,
`ctypes` for advapi32 Cred*W, pytest 8.4.2.

## Ground rules (read before any task)

- Work only in this worktree:
  `/Users/krisanathep/Workspaces/AgentMesh/.claude/worktrees/encrypted-key-storage-28470f`, on branch
  `feat/os-unlock`. Do not push, open PRs, merge, rebase or touch other branches or worktrees.
- **Never touch the real login keychain, real Credential Manager entries, or real identities.** Unit tests
  use `FakeBackend`. Native tests run only when `AGENTMESH_NATIVE_KEYCHAIN_TESTS=1` is set. They use a
  temporary keychain file (Mac) or a random target prefix (Windows) and clean up in `finally`. Do not set
  that variable on this Mac.
- Never put `K`, a passphrase or a private key in argv, environment variables, logs, exceptions or reports.
- Raise every `OSUnlockError` with `from None`. Messages come only from `REASONS`.
- No deprecated APIs: no `SecKeychain*`, `SecAccess*` or `SecKeychainSetUserInteractionAllowed`.
- Test command (run from `memory-sync/`):
  `UV_CACHE_DIR=/private/tmp/claude-501/uvcache uv run --no-project --with pytest==8.4.2 --with pytest-timeout --with-requirements requirements-security.txt python -m pytest -q -p no:cacheprovider --timeout 120 <files>`
  - The full suite has 3 known failures in `test_sqlite_consumers.py` (tmp-symlink). Every other test must
    pass.
- Commit after each task with a conventional message. End each commit message with
  `Co-Authored-By: GPT (Codex) <noreply@openai.com>`.
- Match the surrounding style: compact, few comments, comments only for non-obvious "why".

## File map

| File | Change |
|---|---|
| `memory-sync/os_unlock.py` | **new**: envelope, backends, cache, enable/disable/status |
| `memory-sync/test_os_unlock.py` | **new**: unit tests with FakeBackend |
| `memory-sync/test_native_os_unlock.py` | **new**: env-gated native backend tests |
| `memory-sync/signed_packets.py` | `Security(..., os_unlock=False, backend=None)`, `lock_reason`, accept `os_unlock` field |
| `memory-sync/key_protection.py` | reword `LOCKED` |
| `memory-sync/sync_worker.py`, `memory-sync/memory_sync.py` | `Security(..., os_unlock=True)` |
| `memory-sync/security_wizard.py` | `resume` passes `os_unlock=True` with the interactive unlock |
| `memory-sync/agentmesh.py` | `identity-os-unlock {enable,disable,status} --security-dir DIR` |
| `memory-sync/cli_errors.py` | new reasons, `identity-os-unlock` is a key action, trust `os_unlock` |
| `memory-sync/standalone_build.py` | hidden import `os_unlock` |
| `memory-sync/smoke_standalone.py` | frozen `identity-os-unlock --help` / `status` |
| `.github/workflows/standalone-smoke.yml` | run native tests with the env flag |
| `memory-sync/KEY-PROTECTION.md` | OS unlock section, threats, portability |
| `memory-sync/test_key_protection.py` | update for the new `LOCKED` text |

---

### Task 1: Envelope and validation (`os_unlock.py` core)

**Files:** create `memory-sync/os_unlock.py` and `memory-sync/test_os_unlock.py`.

- [ ] **Step 1: Write failing tests** in `memory-sync/test_os_unlock.py`:

```python
"""OS-protected unattended unlock (#7). Fake backend only; never the real keychain or Credential Manager."""
import json
import os

import pytest
import key_protection as kp
import os_unlock as ou
import signed_packets as signed

GROUP = '00000000-0000-4000-8000-000000000001'
PASS = 'correct horse battery staple'
FAST = {'n': 2 ** 14}


class FakeBackend:
    def __init__(self, name='macos-keychain-file'):
        self.name, self.items, self.calls, self.fail = name, {}, [], {}
    def _maybe_fail(self, op):
        self.calls.append(op)
        if op in self.fail:
            raise ou.OSUnlockError(self.fail[op])
    def store(self, key_id, secret):
        self._maybe_fail('store'); self.items[key_id] = bytes(secret)
    def load(self, key_id):
        self._maybe_fail('load')
        if key_id not in self.items:
            raise ou.OSUnlockError(ou.MISSING)
        return self.items[key_id]
    def delete(self, key_id):
        self._maybe_fail('delete'); self.items.pop(key_id, None)


@pytest.fixture(autouse=True)
def fresh_cache():
    ou.reset_cache()
    yield
    ou.reset_cache()


def protected(tmp_path):
    directory = tmp_path / 'identity'
    public = signed.init_identity(directory, GROUP, 'mac')
    kp.protect_identity(directory, lambda prompt: PASS, **FAST)
    return directory, public


def test_seal_and_open_roundtrip_binds_backend_and_public_fields(tmp_path):
    _, public = protected(tmp_path)
    secret, key = os.urandom(32), os.urandom(32)
    envelope = ou.seal(public, secret, 'windows-credential', key)
    assert set(envelope) == ou.FIELDS and envelope['backend'] == 'windows-credential'
    assert ou.open_envelope(public, envelope, key) == secret
    for changed in ({**envelope, 'backend': 'macos-keychain-file'}, ):
        with pytest.raises(ou.OSUnlockError, match='wrong key or damaged file'):
            ou.open_envelope(public, changed, key)
    with pytest.raises(ou.OSUnlockError, match='wrong key or damaged file'):
        ou.open_envelope(public, envelope, os.urandom(32))
    with pytest.raises(ou.OSUnlockError, match='wrong key or damaged file'):
        ou.open_envelope({**public, 'node': 'windows'}, envelope, key)


@pytest.mark.parametrize('change', [
    {'format': 'v0'}, {'backend': 'linux-secret-service'}, {'nonce': 'ab'},
    {'ciphertext': '00'}, {'extra': 1}])
def test_check_rejects_malformed_envelopes(tmp_path, change):
    _, public = protected(tmp_path)
    envelope = {**ou.seal(public, os.urandom(32), 'macos-keychain-file', os.urandom(32)), **change}
    with pytest.raises(ValueError, match='invalid OS unlock envelope'):
        ou.check(envelope)
```

- [ ] **Step 2: Run and confirm they fail.** Run `... python -m pytest -q test_os_unlock.py`. Expected:
  `ModuleNotFoundError: os_unlock`.

- [ ] **Step 3: Implement the core** in `memory-sync/os_unlock.py`:

```python
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
        raise OSUnlockError(REFUSED)
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
    except (InvalidTag, ValueError):
        raise OSUnlockError(DAMAGED) from None
```

- [ ] **Step 4: Run the tests and confirm they pass.** Run the same command. Expected: all pass.
- [ ] **Step 5: Commit**: `feat: os_unlock envelope bound to backend and public identity (#7)`.

---

### Task 2: Backends

**Files:** modify `memory-sync/os_unlock.py` and `memory-sync/test_os_unlock.py`.

- [ ] **Step 1: Write failing tests** for the Mac backend with an injected runner. No real `security` call
  is made.

```python
import subprocess


class Run:
    """Records commands; returns scripted CompletedProcess or raises."""
    def __init__(self, *results):
        self.results, self.calls = list(results), []
    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def done(code=0, out=''):
    return subprocess.CompletedProcess([], code, out, '')


KEY_ID = 'a' * 64


def test_mac_store_sends_secret_on_stdin_only_and_names_the_keychain(tmp_path):
    keychain = tmp_path / 'test.keychain-db'
    secret = bytes(range(32))
    run = Run(done(0, ''), done(0, secret.hex() + '\n'))
    backend = ou.MacKeychainFile(keychain=keychain, runner=run)
    backend.store(KEY_ID, secret)
    (store_cmd, store_kw), (load_cmd, _) = run.calls
    assert store_cmd == ['/usr/bin/security', '-i'] and secret.hex() not in ' '.join(store_cmd)
    assert secret.hex() in store_kw['input'] and str(keychain) in store_kw['input']
    assert '-A' not in store_kw['input'].split() and '-T' not in store_kw['input'].split()
    assert store_kw['env'] == {'PATH': '/usr/bin:/bin', 'HOME': ou.home(), 'LANG': 'C'}
    assert store_kw['timeout'] == ou.TIMEOUT
    assert load_cmd[-1] == str(keychain) and secret.hex() not in ' '.join(load_cmd)


def test_mac_store_trusts_only_the_load_back(tmp_path):
    run = Run(done(0, ''), done(0, 'b' * 64 + '\n'))
    with pytest.raises(ou.OSUnlockError, match='refused access'):
        ou.MacKeychainFile(keychain=tmp_path / 'k', runner=run).store(KEY_ID, bytes(32))


@pytest.mark.parametrize('result,reason', [
    (done(44), ou.MISSING), (done(36), ou.REFUSED), (done(51), ou.REFUSED),
    (done(0, 'not hex\n'), ou.REFUSED),
    (subprocess.TimeoutExpired(['security'], 15, output='ab' * 32), ou.NO_RESPONSE)])
def test_mac_load_maps_failures_to_fixed_reasons_without_chaining(tmp_path, result, reason):
    backend = ou.MacKeychainFile(keychain=tmp_path / 'k', runner=Run(result))
    with pytest.raises(ou.OSUnlockError) as caught:
        backend.load(KEY_ID)
    assert str(caught.value) == reason
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert 'ab' * 32 not in repr(caught.value) and 'ab' * 32 not in str(caught.value)


def test_mac_delete_treats_missing_as_success_and_other_failures_as_not_removed(tmp_path):
    ou.MacKeychainFile(keychain=tmp_path / 'k', runner=Run(done(44))).delete(KEY_ID)
    with pytest.raises(ou.OSUnlockError, match='could not be removed'):
        ou.MacKeychainFile(keychain=tmp_path / 'k', runner=Run(done(51))).delete(KEY_ID)


@pytest.mark.parametrize('bad', ['A' * 64, 'a' * 63, 'a' * 63 + '"', 'a' * 32 + '\n' + 'a' * 31])
def test_backend_rejects_unsafe_key_ids_before_running_anything(tmp_path, bad):
    run = Run()
    with pytest.raises(ou.OSUnlockError):
        ou.MacKeychainFile(keychain=tmp_path / 'k', runner=run).load(bad)
    assert run.calls == []


def test_mac_rejects_unquotable_keychain_paths(tmp_path):
    with pytest.raises(ou.OSUnlockError):
        ou.MacKeychainFile(keychain=tmp_path / 'a"b', runner=Run()).store(KEY_ID, bytes(32))


def test_current_backend_by_platform(monkeypatch):
    monkeypatch.setattr(ou.sys, 'platform', 'linux')
    assert ou.current_backend() is None
    monkeypatch.setattr(ou.sys, 'platform', 'darwin')
    assert ou.current_backend().name == 'macos-keychain-file'
```

- [ ] **Step 2: Run and confirm they fail.**

- [ ] **Step 3: Implement** in `os_unlock.py`:

```python
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
        if any(c in self.keychain for c in '"\\\n\r'):
            raise OSUnlockError(REFUSED) from None
        try:
            return self.runner(args, input=stdin, capture_output=True, text=True, timeout=TIMEOUT,
                               env={'PATH': '/usr/bin:/bin', 'HOME': home(), 'LANG': 'C'})
        except subprocess.TimeoutExpired:
            raise OSUnlockError(NO_RESPONSE) from None
        except OSError:
            raise OSUnlockError(REFUSED) from None

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
        value = result.stdout.strip() if result.returncode == 0 else ''
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
```

- [ ] **Step 4: Add the Windows backend** in the same file. Unit tests can't call it on a Mac, so cover only
  `target()` naming in a unit test. Task 9 covers it natively.

```python
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
        ctypes, _, advapi = self._api()
        if not advapi.CredDeleteW(self.target(key_id), 1, 0) and ctypes.get_last_error() != self.ERROR_NOT_FOUND:
            raise OSUnlockError(NOT_REMOVED) from None
```

  Add the unit test:
  `assert ou.WindowsCredential().target(KEY_ID) == 'agentmesh.identity-unlock/' + KEY_ID`, plus a bad
  key_id that raises.

- [ ] **Step 5: Run the tests and confirm they pass.** Then commit:
  `feat: Mac keychain and Windows credential backends for OS unlock (#7)`.

---

### Task 3: Security integration and the once-per-process cache

**Files:** modify `memory-sync/os_unlock.py`, `memory-sync/signed_packets.py:252-282`,
`memory-sync/key_protection.py` (`LOCKED`), `memory-sync/cli_errors.py` (`KEY_REASONS`),
`memory-sync/test_key_protection.py` and `memory-sync/test_os_unlock.py`.

- [ ] **Step 1: Write failing tests:**

```python
def enabled(tmp_path, backend):
    directory, public = protected(tmp_path)
    ou.enable(directory, ask=lambda prompt: PASS, backend=backend)
    ou.reset_cache()
    return directory, public


def test_worker_path_unlocks_through_the_os_store_only(tmp_path):
    backend = FakeBackend()
    directory, public = enabled(tmp_path, backend)
    security = signed.Security(directory, os_unlock=True, backend=backend)
    assert not security.locked and security.public == public
    security.sign_bytes(b'probe')
    assert signed.Security(directory).locked  # without the opt-in there is no OS call


def test_missing_item_locks_with_the_specific_reason_and_is_asked_once_per_process(tmp_path):
    backend = FakeBackend()
    directory, _ = enabled(tmp_path, backend)
    backend.items.clear()
    for _ in range(3):
        security = signed.Security(directory, os_unlock=True, backend=backend)
        assert security.locked and security.lock_reason == ou.MISSING
        with pytest.raises(ValueError, match=ou.MISSING):
            security.require_unlocked()
    assert backend.calls.count('load') == 1


def test_wrong_key_is_reported_as_damage_not_skipped(tmp_path):
    backend = FakeBackend()
    directory, _ = enabled(tmp_path, backend)
    key_id = next(iter(backend.items))
    backend.items[key_id] = os.urandom(32)
    security = signed.Security(directory, os_unlock=True, backend=backend)
    assert security.locked and security.lock_reason == ou.DAMAGED


def test_wizard_falls_back_to_the_passphrase_when_the_os_path_fails(tmp_path):
    backend = FakeBackend()
    directory, _ = enabled(tmp_path, backend)
    backend.fail['load'] = ou.REFUSED
    assert not signed.Security(directory, unlock=lambda: PASS, os_unlock=True, backend=backend).locked


def test_backend_of_another_os_is_locked_and_unknown_backend_is_invalid(tmp_path):
    backend = FakeBackend('windows-credential')
    directory, _ = enabled(tmp_path, backend)
    other = FakeBackend('macos-keychain-file')
    other.items.update(backend.items)
    security = signed.Security(directory, os_unlock=True, backend=other)
    assert security.locked and security.lock_reason == ou.UNSUPPORTED and other.calls == []
    data = json.loads((directory / 'identity.json').read_text())
    data['os_unlock']['backend'] = 'linux-secret-service'
    signed.write_local(directory / 'identity.json', data)
    with pytest.raises(ValueError, match='invalid'):
        signed.Security(directory, os_unlock=True, backend=other)


def test_os_unlock_without_protection_is_invalid(tmp_path):
    backend = FakeBackend()
    directory, _ = enabled(tmp_path, backend)
    data = json.loads((directory / 'identity.json').read_text())
    del data['protection']
    signed.write_local(directory / 'identity.json', data)
    with pytest.raises(ValueError, match='invalid private identity'):
        signed.Security(directory, os_unlock=True, backend=backend)
```

  (`enable` comes in Task 4. Write these tests now, and expect them to fail until Task 4 lands. Run only
  the Task 3 subset with `-k` once Task 4 exists, or write Task 3 and Task 4 together. Either is fine; the
  commit after Task 4 must have everything green.)

- [ ] **Step 2: Implement the cache and unlock helper** in `os_unlock.py`:

```python
_RESULTS = {}  # (directory, key_id, ciphertext) -> private key bytes or refusal reason; once per process


def reset_cache():
    _RESULTS.clear()


def unlock(directory, public, envelope, backend=None, *, cache=True):
    """Return the private key bytes or raise OSUnlockError. Asks the OS store at most once per process."""
    check(envelope)
    backend = backend if backend is not None else current_backend()
    if backend is None or backend.name != envelope['backend']:
        raise OSUnlockError(UNSUPPORTED) from None
    entry = (str(directory), public['key_id'], envelope['ciphertext'])
    if cache and entry in _RESULTS:
        result = _RESULTS[entry]
        if isinstance(result, str):
            raise OSUnlockError(result) from None
        return result
    try:
        result = open_envelope(public, envelope, backend.load(public['key_id']))
    except OSUnlockError as exc:
        result = str(exc)
    if cache:
        _RESULTS[entry] = result
    if isinstance(result, str):
        raise OSUnlockError(result) from None
    return result
```

- [ ] **Step 3: Change `Security.__init__`** (signed_packets.py). Accept the three shapes:
  `PUBLIC|{private_key}`, `PUBLIC|{protection}` and `PUBLIC|{protection, os_unlock}`. Add the keyword-only
  parameters `os_unlock=False, backend=None` and the attribute `self.lock_reason = None`.

```python
    def __init__(self, directory, unlock=None, *, os_unlock=False, backend=None):
        ...
        shapes = (PUBLIC_FIELDS | {'private_key'}, PUBLIC_FIELDS | {'protection'},
                  PUBLIC_FIELDS | {'protection', 'os_unlock'})
        if type(raw) is not dict or set(raw) not in shapes:
            raise ValueError('invalid private identity')
        self.public = check_public({k: raw[k] for k in PUBLIC_FIELDS})
        self.key, self.locked, self.lock_reason = None, 'protection' in raw, None
        if self.locked:
            import key_protection
            key_protection.check(raw['protection'])
            secret = None
            if 'os_unlock' in raw:
                import os_unlock as osu
                osu.check(raw['os_unlock'])
                if os_unlock:
                    try:
                        secret = osu.unlock(self.directory, self.public, raw['os_unlock'], backend)
                    except osu.OSUnlockError as exc:
                        self.lock_reason = str(exc)
            if secret is None and unlock is not None:
                secret = key_protection.open_sealed(self.public, raw['protection'], unlock())
            if secret is not None:
                self.key, self.locked, self.lock_reason = Private.from_private_bytes(secret), False, None
        else:
            ...unchanged...
```

  In `require_unlocked`: `raise ValueError(self.lock_reason or key_protection.LOCKED)`.

- [ ] **Step 4: Reword `LOCKED`.** In `key_protection.py`, set
  `LOCKED = 'passphrase-protected identity is locked; enable OS unlock or unlock interactively'`. Update
  the matching `KEY_REASONS` entry in `cli_errors.py`, and every test that matches the old text: grep for
  `unattended unlock is not available` and for `passphrase-protected identity is locked`. Add all of
  `os_unlock.REASONS` to `KEY_REASONS` as `KEY_REASONS = frozenset({...}) | os_unlock.REASONS` (a
  top-level `import os_unlock` is fine, since it only imports signed_packets). Add `'os_unlock'` to
  `TRUSTED_MODULES`.

- [ ] **Step 5:** Run `test_os_unlock.py test_key_protection.py test_signed_packets.py` and expect green
  once Task 4 is in. Commit together with Task 4.

---

### Task 4: enable / disable / status

**Files:** modify `memory-sync/os_unlock.py` and `memory-sync/test_os_unlock.py`.

- [ ] **Step 1: Write failing tests:**

```python
def test_enable_keeps_public_identity_and_passphrase_path(tmp_path):
    backend = FakeBackend()
    directory, public = protected(tmp_path)
    before = json.loads((directory / 'identity.json').read_text())
    report = ou.enable(directory, ask=lambda prompt: PASS, backend=backend)
    assert report == {'status': 'enabled', 'key_id': public['key_id'], 'backend': backend.name}
    after = json.loads((directory / 'identity.json').read_text())
    assert after['protection'] == before['protection'] and set(after) == set(before) | {'os_unlock'}
    assert not signed.Security(directory, unlock=lambda: PASS).locked
    stored = (directory / 'identity.json').read_text()
    assert backend.items[public['key_id']].hex() not in stored and PASS not in json.dumps(report)


def test_enable_refuses_raw_identity_twice_enabled_and_wrong_passphrase(tmp_path):
    backend = FakeBackend()
    raw_dir = tmp_path / 'raw'
    signed.init_identity(raw_dir, GROUP, 'mac')
    with pytest.raises(ValueError, match='must be passphrase-protected'):
        ou.enable(raw_dir, ask=lambda prompt: PASS, backend=backend)
    directory, _ = protected(tmp_path)
    with pytest.raises(ValueError, match='wrong passphrase'):
        ou.enable(directory, ask=lambda prompt: 'wrong passphrase!!', backend=backend)
    assert backend.items == {}
    ou.enable(directory, ask=lambda prompt: PASS, backend=backend)
    with pytest.raises(ValueError, match='already enabled'):
        ou.enable(directory, ask=lambda prompt: PASS, backend=backend)


def test_enable_removes_its_item_when_the_replace_never_happened(tmp_path, monkeypatch):
    backend = FakeBackend()
    directory, _ = protected(tmp_path)
    before = (directory / 'identity.json').read_bytes()
    def crash(source, target, **kwargs):
        raise OSError('fixture failure before replace')
    monkeypatch.setattr(signed, 'replace', crash)
    with pytest.raises(OSError):
        ou.enable(directory, ask=lambda prompt: PASS, backend=backend)
    assert (directory / 'identity.json').read_bytes() == before and backend.items == {}


def test_enable_keeps_the_item_when_the_file_was_replaced_before_the_error(tmp_path, monkeypatch):
    backend = FakeBackend()
    directory, _ = protected(tmp_path)
    original = signed.write_local
    def replaced_then_failed(path, value, **kwargs):
        original(path, value, **kwargs)
        raise OSError('fixture directory fsync failure after replace')
    monkeypatch.setattr(signed, 'write_local', replaced_then_failed)
    with pytest.raises(OSError):
        ou.enable(directory, ask=lambda prompt: PASS, backend=backend)
    monkeypatch.setattr(signed, 'write_local', original)
    ou.reset_cache()
    assert not signed.Security(directory, os_unlock=True, backend=backend).locked


def test_concurrent_enable_is_refused(tmp_path):
    from runtime_lock import lock
    backend = FakeBackend()
    directory, _ = protected(tmp_path)
    with lock(directory / '.os-unlock.lock', timeout=0):
        with pytest.raises(ValueError, match='already in progress'):
            ou.enable(directory, ask=lambda prompt: PASS, backend=backend)
    assert backend.items == {}


def test_disable_removes_envelope_then_item_and_reports_leftovers(tmp_path):
    backend = FakeBackend()
    directory, public = enabled(tmp_path, backend)
    backend.fail['delete'] = ou.NOT_REMOVED
    with pytest.raises(ValueError, match='could not be removed'):
        ou.disable(directory, backend=backend)
    assert 'os_unlock' not in json.loads((directory / 'identity.json').read_text())
    assert ou.status(directory, backend=backend)['stray_item'] is True
    del backend.fail['delete']
    assert ou.disable(directory, backend=backend) == {'status': 'disabled', 'key_id': public['key_id']}
    assert backend.items == {} and ou.status(directory, backend=backend)['stray_item'] is False


def test_status_is_read_only_and_never_prints_secrets(tmp_path):
    backend = FakeBackend()
    directory, public = enabled(tmp_path, backend)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    report = ou.status(directory, backend=backend)
    assert report == {'key_id': public['key_id'], 'protected': True, 'os_unlock': 'enabled',
                      'backend': backend.name, 'available': True, 'reason': None, 'stray_item': False}
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    assert backend.items[public['key_id']].hex() not in json.dumps(report)
    backend.items.clear()
    assert ou.status(directory, backend=backend)['reason'] == ou.MISSING


def test_ota_release_keys_never_reach_os_unlock(tmp_path):
    import ota_update
    source = open(ota_update.__file__, encoding='utf-8').read()
    assert 'os_unlock' not in source
    directory = tmp_path / 'release'
    directory.mkdir(mode=0o700)
    ota_update.release_key(directory)
    with pytest.raises(ValueError):
        ou.enable(directory, ask=lambda prompt: PASS, backend=FakeBackend())
```

  Check the `ota_update.release_key(directory)` signature before using it. If it needs other arguments,
  adapt the call; the intent is "a release key directory is refused".

- [ ] **Step 2: Implement** in `os_unlock.py`:

```python
LOCK_NAME = '.os-unlock.lock'


def _locked(directory):
    from runtime_lock import lock
    try:
        return lock(signed.private_directory(directory) / LOCK_NAME, timeout=0)
    except TimeoutError:
        raise OSUnlockError(BUSY) from None


def _raw(directory):
    return signed.parse(signed.read_local(signed.private_directory(directory) / 'identity.json', private=True))


def enable(directory, ask=None, backend=None):
    import key_protection
    ask = ask or key_protection.console_passphrase
    backend = backend if backend is not None else current_backend()
    if backend is None:
        raise OSUnlockError(UNSUPPORTED)
    with _locked(directory):
        current = signed.Security(directory)
        raw = _raw(directory)
        if 'protection' not in raw:
            raise OSUnlockError(NOT_PROTECTED)
        if 'os_unlock' in raw:
            raise OSUnlockError(ENABLED)
        unlocked = signed.Security(directory, unlock=lambda: ask('Identity passphrase: '))
        private_bytes = unlocked.key.private_bytes_raw()
        key = os.urandom(32)
        envelope = seal(current.public, private_bytes, backend.name, key)
        backend.store(current.public['key_id'], key)
        path = current.directory / 'identity.json'
        try:
            if open_envelope(current.public, envelope, backend.load(current.public['key_id'])) != private_bytes:
                raise OSUnlockError(REFUSED)
            signed.write_local(path, {**raw, 'os_unlock': envelope})
        except BaseException:
            try:
                on_disk = _raw(directory).get('os_unlock')
            except Exception:
                on_disk = None
            if on_disk != envelope:
                backend.delete(current.public['key_id'])
            raise
        reset_cache()
        reread = signed.Security(directory, os_unlock=True, backend=backend)
        if reread.locked or reread.key.private_bytes_raw() != private_bytes:
            raise OSUnlockError(DAMAGED)
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
            raise OSUnlockError(UNSUPPORTED)
        backend.delete(current.public['key_id'])  # also clears a leftover item from an earlier failure
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
```

  Notes:
  - `runtime_lock.lock(..., timeout=0)` may raise something other than `TimeoutError` when the lock is
    held. Read `runtime_lock.py` and catch exactly what it raises. Also note that `lock` is a
    contextmanager, so the raise happens at `__enter__`. Wrap it so `with _locked(directory):` produces
    `OSUnlockError(BUSY)`. For example, make `_locked` a `@contextmanager` that does
    `try: cm = lock(...); cm.__enter__() except <exc>: raise OSUnlockError(BUSY) from None`, yields, and
    exits `cm`.
  - The wrong-passphrase test expects the #6 message `wrong passphrase or damaged file`, which comes
    from `key_protection.open_sealed`.
  - The lock file lives in the security directory. Check that `Security`, the readers of `trust.json`
    and the #6 interrupted-replace test (which lists the directory) tolerate an extra `.os-unlock.lock`.
    If `protect_identity` tests list exact directory contents, they are unaffected, because they never
    call enable.

- [ ] **Step 3:** Run `test_os_unlock.py test_key_protection.py test_signed_packets.py` and expect all
  green.
- [ ] **Step 4: Commit**: `feat: enable/disable/status for OS unlock with lock and crash-safe cleanup (#7)`.

---

### Task 5: Callers and CLI

**Files:** modify `memory-sync/sync_worker.py:30`, `memory-sync/memory_sync.py:424`,
`memory-sync/security_wizard.py:280`, `memory-sync/agentmesh.py`, `memory-sync/cli_errors.py:144` and
`memory-sync/test_os_unlock.py`.

- [ ] **Step 1: Write failing tests:**

```python
def test_worker_and_memory_sync_opt_into_os_unlock(tmp_path, monkeypatch):
    import sync_worker, memory_sync, sqlite_memory
    backend = FakeBackend()
    directory, _ = enabled(tmp_path, backend)
    monkeypatch.setattr(ou, 'current_backend', lambda: backend)
    db = tmp_path / 'mac.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', GROUP)
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    sync_worker.run_once(db, exchange, security_dir=directory)   # unlocked through the OS: no refusal
    backend.items.clear(); ou.reset_cache()
    with pytest.raises(ValueError, match=ou.MISSING):
        sync_worker.run_once(db, exchange, security_dir=directory)


def test_cli_status_and_errors_are_fixed_and_secret_free(tmp_path, monkeypatch, capsys):
    import agentmesh
    backend = FakeBackend()
    directory, public = enabled(tmp_path, backend)
    monkeypatch.setattr(ou, 'current_backend', lambda: backend)
    assert agentmesh.main(['identity-os-unlock', 'status', '--security-dir', str(directory)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['available'] is True and backend.items[public['key_id']].hex() not in json.dumps(report)
    backend.fail['delete'] = ou.NOT_REMOVED
    assert agentmesh.main(['identity-os-unlock', 'disable', '--security-dir', str(directory)]) == 1
    assert json.loads(capsys.readouterr().err)['reason'] == ou.NOT_REMOVED
```

  If `run_once` against a strict-policy-less (legacy) DB doesn't call `Security`, check how
  `test_key_protection.test_worker_refuses_a_locked_identity_before_any_write` triggers it and mirror
  that. `run_once` builds `Security` whenever `security_dir` is given.

- [ ] **Step 2: Implement:**
  - `sync_worker.py:30` → `Security(security_dir, os_unlock=True).require_unlocked()`.
  - `memory_sync.py:424` → `Security(args.security_dir, os_unlock=True).require_unlocked()`.
  - `security_wizard.py:280` → `signed.Security(security_dir, unlock=unlock, os_unlock=True)`.
  - Do **not** change `install_adopt.py:66`, `security_wizard.py:245/367` or worker_diagnostics. Those are
    public checks and must never call the OS store. Add a test asserting `install_adopt.validate` on an
    enabled identity makes zero backend calls (monkeypatch `current_backend` to the FakeBackend and check
    `backend.calls == []`).
  - In `agentmesh.py`, next to `identity-protect`:

```python
    os_unlock_parser = subs.add_parser('identity-os-unlock', help='enable, disable or inspect OS-protected unattended unlock')
    os_unlock_parser.add_argument('mode', choices=('enable', 'disable', 'status'))
    os_unlock_parser.add_argument('--security-dir', required=True, help='existing private identity directory')
```

    and in the dispatch:

```python
        if args.action == 'identity-os-unlock':
            import os_unlock
            report = getattr(os_unlock, args.mode)(args.security_dir)
            print(json.dumps(report, sort_keys=True))
            return 0
```

  - In `cli_errors.py:144`: `is_key = action in ('identity-protect', 'identity-os-unlock')`.
- [ ] **Step 3:** Run `test_os_unlock.py test_key_protection.py test_agentmesh.py test_security_wizard.py test_wizard_resume.py`
  and expect green.
- [ ] **Step 4: Commit**: `feat: worker, wizard and CLI use OS unlock (#7)`.

---

### Task 6: Restart behaviour (subprocess)

**Files:** modify `memory-sync/test_os_unlock.py`.

- [ ] **Step 1: Write the test.** A fresh Python process can't see the FakeBackend, so use a file-backed
  fake that is importable in the child:
  - Write a tiny module into `tmp_path`: `fakestore.py` with a `Backend` class that keeps items in a JSON
    file under `tmp_path`.
  - Run the child with `PYTHONPATH=<memory-sync>:<tmp_path>`. In the child, before constructing
    `Security`, set `os_unlock.current_backend = lambda: fakestore.Backend(path)`.
  - Assert:
    1. A child process unlocks through the OS path and signs (exit 0, prints `signed`).
    2. After `ou.disable(...)` in the parent (same file store), a new child is locked: `sign_bytes`
       raises, exit 1, and stderr contains `LOCKED`.
    3. After re-enabling and then deleting the store entry, a new child is locked with `MISSING`.
- [ ] **Step 2:** Run it and expect pass. Commit: `test: OS unlock across process restarts (#7)`.

---

### Task 7: Native backend tests (env-gated)

**Files:** create `memory-sync/test_native_os_unlock.py`.

- [ ] **Step 1: Write the tests.** Skip everything unless `os.environ.get('AGENTMESH_NATIVE_KEYCHAIN_TESTS') == '1'`.

  **Mac** (skip unless `sys.platform == 'darwin'`), with a fixture:
  1. Save the user keychain search list:
     `subprocess.run(['/usr/bin/security', 'list-keychains', '-d', 'user'], capture_output=True, text=True)`.
  2. Create `tmp_path/'agentmesh-test.keychain-db'` with
     `security create-keychain -p <random 32 hex> <path>` and unlock it with
     `security unlock-keychain -p <pw> <path>`. Test-only throwaway password; argv is acceptable for a
     disposable CI keychain.
  3. In `finally`: `security delete-keychain <path>`, then restore the search list with
     `security list-keychains -d user -s <saved paths...>`.

  Tests:
  - Store, load and delete round trip; a second load gives `MISSING`.
  - `store` with `-U` replaces the value.
  - Locked keychain (`security lock-keychain <path>`): `load` raises `REFUSED` or `NO_RESPONSE` within
    `TIMEOUT + 5` seconds, measured with `time.monotonic()`. Record which one in the assertion message.
  - Missing item → `MISSING`.

  **Windows** (skip unless `os.name == 'nt'`): `WindowsCredential(prefix='agentmesh-test-' + uuid4().hex + '/')`.
  - Round trip; missing after delete → `MISSING`; deleting twice is fine.
  - If the first `store` raises `REFUSED` (no logon session on the runner), `pytest.skip` with the reason
    "Credential Manager unavailable in this CI session (recorded)". That still exercises the refusal
    mapping.
  - `finally`: delete.

- [ ] **Step 2: Wire into CI.** In `.github/workflows/standalone-smoke.yml`, add a step after "Test build
  and inspection contracts":

```yaml
      - name: Verify OS unlock backends on a disposable keychain / credential
        env:
          AGENTMESH_NATIVE_KEYCHAIN_TESTS: '1'
        run: python -m pytest -q -rs memory-sync/test_native_os_unlock.py memory-sync/test_os_unlock.py
```

- [ ] **Step 3:** Locally (Mac, flag NOT set), run `test_native_os_unlock.py` and expect everything
  skipped. Commit: `test: native OS unlock backends on disposable stores (#7)`.

---

### Task 8: Frozen build and smoke

**Files:** modify `memory-sync/standalone_build.py` (HIDDEN_MODULES) and `memory-sync/smoke_standalone.py`.

- [ ] **Step 1:** Add `'os_unlock'` after `'key_protection'` in `HIDDEN_MODULES`.
- [ ] **Step 2:** In `smoke_standalone.py`, after the #6 identity-protect block (search for
  `locked_identity`), add:

```python
        assert 'identity-os-unlock' in run('--help')
        assert 'status' in run('identity-os-unlock', '--help')
        frozen_status = json.loads(run('identity-os-unlock', 'status', '--security-dir', locked_identity))
        assert frozen_status['protected'] is True and frozen_status['os_unlock'] == 'absent'
        assert {p.name: p.read_bytes() for p in locked_identity.iterdir()} == locked_before
```

  `status` with `os_unlock` absent calls `backend.load` to detect a leftover item. On the CI Mac that is
  the runner's login keychain, read-only and for a random key_id, which returns `MISSING`. That is
  acceptable: it's a read, never a write. On Windows it's `CredReadW`, also read-only. Do **not** run
  `enable` in the frozen smoke against the real login keychain.
- [ ] **Step 3:** Run `test_standalone_build.py` and expect green. Commit:
  `build: freeze os_unlock and smoke identity-os-unlock (#7)`.

---

### Task 9: Documentation

**Files:** modify `memory-sync/KEY-PROTECTION.md`.

- [ ] Add a section `## OS-protected unattended unlock (#7)` covering:
  - The commands `identity-os-unlock enable|disable|status`, and when to use them. Enable only on a host
    whose worker runs as the same user (Mac LaunchAgent `gui/<uid>`, Windows logon task LogonType 3).
  - The format (`os_unlock`) and the backends: the Mac interim `macos-keychain-file` (Apple TN3137: the
    file-based keychain is "on the road to deprecation") and Windows `windows-credential`.
  - The threats and limits, copied faithfully from the spec's "Threats and limits" section:
    - same-user readable
    - backups carry `K`, so the backup is only as strong as the weaker of the passphrase and the login
      password
    - a possible macOS dialog, at most once per worker process, with a 15-second cap
    - leftover items for rotated key_ids
    - the SecItem upgrade must re-create the item
  - Every reason string from `os_unlock.REASONS` and what the operator should do for each.
- Update the existing "Worker and restart behaviour" bullets. Replace "Unattended unlock ... is issue #7.
  Until it exists ..." with the new behaviour and the new `LOCKED` text.
- Commit: `docs: OS unlock operation, threats and portability (#7)`.

---

### Task 10: Full verification

- [ ] Run the full suite from `memory-sync/` with the test command and expect only the 3 known
  `test_sqlite_consumers` failures.
- [ ] Run `git grep -n "SecKeychain\|SecAccess\|SetUserInteractionAllowed" -- memory-sync` and expect no
  matches.
- [ ] Run `git grep -n "\-A\b" memory-sync/os_unlock.py` and expect no `-A` passed to security.
- [ ] Write a short report to `docs/superpowers/plans/2026-10-12-os-unattended-unlock.REPORT.md` with:
  - the commits
  - test counts
  - anything deviating from this plan and why
  - open questions
  
  Do not push.
