"""Synthetic SQLite → migration → digest → catch-up → hybrid recall."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import pytest
pytest.importorskip('pyrage')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'profile-log/tests'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'profile-log'))
from broker.support import setup_home
from agentmesh_log.broker.service import Broker
from agentmesh_log.migrate.mapping import Mapping,plan
from agentmesh_log.migrate.run import run as migrate_run
from agentmesh_log.migrate.source import staging_node
from agentmesh_log.migrate.ledger import report
from profile_log_parity import local_client
import recall_memory
import profile_log_bridge
import test_bounded_digest as bounded_fixture


def run(db,mapping,client,**kwargs):
    who=client.request(dict(op='whoami'))
    return migrate_run(db,mapping,client,binding=dict(device_id=who['device_id'],staging_node=staging_node(db)),**kwargs)


def test_end_to_end(tmp_path,monkeypatch):
    test=bounded_fixture.BoundedTests();test.setUp()
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True;store.save(state)
    broker=Broker(home,store=store);client=local_client(broker,agent)
    mapping=Mapping(dict(format='agentmesh-migration-mapping-v1',projects={'demo':'work/a'},prefixes=[],null_project='work/a',**{'global':'work/a'}))
    notifications=[]
    monkeypatch.setattr(profile_log_bridge,'notify_rows',lambda db,refs:notifications.append(refs))
    monkeypatch.setattr(profile_log_bridge,'load_config',lambda db,**kw:dict(enabled=True,recall=True,device_id=state.device.device_id))
    def read(plan,pool,config):
        return client.request(dict(op='recall',query=plan.query,project=plan.project,
            task=plan.task_refs[0] if plan.task_refs else None,compat=True,limit=min(pool,100)))
    monkeypatch.setattr(profile_log_bridge,'broker_recall',read)
    try:
        assert not run(test.db,mapping,client)['errors']
        api=test.api();task=api.prepare(test.db)
        assert api.apply(test.db,task,test.payload(task))['status']=='committed'
        assert len(notifications)==1
        assert run(test.db,mapping,client)['outcomes']['created']==2
        for query in ('manual deployment','deployment status'):
            old=recall_memory._recall(test.db,query)
            new=recall_memory.recall(test.db,query)
            assert new['backend']=='broker'
            assert [(r['source_type'],r['source_ref']['id']) for r in new['results']]==[(r['source_type'],r['id']) for r in old['results'] if r['source_type']!='event']
        test.event(2,'Manual deployment restriction is resolved.')
        task=api.prepare(test.db); api.apply(test.db,task,test.payload(task,'resolved',True))
        assert len(notifications)==2
        assert run(test.db,mapping,client)['outcomes']['retired']==1
        assert not any(r['source_type']=='durable' for r in recall_memory.recall(test.db,'manual deployment')['results'])
    finally: broker.close();test.doCleanups()


@pytest.mark.parametrize('mode',['WAL','DELETE'])
def test_p5_1_operations_read_only(tmp_path,monkeypatch,mode):
    db=tmp_path/'synthetic.db'
    with sqlite3.connect(db) as c:
        c.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
        c.execute('PRAGMA journal_mode='+mode)
        c.execute('CREATE TABLE agentmesh_staging_identity(node TEXT NOT NULL)')
        c.execute("INSERT INTO agentmesh_staging_identity VALUES('synthetic')")
        c.execute("INSERT INTO memory_items(kind,scope,content,created_at,updated_at) VALUES('fact','global','synthetic memory','2020-01-01','2020-01-01')")
    c.close()
    before=hashlib.sha256(db.read_bytes()).digest()
    with sqlite3.connect(db) as c: schema=list(c.execute('SELECT * FROM sqlite_master'))
    c.close()
    home,state,store,agents=setup_home(tmp_path);agent=agents[0]
    state.grants[agent.agent_id].allow_import=True;store.save(state)
    broker=Broker(home,store=store);client=local_client(broker,agent)
    value=dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'})
    mapping=Mapping(value);mapping_file=tmp_path/'mapping.json';mapping_file.write_text(json.dumps(value),encoding='utf-8')
    try:
        plan(db,mapping); run(db,mapping,client);run(db,mapping,client,refs=['memory_items:1']);run(db,mapping,client)
        monkeypatch.setattr(profile_log_bridge,'load_config',lambda db,**kw:dict(enabled=True,recall=True,device_id=state.device.device_id))
        monkeypatch.setattr(profile_log_bridge,'broker_recall',lambda p,n,c:client.request(dict(op='recall',query=p.query,compat=True,limit=n)))
        recall_memory.recall(db,'synthetic memory')
        from profile_log_parity import run as parity
        parity(db,mapping_file,sample=1)
        assert hashlib.sha256(db.read_bytes()).digest()==before
        with sqlite3.connect(db) as c: assert list(c.execute('SELECT * FROM sqlite_master'))==schema
        c.close()
        assert not Path(str(db)+'-journal').exists()
    finally: broker.close()


def test_hook_no_remote(tmp_path,monkeypatch):
    from test_profile_log_bridge import config
    from agentmesh_log.broker.replica import Replica
    from agentmesh_log.storage.s3 import S3Storage
    import subprocess
    def forbidden(*args,**kwargs): pytest.fail('remote operation attempted')
    monkeypatch.setattr(Replica,'sync_once',forbidden)
    monkeypatch.setattr(S3Storage,'__init__',forbidden)
    from agentmesh_log.storage.filesystem import FilesystemStorage
    monkeypatch.setattr(FilesystemStorage,'__init__',forbidden)
    db,path,value=config(tmp_path)
    stub=tmp_path/'stub.py'
    stub.write_text("import sys\nassert sys.argv[1:3]==['migrate','rows']\nassert '--sync-interval' not in sys.argv\nassert 'sync' not in sys.argv\n",encoding='utf-8')
    value['cli_argv']=[sys.executable,str(stub)]
    path.write_text(json.dumps(value),encoding='utf-8')
    real=subprocess.Popen;children=[]
    def start(*args,**kwargs):
        child=real(*args,**kwargs);children.append(child);return child
    monkeypatch.setattr(subprocess,'Popen',start)
    profile_log_bridge.notify_rows(db,[('memory_items',1)])
    assert len(children)==1 and children[0].wait(timeout=10)==0
