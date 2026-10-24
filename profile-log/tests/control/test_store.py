import os
import sqlite3
import stat
import pytest
from agentmesh_log.control.store import Store


def test_backend_switch(tmp_path):
    store=Store(tmp_path/'state.db')
    assert store.backend('ingest')=='launchd' and store.spec('ingest') is None and store.native_units()=={}
    store.set_backend('ingest','native',{'interval':60},1000)
    assert store.backend('ingest')=='native' and store.spec('ingest')=={'interval':60}
    assert store.changed_at('ingest')==1000 and store.native_units()=={'ingest':{'interval':60}}
    store.set_backend('ingest','launchd',{'interval':60},2000)
    assert store.native_units()=={} and store.spec('ingest')=={'interval':60} and store.changed_at('ingest')==2000
    with pytest.raises(sqlite3.IntegrityError): store.set_backend('ingest','cron',None,3000)


def test_owner_only_and_reopen(tmp_path):
    Store(tmp_path/'state.db').set_backend('summary','native',{},1)
    assert Store(tmp_path/'state.db').backend('summary')=='native'
    if os.name!='nt': assert stat.S_IMODE((tmp_path/'state.db').stat().st_mode)==0o600


@pytest.mark.skipif(os.name=='nt',reason='POSIX modes')
def test_refuses_loose_mode_and_symlink(tmp_path):
    (tmp_path/'state.db').write_bytes(b''); (tmp_path/'state.db').chmod(0o644)
    with pytest.raises(ValueError): Store(tmp_path/'state.db')
    (tmp_path/'real').write_bytes(b''); (tmp_path/'link').symlink_to(tmp_path/'real')
    with pytest.raises(Exception): Store(tmp_path/'link')


def test_runs(tmp_path):
    store=Store(tmp_path/'state.db')
    assert store.last_start('ingest') is None and store.last_exit('ingest') is None
    first=store.start_run('ingest',41,'Sat Oct 24 10:00:00 2026',1000)
    second=store.start_run('summary',42,None,2000)
    assert [r['id'] for r in store.open_runs()]==[first,second] and [r['unit'] for r in store.open_runs('ingest')]==['ingest']
    store.end_run(first,3,'failed',1500); store.end_run(first,0,'ok',9999)
    assert store.last_exit('ingest')==3 and store.last_start('ingest')==1000
    assert [r['id'] for r in store.open_runs()]==[second]


def test_kills_and_failed(tmp_path):
    store=Store(tmp_path/'state.db')
    for at in (100,200,300): store.add_kill('ingest',at)
    assert store.kills_since('ingest',150)==2 and store.kills_since('summary',0)==0
    assert store.failed('ingest') is None and store.clear_failed('ingest') is False
    store.set_failed('ingest','restart_storm',400); store.set_failed('ingest','again',500)
    assert store.failed('ingest')=={'since':400,'reason':'restart_storm'}
    assert store.clear_failed('ingest') is True and store.failed('ingest') is None
