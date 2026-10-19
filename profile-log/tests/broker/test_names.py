import pytest
from agentmesh_log.broker.keystore import BrokerState
from .test_cli import cli


def test_state_duplicates():
    with pytest.raises(ValueError): BrokerState.create(['work','work'])
    state=BrokerState.create(['work']); tree=state.trees[0]
    tree.add(tree.root.scope_id,'project','a')
    with pytest.raises(ValueError): tree.add(tree.root.scope_id,'project','a')
    with pytest.raises(ValueError): state.add('work/a')


def test_cli_duplicates(tmp_path):
    assert cli('broker','init','--home',tmp_path/'bad','--profile','work','--profile','work').returncode==1
    home=tmp_path/'home'
    assert cli('broker','init','--home',home,'--profile','work').returncode==0
    assert cli('broker','scope','add','--home',home,'work/a').returncode==0
    assert cli('broker','scope','add','--home',home,'work/a').returncode==1


def test_imported_duplicates():
    state=BrokerState.create(['work','personal']); record=state.record()
    record['trees'][1]['scopes'][0]['name']='work'
    with pytest.raises(ValueError): BrokerState.from_record(record)
    record=state.record(); scopes=record['trees'][0]['scopes']
    state.add('work/a'); record=state.record(); scopes=record['trees'][0]['scopes']
    scopes[-1]['name']='common'
    with pytest.raises(ValueError): BrokerState.from_record(record)


def test_distinct_parents():
    state=BrokerState.create(['work','personal'])
    state.add('work/a'); state.add('personal/a'); state.add('work/a/nested')
    with pytest.raises(ValueError): state.add('work/a/nested')
