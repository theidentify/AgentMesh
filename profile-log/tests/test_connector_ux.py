"""connector approve on the first try: specific errors, the published gateway, defaults, pending and the control app.

Everything runs against a temporary home; gateway admin calls are faked and the real gateway is never contacted."""
import argparse
import io
import json
import plistlib
import urllib.error
import urllib.request
import pytest
from agentmesh_log import connector_cli as cli
from agentmesh_log.cli import parser, main
from agentmesh_log.connector import gateway_record, digest
from agentmesh_log.presence import ConsolePresence
from agentmesh_log.broker.keystore import BrokerHome, BrokerState, FileKeyStore
from agentmesh_log.keyring import initialize_keyring
from agentmesh_log.storage.filesystem import FilesystemStorage

ORIGIN='https://gateway.test.example'


def home_fixture(tmp_path, monkeypatch=None, profiles=('personal','work')):
    if monkeypatch is not None: monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE','1')
    from agentmesh_log.oskeystore import initialize
    home=BrokerHome(tmp_path/'home'); initialize(home,list(profiles),file_keystore=True)
    store=FileKeyStore(home); storage=FilesystemStorage(home.root/'replica')
    return home,store,storage


def publish_gateway(state,storage,origin=ORIGIN,seed=1):
    record=gateway_record(state.device,bytes([seed])*32,origin)
    return cli.controls(state,storage).publish(record)


class FakeGateway:
    """Records admin calls; never touches the network."""
    def __init__(self, code='123456', name='Claude', fail=None):
        self.code,self.name,self.fail,self.calls=code,name,fail or {},[]

    def __call__(self, state, action, payload):
        self.calls.append(action)
        if action in self.fail: raise ValueError(self.fail[action])
        if action=='pending':
            if payload['code_hash']!=digest(self.code): raise ValueError('code_not_found_or_expired')
            return {'client_id':'client','client_name':self.name}
        if action=='list':
            return {'requests':[{'code_hash':digest(self.code),'client_name':self.name,'created':1,'expires':2},{'code_hash':'zz','created':1,'expires':2}],'capped':False}
        return {'ok':True}


def yes(): return ConsolePresence(input_fn=lambda _:'approve',isatty=lambda:True,output=lambda _:None)


# 1. Specific errors

@pytest.mark.parametrize('remember,expected',[(None,'personal/inbox'),('',"personal/inbox"),('work','work/inbox'),('work/inbox','work/inbox'),('work/projects/inbox','work/projects/inbox')],ids=['none','empty','short','full','deep'])
def test_remember_shorthand(remember,expected):
    assert cli.remember_target(remember,['personal','work'])==expected


@pytest.mark.parametrize('remember',['work/common','/inbox','work/inbox/x'],ids=['common','root','nested'])
def test_remember_inbox(remember):
    with pytest.raises(ValueError,match='^remember_must_end_with_inbox$'): cli.remember_target(remember,['work'])


def test_approve_error_codes(tmp_path,monkeypatch):
    home,store,storage=home_fixture(tmp_path,monkeypatch)
    for code,expected in [(None,'code_required'),('12345','invalid_code'),('abcdef','invalid_code')]:
        with pytest.raises(ValueError,match='^'+expected+'$'): cli.approve(home,store,storage,code,None,None,yes(),admin=FakeGateway())
    with pytest.raises(ValueError,match='^code_mismatch$'):
        cli.approve(home,store,storage,'123456',None,None,yes(),admin=FakeGateway(),code_hash='00'*32)
    with pytest.raises(ValueError,match='^unknown_profile$'):
        cli.approve(home,store,storage,'123456',['nope'],None,yes(),admin=FakeGateway())
    with pytest.raises(ValueError,match='^remember_must_end_with_inbox$'):
        cli.approve(home,store,storage,'123456',['work'],'work/common',yes(),admin=FakeGateway())
    gateway=FakeGateway(code='999999')
    with pytest.raises(ValueError,match='^code_not_found_or_expired$'):
        cli.approve(home,store,storage,'123456',None,None,yes(),admin=gateway)
    assert gateway.calls==['pending']  # refused before the presence prompt and before any write
    assert list(storage.list('connectors/'))==[]


def test_no_gateway_origin(tmp_path,monkeypatch):
    home,store,storage=home_fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='^no_gateway_origin$'): cli.approve(home,store,storage,'123456',None,None,yes())


class Opener:
    def __init__(self, outcome): self.outcome,self.urls=outcome,[]
    def open(self, request, timeout):
        self.urls.append(request.full_url)
        if isinstance(self.outcome,Exception): raise self.outcome
        return io.BytesIO(self.outcome)


@pytest.mark.parametrize('status,body,expected',[
    (404,b'{"error":"code_not_found_or_expired"}','code_not_found_or_expired'),
    (409,b'{"error":"code_already_used"}','code_already_used'),
    (403,b'{"error":"clock_skew"}','gateway_clock_skew'),
    (403,b'{"error":"denied"}','gateway_denied'),
    (403,b'not json','gateway_denied'),
    (500,b'{"error":"secret-looking-token-abc"}','gateway_unavailable'),
],ids=['expired','used','skew','denied','html','other'])
def test_admin_http_errors(monkeypatch,status,body,expected):
    state=BrokerState.create(['personal'])
    error=urllib.error.HTTPError(ORIGIN,status,'x',{},io.BytesIO(body))
    monkeypatch.setattr(urllib.request,'build_opener',lambda *a:Opener(error))
    with pytest.raises(ValueError,match='^'+expected+'$'): cli.admin_request(state,'pending',{},origin=ORIGIN)


def test_admin_unreachable(monkeypatch):
    state=BrokerState.create(['personal'])
    monkeypatch.setattr(urllib.request,'build_opener',lambda *a:Opener(urllib.error.URLError('down')))
    with pytest.raises(ValueError,match='^gateway_unreachable$'): cli.admin_request(state,'pending',{},origin=ORIGIN)
    monkeypatch.setattr(urllib.request,'build_opener',lambda *a:Opener(b'[1]'))
    with pytest.raises(ValueError,match='^gateway_unavailable$'): cli.admin_request(state,'pending',{},origin=ORIGIN)


def test_cli_hint(monkeypatch,capsys):
    from agentmesh_log import cli as top
    def fail(*a,**k): raise ValueError('remember_must_end_with_inbox')
    monkeypatch.setattr(top,'execute',fail)
    assert main(['connector','approve','123456','--home','unused'])==1
    out=json.loads(capsys.readouterr().out)
    assert out['error']=='remember_must_end_with_inbox' and 'work/inbox' in out['hint']
    assert all('123456' not in hint for hint in cli.CODES.values())


# 2. The published gateway

def test_published_gateway(tmp_path,monkeypatch):
    home,store,storage=home_fixture(tmp_path,monkeypatch); state=store.load()
    assert cli.published_gateways(state,storage)==[]
    publish_gateway(state,storage)
    assert 'connector_origin' not in store.load().control
    assert cli.gateway_origin(store.load(),storage)==ORIGIN
    seen=[]
    monkeypatch.setattr(cli,'admin_request',lambda s,a,p,origin=None,**k:seen.append((a,origin)) or FakeGateway()(s,a,p))
    result=cli.approve(home,store,storage,'123456',None,None,yes())
    assert seen==[('pending',ORIGIN),('approve',ORIGIN)] and result['state']=='active'
    assert 'connector_origin' not in store.load().control  # approve never writes the pointer


def test_two_gateways(tmp_path,monkeypatch):
    home,store,storage=home_fixture(tmp_path,monkeypatch); state=store.load()
    publish_gateway(state,storage); publish_gateway(state,storage,'https://other.test.example',seed=2)
    with pytest.raises(ValueError,match='^no_gateway_origin$'): cli.gateway_origin(state,storage)
    with pytest.raises(ValueError,match='^no_gateway_origin$'): cli.use_gateway(store,storage,None)
    with pytest.raises(ValueError,match='^gateway_origin_unknown$'): cli.use_gateway(store,storage,'https://evil.example')
    before=sorted(storage.list('registry/gateway/'))
    assert cli.use_gateway(store,storage,ORIGIN+'/')=={'origin':ORIGIN}
    assert store.load().control['connector_origin']==ORIGIN
    assert sorted(storage.list('registry/gateway/'))==before  # gateway-use never republishes


def test_forged_gateway(tmp_path,monkeypatch):
    home,store,storage=home_fixture(tmp_path,monkeypatch); state=store.load()
    path=publish_gateway(state,storage); record=json.loads(storage.get(path)); record['origin']='https://evil.example'
    target=home.root/'replica'/path; target.chmod(0o600); target.write_text(json.dumps(record))
    assert cli.published_gateways(state,storage)==[]


def test_gateway_use_cli(tmp_path,monkeypatch):
    home,store,storage=home_fixture(tmp_path,monkeypatch); publish_gateway(store.load(),storage)
    monkeypatch.setattr(cli,'broker_config',lambda *a:{})
    monkeypatch.setattr(cli,'select_store',lambda *a:store)
    args=parser().parse_args(['connector','gateway-use','--home',str(home.root)])
    assert cli.execute(args,backend=None,console=None)=={'origin':ORIGIN}


# 3. Fewer arguments

def test_parser_defaults():
    args=parser().parse_args(['connector','approve'])
    assert args.code is None and args.home is None and args.profiles is None and args.remember is None


def test_broker_config(tmp_path):
    plist=tmp_path/'broker.plist'; home=tmp_path/'home'
    plist.write_bytes(plistlib.dumps({'ProgramArguments':['/py','-m','agentmesh_log.cli','broker','run','--home',str(home),
        '--s3-bucket','bucket','--s3-prefix','test/','--s3-endpoint','https://r2.test.example','--s3-credentials','ref','--sync-interval','120']}))
    config=cli.broker_config(plist)
    assert config=={'home':str(home),'s3_bucket':'bucket','s3_prefix':'test/','s3_endpoint':'https://r2.test.example','s3_credentials':'ref'}
    blank=lambda **k:argparse.Namespace(**{'home':None,'filesystem':None,'s3_bucket':None,'s3_prefix':None,'s3_endpoint':None,'s3_credentials':None,**k})
    args=cli.fill_from_broker(blank(),config)
    assert (args.home,args.s3_bucket,args.s3_prefix,args.s3_endpoint,args.s3_credentials)==(str(home),'bucket','test/','https://r2.test.example','ref')
    other=cli.fill_from_broker(blank(home=str(tmp_path/'scratch')),config)  # another home never inherits the real remote
    assert other.s3_bucket is None and other.s3_prefix==''
    explicit=cli.fill_from_broker(blank(filesystem=str(tmp_path/'r')),config)
    assert explicit.s3_bucket is None and explicit.filesystem==str(tmp_path/'r')
    assert cli.broker_config(tmp_path/'missing.plist')=={}
    plist.write_bytes(b'not a plist'); assert cli.broker_config(plist)=={}


def test_approve_defaults(tmp_path,monkeypatch):
    home,store,storage=home_fixture(tmp_path,monkeypatch); state=store.load()
    state.control['connector_origin']=ORIGIN; store.save(state)
    monkeypatch.setattr(cli,'broker_config',lambda *a:{})
    monkeypatch.setattr(cli,'select_store',lambda *a:store)
    gateway=FakeGateway(); monkeypatch.setattr(cli,'admin_request',lambda s,a,p,**k:gateway(s,a,p))
    answers=iter(['123456','approve'])
    console=ConsolePresence(input_fn=lambda _:next(answers),isatty=lambda:True,output=lambda _:None)
    args=parser().parse_args(['connector','approve','--home',str(home.root)])
    result=cli.execute(args,backend=None,console=console)
    assert result['read']==['personal','work'] and result['remember']=='personal/inbox' and result['client_name']=='Claude'
    args=parser().parse_args(['connector','approve','123456','--home',str(home.root),'--profiles','work','--remember','work'])
    gateway.calls.clear(); answers=iter(['approve'])
    assert cli.execute(args,backend=None,console=console)['remember']=='work/inbox'
    no_tty=ConsolePresence(input_fn=lambda _:'123456',isatty=lambda:False,output=lambda _:None)
    with pytest.raises(ValueError,match='^code_required$'):
        cli.execute(parser().parse_args(['connector','approve','--home',str(home.root)]),backend=None,console=no_tty)


# 4. Pending requests

def test_pending_requests(tmp_path,monkeypatch):
    home,store,storage=home_fixture(tmp_path,monkeypatch)
    result=cli.pending_requests(store.load(),storage,admin=FakeGateway())
    assert result=={'requests':[{'code_hash':digest('123456'),'client_name':'Claude','created':1,'expires':2}],'capped':False}
    with pytest.raises(ValueError,match='^gateway_unavailable$'): cli.pending_requests(store.load(),storage,admin=lambda *a:{'nope':1})


# 6. The control app: the supervisor's own presence

class Sheet:
    def __init__(self, answer=True, available=True): self.answer,self._available,self.seen=answer,available,[]
    def available(self): return self._available
    def confirm(self, action, summary): self.seen.append((action,summary)); return self.answer


def control_fixture(tmp_path,monkeypatch,sheet=None,gateway=None):
    from agentmesh_log.control.connector import Connector
    from agentmesh_log.control.layout import Layout
    from agentmesh_log.control.moves import Moves
    from agentmesh_log.control.ops import Control
    from agentmesh_log.control.store import Store
    from control.support import FakeBackend
    home,store,storage=home_fixture(tmp_path,monkeypatch)
    gateway=gateway or FakeGateway()
    monkeypatch.setattr(cli,'admin_request',lambda s,a,p,**k:gateway(s,a,p))
    state=store.load(); state.control['connector_origin']=ORIGIN; store.save(state)
    layout=Layout(tmp_path/'am').ensure(); sheet=sheet or Sheet()
    moves=Moves(layout,Store(layout.state),sheet); clock=[0.0]
    connector=Connector(layout,sheet,moves,context=lambda:(home,store,storage),clock=lambda:clock[0])
    control=Control(layout,FakeBackend(),moves=moves,connector=connector)
    call=lambda op,args:control.handle(dict(v=1,id='1',op=op,args=args),dict(pid=1,exe='app'))
    return control,call,sheet,gateway,layout,storage,clock


def test_ctl_pending_cache(tmp_path,monkeypatch):
    control,call,sheet,gateway,layout,storage,clock=control_fixture(tmp_path,monkeypatch)
    first=call('connector.pending',{}); assert first['ok'] and first['result']['requests'][0]['client_name']=='Claude'
    call('connector.pending',{}); assert gateway.calls==['list']
    clock[0]=30; call('connector.pending',{}); assert gateway.calls==['list','list']
    assert not layout.audit.exists()  # tier 0: never audited


def test_ctl_approve(tmp_path,monkeypatch):
    control,call,sheet,gateway,layout,storage,clock=control_fixture(tmp_path,monkeypatch)
    response=call('connector.approve',{'code':'123456','code_hash':digest('123456')})
    assert response['ok'] and response['result']['state']=='active'
    assert sheet.seen and sheet.seen[0][0]=='approve' and 'Claude' in sheet.seen[0][1] and 'personal/inbox' in sheet.seen[0][1]
    assert gateway.calls==['pending','approve'] and list(storage.list('connectors/'))
    audit=layout.audit.read_text()
    assert '123456' not in audit and '"code": "redacted"' in audit.replace('":"','": "')


@pytest.mark.parametrize('args,expected',[
    ({'code':'123456','code_hash':'00'*32},'code_mismatch'),
    ({'code':'654321','code_hash':None},'code_not_found_or_expired'),
    ({'code':'12','code_hash':None},'invalid_code'),
    ({'code':7,'code_hash':None},'bad_args'),
],ids=['mismatch','expired','short','type'])
def test_ctl_refusals(tmp_path,monkeypatch,args,expected):
    control,call,sheet,gateway,layout,storage,clock=control_fixture(tmp_path,monkeypatch)
    response=call('connector.approve',args)
    assert not response['ok'] and response['error']['code']==expected
    assert sheet.seen==[] and list(storage.list('connectors/'))==[]


def test_ctl_denied_sheet(tmp_path,monkeypatch):
    control,call,sheet,gateway,layout,storage,clock=control_fixture(tmp_path,monkeypatch,sheet=Sheet(answer=False))
    response=call('connector.approve',{'code':'123456','code_hash':None})
    assert response['error']['code']=='presence_required' and list(storage.list('connectors/'))==[]
    unavailable=control_fixture(tmp_path/'b',monkeypatch,sheet=Sheet(available=False))[1]
    assert unavailable('connector.approve',{'code':'123456','code_hash':None})['error']['code']=='presence_unavailable'


def test_ctl_no_connector():
    from agentmesh_log.control.ops import Control
    from agentmesh_log.control.layout import Layout
    control=Control(Layout('/nonexistent'),None)
    assert control.handle(dict(v=1,id='1',op='connector.pending',args={}),{})['error']['code']=='presence_unavailable'
