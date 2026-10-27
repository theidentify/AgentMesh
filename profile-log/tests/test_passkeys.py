"""P6-P1..P6-P5: real WebAuthn verification with a software P-256 authenticator."""
import hashlib
import json
import secrets
import struct
import cbor2
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from phase6_support import device
from agentmesh_log import passkeys
from agentmesh_log.connector import digest
from agentmesh_log.registry_store import now

RP='status.krisb8.com'
ORIGIN='https://'+RP

class Authenticator:
    def __init__(self):
        self.key=ec.generate_private_key(ec.SECP256R1()); self.id=secrets.token_bytes(32)
    def response(self,options,*,register=False,uv=True,origin=ORIGIN,rp=RP,count=0,forged=False):
        client=json.dumps({'type':'webauthn.create' if register else 'webauthn.get','challenge':options['challenge'],'origin':origin,'crossOrigin':False},separators=(',',':')).encode()
        data=hashlib.sha256(rp.encode()).digest()+bytes([1|(4 if uv else 0)|(64 if register else 0)])+struct.pack('>I',count)
        response={'clientDataJSON':passkeys.b64(client)}
        if register:
            n=self.key.public_key().public_numbers()
            cose=cbor2.dumps({1:2,3:-7,-1:1,-2:n.x.to_bytes(32,'big'),-3:n.y.to_bytes(32,'big')})
            data+=bytes(16)+struct.pack('>H',len(self.id))+self.id+cose
            response['attestationObject']=passkeys.b64(cbor2.dumps({'fmt':'none','attStmt':{},'authData':data}))
        else:
            response.update(authenticatorData=passkeys.b64(data),signature=passkeys.b64(self.key.sign(data+hashlib.sha256(client).digest(),ec.ECDSA(hashes.SHA256())) if not forged else b'bad'),userHandle=None)
        return {'id':passkeys.b64(self.id),'rawId':passkeys.b64(self.id),'type':'public-key','response':response}

@pytest.fixture
def setup(tmp_path):
    h,s,storage,cr,p,key,pin,broker,b=device(tmp_path)
    service=passkeys.Passkeys(h,s,storage)
    yield service,s,cr,b
    broker.close()

def enroll(service,store,auth=None):
    auth=auth or Authenticator(); code='ABCDEFGHJK'
    state=store.load();state.control['passkey_codes']={digest(code):{'exp':now()+600000,'label':'Test'}};store.save(state)
    start=service.handle({'step':'register_begin','code':code})
    assert 'options' in start
    result=service.handle({'step':'register_finish','device_id':start['device_id'],'challenge_id':start['challenge_id'],'credential':auth.response(start['options'],register=True)})
    assert result=={'status':'ok'}
    return auth

def begin(service):
    return service.handle({'step':'approve_begin','code_hash':'ab'*32,'client_id_hash':digest('client'),'client_name':'Test app'})

def finish(service,start,auth,**kw):
    return service.handle({'step':'approve_finish','code_hash':'ab'*32,'device_id':start['device_id'],'challenge_id':start['challenge_id'],'assertion':auth.response(start['options'],**kw)})

def test_p6_p3_code_and_registration(setup):
    service,store,cr,b=setup
    for code in ('','bad','ABCDEFGHJK'):
        assert service.handle({'step':'register_begin','code':code})=={'error':'denied'}
    auth=enroll(service,store)
    assert service.handle({'step':'register_begin','code':'ABCDEFGHJK'})=={'error':'denied'}
    assert len(service.credentials())==1

@pytest.mark.parametrize('variation',[{'uv':False},{'origin':'https://evil.example'},{'rp':'krisb8.com'}],ids=['uv','origin','rp'])
def test_p6_p3_register_verify(setup,variation):
    service,store,cr,b=setup
    state=store.load();state.control['passkey_codes']={digest('ABCDEFGHJK'):{'exp':now()+600000,'label':'Test'}};store.save(state)
    start=service.handle({'step':'register_begin','code':'ABCDEFGHJK'})
    result=service.handle({'step':'register_finish','device_id':start['device_id'],'challenge_id':start['challenge_id'],'credential':Authenticator().response(start['options'],register=True,**variation)})
    assert result=={'error':'denied'} and service.credentials()=={}

@pytest.mark.parametrize('variation',[{'uv':False},{'forged':True},{'origin':'https://evil.example'},{'rp':'krisb8.com'}],ids=['uv','signature','origin','rp'])
def test_p6_p4_assertion_refusal(setup,variation):
    service,store,cr,b=setup;auth=enroll(service,store);start=begin(service)
    before=list(cr.storage.list('connectors/'))
    assert finish(service,start,auth,**variation)=={'error':'denied'}
    assert list(cr.storage.list('connectors/'))==before

def test_p6_p5_approve_and_replay(setup):
    service,store,cr,b=setup;auth=enroll(service,store);start=begin(service)
    result=finish(service,start,auth)
    assert result['action']=='approve'
    policy=cr.policy(result['payload']['app_id'])
    assert policy['read']==start['policy']['read'] and policy['remember']==start['policy']['remember']
    assert policy['oauth_client_id_hash']==digest('client')
    assert finish(service,start,auth)=={'error':'denied'}
    # Synced passkeys allow a zero counter to remain zero.
    assert finish(service,begin(service),auth)['action']=='approve'

@pytest.mark.parametrize('mutation',['expired','code','client','policy','challenge','removed','unknown','counter','wrong_device'],ids=['expired','code','client','policy','challenge','removed','unknown','counter','wrong-device'])
def test_p6_p4_bindings(setup,mutation):
    service,store,cr,b=setup;auth=enroll(service,store);start=begin(service)
    state=store.load();pending=state.control['passkey_challenges'][start['challenge_id']]
    if mutation=='expired': pending['exp']=now()-1
    if mutation=='client': pending['client_id_hash']='cd'*32
    if mutation=='policy': pending['policy']['max_label']='general'
    if mutation=='challenge': start['options']['challenge']=passkeys.b64(secrets.token_bytes(32))
    if mutation=='counter': state.control['passkey_counters'][passkeys.credential_hash(auth.id)]=4
    store.save(state)
    if mutation=='code':
        args={'step':'approve_finish','code_hash':'cd'*32,'device_id':start['device_id'],'challenge_id':start['challenge_id'],'assertion':auth.response(start['options'])}
        invoke=lambda:service.handle(args)
    else:
        if mutation=='removed': service.publish('passkey-removal',passkeys.credential_hash(auth.id),credential_id_sha256=passkeys.credential_hash(auth.id),removed_at=now())
        if mutation=='unknown': auth=Authenticator()
        if mutation=='wrong_device': start['device_id']='ef'*16
        invoke=lambda:finish(service,start,auth,count=3 if mutation=='counter' else 0)
    before={p:cr.storage.get(p) for p in cr.storage.list('connectors/')}
    assert invoke()=={'error':'wrong_device' if mutation=='wrong_device' else 'denied'}
    assert before=={p:cr.storage.get(p) for p in cr.storage.list('connectors/')}


def test_p6_p3_expired_and_reused_registration(setup):
    service,store,cr,b=setup
    state=store.load();state.control['passkey_codes']={digest('ABCDEFGHJK'):{'exp':now()-1,'label':'Test'}};store.save(state)
    assert service.handle({'step':'register_begin','code':'ABCDEFGHJK'})=={'error':'denied'}
    state.control['passkey_codes'][digest('ABCDEFGHJK')]['exp']=now()+600000;store.save(state)
    start=service.handle({'step':'register_begin','code':'ABCDEFGHJK'})
    auth=Authenticator();args={'step':'register_finish','device_id':start['device_id'],'challenge_id':start['challenge_id'],'credential':auth.response(start['options'],register=True)}
    assert service.handle(args)=={'status':'ok'}
    assert service.handle(args)=={'error':'denied'}
    assert len(service.credentials())==1


def test_p6_p1_registry_invalid_and_revoked(setup):
    from agentmesh_log.registry_store import RegistryStore,revocation,signed
    from agentmesh_log.encoding import wire
    service,store,cr,b=setup;auth=enroll(service,store);state=store.load();key=passkeys.credential_hash(auth.id)
    path='registry/passkeys/'+key+'.json';original=cr.storage.get(path)
    record=json.loads(original);record['label']='tampered';(cr.storage.root/path).write_bytes(wire(record))
    assert service.credentials()=={}
    (cr.storage.root/path).write_bytes(original)
    assert len(service.credentials())==1
    from agentmesh_log.identity import DeviceIdentity
    from agentmesh_log.agekeys import DeviceAgeIdentity
    from agentmesh_log.registry_store import device_record
    other=DeviceIdentity();registry=RegistryStore(cr.storage,state.account.public_record())
    registry.publish_device(device_record(other,other.certificate(state.account),DeviceAgeIdentity()))
    registry.publish_revocation(revocation(state.device.device_id,other))
    assert service.credentials()=={}


def test_p6_p1_invalid_records(setup):
    from agentmesh_log.identity import DeviceIdentity
    from agentmesh_log.agekeys import DeviceAgeIdentity
    from agentmesh_log.registry_store import RegistryStore,device_record,signed
    from agentmesh_log.encoding import wire
    service,store,cr,b=setup;auth=enroll(service,store);state=store.load();key=passkeys.credential_hash(auth.id)
    path='registry/passkeys/'+key+'.json';record=json.loads(cr.storage.get(path));other=DeviceIdentity()
    body={k:v for k,v in record.items() if k not in ('signature','format','issuer_device')}
    unknown=signed('passkey',other._private,**body,issuer_device=other.device_id)
    (cr.storage.root/path).write_bytes(wire(unknown));assert service.credentials()=={}
    RegistryStore(cr.storage,state.account.public_record()).publish_device(device_record(other,other.certificate(state.account,issuer=state.device),DeviceAgeIdentity()))
    assert service.credentials()=={}  # account certificate without read authority
    for change in ({'public_key':'bad'},{'credential_id':'bad='},{'label':'\n'},{'created_at':-1},{'extra':True}):
        (cr.storage.root/path).write_bytes(wire(signed('passkey',state.device._private,**{**body,**change},issuer_device=state.device.device_id)))
        assert service.credentials()=={}


def test_p6_p2_presence_list_remove_and_settings(setup):
    from agentmesh_log.presence import ConsolePresence
    from agentmesh_log.cli import parser
    service,store,cr,b=setup
    no=ConsolePresence(input_fn=lambda _:'no',isatty=lambda:True,output=lambda _:None)
    yes=ConsolePresence(input_fn=lambda prompt:'approve' if 'approve' in prompt else 'revoke',isatty=lambda:True,output=lambda _:None)
    with pytest.raises(ValueError,match='presence_required'): service.add('Test',no)
    code=service.add('Test',yes)['code'];assert len(code)==10
    state=store.load();assert code not in json.dumps(state.control)
    assert digest(code) in state.control['passkey_codes']
    auth=enroll(service,store);listing=service.listing()['passkeys'];assert len(listing)==1
    assert set(listing[0])=={'label','credential_hash_prefix','created_at','issuer'}
    with pytest.raises(ValueError,match='presence_required'): service.remove(listing[0]['credential_hash_prefix'],no)
    original=service.credentials
    service.credentials=lambda:{'abc':{},'abd':{}}
    with pytest.raises(ValueError,match='ambiguous_passkey'): service.remove('ab',yes)
    service.credentials=original
    assert service.remove(listing[0]['credential_hash_prefix'],yes)=={'status':'ok'}
    assert service.listing()=={'passkeys':[]}
    for action,extra in [('add',['--label','Key']),('list',[]),('remove',['abcdef']),('settings',['--rp-id','status.example','--origin','https://status.example'])]:
        args=parser().parse_args(['connector','passkey',action,'--home',str(service.home.root),*extra]);assert args.passkey_action==action


def test_p6_p5_inbox_envelope(tmp_path,monkeypatch):
    from agentmesh_log.connector_cli import controls
    from test_phase6_cli import home_fixture
    from agentmesh_log.identity import verify_signature
    from agentmesh_log.registry_store import domain
    from agentmesh_log.broker.grants import resolve
    from agentmesh_log.broker.keystore import BrokerHome,FileKeyStore
    from agentmesh_log.oskeystore import initialize
    from agentmesh_log.storage.filesystem import FilesystemStorage
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE','1')
    h=BrokerHome(tmp_path/'home');initialize(h,['personal','work'],file_keystore=True)
    s=FileKeyStore(h);storage=FilesystemStorage(h.root/'replica');remote=WriteOnlyRemote()
    service=passkeys.Passkeys(h,s,remote);auth=enroll(service,s)
    start=begin(service)
    with pytest.raises(ValueError): resolve(s.load(),'personal/inbox')
    result=finish(service,start,auth,count=1)
    verify_signature(result,s.load().device.public_bytes,domain('connector-admin'))
    assert remote.reads==0 and any(p.startswith('connectors/inboxes/') for p in remote.writes)
    for path,data in remote.writes.items(): assert storage.get(path)==data
    assert resolve(s.load(),'personal/inbox')==start['policy']['remember']['scope']
    policy=controls(s.load(),storage).policy(result['payload']['app_id'])
    for key,value in start['policy'].items(): assert policy[key]==value
    assert finish(service,begin(service),auth,count=1)=={'error':'denied'}
    assert finish(service,begin(service),auth,count=2)['action']=='approve'


def test_p6_p5_reserved_bridge(setup):
    from agentmesh_log.bridge import STATUS_APP,request_headers
    from agentmesh_log.encoding import wire
    service,store,cr,b=setup;auth=enroll(service,store)
    args={'step':'approve_begin','code_hash':'ab'*32,'client_id_hash':digest('client'),'client_name':'Test'}
    assert b.invoke('ab'*16,{'tool':'passkey','arguments':args})=={'error':'denied'}
    start=b.invoke(STATUS_APP,{'tool':'passkey','arguments':args})
    assert 'options' in start
    assert b.invoke(STATUS_APP,{'tool':'passkey','arguments':{'step':'approve_finish','device_id':'ef'*16,'challenge_id':start['challenge_id'],'code_hash':'ab'*32,'assertion':{}}})=={'error':'wrong_device'}


def test_p6_p2_cli_execution(setup,monkeypatch):
    from agentmesh_log.cli import parser
    from agentmesh_log.connector_cli import execute
    from agentmesh_log.presence import ConsolePresence
    service,store,cr,b=setup
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE','1')
    yes=ConsolePresence(input_fn=lambda prompt:'approve' if 'approve' in prompt else 'revoke',isatty=lambda:True,output=lambda _:None)
    def run(action,*args):
        return execute(parser().parse_args(['connector','passkey',action,'--home',str(service.home.root),*args]),backend=None,console=yes)
    assert len(run('add','--label','CLI')['code'])==10
    assert run('list')=={'passkeys':[]}
    auth=enroll(service,store)
    assert len(run('list')['passkeys'])==1
    assert run('remove',passkeys.credential_hash(auth.id)[:16])=={'status':'ok'}
    assert run('settings','--rp-id','status.example','--profiles','work','--remember','work/inbox','--max-label','general')['settings']=={'rp_id':'status.example','origin':'https://status.example','profiles':['work'],'remember':'work/inbox','max_label':'general'}
    with pytest.raises(ValueError,match='invalid_request'): run('settings','--origin','https://other.example')


def test_p6_p4_recheck_scope_authority(setup):
    service,store,cr,b=setup;auth=enroll(service,store);start=begin(service)
    # Lose one requested profile's grants between begin and finish.
    profile=start['policy']['read'][1]['profile'];storage=service.storage
    class Narrowed:
        def list(self,prefix): return [p for p in storage.list(prefix) if not p.startswith('keyring/'+profile+'/')]
        def get(self,path): return storage.get(path)
        def put(self,*args): raise AssertionError('must not publish')
    service.local=Narrowed()
    before=list(storage.list('connectors/'))
    assert finish(service,start,auth)=={'error':'denied'}
    assert list(storage.list('connectors/'))==before


def test_p6_p4_cli_lock(setup,monkeypatch):
    from agentmesh_log import connector_cli
    from agentmesh_log.cli import parser
    service,store,cr,b=setup
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE','1')
    # A concurrent CLI save must never restore a consumed challenge or old counter.
    monkeypatch.setattr(connector_cli,'approve',lambda *args,**kwargs:{'ok':True})
    args=parser().parse_args(['connector','approve','123456','--home',str(service.home.root),'--profiles','personal','--remember','personal/inbox'])
    with service.home.lock(),pytest.raises(FileExistsError):
        connector_cli.execute(args,backend=None,console=None)


def test_p6_p4_broker_lock(setup):
    service,store,cr,b=setup
    client=b.client_factory(b.bridge_key); client.broker.lock_wait=0.05
    with service.home.lock():
        assert client.request({'op':'connector_close_secret','app_id':'ab'*16})=={'error':'busy'}


class WriteOnlyRemote:
    def __init__(self): self.writes={};self.reads=0
    def get(self,*args): self.reads+=1;raise AssertionError('request read remote')
    def list(self,*args,**kwargs): self.reads+=1;raise AssertionError('request listed remote')
    def put(self,path,data): self.writes[path]=data


def test_p6_p8_local_reads(setup):
    service,store,cr,b=setup;remote=WriteOnlyRemote();service.storage=remote
    from agentmesh_log.bridge import STATUS_APP
    b.storage=remote
    service.handle=lambda args:b.invoke(STATUS_APP,{'tool':'passkey','arguments':args})
    auth=enroll(service,store);start=begin(service);result=finish(service,start,auth)
    assert result['action']=='approve' and remote.reads==0
    assert any(p.startswith('registry/passkeys/') for p in remote.writes)
    assert result['payload']['policy_ref'] in remote.writes
    for path,data in remote.writes.items(): assert cr.storage.get(path)==data
    key=passkeys.credential_hash(auth.id)
    service.publish('passkey-removal',key,credential_id_sha256=key,removed_at=now())
    assert service.credentials()=={} and remote.reads==0


def test_p6_p8_control_sync(setup,tmp_path):
    from agentmesh_log.keyring import sync_control
    from agentmesh_log.storage.filesystem import FilesystemStorage
    service,store,cr,b=setup;auth=enroll(service,store);key=passkeys.credential_hash(auth.id)
    remote=FilesystemStorage(tmp_path/'remote')
    for path in cr.storage.list(''): remote.put(path,cr.storage.get(path))
    credential_path='registry/passkeys/'+key+'.json'
    (cr.storage.root/credential_path).unlink()
    sync_control(service.home,store,remote,apply_lock=b.local_update)
    assert key in service.credentials()
    from agentmesh_log.registry_store import signed
    from agentmesh_log.encoding import wire
    state=store.load();path='registry/passkey-removals/'+key+'.json'
    remote.put(path,wire(signed('passkey-removal',state.device._private,credential_id_sha256=key,removed_at=now(),issuer_device=state.device.device_id)))
    sync_control(service.home,store,remote,apply_lock=b.local_update)
    assert cr.storage.get(path)==remote.get(path) and service.credentials()=={}


@pytest.mark.parametrize('wait_at',['publish','verify'],ids=['publish','verify'])
def test_p6_p8_concurrent_recall(tmp_path,monkeypatch,wait_at):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from io import BytesIO
    from email.message import Message
    from types import SimpleNamespace
    from agentmesh_log import bridge
    from agentmesh_log.encoding import wire,parse
    h,s,storage,cr,p,key,pin,broker,b=device(tmp_path)
    service=passkeys.Passkeys(h,s,storage);auth=enroll(service,s);start=begin(service)
    entered=Event();release=Event()
    class Remote(WriteOnlyRemote):
        def put(self,path,data):
            entered.set();assert release.wait(5);super().put(path,data)
    remote=Remote() if wait_at=='publish' else WriteOnlyRemote();b.storage=remote
    if wait_at=='verify':
        verify=passkeys.webauthn.verify_authentication_response
        def waiting(**kwargs):
            entered.set();assert release.wait(5);return verify(**kwargs)
        monkeypatch.setattr(passkeys.webauthn,'verify_authentication_response',waiting)
    # Exercise the real HTTP handler without opening sockets in this sandbox.
    class Server:
        def __init__(self,address,handler): self.handler=handler
    monkeypatch.setattr(bridge,'ThreadingHTTPServer',Server)
    handler=bridge.bind_server(b,'127.0.0.1',8766).handler
    def request(app,tool,args):
        raw=wire({'tool':tool,'arguments':args});headers=Message()
        for k,v in bridge.request_headers(key,pin,app,raw).items(): headers[k]=v
        headers['Content-Length']=str(len(raw));obj=handler.__new__(handler)
        obj.headers=headers;obj.rfile=BytesIO(raw);obj.wfile=BytesIO();obj.path='/bridge'
        obj.connection=SimpleNamespace(settimeout=lambda _:None)
        obj.send_response=lambda *_:None;obj.send_header=lambda *_:None;obj.end_headers=lambda:None
        obj.do_POST();return parse(obj.wfile.getvalue())
    args={'step':'approve_finish','code_hash':'ab'*32,'device_id':start['device_id'],'challenge_id':start['challenge_id'],'assertion':auth.response(start['options'])}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            task=pool.submit(request,bridge.STATUS_APP,'passkey',args)
            try:
                assert entered.wait(2)
                recall=pool.submit(request,p['app_id'],'recall',{'query':'test'})
                result=recall.result(timeout=2)
                assert 'error' not in result
                assert remote.reads==0
                if wait_at=='publish': assert start['challenge_id'] not in s.load().control['passkey_challenges']
            finally: release.set()
            assert task.result(timeout=2)['action']=='approve'
    finally: release.set();broker.close()


@pytest.mark.parametrize('register',[False,True],ids=['approve','register'])
def test_p6_p8_finish_race(setup,monkeypatch,register):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    service,store,cr,b=setup;auth=enroll(service,store)
    if register:
        state=store.load();state.control['passkey_codes']={digest('ABCDEFGHJK'):{'exp':now()+600000,'label':'Other'}};store.save(state)
        start=service.handle({'step':'register_begin','code':'ABCDEFGHJK'});auth=Authenticator()
        args={'step':'register_finish','device_id':start['device_id'],'challenge_id':start['challenge_id'],'credential':auth.response(start['options'],register=True)}
    else:
        start=begin(service)
        args={'step':'approve_finish','code_hash':'ab'*32,'device_id':start['device_id'],'challenge_id':start['challenge_id'],'assertion':auth.response(start['options'])}
    name='verify_registration_response' if register else 'verify_authentication_response'
    verify=getattr(passkeys.webauthn,name);barrier=Barrier(2)
    def concurrent(**kwargs):
        result=verify(**kwargs);barrier.wait(timeout=3);return result
    monkeypatch.setattr(passkeys.webauthn,name,concurrent)
    remote=WriteOnlyRemote();service.storage=remote
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks=[pool.submit(service.handle,args) for _ in range(2)]
        results=[task.result(timeout=5) for task in tasks]
    assert sum('error' not in r for r in results)==1
    assert sum(r=={'error':'denied'} for r in results)==1
    assert len(remote.writes)==1 and remote.reads==0
    assert start['challenge_id'] not in store.load().control['passkey_challenges']


@pytest.mark.parametrize('case',['missing','unissued','wrong','expired'],ids=['missing','unissued','wrong','expired'])
def test_p6_p9_begin_no_state(setup,monkeypatch,case):
    service,store,cr,b=setup;at=now();service.clock=lambda:at
    state=store.load();state.control['passkey_codes']={digest('ABCDEFGHJK'):{'exp':at+600000,'label':'Test'}};store.save(state)
    # Preserve an existing valid challenge as well as refusing to create a new one.
    assert 'options' in service.handle({'step':'register_begin','code':'ABCDEFGHJK'})
    state=store.load()
    if case=='unissued': state.control['passkey_codes']={}
    if case=='expired': state.control['passkey_codes'][digest('ABCDEFGHJK')]['exp']=at-1
    store.save(state);before=store.fingerprint();challenges=set(state.control['passkey_challenges'])
    calls=[];challenge=service.challenge
    def observed(*args,**kwargs):
        calls.append(True);return challenge(*args,**kwargs)
    monkeypatch.setattr(service,'challenge',observed)
    args={'step':'register_begin'}
    if case!='missing': args['code']='LMNPQRSTUV' if case=='wrong' else 'ABCDEFGHJK'
    result=service.handle(args)
    assert result=={'error':'denied'}
    assert store.fingerprint()==before
    assert set(store.load().control['passkey_challenges'])==challenges
    # Validate at the entry point, before invoking even the defensive challenge guard.
    assert not calls


@pytest.mark.parametrize('register',[False,True],ids=['approve','register'])
def test_p6_p9_expired_finish(setup,register):
    from copy import deepcopy
    service,store,cr,b=setup;auth=enroll(service,store)
    at=now();service.clock=lambda:at
    if register:
        state=store.load();state.control['passkey_codes']={digest('ABCDEFGHJK'):{'exp':at+600000,'label':'Other'}};store.save(state)
        start=service.handle({'step':'register_begin','code':'ABCDEFGHJK'});auth=Authenticator()
        args={'step':'register_finish','device_id':start['device_id'],'challenge_id':start['challenge_id'],'credential':auth.response(start['options'],register=True)}
    else:
        start=begin(service)
        args={'step':'approve_finish','code_hash':'ab'*32,'device_id':start['device_id'],'challenge_id':start['challenge_id'],'assertion':auth.response(start['options'],count=1)}
    state=store.load();challenge=deepcopy(state.control['passkey_challenges'][start['challenge_id']])
    assert challenge['exp']==at+120000
    counters=deepcopy(state.control.get('passkey_counters',{}))
    before={path:cr.storage.get(path) for path in cr.storage.list('')}
    # Advance time without tampering with the signed challenge binding or assertion.
    service.clock=lambda:challenge['exp']+1
    if register: assert service.clock()<state.control['passkey_codes'][digest('ABCDEFGHJK')]['exp']
    result=service.handle(args)
    assert result=={'error':'denied'}
    assert store.load().control.get('passkey_counters',{})==counters
    assert before=={path:cr.storage.get(path) for path in cr.storage.list('')}
    # Expiry may consume the challenge: restore the pre-finish snapshot for the control.
    store.save(state)
    # The same real-library response succeeds before challenge expiry.
    service.clock=lambda:at+1
    result=service.handle(args)
    assert result.get('status')=='ok' if register else result.get('action')=='approve'
