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
            raise ou.OSUnlockError(self.fail[op]) from None
    def store(self, key_id, secret):
        self._maybe_fail('store'); self.items[key_id] = bytes(secret)
    def load(self, key_id):
        self._maybe_fail('load')
        if key_id not in self.items:
            raise ou.OSUnlockError(ou.MISSING) from None
        return self.items[key_id]
    def delete(self, key_id):
        self._maybe_fail('delete'); self.items.pop(key_id, None)


@pytest.fixture(autouse=True)
def fresh_cache():
    if hasattr(ou, "reset_cache"): ou.reset_cache()
    yield
    if hasattr(ou, "reset_cache"): ou.reset_cache()


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


def test_windows_target_validation():
    assert ou.WindowsCredential().target(KEY_ID) == ou.SERVICE + '/' + KEY_ID
    with pytest.raises(ou.OSUnlockError):
        ou.WindowsCredential().target('bad')


def test_mac_timeout_discards_secret_bearing_context(tmp_path):
    backend = ou.MacKeychainFile(keychain=tmp_path / 'k', runner=Run(
        subprocess.TimeoutExpired(['security'], 15, output='ab' * 32)))
    with pytest.raises(ou.OSUnlockError) as caught:
        backend.load(KEY_ID)
    assert caught.value.__context__ is None
