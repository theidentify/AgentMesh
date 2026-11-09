"""Status gates use only generated keys and synthetic rows in disposable homes."""
import json
import os
from pathlib import Path
import sys
import pytest
from test_apply import env, desired, console, run
from agentmesh_log import status, settings, cli
from agentmesh_log.broker.keystore import atomic_write
from agentmesh_log.encoding import wire, parse


@pytest.fixture
def configured(env):
    root,home,store=env
    value=desired(); value['migration']={'global':'work/common','null_project':'work/common','projects':{'AgentMesh':'work/common'},'prefixes':[]}
    settings.save_user(value)
    return root,home,store


def read(root): return json.loads((root/'profile-log-status.json').read_text())


def test_g9_dry_run_and_rollback(configured,monkeypatch):
    root,home,store=configured; path=root/'profile-log-status.json'
    assert run(dry_run=True)['ok'] and not path.exists()
    assert run(console=console())['ok']; before=path.read_bytes()
    assert run(dry_run=True)['ok'] and path.read_bytes()==before
    value=desired(); value['agents']['second']={'read':['work']}; settings.save_user(value)
    from agentmesh_log.broker.keystore import FileKeyStore
    def fail(*_): raise OSError('PRIVATE_MESSAGE')
    monkeypatch.setattr(FileKeyStore,'save',fail)
    assert run(console=console())['error']=='apply_failed'
    value=read(root); assert value['last_apply']['outcome']=='apply_failed'
    assert [a['name'] for a in value['overview']['agents']]==['reader']
    assert 'PRIVATE_MESSAGE' not in json.dumps(value)


def test_g8_invalid_and_unsafe(configured):
    root,home,store=configured; path=root/'profile-log-status.json'
    for raw in (b'{bad',wire({'format':status.FORMAT,'updated_at':'now','key':'FORBIDDEN'})):
        atomic_write(path,raw); status.write(root,last_backup=dict(at='2026-11-09T00:00:00Z',files=1,bytes=1))
        assert status.valid(read(root)) and 'FORBIDDEN' not in path.read_text()
    if os.name=='nt': return  # POSIX mode checks only; Windows ACLs are out of scope.
    before=path.read_bytes(); root.chmod(0o755)
    try: status.write(root,last_backup=dict(at='2026-11-09T00:00:01Z',files=2,bytes=2)); assert path.read_bytes()==before
    finally: root.chmod(0o700)


def sync_row():
    return dict(at='2026-10-20T00:00:00Z',outcome='ok',uploaded=1,downloaded=0,invalid=0,errors=0,outbox=0,local_objects=1,remote_objects=1,interval_s=60,backend='s3')


def test_sync_schema():
    row=dict(sync_row(),last_ok_at=None); value=dict(format=status.FORMAT,updated_at='now',last_sync=row)
    assert status.valid(value)
    for key,bad in [('extra',1),('outcome','unknown'),('uploaded',-1),('interval_s',True),('backend','other'),('at','bad'),('last_ok_at',42)]:
        assert not status.valid(dict(value,last_sync=dict(row,**{key:bad})))
    del row['last_ok_at']; assert not status.valid(value)


def test_synced_preserves_sections(tmp_path):
    tmp_path.chmod(0o700); row=sync_row(); error=dict(row,outcome='error',at='2026-10-20T00:01:00Z')
    status.synced(tmp_path,error); assert read(tmp_path)['last_sync']['last_ok_at'] is None
    backup=dict(at='2026-10-20T00:00:30Z',files=3,bytes=10); status.write(tmp_path,last_backup=backup)
    status.synced(tmp_path,row); status.synced(tmp_path,error)
    value=read(tmp_path); assert value['last_sync']==dict(error,last_ok_at=row['at']) and value['last_backup']==backup
    status.write(tmp_path,last_apply=dict(at='now',outcome='approved',changes={}))
    assert read(tmp_path)['last_sync']==value['last_sync']
    atomic_write(tmp_path/'profile-log-status.json',wire(dict(value,extra='invalid')))
    status.synced(tmp_path,error); assert read(tmp_path)['last_sync']['last_ok_at'] is None


def test_legacy_device_rows_valid(tmp_path):
    """A status file written before heartbeats must stay valid after upgrade (and after rollback)."""
    tmp_path.chmod(0o700); row=sync_row(); status.synced(tmp_path,row)
    legacy=dict(generated_at='now',profiles=[],agents=[],devices=[dict(device_id='a'*32,revoked=False)])
    value=dict(read(tmp_path),overview=legacy); assert status.valid(value)
    atomic_write(tmp_path/'profile-log-status.json',wire(value))
    status.write(tmp_path,last_apply=dict(at='now',outcome='approved',changes={}))
    after=read(tmp_path); assert after['last_sync']==value['last_sync'] and after['overview']==legacy
    assert not status.valid(dict(value,overview=dict(legacy,devices=[dict(device_id='a'*32,revoked=False,name='x')])))


def broker_remote():
    from agentmesh_log.storage import StorageError
    def remote(): raise StorageError('remote operation failed')
    return remote


def test_sync_overview_refresh(env,monkeypatch):
    from agentmesh_log.broker import service
    root,home,store=env; calls=[]; rows=[]; clock=[1000.0]
    monkeypatch.setattr(service.time,'monotonic',lambda:clock[0])
    def on_overview(sections): calls.append(sections); status.write(root,**sections)
    broker=service.Broker(home,store=store,on_sync=rows.append,on_overview=on_overview)
    try:
        status.synced(root,sync_row()); broker.sync_attempt(broker_remote(),60)
        value=read(root); device=store.load().device.device_id
        row=value['overview']['devices'][0]
        assert len(value['overview']['devices'])==1 and row['device_id']==device and not row['revoked']
        assert row['this_device'] and row['name'] and row['last_sync_outcome']=='remote_error'
        assert value['overview']['profiles'][0]['name']=='work' and value['last_sync']['outcome']=='ok'
        assert value['keys']['recoveries']==dict(count=0,latest_issued_at=None)
        assert status.valid(value) and rows
        broker.sync_attempt(broker_remote(),60); assert len(calls)==1
        clock[0]+=60; broker.sync_attempt(broker_remote(),60); assert len(calls)==2
    finally: broker.close()


def test_sync_overview_failure(env,monkeypatch):
    from agentmesh_log.broker import service
    root,home,store=env; rows=[]
    def broken(value): raise RuntimeError('status writer failed')
    broker=service.Broker(home,store=store,on_sync=rows.append,on_overview=broken)
    try:
        broker.sync_attempt(broker_remote(),60); assert rows and rows[0]['outcome']=='remote_error'
        monkeypatch.setattr(status,'overview',lambda state:1/0); broker._overview_written=None
        broker.sync_attempt(broker_remote(),60)
    finally: broker.close()
    assert not (root/'profile-log-status.json').exists()


def test_retired_sections_dropped(tmp_path):
    """R5b: a file written while catch-up ran keeps its other sections; the SQLite-only ones go on the next write."""
    tmp_path.chmod(0o700); row=sync_row(); status.synced(tmp_path,row)
    old=dict(read(tmp_path),migration=dict(generated_at='now',projects=[],unmapped=[]),
             last_catch_up=dict(at='now',outcome='disabled',outcomes={},skipped={},unmapped=[],errors=[],busy_retries=0,timestamp_fallback=0,secret_labelled=0))
    atomic_write(tmp_path/'profile-log-status.json',wire(old))
    status.write(tmp_path,last_backup=dict(at='2026-11-09T00:00:00Z',files=1,bytes=1))
    value=read(tmp_path)
    assert 'migration' not in value and 'last_catch_up' not in value and value['last_sync']['at']==row['at']
