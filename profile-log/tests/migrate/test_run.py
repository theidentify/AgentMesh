import os
from test_source import database
from test_import import setup_home
from agentmesh_log.broker.service import Broker,Session
from agentmesh_log.migrate.mapping import Mapping
from agentmesh_log.migrate.run import run
from agentmesh_log.migrate.ledger import report
from agentmesh_log.events import EVENT_DOMAIN
from agentmesh_log.encoding import typed


class LocalClient:
    def __init__(self,broker,agent): self.broker,self.agent,self.session=broker,agent,Session(agent.certificate())
    def request(self,req): return self.broker.request(self.session,req)
    def import_row(self,**kwargs):
        result=self.request(dict(op='import',**kwargs))
        while result.get('stage')=='sign':
            result=self.request(dict(op='sign',signature=self.agent._private.sign(EVENT_DOMAIN+typed(result['unsigned'])).hex()))
        return result


def test_p5_2_run_report(database,tmp_path):
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); client=LocalClient(broker,agent)
    mapping=Mapping(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'}))
    assert run(database,mapping,client)['outcomes']['created']==2
    assert run(database,mapping,client)['outcomes']['unchanged']==2
    result=report(client,database,mapping)
    assert set(result)=={'memory_items:1','memory_summaries:1'}
    assert all(len(v['chain'])==1 for v in result.values())
    broker.close()


def test_p5_2_skipped_and_catchup(database,tmp_path):
    import sqlite3
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); client=LocalClient(broker,agent)
    mapping=Mapping(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'}))
    run(database,mapping,client)
    with sqlite3.connect(database) as c:
        c.execute("UPDATE memory_items SET status='resolved' WHERE id=1")
        c.execute("INSERT INTO memory_summaries(scope,scope_key,version,content) VALUES('task','AM-1',2,'new summary')")
        c.execute("INSERT INTO memory_summaries(scope,scope_key,content) VALUES('session','session','ignored')")
    result=run(database,mapping,client)
    # A second summary row for the same key is its own source; the first stays live.
    assert result['outcomes']['retired']==1 and result['outcomes']['created']==1
    ledger=report(client,database,mapping)
    assert len(ledger)==4 and ledger['memory_summaries:3']['reason']=='not_recalled'
    assert ledger['memory_items:1']['status']=='retired'
    assert ledger['memory_summaries:1']['status']=='created' and ledger['memory_summaries:2']['status']=='created'
    with sqlite3.connect(database) as c: c.execute('DELETE FROM memory_summaries WHERE id=2')
    assert run(database,mapping,client)['outcomes']['retired']==1
    broker.close()


def test_p5_4_resume(database,tmp_path):
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); client=LocalClient(broker,agent)
    mapping=Mapping(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'}))
    normal=client.import_row; count=0
    def fail(**kwargs):
        nonlocal count
        response=normal(**kwargs); count+=1
        if count==1: raise RuntimeError('injected after persistence')
        return response
    client.import_row=fail
    assert run(database,mapping,client)['errors']==['RuntimeError']
    broker.close(); broker=Broker(home,store=store); client=LocalClient(broker,agent)
    result=run(database,mapping,client)
    assert result['outcomes']==dict(unchanged=1,created=1)
    assert all(len(e['chain'])==1 for e in report(client).values())
    broker.close()


def test_process_exit(database,tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from broker.test_isolation import keyfile
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True;store.save(state)
    key=keyfile(home,state,agent)
    script='''
import os,sys
from pathlib import Path
from agentmesh_log.encoding import parse
from agentmesh_log.identity import AgentIdentity
from agentmesh_log.broker.keystore import BrokerHome,FileKeyStore
from agentmesh_log.broker.service import Broker
from test_run import LocalClient,Mapping,run
home=BrokerHome(sys.argv[1]); store=FileKeyStore(home)
record=parse(Path(sys.argv[3]).read_bytes())
agent=AgentIdentity.from_private(record['agent_cert'],bytes.fromhex(record['agent_private_key']))
broker=Broker(home,store=store); client=LocalClient(broker,agent)
normal=client.import_row
count=0
def crash(**kwargs):
    global count
    result=normal(**kwargs); count+=1
    if count==1: os._exit(73)
    return result
client.import_row=crash
mapping=Mapping(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'}))
run(sys.argv[2],mapping,client)
'''
    package=Path(__file__).resolve().parents[2]
    env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,[package,package/'tests/migrate',package/'tests'])),'PYTHONDONTWRITEBYTECODE':'1'}
    child=subprocess.run([sys.executable,'-c',script,str(home.root),str(database),str(key)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
    assert child.returncode==73
    broker=Broker(home,store=store)
    try:
        mapping=Mapping(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'}))
        client=LocalClient(broker,agent)
        assert run(database,mapping,client)['outcomes']==dict(unchanged=1,created=1)
        assert len(report(client))==2
        assert all(len(e['chain'])==1 for e in report(client).values())
    finally: broker.close()


def test_no_remote(database,tmp_path,monkeypatch):
    from agentmesh_log.broker.replica import Replica
    from agentmesh_log.storage.s3 import S3Storage
    import pytest
    def forbidden(*args,**kwargs): pytest.fail('remote operation attempted')
    monkeypatch.setattr(Replica,'sync_once',forbidden)
    monkeypatch.setattr(S3Storage,'__init__',forbidden)
    home,state,store,agents=setup_home(tmp_path);agent=agents[0]
    state.grants[agent.agent_id].allow_import=True;store.save(state)
    from agentmesh_log.storage.filesystem import FilesystemStorage
    from pathlib import Path
    original=FilesystemStorage.__init__
    def local_only(self,path,*args,**kwargs):
        assert Path(path)==home.root/'replica'
        original(self,path,*args,**kwargs)
    monkeypatch.setattr(FilesystemStorage,'__init__',local_only)
    broker=Broker(home,store=store)
    try:
        before=broker.replica.outbox()
        mapping=Mapping(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'}))
        assert run(database,mapping,LocalClient(broker,agent))['outcomes']['created']==2
        assert broker.replica.outbox()==before+2
    finally: broker.close()


def test_no_evidence_text(database,tmp_path):
    import json
    import secrets
    import sqlite3
    marker='synthetic-'+secrets.token_hex(12)
    with sqlite3.connect(database) as c:
        c.execute("INSERT INTO observation_events(id,event_key,source_path,kind,content,source_hash) VALUES(1,'synthetic','synthetic','tool_result',?,'synthetic')",(marker,))
        c.execute('INSERT INTO memory_sources(memory_id,event_id) VALUES(1,1)')
        c.execute('UPDATE memory_items SET metadata=?',(json.dumps(dict(evidence=marker,evidence_history=[marker])),))
        c.execute('UPDATE memory_summaries SET metadata=?',(json.dumps(dict(event_ids=[1],evidence=marker,source_event_ids=[marker])),))
    home,state,store,agents=setup_home(tmp_path);agent=agents[0]
    state.grants[agent.agent_id].allow_import=True;store.save(state)
    broker=Broker(home,store=store)
    try:
        mapping=Mapping(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'}))
        assert run(database,mapping,LocalClient(broker,agent))['outcomes']['created']==2
        payloads=[p for _,p in broker.payloads()]
        assert len(payloads)==2
        assert all(marker not in json.dumps(p) for p in payloads)
        assert all(p['body']['metadata']['evidence_event_ids']==[1] for p in payloads)
    finally: broker.close()


def test_unmapped_refuses_all(database,tmp_path):
    import sqlite3
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); client=LocalClient(broker,agent)
    with sqlite3.connect(database) as c:
        c.execute("INSERT INTO memory_items(kind,scope,project,content) VALUES('fact','project','unlisted','x')")
    mapping=Mapping(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'}))
    before=len(list(broker.replica.keys()))
    result=run(database,mapping,client)
    assert result['unmapped']==['unlisted'] and result['errors']==['UnmappedProject']
    assert result['outcomes']=={} and len(list(broker.replica.keys()))==before
    broker.close()


def audit_ops(home):
    import json
    from collections import Counter
    path=home.root/'audit.jsonl'
    if not path.exists(): return Counter()
    return Counter(json.loads(line)['op'] for line in path.read_text(encoding='utf-8').splitlines())


def test_cycles_no_reaudit(database,tmp_path):
    import sqlite3
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); client=LocalClient(broker,agent)
    mapping=Mapping(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'}))
    cache=tmp_path/'fixture.db.profile-log.catch-up.json'
    try:
        assert run(database,mapping,client,cache=cache)['outcomes']==dict(created=2)
        first=audit_ops(home); assert first['import']==4
        for cycle in range(1,4):
            assert run(database,mapping,client,cache=cache)['outcomes']==dict(unchanged=2)
            ops=audit_ops(home)
            # Idle cycles audit only the ledger and whoami requests, never per-row imports.
            assert ops['import']==first['import']
            assert ops['import_ledger']==first['import_ledger']+cycle and ops['whoami']==first['whoami']+cycle
        with sqlite3.connect(database) as c: c.execute("UPDATE memory_items SET content='changed' WHERE id=1")
        assert run(database,mapping,client,cache=cache)['outcomes']==dict(revised=1,unchanged=1)
        assert audit_ops(home)['import']==first['import']+2
        assert run(database,mapping,client,cache=cache)['outcomes']==dict(unchanged=2)
        assert audit_ops(home)['import']==first['import']+2
        assert all(len(v['chain'])<=2 for v in report(client).values())
        if os.name!='nt': assert cache.stat().st_mode&0o777==0o600
    finally: broker.close()


def test_cache_ledger_moved(database,tmp_path):
    import sqlite3
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); client=LocalClient(broker,agent)
    mapping=Mapping(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project='work/a',**{'global':'work/a'}))
    cache=tmp_path/'cache.json'
    try:
        run(database,mapping,client,cache=cache)
        # A post-commit hook revises outside catch-up; its cache entry is now stale.
        with sqlite3.connect(database) as c: c.execute("UPDATE memory_items SET content='hooked' WHERE id=1")
        assert run(database,mapping,client,refs=['memory_items:1'])['outcomes']==dict(revised=1)
        before=audit_ops(home)['import']
        assert run(database,mapping,client,cache=cache)['outcomes']==dict(unchanged=2)
        assert audit_ops(home)['import']==before+1
        assert run(database,mapping,client,cache=cache)['outcomes']==dict(unchanged=2)
        assert audit_ops(home)['import']==before+1
        cache.write_text('not json',encoding='utf-8')
        assert run(database,mapping,client,cache=cache)['outcomes']==dict(unchanged=2)
        assert audit_ops(home)['import']==before+3
    finally: broker.close()
