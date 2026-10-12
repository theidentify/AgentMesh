"""CI-only native store fixture; never used by the application backend selector."""
from contextlib import contextmanager
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid

import os_unlock as ou


def security(args, stdin=None):
    result = None
    try:
        result = subprocess.run(['/usr/bin/security', *args], input=stdin, capture_output=True,
                                text=True, timeout=ou.TIMEOUT,
                                env={'PATH': '/usr/bin:/bin', 'HOME': ou.home(), 'LANG': 'C'})
    except (OSError, subprocess.SubprocessError):
        pass
    if result is None or result.returncode:
        raise RuntimeError('disposable keychain fixture command failed') from None
    return result.stdout


@contextmanager
def disposable_store(directory):
    if os.environ.get('AGENTMESH_NATIVE_KEYCHAIN_TESTS') != '1':
        raise RuntimeError('native credential tests require explicit opt-in')
    if sys.platform == 'darwin':
        keychain = Path(directory).resolve() / 'agentmesh-test.keychain-db'
        if keychain.exists() or any(c in str(keychain) for c in '\\"\n\r'):
            raise RuntimeError('unsafe disposable keychain path')
        saved = shlex.split(security(['list-keychains', '-d', 'user']))
        password = uuid.uuid4().hex
        try:
            # Even disposable passwords stay off argv and out of captured error reports.
            security(['-i'], 'create-keychain -p %s "%s"\n' % (password, keychain))
            if not keychain.is_file():
                raise RuntimeError('disposable keychain was not created')
            security(['-i'], 'unlock-keychain -p %s "%s"\n' % (password, keychain))
            yield ou.MacKeychainFile(keychain=keychain)
        finally:
            try:
                if keychain.exists():
                    security(['delete-keychain', str(keychain)])
            finally:
                security(['list-keychains', '-d', 'user', '-s', *saved])
    elif os.name == 'nt':
        yield ou.WindowsCredential(prefix='agentmesh-test-' + uuid.uuid4().hex + '/')
    else:
        raise RuntimeError('native credential tests require Mac or Windows')
