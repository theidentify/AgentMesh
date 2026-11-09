"""Phase 7 settings gates on disposable broker state. The staging bridge cases went with SQLite retirement R5b."""
import json
import sys
import pytest
from test_apply import env, console, desired, run
from agentmesh_log import settings
from agentmesh_log.broker.keystore import BrokerState, atomic_write
from agentmesh_log.broker.grants import resolve
from agentmesh_log.encoding import wire
from agentmesh_log.registry_store import now


@pytest.mark.parametrize('bridge',[
    {'profiles':[]}, {'profiles':['work','work']}, {'profiles':['work/common']},
    {'profiles':['']}, {'profiles':[None]}, {'profiles':'work'},
    {'profiles':['work'],'profile':'work'},
],ids=['empty','dupe','scope','blank','null','str','both'])
def test_p7_profiles(bridge):
    with pytest.raises(ValueError,match='invalid_settings'):
        settings.validate(dict(bridge=bridge))


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
