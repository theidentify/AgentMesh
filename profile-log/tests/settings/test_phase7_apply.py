"""Phase 7 settings and enrolment gates use disposable broker and staging state."""
import json
import sqlite3
import sys
import pytest
from test_apply import env, console, desired, run
from agentmesh_log import settings
from agentmesh_log.broker.keystore import BrokerState, atomic_write
from agentmesh_log.broker.grants import resolve
from agentmesh_log.encoding import wire
from agentmesh_log.registry_store import now


def configure(env, profiles=None):
    root,home,store=env
    state=BrokerState.create(['work','personal'])
    state.control['windows']={'agent':now()+900000,'first_access':now()+900000}
    store.save(state)
    value=desired()
    value['bridge']=dict(enabled=True,profiles=profiles or ['work','personal'],recall_agent='reader',import_agent='reader')
    value['migration']=dict(projects={},prefixes=[],null_project='work/common',**{'global':'work/common'})
    settings.save_user(value)
    local=dict(database=str(root/'db'),authority=True,cli_argv=[sys.executable,'-m','agentmesh_log.cli'])
    atomic_write(root/'settings.local.json',wire(dict(bridge=local)))
    return value,local


@pytest.mark.parametrize('bridge',[
    {'profiles':[]}, {'profiles':['work','work']}, {'profiles':['work/common']},
    {'profiles':['']}, {'profiles':[None]}, {'profiles':'work'},
    {'profiles':['work'],'profile':'work'},
],ids=['empty','dupe','scope','blank','null','str','both'])
def test_p7_profiles(bridge):
    with pytest.raises(ValueError,match='invalid_settings'):
        settings.validate(dict(bridge=bridge))


def test_p7_render(env):
    root,home,store=env
    configure(env)
    assert run(console=console())['ok']
    value=json.loads((root/'db.profile-log.json').read_text())
    assert value['profiles']==['work','personal'] and 'profile' not in value
    assert value['device_id']==store.load().device.device_id
    assert value['staging_node']=='test-node' and value['recall_fallback'] is False
    assert set(value)=={'format','enabled','recall','recall_fallback','dual_write','authority','cli_argv','device_id','staging_node','timeout_seconds','profiles','agent_key','import_agent_key','mapping'}


def test_p7_exists(env):
    root,home,store=env
    configure(env,['work','absent'])
    before=store.path.read_bytes()
    assert run(console=console())['error']=='invalid_settings'
    assert store.path.read_bytes()==before
    assert not (root/'db.profile-log.json').exists()
    assert not (root/'agents/reader.key').exists()


def test_p7_legacy(env):
    root,home,store=env
    value,_=configure(env)
    value['bridge'].pop('profiles'); value['bridge']['profile']='work'
    settings.save_user(value)
    assert run(console=console())['ok']
    bridge=json.loads((root/'db.profile-log.json').read_text())
    assert bridge['profile']=='work' and 'profiles' not in bridge


def test_p7_presence(env):
    root,home,store=env
    value,_=configure(env,['work'])
    assert run(console=console())['ok']
    value['agents']['reader']['read']=['work','personal']
    value['bridge']['profiles']=['work','personal']
    settings.save_user(value)
    before=store.path.read_bytes(); bridge=(root/'db.profile-log.json').read_bytes()
    assert run(console=console(tty=False))['error']=='presence_required'
    assert store.path.read_bytes()==before and (root/'db.profile-log.json').read_bytes()==bridge
    calls=[]
    assert run(console=console(calls=calls))['ok'] and len(calls)==1
    state=store.load(); grant=state.grants[state.control['agent_names']['reader']]
    assert set(grant.read)=={resolve(state,'work'),resolve(state,'personal')}
    assert grant.allow_cross_profile is False
    assert 'cross_profile' not in state.control['windows']


@pytest.mark.parametrize('field,value',[('device_id','0'*32),('staging_node','other-node')],ids=['device','node'])
def test_p7_binding(env,field,value):
    root,home,store=env
    _,local=configure(env)
    local[field]=value
    atomic_write(root/'settings.local.json',wire(dict(bridge=local)))
    before=store.path.read_bytes()
    assert run(console=console())['error']=='invalid_settings'
    assert store.path.read_bytes()==before
    assert not (root/'db.profile-log.json').exists()
    assert not (root/'agents/reader.key').exists()


def test_p7_rebind(env):
    root,home,store=env
    configure(env)
    assert run(console=console())['ok']
    before=store.path.read_bytes(); bridge=(root/'db.profile-log.json').read_bytes()
    with sqlite3.connect(root/'db') as c:
        c.execute("UPDATE agentmesh_staging_identity SET node='replacement'")
    c.close()
    assert run(console=console())['error']=='invalid_settings'
    assert store.path.read_bytes()==before and (root/'db.profile-log.json').read_bytes()==bridge


@pytest.mark.parametrize('enabled',[None,False,True],ids=['default','off','on'])
def test_p7_status(env,enabled):
    root,home,store=env
    value=desired()
    if enabled is not None: value['packet_sync']={'enabled':enabled}
    settings.save_user(value)
    assert run(console=console())['ok']
    status=json.loads((root/'packet-sync-status.json').read_text())
    assert status==dict(format='agentmesh-packet-sync-status-v1',packet_sync='enabled' if enabled else 'retired')
    assert str(root) not in json.dumps(status)


def test_p7_toggle(env):
    root,home,store=env
    value=desired(); settings.save_user(value)
    assert run(dry_run=True)['ok']
    path=root/'packet-sync-status.json'
    assert not path.exists()
    assert run(console=console())['ok']
    for enabled in (True,False):
        value['packet_sync']={'enabled':enabled}; settings.save_user(value)
        before=path.read_bytes()
        assert run(dry_run=True)['ok'] and path.read_bytes()==before
        assert run()['ok']
        assert json.loads(path.read_text())['packet_sync']==('enabled' if enabled else 'retired')
