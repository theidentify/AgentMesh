import logging
import subprocess
from types import SimpleNamespace
import pytest
from moto import mock_aws
from agentmesh_log import cli
from agentmesh_log.storage import StorageError
from agentmesh_log.storage.s3 import S3Storage

ID='abcd0123'*4
SECRET='fedc9876'*8
SERVICE='org.agentmesh.profile-log.r2'


@pytest.fixture(autouse=True)
def macos_backend(monkeypatch):
    from agentmesh_log.storage import r2creds
    monkeypatch.setattr(r2creds,'sys',SimpleNamespace(platform='darwin'))


def loader():
    from agentmesh_log.storage.r2creds import load_keychain_credentials
    return load_keychain_credentials


def clean(exc):
    assert exc.__context__ is None and exc.__cause__ is None
    assert ID not in repr(exc) and SECRET not in repr(exc)


def test_keychain_contract(tmp_path):
    calls=[]; keychain=str(tmp_path/'login.keychain-db')
    def run(argv,**kw):
        calls.append((argv,kw))
        return SimpleNamespace(returncode=0,stdout=(ID if argv[5]=='access-key-id' else SECRET)+'\n')
    assert loader()(SERVICE,runner=run,keychain=keychain)==(ID,SECRET)
    assert [a for a,k in calls]==[['/usr/bin/security','find-generic-password','-s',SERVICE,'-a',a,'-w',keychain]
        for a in ('access-key-id','secret-access-key')]
    for argv,kw in calls:
        assert kw['env']['PATH']=='/usr/bin:/bin' and set(kw['env'])=={'PATH','HOME','LANG'} and 0<kw['timeout']<=15
        assert kw['capture_output'] and kw['text']
        assert ID not in repr(argv) and SECRET not in repr(argv)


@pytest.mark.parametrize('mode',['missing','refused','malformed','uppercase','timeout','oserror','unexpected'],ids=['missing','refused','shape','case','timeout','os','other'])
def test_keychain_safe_failures(tmp_path,mode,caplog):
    def run(*a,**kw):
        if mode=='timeout': raise subprocess.TimeoutExpired(a,1,output=SECRET,stderr=ID)
        if mode=='oserror': raise OSError(SECRET)
        if mode=='unexpected': raise RuntimeError(SECRET)
        return SimpleNamespace(returncode=44 if mode=='missing' else 1 if mode=='refused' else 0,
            stdout=ID.upper() if mode=='uppercase' else SECRET,stderr=SECRET)
    with pytest.raises(StorageError) as caught: loader()(SERVICE,runner=run,keychain=str(tmp_path/'keychain'))
    assert str(caught.value)=='S3 credentials '+('missing' if mode=='missing' else 'refused')
    clean(caught.value); assert SECRET not in caplog.text


def args(tmp_path,action='sync',**kw):
    values=dict(filesystem=None,s3_bucket='profile-r2',s3_prefix='v1/',s3_endpoint='https://s3.amazonaws.com',
        s3_credentials='keychain:'+SERVICE)
    values.update(kw)
    return SimpleNamespace(**values)


@pytest.mark.parametrize('field,value',[('s3_credentials',None),('s3_credentials',SECRET),
    ('s3_credentials','keychain:bad\nservice'),('s3_endpoint','http://example.test'),
    ('s3_endpoint','https://user:pass@example.test'),('s3_endpoint','https://example.test\n')],ids=['absent','raw','service','http','userinfo','control'])
def test_remote_config_rejected(tmp_path,monkeypatch,field,value):
    import agentmesh_log.storage.r2creds as creds
    monkeypatch.setattr(creds,'load_credentials',lambda *a: pytest.fail('invalid configuration reached keychain'))
    with pytest.raises(ValueError) as caught: cli.remote_storage(args(tmp_path,**{field:value}))
    assert SECRET not in repr(caught.value)


def test_moto_explicit_credentials(tmp_path,monkeypatch,caplog):
    import boto3
    import agentmesh_log.storage.r2creds as creds
    with mock_aws():
        monkeypatch.setenv('HOME',str(tmp_path)); aws=tmp_path/'.aws'; aws.mkdir()
        (aws/'credentials').write_text('[default]\naws_access_key_id=bogus\naws_secret_access_key=bogus\n')
        monkeypatch.setenv('AWS_ACCESS_KEY_ID','bogus'); monkeypatch.setenv('AWS_SECRET_ACCESS_KEY','bogus')
        monkeypatch.setenv('AWS_SESSION_TOKEN','bogus')
        monkeypatch.setattr(creds,'load_credentials',lambda service:(ID,SECRET))
        caplog.set_level(logging.DEBUG)
        remote=cli.remote_storage(args(tmp_path))
        frozen=remote.client._request_signer._credentials.get_frozen_credentials()
        assert (frozen.access_key,frozen.secret_key,frozen.token)==(ID,SECRET,None)
        cfg=remote.client.meta.config
        assert (cfg.connect_timeout,cfg.read_timeout,cfg.retries.get('total_max_attempts'))==(5,20,4)
        remote.client.create_bucket(Bucket='profile-r2',CreateBucketConfiguration={'LocationConstraint':'auto'})
        remote.put('events/a',b'opaque'); assert remote.get('events/a')==b'opaque'
        assert list(remote.list(''))==['events/a']
        assert ID not in caplog.text and SECRET not in caplog.text
        assert ID not in repr(remote) and SECRET not in repr(remote)


@pytest.mark.parametrize('action',['init','put','get','list'],ids=['init','put','get','list'])
def test_s3_safe_errors(action):
    class Bad:
        def __getattr__(self,name):
            def fail(*a,**kw): raise RuntimeError(SECRET)
            return fail
    def factory(*a,**kw): raise RuntimeError(SECRET)
    with pytest.raises(StorageError) as caught:
        if action=='init': S3Storage('bucket',client_factory=factory)
        else:
            remote=S3Storage('bucket',client=Bad())
            if action=='put': remote.put('a',b'opaque')
            elif action=='get': remote.get('a')
            else: list(remote.list(''))
    clean(caught.value)


@pytest.mark.parametrize('action',['run','sync'],ids=['run','sync'])
def test_cli_requires_credentials(tmp_path,monkeypatch,action):
    from broker.test_cli import cli as invoke
    from broker.support import setup_home
    from agentmesh_log.broker.service import Broker
    import agentmesh_log.storage.s3 as s3
    home,*_=setup_home(tmp_path); calls=[]
    monkeypatch.setattr(s3,'S3Storage',lambda *a,**kw:calls.append('client'))
    monkeypatch.setattr(Broker,'serve',lambda *a,**kw:calls.append('serve'))
    result=invoke('broker',action,'--home',home.root,'--s3-bucket','profile-r2','--s3-endpoint','https://s3.amazonaws.com')
    assert result.returncode!=0 and SECRET not in result.stdout+result.stderr
    assert calls==[]


def test_cli_missing_one_shot(tmp_path,monkeypatch):
    from broker.support import setup_home
    from broker.test_cli import cli as invoke
    import agentmesh_log.storage.r2creds as creds
    home,*_=setup_home(tmp_path)
    def missing(*a): raise StorageError('S3 credentials missing')
    monkeypatch.setattr(creds,'load_credentials',missing)
    result=invoke('broker','sync','--home',home.root,'--s3-bucket','profile-r2',
        '--s3-endpoint','https://s3.amazonaws.com','--s3-credentials','keychain:'+SERVICE)
    assert result.returncode!=0
    assert result.stdout.strip()=='{"error": "credentials_missing"}'


@pytest.mark.parametrize('response',[None,{'Error':None},{'Error':{'Code':SECRET}}],ids=['none','error','code'])
def test_s3_error_response(response):
    class Bad:
        def get_object(self,**kw):
            exc=RuntimeError(SECRET); exc.response=response; raise exc
    with pytest.raises(StorageError) as caught: S3Storage('bucket',client=Bad()).get('a')
    clean(caught.value)


@pytest.mark.parametrize('code,value',[(44,SECRET),(0,'wrong-'+SECRET),(1,SECRET)],ids=['missing','shape','refused'])
def test_second_item(tmp_path,code,value):
    def run(argv,**kw):
        return SimpleNamespace(returncode=0 if argv[5]=='access-key-id' else code,
            stdout=ID if argv[5]=='access-key-id' else value)
    with pytest.raises(StorageError) as caught: loader()(SERVICE,runner=run,keychain=str(tmp_path/'keychain'))
    clean(caught.value)
    assert str(caught.value)=='S3 credentials '+('missing' if code==44 else 'refused')


def test_access_keychain(monkeypatch,tmp_path):
    from agentmesh_log.storage import r2creds
    from agentmesh_log import oskeystore
    monkeypatch.setattr(oskeystore,'login_keychain',lambda:str(tmp_path/'keychain'))
    calls=[]
    def run(argv,**kw):
        calls.append(argv[5])
        return SimpleNamespace(returncode=0,stdout=('a'*32+'.access' if argv[5]=='client-id' else 'b'*64)+'\n')
    assert r2creds.load_credentials('keychain:org.agentmesh.embed',kind='access',runner=run)==('a'*32+'.access','b'*64)
    assert calls==['client-id','client-secret']


@pytest.mark.parametrize('code,value,reason',[(0,'a'*32,'refused'),(0,'A'*32+'.access','refused'),(44,'','missing')],
    ids=['suffix','case','missing'])
def test_access_keychain_failures(tmp_path,code,value,reason):
    def run(argv,**kw):
        return SimpleNamespace(returncode=code,stdout=value if argv[5]=='client-id' else 'b'*64)
    with pytest.raises(StorageError,match='^embedding credentials '+reason+'$'):
        loader()('org.agentmesh.embed',runner=run,keychain=str(tmp_path/'keychain'),kind='access')


@pytest.mark.parametrize('entry',['load_credentials','load_keychain_credentials','WindowsR2Credentials','credentials_command'],
    ids=['load','mac','win','cmd'])
def test_unknown_credentials_kind(entry):
    from agentmesh_log.storage import r2creds
    args=() if entry=='WindowsR2Credentials' else ('check','wincred:test') if entry=='credentials_command' else ('test',)
    with pytest.raises(ValueError): getattr(r2creds,entry)(*args,kind='unknown')
