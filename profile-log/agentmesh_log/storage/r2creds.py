"""Explicit R2 credentials from the login Keychain; never the boto credential chain."""
import os
import re
import subprocess
from urllib.parse import urlsplit
from . import StorageError


def validate_remote(credentials, endpoint):
    if type(credentials) is not str or not re.fullmatch(r'keychain:[A-Za-z0-9.-]+',credentials):
        raise ValueError('invalid S3 credentials reference')
    if type(endpoint) is not str or any(ord(c)<=32 or ord(c)==127 for c in endpoint):
        raise ValueError('invalid S3 endpoint')
    parsed=urlsplit(endpoint)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('invalid S3 endpoint')


def load_keychain_credentials(service, runner=subprocess.run, keychain=None):
    if type(service) is not str or not re.fullmatch('[A-Za-z0-9.-]+',service):
        raise StorageError('S3 credentials refused')
    reason='S3 credentials refused'
    try:
        from ..oskeystore import login_keychain, home
        if keychain is None: keychain=login_keychain()
        keychain=str(keychain)
        if not os.path.isabs(keychain) or any(ord(c)<32 or ord(c)==127 for c in keychain):
            raise ValueError()
        values=[]
        for account,size in (('access-key-id',32),('secret-access-key',64)):
            result=runner(['/usr/bin/security','find-generic-password','-s',service,'-a',account,'-w',keychain],
                capture_output=True,text=True,encoding='utf-8',timeout=15,env={'PATH':'/usr/bin:/bin','HOME':home(),'LANG':'C'})
            if result.returncode==44:
                reason='S3 credentials missing'; break
            value=result.stdout.rstrip('\r\n') if result.returncode==0 else ''
            if not re.fullmatch('[0-9a-f]{'+str(size)+'}',value): break
            values.append(value)
        else: return tuple(values)
    except Exception: pass
    # Suppressing traceback display alone still retains captured subprocess output.
    raise StorageError(reason) from None
