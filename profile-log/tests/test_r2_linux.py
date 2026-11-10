"""R2 (and other S3) credentials in the Linux Secret Service: `secret:` references, secrets over stdin only."""
import functools
import pytest
from agentmesh_log.storage import r2creds, StorageError

ID, SECRET = 'a' * 32, 'b' * 64


class FakeSecretTool:
    """secret-tool's store/lookup/clear over a dict; records every argv to prove no secret is on it."""
    def __init__(self): self.items, self.argvs = {}, []

    def __call__(self, argv, input=None, **kw):
        self.argvs.append(argv)
        attrs = argv[argv.index('service'):]
        key = (attrs[1], attrs[3])
        class Result: returncode, stdout = 0, ''
        result = Result()
        if argv[1] == 'store': self.items[key] = input
        elif argv[1] == 'lookup':
            if key in self.items: result.stdout = self.items[key] + '\n'
            else: result.returncode = 1
        elif argv[1] == 'clear': self.items.pop(key, None)
        return result


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(r2creds.sys, 'platform', 'linux')
    tool = FakeSecretTool()
    monkeypatch.setattr(r2creds, 'LinuxR2Credentials',
                        functools.partial(r2creds.LinuxR2Credentials, runner=tool, tool='/usr/bin/secret-tool',
                                          environ={'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1/bus'}))
    return tool


def test_store_load_delete(linux):
    creds = r2creds.LinuxR2Credentials()
    creds.store('org.agentmesh.r2', ID, SECRET)
    assert r2creds.load_credentials('secret:org.agentmesh.r2') == (ID, SECRET)
    assert all(SECRET not in ' '.join(argv) and ID not in ' '.join(argv) for argv in linux.argvs)   # stdin only
    assert linux.items[('org.agentmesh.r2', 'access-key-id')] == ID
    creds.delete('org.agentmesh.r2')
    with pytest.raises(StorageError, match='missing'): r2creds.load_credentials('secret:org.agentmesh.r2')


def test_refusals(linux):
    creds = r2creds.LinuxR2Credentials()
    with pytest.raises(StorageError): creds.store('org.agentmesh.r2', 'not-hex', SECRET)
    with pytest.raises(StorageError): creds.store('bad service!', ID, SECRET)
    linux.items[('org.agentmesh.r2', 'access-key-id')] = ID
    linux.items[('org.agentmesh.r2', 'secret-access-key')] = 'short'
    with pytest.raises(StorageError, match='refused'): r2creds.load_credentials('secret:org.agentmesh.r2')
    with pytest.raises(StorageError): r2creds.load_credentials('keychain:org.agentmesh.r2')   # wrong store for Linux


def test_other_kinds(linux):
    creds = r2creds.LinuxR2Credentials(kind='access')
    creds.store('org.agentmesh.embed', 'c' * 32 + '.access', 'd' * 43)
    assert r2creds.load_credentials('secret:org.agentmesh.embed', kind='access') == ('c' * 32 + '.access', 'd' * 43)


def test_cli_store_and_check(linux):
    answers = iter((ID, SECRET))
    assert r2creds.credentials_command('store', 'secret:org.agentmesh.r2', getpass_fn=lambda _: next(answers)) == \
        {'ok': True, 'ref': 'secret:org.agentmesh.r2'}
    assert r2creds.credentials_command('check', 'secret:org.agentmesh.r2')['ok'] is True
    assert r2creds.credentials_command('check', 'secret:org.agentmesh.none') == {'ok': False, 'ref': 'secret:org.agentmesh.none', 'status': 'missing'}
    assert r2creds.credentials_command('check', 'wincred:org.agentmesh.r2') == {'ok': False, 'status': 'refused'}


def test_secret_refs_valid_everywhere():
    r2creds.validate_remote('secret:org.agentmesh.r2', 'https://acct.r2.cloudflarestorage.com')
    import re
    assert re.fullmatch(r2creds.REF, 'secret:org.agentmesh.r2') and not re.fullmatch(r2creds.REF, 'file:/tmp/x')
