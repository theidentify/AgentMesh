"""Explicit R2 credentials from OS credential stores; never the boto credential chain."""
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit
from . import StorageError


def validate_remote(credentials, endpoint):
    if type(credentials) is not str or not re.fullmatch(r'(?:keychain|wincred):[A-Za-z0-9.-]+',credentials):
        raise ValueError('invalid S3 credentials reference')
    if type(endpoint) is not str or any(ord(c)<=32 or ord(c)==127 for c in endpoint):
        raise ValueError('invalid S3 endpoint')
    parsed=urlsplit(endpoint)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('invalid S3 endpoint')


def load_keychain_credentials(service, runner=subprocess.run, keychain=None):
    if sys.platform!='darwin' or type(service) is not str or not re.fullmatch('[A-Za-z0-9.-]+',service):
        raise StorageError('S3 credentials refused')
    reason='S3 credentials refused'
    try:
        from ..oskeystore import login_keychain, home
        if keychain is None: keychain=login_keychain()
        user_home=home() if os.name=='posix' else os.path.expanduser('~')
        keychain=str(keychain)
        if not os.path.isabs(keychain) or any(ord(c)<32 or ord(c)==127 for c in keychain):
            raise ValueError()
        values=[]
        for account,size in (('access-key-id',32),('secret-access-key',64)):
            result=runner(['/usr/bin/security','find-generic-password','-s',service,'-a',account,'-w',keychain],
                capture_output=True,text=True,encoding='utf-8',timeout=15,env={'PATH':'/usr/bin:/bin','HOME':user_home,'LANG':'C'})
            if result.returncode==44:
                reason='S3 credentials missing'; break
            value=result.stdout.rstrip('\r\n') if result.returncode==0 else ''
            if not re.fullmatch('[0-9a-f]{'+str(size)+'}',value): break
            values.append(value)
        else: return tuple(values)
    except Exception: pass
    # Suppressing traceback display alone still retains captured subprocess output.
    raise StorageError(reason) from None


def load_credentials(ref, *, runner=subprocess.run, api=None):
    if type(ref) is not str or not re.fullmatch(r'(?:keychain|wincred):[A-Za-z0-9.-]+',ref):
        raise StorageError('S3 credentials refused') from None
    kind,service=ref.split(':',1)
    if sys.platform=='darwin' and kind=='keychain':
        return load_keychain_credentials(service,runner=runner)
    if sys.platform=='win32' and kind=='wincred':
        return WindowsR2Credentials(api).load(service)
    raise StorageError('S3 credentials refused') from None


class WindowsR2Credentials:
    def __init__(self,api=None):
        from ..oskeystore import WindowsCredential
        self.api=api or WindowsCredential._api

    def _check(self,service):
        if sys.platform!='win32' or type(service) is not str or not re.fullmatch('[A-Za-z0-9.-]+',service):
            raise StorageError('S3 credentials refused') from None

    def load(self,service):
        self._check(service); reason='S3 credentials refused'
        try:
            ctypes,CREDENTIAL,advapi=self.api(); values=[]
            for account,size in (('access-key-id',32),('secret-access-key',64)):
                pointer=ctypes.POINTER(CREDENTIAL)()
                if not advapi.CredReadW(service+'/'+account,1,0,ctypes.byref(pointer)):
                    if ctypes.get_last_error()==1168: reason='S3 credentials missing'
                    break
                try:
                    if pointer.contents.CredentialBlobSize!=size: break
                    value=ctypes.string_at(pointer.contents.CredentialBlob,size).decode('ascii')
                    if not re.fullmatch('[0-9a-f]{'+str(size)+'}',value): break
                    values.append(value)
                finally: advapi.CredFree(pointer)
            else: return tuple(values)
        except Exception: pass
        raise StorageError(reason) from None

    def store(self,service,access,secret):
        self._check(service)
        if any(type(value) is not str or not re.fullmatch('[0-9a-f]{'+str(size)+'}',value)
               for value,size in ((access,32),(secret,64))):
            raise StorageError('S3 credentials refused') from None
        try:
            ctypes,CREDENTIAL,advapi=self.api()
            for account,value in (('access-key-id',access),('secret-access-key',secret)):
                blob=(ctypes.c_ubyte*len(value)).from_buffer_copy(value.encode('ascii'))
                credential=CREDENTIAL(Type=1,TargetName=service+'/'+account,CredentialBlobSize=len(value),
                    CredentialBlob=ctypes.cast(blob,ctypes.POINTER(ctypes.c_ubyte)),Persist=2,UserName='agentmesh')
                if not advapi.CredWriteW(ctypes.byref(credential),0): break
            else:
                if self.load(service)==(access,secret): return
        except Exception: pass
        raise StorageError('S3 credentials refused') from None

    def delete(self,service):
        self._check(service); failed=False
        try:
            ctypes,_,advapi=self.api()
            for account in ('access-key-id','secret-access-key'):
                if not advapi.CredDeleteW(service+'/'+account,1,0) and ctypes.get_last_error()!=1168: failed=True
            if not failed: return
        except Exception: pass
        raise StorageError('S3 credentials refused') from None


def credentials_command(action,ref,*,getpass_fn=None,api=None):
    if sys.platform!='win32' or type(ref) is not str or not re.fullmatch(r'wincred:[A-Za-z0-9.-]+',ref):
        return {'ok':False,'status':'refused'}
    backend=WindowsR2Credentials(api); service=ref.split(':',1)[1]; status='refused'
    try:
        if action=='store':
            from getpass import getpass
            prompt=getpass_fn or getpass
            backend.store(service,prompt('Access key ID: '),prompt('Secret access key: '))
        elif action=='check': backend.load(service)
        else: return {'ok':False,'status':'refused'}
        return {'ok':True,'ref':ref}
    except StorageError as exc:
        if str(exc)=='S3 credentials missing': status='missing'
    except Exception: pass
    return {'ok':False,'ref':ref,'status':status}
