import json
import os
import sys
import pytest
from agentmesh_log.apply import apply_settings
from agentmesh_log.broker.keystore import BrokerHome, BrokerState, FileKeyStore
from agentmesh_log.broker.grants import names, resolve
from agentmesh_log.presence import ConsolePresence
from agentmesh_log.registry_store import now
from agentmesh_log.settings import save_user


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / 'am'; root.mkdir(mode=0o700); monkeypatch.setenv('AGENTMESH_HOME', str(root))
    # CI uses --basetemp outside the system temp directory; these homes are still disposable.
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE', '1')
    home = BrokerHome(root / 'profile-log'); store = FileKeyStore(home)
    state = BrokerState.create(['work']); state.control['windows'] = {'agent': now()+900000, 'first_access': now()+900000}
    store.save(state)
    return root, home, store


def console(answer='apply', tty=True, calls=None):
    def reply(prompt):
        if calls is not None: calls.append(prompt)
        return answer
    return ConsolePresence(input_fn=reply, isatty=lambda: tty, output=lambda _: None)


def desired():
    return {'agents': {'reader': {'read': ['work/common'], 'write': ['work/common'], 'allow_import': True}}}


def run(**kw): return apply_settings(output=lambda _: None, **kw)


@pytest.mark.parametrize('tty,answer', [(False,'apply'),(True,'no')])
def test_s2_declined(env, tty, answer):
    root, home, store = env; save_user(desired()); before = store.path.read_bytes()
    assert run(console=console(answer, tty))['error'] == 'presence_required'
    assert store.path.read_bytes() == before and not (root / 'agents' / 'reader.key').exists()
    assert len((root / 'apply.jsonl').read_text(encoding='utf-8').splitlines()) == 1


def test_s3_s5_s14_lifecycle(env):
    root, home, store = env; save_user(desired()); calls = []
    result = run(console=console(calls=calls)); assert result['ok'] and len(calls) == 1
    state = store.load(); aid = state.control['agent_names']['reader']; grant = state.grants[aid]
    assert grant.read == grant.write == [resolve(state,'work/common')] and grant.allow_import
    key = root / 'agents' / 'reader.key'; assert key.exists()
    if os.name != 'nt': assert key.stat().st_mode & 0o777 == 0o600
    before = store.path.read_bytes(); output=[]
    assert apply_settings(console=console(calls=calls), output=output.append)['diff'] == []
    assert output == ['no changes'] and store.path.read_bytes() == before and len(calls) == 1
    save_user({'agents': {}})
    assert run(console=console(calls=calls))['ok']; assert len(calls) == 1
    state = store.load(); assert aid not in state.grants and aid in state.control['import_agents'] and not key.exists()
    lines = (root / 'apply.jsonl').read_text(encoding='utf-8').splitlines()
    assert len(lines) == 3 and all(set(json.loads(s)) == {'kinds','names','outcome'} for s in lines)
    assert str(root) not in '\n'.join(lines)


def test_s14_dry_run_no_writes(env):
    root, home, store = env; save_user(desired())
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert run(dry_run=True)['diff'][0]['kind'] == 'agent_create'
    assert before == {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_s2_narrow_mixed_rename(env):
    root, home, store = env; save_user(desired()); run(console=console())
    value=desired(); value['agents']['reader']['read']=[]; save_user(value); calls=[]
    assert run(console=console(calls=calls))['diff'][0]['kind']=='agent_narrow'; assert not calls
    value['agents']['second']={'read':['work']}; value['agents']['reader']['write']=[]; save_user(value)
    result=run(console=console(calls=calls)); assert len(calls)==1
    assert {d['kind'] for d in result['diff']}=={'agent_create','agent_narrow'}
    save_user({'agents':{'renamed':{'read':['work']}}})
    assert {d['kind'] for d in run(dry_run=True)['diff']}=={'agent_create','agent_remove'}


@pytest.mark.parametrize('profiles', [{}, {'work':{'scopes':{}}}, {'work':{'scopes':{'common':{'label':'secret'}}}}, {'unknown':{}}])
def test_s6_unsupported(env, profiles):
    root, home, store=env; before=store.path.read_bytes(); save_user({'profiles':profiles})
    assert run(console=console())['error']=='unsupported'; assert store.path.read_bytes()==before


def test_s6_scope(env):
    root, home, store=env
    save_user({'profiles':{'work':{'scopes':{'common':{'label':'general'},'new':{'label':'internal'}}}}})
    assert run(console=console())['ok']; assert 'work/new' in names(store.load()).values()


def test_s1_apply_invalid(env):
    root, home, store=env; before=store.path.read_bytes()
    (root/'settings.json').write_text('{"unknown":true}',encoding='utf-8'); (root/'settings.json').chmod(0o600)
    assert run()['error']=='invalid_settings'; assert store.path.read_bytes()==before


def test_s8_bridge(env):
    root, home, store=env
    value=desired(); value['bridge']={'enabled':True,'profile':'work','recall_agent':'reader','import_agent':'reader'}
    value['migration']={'global':'work/common','null_project':'work/common','projects':{},'prefixes':[]}
    save_user(value)
    local=root/'settings.local.json'; local.write_text(json.dumps({'bridge':{'database':str(root/'db'),'authority':True,'cli_argv':[sys.executable,'-m','agentmesh_log.cli']}}),encoding='utf-8'); local.chmod(0o600)
    assert run(console=console())['ok']
    path=root/'db.profile-log.json'; config=json.loads(path.read_text(encoding='utf-8'))
    assert set(config)=={'format','enabled','recall','dual_write','authority','cli_argv','agent_key','import_agent_key','mapping','profile','timeout_seconds'}
    assert config['agent_key']==str(root/'agents/reader.key')
    value['bridge']['enabled']=False; save_user(value)
    assert run()['ok']; assert json.loads(path.read_text(encoding='utf-8'))['enabled'] is False


@pytest.mark.parametrize('change', ['scope','create','read','write','allow_secret','allow_cross_profile','allow_import','bridge_enable','recall','dual_write','authority','mapping','cli_argv','timeout'])
def test_s2_widening(env, change):
    root, home, store=env
    value={'agents':{'a':{'read':[],'write':[]}}}; save_user(value); assert run(console=console())['ok']
    if change == 'scope': value['profiles']={'work':{'scopes':{'common':{},'new':{}}}}
    elif change == 'create': value['agents']['b']={}
    elif change in ('read','write'): value['agents']['a'][change]=['work/common']
    elif change in ('allow_secret','allow_cross_profile','allow_import'): value['agents']['a'][change]=True
    elif change == 'mapping': value['migration']={'global':'work/common','null_project':'work/common','projects':{},'prefixes':[]}
    else:
        state=store.load()
        old=dict(enabled=True,recall=False,dual_write=False,authority=False,timeout_seconds=10,database=str(root/'db'),cli_argv=[sys.executable,'-m','agentmesh_log.cli'],profile='work',recall_agent='a',import_agent='a')
        if change=='bridge_enable': old['enabled']=False
        state.control['applied_bridge']=old
        state.control['applied_mapping']={'format':'agentmesh-migration-mapping-v1','global':'work/common','null_project':'work/common','projects':{},'prefixes':[]}
        store.save(state)
        value['bridge']={('enabled' if change=='bridge_enable' else change):True}
        if change=='timeout': value['bridge']={'timeout_seconds':20}
        if change=='cli_argv':
            # Redirecting the program that receives the agent key is widening.
            value['bridge']={}; local=root/'settings.local.json'; local.write_text(json.dumps({'bridge':{'cli_argv':[sys.executable,str(root/'other-cli')]}}),encoding='utf-8'); local.chmod(0o600)
        if change=='authority':
            value['bridge']={}; local=root/'settings.local.json'; local.write_text('{"bridge":{"authority":true}}',encoding='utf-8'); local.chmod(0o600)
    save_user(value); before=store.path.read_bytes()
    assert run(console=console(tty=False))['error']=='presence_required'
    assert store.path.read_bytes()==before
    calls=[]; assert run(console=console(calls=calls))['ok']; assert len(calls)==1


def test_s4_broker_authority_and_s5_revoke(env):
    from agentmesh_log.broker.service import Broker, Session
    from agentmesh_log.encoding import parse
    root, home, store=env; value=desired(); save_user(value); assert run(console=console())['ok']
    record=parse((root/'agents/reader.key').read_bytes()); session=Session(record['agent_cert'])
    broker=Broker(home,store=store)
    try:
        query=dict(op='recall',query='synthetic',scopes=['work'])
        assert broker.request(session,query)=={'error':'denied'}
        value['agents']['reader']['read']=['work']; save_user(value)
        assert broker.request(session,query)=={'error':'denied'}
        assert run(console=console())['ok']; assert 'results' in broker.request(session,query)
        save_user({'agents':{}}); assert run()['ok']
        assert 'error' in broker.request(session,dict(op='whoami'))
    finally: broker.close()


def test_s6_phase4_scope(env):
    from agentmesh_log.oskeystore import initialize
    from agentmesh_log.storage.filesystem import FilesystemStorage
    root, home, store=env; store.path.unlink(); initialize(home,['work'],file_keystore=True)
    before=set(FilesystemStorage(home.root/'replica').list('keyring/'))
    save_user({'profiles':{'work':{'scopes':{'common':{},'new':{'label':'internal'}}}}})
    assert run(console=console())['ok']; state=store.load(); sid=resolve(state,'work/new')
    after=set(FilesystemStorage(home.root/'replica').list('keyring/'))
    assert any('/'+sid+'/' in p for p in after-before)
    assert any('/recovery/' in p for p in after-before)


def test_s2_sealed_bytes(env):
    from agentmesh_log.oskeystore import OsKeyStore
    from phase4.test_oskeystore import FakeBackend
    root,home,plain=env; backend=FakeBackend(); sealed=OsKeyStore(home,backend); sealed.save(plain.load()); plain.path.unlink()
    save_user(desired()); before=sealed.path.read_bytes()
    assert run(console=console(tty=False),backend=backend)['error']=='presence_required'
    assert sealed.path.read_bytes()==before
    assert run(console=console(),backend=backend)['ok']; before=sealed.path.read_bytes()
    assert run(backend=backend)['diff']==[]; assert sealed.path.read_bytes()==before


def test_failure_rollback(env, monkeypatch):
    root,home,store=env; save_user(desired()); before=store.path.read_bytes()
    monkeypatch.setattr(FileKeyStore,'save',lambda *_: (_ for _ in ()).throw(OSError('injected')))
    assert run(console=console())['error']=='apply_failed'
    assert store.path.read_bytes()==before and not (root/'agents/reader.key').exists()


@pytest.mark.parametrize('bad',['symlink','mode','root','unknown','duplicate','local','material'])
def test_s1_no_mutation(env,bad):
    root,home,store=env; before=store.path.read_bytes(); save_user(desired()); path=root/'settings.json'
    if bad=='symlink': path.rename(root/'target'); path.symlink_to(root/'target')
    elif bad=='mode': path.chmod(0o644)
    elif bad=='root': root.chmod(0o755)
    else:
        text={'unknown':'{"bad":true}','duplicate':'{"agents":{},"agents":{}}','local':'{"bridge":{"authority":true}}','material':'{"bridge":{"profile":"-----BEGIN"}}'}[bad]
        path.write_text(text,encoding='utf-8')
    if os.name=='nt' and bad in ('mode','root'): return
    assert run()['error']=='invalid_settings'
    assert store.path.read_bytes()==before and not (root/'agents').exists()


def test_s6_allow_unsupported(env):
    root,home,store=env
    value=desired(); value['profiles']={'work':{'scopes':{'common':{'label':'secret'}}}}; save_user(value)
    result=run(allow_unsupported_report=True,console=console())
    assert result['ok'] and {c['kind'] for c in result['diff']}=={'unsupported','agent_create'}
    state=store.load(); assert state.trees[0].scopes[resolve(state,'work/common')].default_label.name=='general'


def test_s5_exclusive_file(env):
    root,home,store=env; save_user(desired()); folder=root/'agents'; folder.mkdir(mode=0o700)
    path=folder/'reader.key'; path.write_text('reserved',encoding='utf-8'); path.chmod(0o600); before=store.path.read_bytes()
    assert run(console=console())['error']=='agent_file_exists'
    assert path.read_text(encoding='utf-8')=='reserved' and store.path.read_bytes()==before


def test_initial_disable_no_prompt(env):
    root,home,store=env; save_user({'bridge':{'enabled':False}})
    assert run()['ok']


def test_s14_unsafe_audit(env):
    root,home,store=env; save_user(desired()); target=root/'protected'; target.write_text('unchanged',encoding='utf-8'); target.chmod(0o600)
    (root/'apply.jsonl').symlink_to(target); before=store.path.read_bytes()
    assert run(console=console())['error']=='invalid_settings'
    assert store.path.read_bytes()==before and target.read_text(encoding='utf-8')=='unchanged'


def test_s14_decline_noop(env):
    from agentmesh_log.encoding import parse
    root,home,store=env; save_user(desired())
    assert run(console=console(tty=False))['error']=='presence_required'
    assert run(console=console())['ok']; assert run()['diff']==[]
    lines=(root/'apply.jsonl').read_bytes().splitlines()
    assert [parse(line)['outcome'] for line in lines]==['presence_required','approved','no-op']
    key=parse((root/'agents/reader.key').read_bytes())
    forbidden=[str(root).encode(), key['agent_private_key'].encode(), b'agent_key', b'content']
    assert all(marker not in b'\n'.join(lines) for marker in forbidden)
    if os.name!='nt': assert (root/'apply.jsonl').stat().st_mode & 0o777==0o600


def test_dry_run_no_write_calls(env,monkeypatch):
    root,home,store=env; save_user(desired()); original=os.open
    def readonly(path,flags,*args,**kwargs):
        assert not flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(os,'open',readonly)
    assert run(dry_run=True)['ok']


def test_s2_bridge_flags_off_narrow(env):
    root, home, store=env
    value={'agents':{'a':{'read':[],'write':[]}}}; save_user(value); assert run(console=console())['ok']
    state=store.load()
    state.control['applied_bridge']=dict(enabled=True,recall=True,dual_write=True,authority=False,timeout_seconds=10,database=str(root/'db'),cli_argv=[sys.executable,'-m','agentmesh_log.cli'],profile='work',recall_agent='a',import_agent='a')
    state.control['applied_mapping']={'format':'agentmesh-migration-mapping-v1','global':'work/common','null_project':'work/common','projects':{},'prefixes':[]}
    store.save(state)
    value['bridge']={'recall':False,'dual_write':False}; save_user(value); calls=[]
    result=run(console=console(calls=calls)); assert result['ok'] and not calls
    value['bridge']={'enabled':False,'recall':False,'dual_write':False}; save_user(value)
    result=run(console=console(calls=calls)); assert result['ok'] and not calls
