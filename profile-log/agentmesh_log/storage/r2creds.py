"""Explicit R2 credentials from OS credential stores; never the boto credential chain."""
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit
from . import StorageError


# keychain: macOS login keychain, wincred: Windows Credential Manager, secret: the Linux desktop Secret Service.
REF=r'(?:keychain|wincred|secret):[A-Za-z0-9.-]+'


# (account, maximum length, pattern). Cloudflare Access client secrets were 64 hex
# characters; current ones are shorter URL-safe tokens (54 observed), so accept both.
KINDS={
    's3': ('S3', (('access-key-id',32,'[0-9a-f]{32}'),('secret-access-key',64,'[0-9a-f]{64}'))),
    'access': ('embedding', (('client-id',39,r'[0-9a-f]{32}\.access'),('client-secret',128,'[A-Za-z0-9_-]{32,128}'))),
    # Other S3-compatible stores (one-click join): AWS IAM keys, and a bounded generic form for MinIO, Wasabi and
    # similar. No quote, backslash or whitespace, so a value can never break the `security -i` command line.
    's3-aws': ('S3', (('access-key-id',20,'(?:AKIA|ASIA)[A-Z0-9]{16}'),('secret-access-key',40,'[A-Za-z0-9/+]{40}'))),
    's3-generic': ('S3', (('access-key-id',128,'[A-Za-z0-9._~+=/-]{3,128}'),('secret-access-key',128,'[A-Za-z0-9._~+=/-]{8,128}'))),
}
S3_KINDS=('s3','s3-aws','s3-generic')


def check_values(kind, values):
    """Whether ``values`` (access key ID, secret) fit ``kind``; raises without echoing them."""
    _,accounts=_kind(kind)
    if type(values) not in (tuple,list) or len(values)!=len(accounts) or any(
            type(v) is not str or len(v)>size or not re.fullmatch(pattern,v) for v,(_,size,pattern) in zip(values,accounts)):
        raise StorageError('credentials refused') from None
    return tuple(values)


def store_keychain_credentials(service, values, runner=subprocess.run, keychain=None, kind='s3'):
    """Store a pair in the login keychain, the secret over stdin (never argv), then load it back to check."""
    label,accounts=_kind(kind); values=check_values(kind,values)
    if sys.platform!='darwin' or type(service) is not str or not re.fullmatch('[A-Za-z0-9.-]+',service):
        raise StorageError(f'{label} credentials refused')
    try:
        from ..oskeystore import login_keychain, home
        keychain=str(keychain or login_keychain())
        if not os.path.isabs(keychain) or any(c in keychain for c in '"\\\n\r'): raise ValueError()
        script=''.join('add-generic-password -U -s %s -a %s -w %s "%s"\n'%(service,account,value,keychain)
                       for (account,_,_),value in zip(accounts,values))
        runner(['/usr/bin/security','-i'],input=script,capture_output=True,text=True,encoding='utf-8',timeout=15,
               env={'PATH':'/usr/bin:/bin','HOME':home(),'LANG':'C'})
        ok=load_keychain_credentials(service,runner=runner,keychain=keychain,kind=kind)==values
    except Exception: ok=False
    if not ok: raise StorageError(f'{label} credentials refused') from None


def store_credentials(ref, values, *, runner=subprocess.run, api=None, kind='s3'):
    """Store a key pair under ``ref`` (``keychain:`` on macOS, ``wincred:`` on Windows, ``secret:`` on Linux)."""
    label,_=_kind(kind)
    if type(ref) is not str or not re.fullmatch(REF,ref):
        raise StorageError(f'{label} credentials refused') from None
    backend,service=ref.split(':',1)
    if sys.platform=='darwin' and backend=='keychain': return store_keychain_credentials(service,values,runner=runner,kind=kind)
    if sys.platform=='win32' and backend=='wincred': return WindowsR2Credentials(api,kind=kind).store(service,*check_values(kind,values))
    if sys.platform.startswith('linux') and backend=='secret':
        return LinuxR2Credentials(**({} if runner is subprocess.run else {'runner':runner}),kind=kind).store(service,*check_values(kind,values))
    raise StorageError(f'{label} credentials refused') from None


def default_ref(service):
    return ('wincred:' if sys.platform=='win32' else 'secret:' if sys.platform.startswith('linux') else 'keychain:')+service


def _kind(kind):
    if kind not in KINDS: raise ValueError('unknown credentials kind')
    return KINDS[kind]


def validate_remote(credentials, endpoint):
    if type(credentials) is not str or not re.fullmatch(REF,credentials):
        raise ValueError('invalid S3 credentials reference')
    if type(endpoint) is not str or any(ord(c)<=32 or ord(c)==127 for c in endpoint):
        raise ValueError('invalid S3 endpoint')
    parsed=urlsplit(endpoint)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('invalid S3 endpoint')


def load_keychain_credentials(service, runner=subprocess.run, keychain=None, kind='s3'):
    label,accounts=_kind(kind)
    if sys.platform!='darwin' or type(service) is not str or not re.fullmatch('[A-Za-z0-9.-]+',service):
        raise StorageError(f'{label} credentials refused')
    reason=f'{label} credentials refused'
    try:
        from ..oskeystore import login_keychain, home
        if keychain is None: keychain=login_keychain()
        user_home=home() if os.name=='posix' else os.path.expanduser('~')
        keychain=str(keychain)
        if not os.path.isabs(keychain) or any(ord(c)<32 or ord(c)==127 for c in keychain):
            raise ValueError()
        values=[]
        for account,size,pattern in accounts:
            result=runner(['/usr/bin/security','find-generic-password','-s',service,'-a',account,'-w',keychain],
                capture_output=True,text=True,encoding='utf-8',timeout=15,env={'PATH':'/usr/bin:/bin','HOME':user_home,'LANG':'C'})
            if result.returncode==44:
                reason=f'{label} credentials missing'; break
            value=result.stdout.rstrip('\r\n') if result.returncode==0 else ''
            if not re.fullmatch(pattern,value): break
            values.append(value)
        else: return tuple(values)
    except Exception: pass
    # Suppressing traceback display alone still retains captured subprocess output.
    raise StorageError(reason) from None


def load_credentials(ref, *, runner=subprocess.run, api=None, kind='s3'):
    label,_=_kind(kind)
    if type(ref) is not str or not re.fullmatch(REF,ref):
        raise StorageError(f'{label} credentials refused') from None
    backend,service=ref.split(':',1)
    if sys.platform=='darwin' and backend=='keychain':
        return load_keychain_credentials(service,runner=runner,kind=kind)
    if sys.platform=='win32' and backend=='wincred':
        return WindowsR2Credentials(api,kind=kind).load(service)
    if sys.platform.startswith('linux') and backend=='secret':
        return LinuxR2Credentials(**({} if runner is subprocess.run else {'runner':runner}),kind=kind).load(service)
    raise StorageError(f'{label} credentials refused') from None


class LinuxR2Credentials:
    """A key pair in the desktop Secret Service (GNOME Keyring, KWallet) through ``secret-tool``: attributes
    ``service`` and ``account`` as on macOS, secrets over stdin, never argv. Needs a session bus, like the profile-log
    key store on Linux; a headless server without one has no backend."""
    def __init__(self,runner=subprocess.run,environ=None,tool=None,kind='s3'):
        self.label,self.accounts=_kind(kind)
        from ..oskeystore import LinuxSecretService
        self.service=LinuxSecretService(tool=tool,runner=runner,environ=environ)

    def _check(self,service):
        if not sys.platform.startswith('linux') or type(service) is not str or not re.fullmatch('[A-Za-z0-9.-]+',service):
            raise StorageError(f'{self.label} credentials refused') from None

    def load(self,service):
        self._check(service); reason=f'{self.label} credentials refused'
        try:
            values=[]
            for account,size,pattern in self.accounts:
                result=self.service._run(['lookup','service',service,'account',account])
                value=result.stdout.rstrip('\r\n') if result.returncode==0 else ''
                if not value:
                    reason=f'{self.label} credentials missing'; break
                if len(value)>size or not re.fullmatch(pattern,value): break
                values.append(value)
            else: return tuple(values)
        except Exception: pass
        raise StorageError(reason) from None

    def store(self,service,access,secret):
        self._check(service)
        try:
            values=check_values_for(self.accounts,(access,secret))
            for (account,_,_),value in zip(self.accounts,values):
                self.service._run(['store','--label','AgentMesh '+service+' '+account,'service',service,'account',account],value)
            if self.load(service)==values: return
        except Exception: pass
        raise StorageError(f'{self.label} credentials refused') from None

    def delete(self,service):
        self._check(service)
        try:
            for account,_,_ in self.accounts: self.service._run(['clear','service',service,'account',account])
            return
        except Exception: pass
        raise StorageError(f'{self.label} credentials refused') from None


def check_values_for(accounts,values):
    if len(values)!=len(accounts) or any(type(v) is not str or len(v)>size or not re.fullmatch(pattern,v)
                                         for v,(_,size,pattern) in zip(values,accounts)):
        raise StorageError('credentials refused') from None
    return tuple(values)


class WindowsR2Credentials:
    def __init__(self,api=None,kind='s3'):
        self.label,self.accounts=_kind(kind)
        from ..oskeystore import WindowsCredential
        self.api=api or WindowsCredential._api

    def _check(self,service):
        if sys.platform!='win32' or type(service) is not str or not re.fullmatch('[A-Za-z0-9.-]+',service):
            raise StorageError(f'{self.label} credentials refused') from None

    def load(self,service):
        self._check(service); reason=f'{self.label} credentials refused'
        try:
            ctypes,CREDENTIAL,advapi=self.api(); values=[]
            for account,size,pattern in self.accounts:
                pointer=ctypes.POINTER(CREDENTIAL)()
                if not advapi.CredReadW(service+'/'+account,1,0,ctypes.byref(pointer)):
                    if ctypes.get_last_error()==1168: reason=f'{self.label} credentials missing'
                    break
                try:
                    length=pointer.contents.CredentialBlobSize
                    if not 0<length<=size: break
                    value=ctypes.string_at(pointer.contents.CredentialBlob,length).decode('ascii')
                    if not re.fullmatch(pattern,value): break
                    values.append(value)
                finally: advapi.CredFree(pointer)
            else: return tuple(values)
        except Exception: pass
        raise StorageError(reason) from None

    def store(self,service,access,secret):
        self._check(service)
        if any(type(value) is not str or not re.fullmatch(pattern,value)
               for value,(_,size,pattern) in zip((access,secret),self.accounts)):
            raise StorageError(f'{self.label} credentials refused') from None
        try:
            ctypes,CREDENTIAL,advapi=self.api()
            for (account,size,pattern),value in zip(self.accounts,(access,secret)):
                blob=(ctypes.c_ubyte*len(value)).from_buffer_copy(value.encode('ascii'))
                credential=CREDENTIAL(Type=1,TargetName=service+'/'+account,CredentialBlobSize=len(value),
                    CredentialBlob=ctypes.cast(blob,ctypes.POINTER(ctypes.c_ubyte)),Persist=2,UserName='agentmesh')
                if not advapi.CredWriteW(ctypes.byref(credential),0): break
            else:
                if self.load(service)==(access,secret): return
        except Exception: pass
        raise StorageError(f'{self.label} credentials refused') from None

    def delete(self,service):
        self._check(service); failed=False
        try:
            ctypes,_,advapi=self.api()
            for account,size,pattern in self.accounts:
                if not advapi.CredDeleteW(service+'/'+account,1,0) and ctypes.get_last_error()!=1168: failed=True
            if not failed: return
        except Exception: pass
        raise StorageError(f'{self.label} credentials refused') from None


def credentials_command(action,ref,*,getpass_fn=None,api=None,kind='s3'):
    label,_=_kind(kind)
    windows=sys.platform=='win32' and type(ref) is str and re.fullmatch(r'wincred:[A-Za-z0-9.-]+',ref)
    linux=sys.platform.startswith('linux') and type(ref) is str and re.fullmatch(r'secret:[A-Za-z0-9.-]+',ref)
    if not (windows or linux): return {'ok':False,'status':'refused'}
    backend=WindowsR2Credentials(api,kind=kind) if windows else LinuxR2Credentials(kind=kind)
    service=ref.split(':',1)[1]; status='refused'
    try:
        if action=='store':
            from getpass import getpass
            prompt=getpass_fn or getpass
            prompts=('Access client ID: ','Access client secret: ') if kind=='access' else ('Access key ID: ','Secret access key: ')
            backend.store(service,prompt(prompts[0]),prompt(prompts[1]))
        elif action=='check': backend.load(service)
        else: return {'ok':False,'status':'refused'}
        return {'ok':True,'ref':ref}
    except StorageError as exc:
        if str(exc)==f'{label} credentials missing': status='missing'
    except Exception: pass
    return {'ok':False,'ref':ref,'status':status}
