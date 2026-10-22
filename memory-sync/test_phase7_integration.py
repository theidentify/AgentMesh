"""Cross-component Phase 7 gates on disposable state only."""
import json
from pathlib import Path
import sqlite3
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'profile-log'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'profile-log/tests'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'profile-log/tests/migrate'))
from test_phase7 import make,database,mapping,migrate,peer
from profile_log_parity import compare
import profile_log_bridge
import shared_memory_context


def test_p7_4_local_parity(tmp_path):
    a,_,ca=make(tmp_path); da=database(tmp_path/'a.db',(1,2))
    try:
        migrate(da,a,ca)
        b,_,cb=peer(tmp_path,a); db=database(tmp_path/'b.db')
        try:
            migrate(db,b,cb)
            binding=dict(device_id=b.state.device.device_id,staging_node='fixture')
            assert compare(db,mapping(),b,binding)==dict(missing=0,changed=0)
            with sqlite3.connect(db) as c:
                c.execute("UPDATE memory_items SET content='changed local row' WHERE id=1")
                c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(3,'fact','global','new local row')")
            assert compare(db,mapping(),b,binding)==dict(missing=1,changed=1)
            assert not migrate(db,b,cb)['errors']
            assert compare(db,mapping(),b,binding)==dict(missing=0,changed=0)
        finally: b.close()
    finally: a.close()


def test_p7_1_context_error(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(profile_log_bridge,'load_config',lambda *a,**k:dict(enabled=True,recall=True))
    def unavailable(*a,**k): raise profile_log_bridge.BrokerUnavailable()
    monkeypatch.setattr(profile_log_bridge,'broker_recall',unavailable)
    assert shared_memory_context.main(['--database',str(tmp_path/'absent.db')])==1
    assert json.loads(capsys.readouterr().out)==dict(error='profile_log_unavailable')


def test_p7_6_recall_and_packets(tmp_path,monkeypatch):
    import packet_sync, sync_worker, recall_memory
    from test_phase7_recall import staging,config,save
    db,_=staging(tmp_path); value=config(tmp_path,('work',)); value['recall_fallback']=True
    save(db,value)
    def unavailable(*a,**k): raise profile_log_bridge.BrokerUnavailable()
    monkeypatch.setattr(profile_log_bridge,'broker_recall',unavailable)
    assert recall_memory.recall(db,'local')['backend']=='sqlite-fallback'
    packet_sync.configure('enable',root=Path(__import__('os').environ['AGENTMESH_HOME']),system='Darwin')
    calls=[]; monkeypatch.setattr(sync_worker,'_run_once',lambda *a,**k:calls.append(a) or dict(packet_sync='enabled'))
    assert sync_worker.run_once(db,tmp_path/'exchange')['packet_sync']=='enabled' and len(calls)==1
