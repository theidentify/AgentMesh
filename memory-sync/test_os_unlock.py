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

def enabled(tmp_path, backend):
    directory, public = protected(tmp_path)
    raw = json.loads((directory / 'identity.json').read_text())
    secret = signed.Security(directory, unlock=lambda: PASS).key.private_bytes_raw()
    key = os.urandom(32)
    backend.store(public['key_id'], key)
    signed.write_local(directory / 'identity.json', {**raw, 'os_unlock': ou.seal(public, secret, backend.name, key)})
    backend.calls.clear()
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


@pytest.mark.parametrize('field,value', [('nonce', '00' * 12), ('backend', 'windows-credential')])
def test_cached_success_cannot_hide_tampering(tmp_path, field, value):
    backend = FakeBackend()
    directory, _ = enabled(tmp_path, backend)
    assert not signed.Security(directory, os_unlock=True, backend=backend).locked
    raw = json.loads((directory / 'identity.json').read_text())
    raw['os_unlock'][field] = value
    signed.write_local(directory / 'identity.json', raw)
    security = signed.Security(directory, os_unlock=True, backend=backend)
    assert security.locked
    assert backend.calls.count('load') == 1

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
    ota_update.release_key(directory)
    with pytest.raises((ValueError, FileNotFoundError)):
        ou.enable(directory, ask=lambda prompt: PASS, backend=FakeBackend())
    device, _ = protected(tmp_path)
    signed.write_local(device / 'identity.json', json.loads((directory / 'release-private.json').read_text()))
    with pytest.raises(ValueError, match='invalid private identity'):
        ou.enable(device, ask=lambda prompt: PASS, backend=FakeBackend())

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


def test_public_validation_never_reads_os_store(tmp_path, monkeypatch):
    import install_adopt, memory_sync
    from test_install_inspect import fixture_install
    runtime, db, exchange = fixture_install(tmp_path)
    backend = FakeBackend()
    directory, _ = enabled(tmp_path, backend)
    security = signed.Security(directory, unlock=lambda: PASS)
    memory_sync.cycle(db, exchange, security=security)
    monkeypatch.setattr(ou, 'current_backend', lambda: backend)
    config = json.loads(runtime.read_text())
    config['security_dir'] = str(directory)
    assert install_adopt.validate(config, runtime)['policy'] == 'required'
    assert backend.calls == []


def test_memory_sync_cli_uses_os_unlock(tmp_path, monkeypatch, capsys):
    import memory_sync
    from test_install_inspect import fixture_install
    _, db, exchange = fixture_install(tmp_path)
    backend = FakeBackend()
    directory, _ = enabled(tmp_path, backend)
    monkeypatch.setattr(ou, 'current_backend', lambda: backend)
    assert memory_sync.main(['once', str(db), str(exchange), '--security-dir', str(directory)]) == 0
    assert backend.calls == ['load']


def test_wizard_resume_prefers_os_then_falls_back(tmp_path, monkeypatch):
    import security_wizard
    from test_install_inspect import fixture_install
    _, db, exchange = fixture_install(tmp_path)
    backend = FakeBackend()
    directory, _ = enabled(tmp_path, backend)
    monkeypatch.setattr(ou, 'current_backend', lambda: backend)
    asked = []
    def ask():
        asked.append(True)
        return PASS
    state = tmp_path / 'wizard.json'
    security_wizard.resume(db, exchange, directory, state, unlock=ask)
    assert asked == [] and backend.calls == ['load']
    ou.reset_cache()
    backend.fail['load'] = ou.REFUSED
    security_wizard.resume(db, exchange, directory, state, unlock=ask)
    assert asked == [True]


def test_os_unlock_across_process_restarts(tmp_path):
    import importlib.util
    from pathlib import Path
    import sys
    module = tmp_path / 'fakestore.py'
    module.write_text('''import json
from pathlib import Path
import os_unlock as ou
class Backend:
    name = 'macos-keychain-file'
    def __init__(self, path): self.path = Path(path)
    def items(self): return json.loads(self.path.read_text()) if self.path.exists() else {}
    def store(self, key_id, secret):
        items = self.items(); items[key_id] = secret.hex()
        self.path.write_text(json.dumps(items)); self.path.chmod(0o600)
    def load(self, key_id):
        value = self.items().get(key_id)
        if value is None: raise ou.OSUnlockError(ou.MISSING) from None
        return bytes.fromhex(value)
    def delete(self, key_id):
        items = self.items(); items.pop(key_id, None)
        self.path.write_text(json.dumps(items))
''')
    spec = importlib.util.spec_from_file_location('fakestore', module)
    fake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake)
    store = tmp_path / 'store.json'
    backend = fake.Backend(store)
    directory, public = protected(tmp_path)
    ou.enable(directory, ask=lambda prompt: PASS, backend=backend)
    script = '''import sys
import fakestore, os_unlock, signed_packets
os_unlock.current_backend = lambda: fakestore.Backend(sys.argv[2])
try:
    signed_packets.Security(sys.argv[1], os_unlock=True).sign_bytes(b'probe')
except ValueError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
print('signed')
'''
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(Path(__file__).parent), str(tmp_path)])}
    def child():
        return subprocess.run([sys.executable, '-c', script, str(directory), str(store)],
                              env=env, capture_output=True, text=True, timeout=30)
    result = child()
    assert result.returncode == 0 and result.stdout.strip() == 'signed'
    ou.disable(directory, backend=backend)
    result = child()
    assert result.returncode == 1 and kp.LOCKED in result.stderr
    ou.enable(directory, ask=lambda prompt: PASS, backend=backend)
    backend.delete(public['key_id'])
    result = child()
    assert result.returncode == 1 and ou.MISSING in result.stderr
