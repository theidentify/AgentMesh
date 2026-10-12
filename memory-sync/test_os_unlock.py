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
