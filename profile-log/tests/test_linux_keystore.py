import subprocess
import pytest
from agentmesh_log.oskeystore import LinuxSecretService, OSUnlockError, SERVICE

KEY = 'ab' * 16


class Fake:
    """secret-tool stand-in: an in-memory item table, recording argv and stdin."""
    def __init__(self): self.items, self.calls = {}, []
    def __call__(self, argv, input=None, **kw):
        self.calls.append((argv, input, kw['env']))
        action, attrs = argv[1], argv[2:]
        if action == 'store':
            self.items[tuple(attrs[2:])] = input; return subprocess.CompletedProcess(argv, 0, '', '')
        if action == 'lookup':
            value = self.items.get(tuple(attrs))
            return subprocess.CompletedProcess(argv, 0 if value else 1, value or '', '')
        self.items.pop(tuple(attrs), None); return subprocess.CompletedProcess(argv, 0, '', '')


def backend(fake, **env):
    return LinuxSecretService(tool='/usr/bin/secret-tool', runner=fake,
                              environ=dict(DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/1/bus', SECRET='x', **env))


def test_round_trip_secret_never_in_argv():
    fake = Fake(); b = backend(fake); secret = bytes(range(32))
    assert b.load(KEY) is None
    b.store(KEY, secret)
    assert b.load(KEY) == secret
    assert all(secret.hex() not in ' '.join(argv) for argv, _, _ in fake.calls)
    assert any(stdin == secret.hex() for _, stdin, _ in fake.calls)
    assert all('SECRET' not in env and env['DBUS_SESSION_BUS_ADDRESS'] for _, _, env in fake.calls)
    assert ['service', SERVICE, 'account', KEY] == fake.calls[-1][0][2:]
    b.delete(KEY); assert b.load(KEY) is None


def test_failures():
    fake = Fake(); b = backend(fake)
    fake.items[('service', SERVICE, 'account', KEY)] = 'not-hex'
    with pytest.raises(OSUnlockError): b.load(KEY)
    with pytest.raises(OSUnlockError): b.store(KEY, b'short')
    with pytest.raises(ValueError): b.load('bad-id')
    def slow(argv, **kw): raise subprocess.TimeoutExpired(argv, 15)
    with pytest.raises(OSUnlockError): backend(slow).load(KEY)
    with pytest.raises(OSUnlockError): LinuxSecretService(tool=None, runner=fake).load(KEY)
    assert not LinuxSecretService.available(environ={})
