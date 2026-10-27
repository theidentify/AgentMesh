import ctypes
import json
from types import SimpleNamespace
import pytest
from agentmesh_log import cli
from agentmesh_log.oskeystore import WindowsCredential
from agentmesh_log.storage import StorageError, r2creds

ID='abcd0123'*4
SECRET='fedc9876'*8
SERVICE='test.r2'
REF='wincred:'+SERVICE


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr(r2creds,'sys',SimpleNamespace(platform='win32'))
    state=SimpleNamespace(values={},writes=[],reads=[],freed=[],error=1168,fail_write=False,tamper=False,explode=False)
    def read(target,kind,flags,out):
        assert (kind,flags)==(1,0); state.reads.append(target)
        if state.explode: raise RuntimeError(SECRET)
        if target not in state.values: return False
        value=state.values[target]; blob=(ctypes.c_ubyte*len(value)).from_buffer_copy(value)
        credential=credential_type(CredentialBlobSize=len(value),CredentialBlob=ctypes.cast(blob,ctypes.POINTER(ctypes.c_ubyte)))
        state.live=(blob,credential)
        ctypes.cast(out,ctypes.POINTER(ctypes.POINTER(credential_type)))[0]=ctypes.pointer(credential)
        return True
    def write(ptr,flags):
        row=ptr._obj
        assert (row.Type,row.Persist,row.UserName,flags)==(1,2,'agentmesh',0)
        state.writes.append(row.TargetName)
        if state.explode: raise RuntimeError(SECRET)
        if state.fail_write: return False
        state.values[row.TargetName]=ctypes.string_at(row.CredentialBlob,row.CredentialBlobSize)
        if state.tamper: state.values[row.TargetName]=b'0'*row.CredentialBlobSize
        return True
    def free(ptr): state.freed.append(ptr.contents.CredentialBlobSize)
    def delete(target,kind,flags):
        assert (kind,flags)==(1,0)
        state.values.pop(target,None); return True
    advapi=SimpleNamespace(CredReadW=read,CredWriteW=write,CredFree=free,CredDeleteW=delete)
    def dll(name,**kw):
        assert name=='advapi32' and kw=={'use_last_error':True}
        return advapi
    monkeypatch.setattr(ctypes,'WinDLL',dll,raising=False)
    _,credential_type,_=WindowsCredential._api()
    fake_ctypes=SimpleNamespace(**{name:getattr(ctypes,name) for name in ('POINTER','byref','c_ubyte','cast','string_at')},get_last_error=lambda:state.error)
    state.api=lambda:(fake_ctypes,credential_type,advapi)
    state.values={SERVICE+'/access-key-id':ID.encode('ascii'),SERVICE+'/secret-access-key':SECRET.encode('ascii')}
    return state


def clean(exc):
    assert exc.__context__ is None and exc.__cause__ is None
    assert ID not in str(exc)+repr(exc) and SECRET not in str(exc)+repr(exc)


@pytest.mark.parametrize('platform,ref',[('darwin',REF),('win32','keychain:'+SERVICE),('linux',REF),('linux','keychain:'+SERVICE)],ids=['mac-wincred','win-keychain','linux-wincred','linux-keychain'])
def test_os_refusal(monkeypatch,platform,ref):
    monkeypatch.setattr(r2creds,'sys',SimpleNamespace(platform=platform))
    with pytest.raises(StorageError,match='^S3 credentials refused$') as caught:
        r2creds.load_credentials(ref,api=lambda:pytest.fail('native API reached'))
    clean(caught.value)


def test_keychain_backend_refuses_off_mac(monkeypatch):
    monkeypatch.setattr(r2creds,'sys',SimpleNamespace(platform='win32'))
    with pytest.raises(StorageError,match='^S3 credentials refused$') as caught:
        r2creds.load_keychain_credentials(SERVICE,runner=lambda *a,**k:pytest.fail('security reached'))
    clean(caught.value)


def test_dispatch_and_reference_validation(native,monkeypatch):
    assert r2creds.load_credentials(REF,api=native.api)==(ID,SECRET)
    assert native.reads==[SERVICE+'/access-key-id',SERVICE+'/secret-access-key']
    assert native.freed==[32,64]
    for ref in (REF,'keychain:'+SERVICE): r2creds.validate_remote(ref,'https://example.test')
    for ref in ('wincred:', 'wincred:a/b','wincred:a_b','wincred:a\n'):
        with pytest.raises(ValueError): r2creds.validate_remote(ref,'https://example.test')
    monkeypatch.setattr(r2creds,'sys',SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(r2creds,'load_keychain_credentials',lambda service,runner,kind='s3':(ID,SECRET) if service==SERVICE else None)
    assert r2creds.load_credentials('keychain:'+SERVICE)==(ID,SECRET)


@pytest.mark.parametrize('account,size',[('access-key-id',32),('secret-access-key',64)],ids=['access','secret'])
@pytest.mark.parametrize('mode',['missing','length','long','uppercase','nonhex','nul','nonascii','error','exception'],ids=['missing','length','long','uppercase','nonhex','nul','nonascii','error','exception'])
def test_native_failures(native,account,size,mode):
    target=SERVICE+'/'+account
    if mode in ('missing','error'): native.values.pop(target); native.error=1168 if mode=='missing' else 5
    elif mode=='exception': native.explode=True
    else: native.values[target]={'length':b'a'*(size-1),'long':b'a'*(size+1),'uppercase':b'A'*size,'nonhex':b'g'*size,'nul':b'a'*(size-1)+b'\0','nonascii':b'\xff'*size}[mode]
    with pytest.raises(StorageError) as caught: r2creds.load_credentials(REF,api=native.api)
    assert str(caught.value)=='S3 credentials '+('missing' if mode=='missing' else 'refused')
    clean(caught.value)
    if mode not in ('missing','error','exception'): assert native.freed[-1]==len(native.values[target])


@pytest.mark.parametrize('values',[(ID[:-1],SECRET),(ID,SECRET.upper()),(ID,'g'*64),(ID,SECRET[:-1]+'\0')],ids=['length','uppercase','nonhex','nul'])
def test_store_bad_format(native,values):
    with pytest.raises(StorageError) as caught: r2creds.WindowsR2Credentials(native.api).store(SERVICE,*values)
    clean(caught.value); assert native.writes==[]
    prompts=iter(values)
    result=r2creds.credentials_command('store',REF,getpass_fn=lambda _:next(prompts),api=native.api)
    assert result=={'ok':False,'ref':REF,'status':'refused'} and native.writes==[]


@pytest.mark.parametrize('mode',['ok','tamper','fail_write','explode'],ids=['ok','tamper','write-error','exception'])
def test_store_verify(native,mode):
    if mode!='ok': setattr(native,mode,True)
    prompts=iter((ID,SECRET))
    result=r2creds.credentials_command('store',REF,getpass_fn=lambda _:next(prompts),api=native.api)
    assert result==({'ok':True,'ref':REF} if mode=='ok' else {'ok':False,'ref':REF,'status':'refused'})
    assert ID not in json.dumps(result) and SECRET not in json.dumps(result)
    if mode=='ok':
        assert native.reads==[SERVICE+'/access-key-id',SERVICE+'/secret-access-key']
        assert native.writes==native.reads
    else:
        with pytest.raises(StorageError) as caught: r2creds.WindowsR2Credentials(native.api).store(SERVICE,ID,SECRET)
        clean(caught.value)


@pytest.mark.parametrize('platform',['darwin','linux'],ids=['mac','linux'])
def test_helper_refuses_non_windows(native,monkeypatch,platform):
    monkeypatch.setattr(r2creds,'sys',SimpleNamespace(platform=platform))
    for action in ('store','check'):
        result=r2creds.credentials_command(action,REF,getpass_fn=lambda _:pytest.fail('prompted'),api=lambda:pytest.fail('API called'))
        assert result=={'ok':False,'status':'refused'}


def test_helper_cli_output(native,monkeypatch,capsys):
    import getpass
    monkeypatch.setattr(WindowsCredential,'_api',staticmethod(native.api))
    prompts=iter((ID,SECRET)); monkeypatch.setattr(getpass,'getpass',lambda _:next(prompts))
    assert cli.main(['r2-credentials','store','--ref',REF])==0
    assert json.loads(capsys.readouterr().out)=={'ok':True,'ref':REF}
    assert cli.main(['r2-credentials','check','--ref',REF])==0
    assert json.loads(capsys.readouterr().out)=={'ok':True,'ref':REF}
    native.values.clear()
    assert cli.main(['r2-credentials','check','--ref',REF])==1
    assert json.loads(capsys.readouterr().out)=={'ok':False,'ref':REF,'status':'missing'}
    native.error=5
    assert cli.main(['r2-credentials','check','--ref',REF])==1
    assert json.loads(capsys.readouterr().out)=={'ok':False,'ref':REF,'status':'refused'}


def test_access_native_load_store_delete(native):
    backend=r2creds.WindowsR2Credentials(native.api,kind='access')
    backend.store(SERVICE,'a'*32+'.access','b'*64)
    assert backend.load(SERVICE)==('a'*32+'.access','b'*64)
    assert native.writes==[SERVICE+'/client-id',SERVICE+'/client-secret']
    assert native.freed==[39,64,39,64]
    assert r2creds.load_credentials(REF,api=native.api,kind='access')==('a'*32+'.access','b'*64)
    backend.delete(SERVICE)
    assert SERVICE+'/client-id' not in native.values and SERVICE+'/client-secret' not in native.values
    assert SERVICE+'/access-key-id' in native.values


def test_access_prompts_and_missing(native):
    prompts=[]; values=iter(('a'*32+'.access','b'*64))
    def prompt(text): prompts.append(text); return next(values)
    assert r2creds.credentials_command('store',REF,getpass_fn=prompt,api=native.api,kind='access')=={'ok':True,'ref':REF}
    assert prompts==['Access client ID: ','Access client secret: ']
    native.values.clear()
    assert r2creds.credentials_command('check',REF,api=native.api,kind='access')=={'ok':False,'ref':REF,'status':'missing'}


@pytest.mark.parametrize('value',[b'a'*32,b'a'*38,b'a'*40,b'A'*32+b'.access'],
    ids=['suffix','short','long','case'])
def test_access_native_invalid(native,value):
    native.values[SERVICE+'/client-id']=value
    native.values[SERVICE+'/client-secret']=b'b'*64
    with pytest.raises(StorageError,match='^embedding credentials refused$'):
        r2creds.WindowsR2Credentials(native.api,kind='access').load(SERVICE)


def test_access_native_new_secret(native):
    secret='Ab3_'*13+'x-'
    backend=r2creds.WindowsR2Credentials(native.api,kind='access')
    backend.store(SERVICE,'a'*32+'.access',secret)
    assert backend.load(SERVICE)==('a'*32+'.access',secret)
    native.values[SERVICE+'/client-secret']=b'b'*129
    with pytest.raises(StorageError,match='^embedding credentials refused$'): backend.load(SERVICE)


def test_access_store_no_suffix(native):
    with pytest.raises(StorageError,match='^embedding credentials refused$'):
        r2creds.WindowsR2Credentials(native.api,kind='access').store(SERVICE,'a'*32,'b'*64)
    assert native.writes==[]
