import pytest
from agentmesh_log.cli import parser
from agentmesh_log.bridge.services import render
from agentmesh_log.connector_cli import approve, allow_secret
from agentmesh_log.presence import ConsolePresence
from agentmesh_log.broker.keystore import BrokerHome, BrokerState, FileKeyStore
from agentmesh_log.keyring import initialize_keyring
from agentmesh_log.storage.filesystem import FilesystemStorage


def home_fixture(tmp_path):
    home=BrokerHome(tmp_path/'home'); state=BrokerState.create(['personal','work']); store=FileKeyStore(home)
    storage=FilesystemStorage(home.root/'replica'); initialize_keyring(state,storage); store.save(state)
    return home,store,storage


def test_phase6_cli_parser_and_render(tmp_path):
    args=parser().parse_args(['connector','approve','123456','--home',str(tmp_path),'--profiles','personal,work','--remember','personal/inbox'])
    assert args.action=='approve'
    for platform in ('macos','windows'):
        result=render(tmp_path/platform,tmp_path,tmp_path/'bin'/'python',platform)
        assert result['rendered']


def test_p6_4_presence(tmp_path):
    home,store,storage=home_fixture(tmp_path)
    console=ConsolePresence(input_fn=lambda _: 'no',isatty=lambda:True,output=lambda _:None)
    with pytest.raises(ValueError,match='presence_required'):
        allow_secret(home,store,storage,'ab'*16,15,console)
    assert list(storage.list('connectors/'))==[]


def test_p6_3_approve_no_writes(tmp_path):
    home,store,storage=home_fixture(tmp_path)
    # Remove all grants from this reader's view without changing its valid device chain.
    personal=store.load().trees[0].root.scope_id
    class NoGrants:
        def list(self,prefix): return [p for p in storage.list(prefix) if not p.startswith('keyring/'+personal+'/')] if prefix=='keyring/' else storage.list(prefix)
        def get(self,path): return storage.get(path)
        def put(self,*args): raise AssertionError('must refuse before writes')
    console=ConsolePresence(input_fn=lambda _: 'approve',isatty=lambda:True,output=lambda _:None)
    before={p:p.read_bytes() for p in home.root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='denied'):
        approve(home,store,NoGrants(),'123456',['personal'],'personal/inbox',console,admin=lambda *args:{'client_id':'client','client_name':'Claude'})
    assert before=={p:p.read_bytes() for p in home.root.rglob('*') if p.is_file()}


def test_p6_inbox_peer_metadata(tmp_path):
    from agentmesh_log.pairing import JoinRequest,Approver
    from agentmesh_log.bridge.inboxes import publish
    from agentmesh_log.keyring import sync_control
    from agentmesh_log.broker.grants import resolve
    home,store,storage=home_fixture(tmp_path/'a');state=store.load()
    join=JoinRequest(storage);a=Approver(storage,state.device,state.registry(),{})
    approval=a.begin(join.request_id);reveal=join.reveal(approval,state.registry());a.confirm(reveal,lambda _:True)
    a.grant(state,[state.trees[0].root.scope_id]);other=BrokerHome(tmp_path/'b');peer=FileKeyStore(other);join.finish(state.registry(),other,peer)
    sync_control(home,store,storage);state=store.load();scope=state.add('personal/inbox');initialize_keyring(state,storage);store.save(state)
    publish(state,storage,scope.scope_id);sync_control(other,peer,storage)
    restored=peer.load();assert resolve(restored,'personal/inbox')==scope.scope_id
    assert restored.trees[0].keys[scope.scope_id].key_id==state.trees[0].keys[scope.scope_id].key_id


def test_p6_approve_creates_inbox(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE','1')  # CI basetemp is outside the system temp dir
    from agentmesh_log.oskeystore import initialize
    from agentmesh_log.connector_cli import controls
    from agentmesh_log.broker.grants import resolve
    home=BrokerHome(tmp_path/'home');initialize(home,['personal','work'],file_keystore=True)
    store=FileKeyStore(home);storage=FilesystemStorage(home.root/'replica');calls=[]
    def admin(state,action,payload):
        calls.append(action)
        return {'client_id':'client','client_name':'Claude'} if action=='pending' else {'ok':True}
    console=ConsolePresence(input_fn=lambda _:'approve',isatty=lambda:True,output=lambda _:None)
    result=approve(home,store,storage,'123456',['personal','work'],'personal/inbox',console,admin=admin)
    state=store.load();policy=controls(state,storage).policy(result['app_id'])
    assert calls==['pending','approve']
    assert policy['remember']['scope']==resolve(state,'personal/inbox')
    assert len(policy['read'])==2 and policy['max_label']=='internal'
    assert list(storage.list('connectors/inboxes/'))


def test_admin_request_user_agent(monkeypatch):
    from io import BytesIO
    from agentmesh_log.connector_cli import admin_request
    import urllib.request
    state=BrokerState.create(['personal']); state.control['connector_origin']='https://gateway.example'
    seen=[]
    class Opener:
        def open(self,request,timeout):
            seen.append(request.get_header('User-agent'))
            return BytesIO(b'{"ok":true}')
    monkeypatch.setattr(urllib.request,'build_opener',lambda *args:Opener())
    assert admin_request(state,'revoke',{})=={'ok':True}
    assert seen==['agentmesh-log/1']
