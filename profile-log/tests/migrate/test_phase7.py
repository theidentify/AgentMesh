"""Per-device migration gates, using only synthetic staging and broker state."""
import sqlite3
from pathlib import Path
import pytest
from broker.support import setup_home
from agentmesh_log.broker.service import Broker,Session
from agentmesh_log.broker.keystore import BrokerHome,BrokerState,FileKeyStore
from agentmesh_log.broker.grants import Grant,resolve
from agentmesh_log.identity import DeviceIdentity,AgentIdentity
from agentmesh_log.events import seal_event
from agentmesh_log.migrate.source import rows,body_for
from agentmesh_log.migrate.mapping import Mapping
from agentmesh_log.migrate.run import run
from test_run import LocalClient


def database(path,ids=(1,)):
    with sqlite3.connect(path) as c:
        c.executescript((Path(__file__).resolve().parents[3]/'memory-sync/schema.sql').read_text(encoding='utf-8'))
        c.execute('CREATE TABLE agentmesh_staging_identity(node TEXT NOT NULL)')
        c.execute("INSERT INTO agentmesh_staging_identity VALUES('fixture')")
        for ident in ids:
            c.execute("INSERT INTO memory_items(id,kind,scope,content,updated_at) VALUES(?,'fact','global','synthetic memory','2020-01-01')",(ident,))
    return path


def mapping(scope='work/a'):
    return Mapping(dict(format='agentmesh-migration-mapping-v1',projects={},prefixes=[],null_project=scope,**{'global':scope}))


def make(tmp_path):
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    grant=state.grants[agent.agent_id]; grant.allow_import=True
    grant.write.append(resolve(state,'work/b')); grant.read.append(resolve(state,'work/b')); store.save(state)
    broker=Broker(home,store=store)
    return broker,agent,LocalClient(broker,agent)


def migrate(db,broker,client,scope='work/a',**options):
    return run(db,mapping(scope),client,binding=dict(device_id=broker.state.device.device_id,staging_node='fixture'),**options)


def legacy(broker,agent,db,ident=1):
    row=next(r for r in rows(db) if r['id']==ident)
    body,stamp,_=body_for(row,'work/a'); sid=resolve(broker.state,'work/a')
    key=broker.state.trees[0].keys[sid]
    data=seal_event(key.write_key,agent,body,created_at=stamp)
    env=broker.replica.put_event(data); broker.ingest(env,data)
    return env


def peer(tmp_path,origin):
    state=BrokerState.from_record(origin.state.record()); device=DeviceIdentity()
    state.certificates.append(device.certificate(state.account)); state.device=device
    agent=AgentIdentity(device,state.account.account_id); sid=resolve(state,'work/a')
    state.grants={agent.agent_id:Grant(agent.agent_id,agent.public_bytes.hex(),[sid],[sid],sid,state.trees[0].root.scope_id,allow_import=True)}
    home=BrokerHome(tmp_path/'peer'); store=FileKeyStore(home); store.save(state)
    broker=Broker(home,store=store)
    for path in origin.replica.keys():
        if path.startswith('events/'):
            data=origin.replica.storage.get(path); env=broker.replica.put_event(data); broker.ingest(env,data)
    return broker,agent,LocalClient(broker,agent)


def test_p7_3_devices(tmp_path):
    a,agent,ca=make(tmp_path); da=database(tmp_path/'a.db',(1,2))
    try:
        legacy(a,agent,da)
        assert migrate(da,a,ca)['outcomes']==dict(unchanged=1,created=1)
        b,_,cb=peer(tmp_path,a); db=database(tmp_path/'b.db')
        try:
            before={k:set(v) for k,v in b.imports.entries.items()}
            assert migrate(db,b,cb)['outcomes']==dict(created=1)
            assert all(set(b.imports.entries[k])==v for k,v in before.items())
            with sqlite3.connect(db) as c: c.execute('DELETE FROM memory_items')
            assert migrate(db,b,cb)['outcomes']==dict(retired=1)
            assert all(set(b.imports.entries[k])==v for k,v in before.items())
            assert len(b.imports.entries)==3
        finally: b.close()
    finally: a.close()


def test_p7_3_same_ids(tmp_path):
    a,_,ca=make(tmp_path); da=database(tmp_path/'a.db')
    try:
        migrate(da,a,ca)
        b,_,cb=peer(tmp_path,a)
        try:
            db=database(tmp_path/'b.db'); migrate(db,b,cb)
            entries=b.imports.ledger(b.state.grants[cb.agent.agent_id])
            assert len(entries)==2 and len({e['source']['device'] for e in entries})==2
            assert all(e['status']=='created' and len(e['chain'])==1 for e in entries)
        finally: b.close()
    finally: a.close()


def test_p7_3_binding(tmp_path):
    broker,_,client=make(tmp_path); db=database(tmp_path/'source.db')
    try:
        for binding in (None,{},dict(device_id='0'*32,staging_node='fixture'),dict(device_id=broker.state.device.device_id,staging_node='other')):
            with pytest.raises(ValueError,match='binding'):
                run(db,mapping(),client,binding=binding)
        assert not broker.imports.entries
    finally: broker.close()


def test_p7_3_source_gate(tmp_path):
    broker,agent,client=make(tmp_path); db=database(tmp_path/'source.db')
    try:
        body,stamp,_=body_for(rows(db)[0],'work/a')
        req=dict(op='import',source=dict(table='memory_items',id=1),body=body,scope='work/a',created_at=stamp,row_hash=body['metadata']['row_hash'])
        assert client.request(req)['error']=='denied'
        req['source']['device']='0'*32; body['metadata']['source']['device']='0'*32
        assert client.request(req)['error']=='denied'
        from agentmesh_log.broker.imports import admitted
        env=legacy(broker,agent,db)
        assert admitted(broker,env,dict(body=body)) is None
        assert not any(key[0]=='0'*32 for key in broker.imports.entries)
    finally: broker.close()


@pytest.mark.parametrize('retire',[False,True],ids=['revise','retire'])
def test_p7_3_legacy_peer(tmp_path,retire):
    a,agent,_=make(tmp_path); db=database(tmp_path/'source.db')
    try:
        first=legacy(a,agent,db)
        b,_,client=peer(tmp_path,a)
        try:
            key=(None,'memory_items',1)
            assert b.imports.head(key)[0]['device']==a.state.device.device_id
            assert a.state.device.device_id!=b.state.device.device_id
            before=set(b.replica.keys())
            row=rows(db)[0]; row['content']='foreign revision attempt'
            body,stamp,_=body_for(row,'work/a')
            result=client.import_row(source=dict(table='memory_items',id=1),scope='work/a',
                created_at=stamp,row_hash=body['metadata']['row_hash'],body=None if retire else body)
            assert result.get('error')=='denied'
            assert set(b.replica.keys())==before
            assert set(b.imports.entries[key])=={first['id']}
        finally: b.close()
    finally: a.close()


@pytest.mark.parametrize('interrupt',[False,True])
def test_p7_3_legacy_move(tmp_path,interrupt):
    broker,agent,client=make(tmp_path); db=database(tmp_path/'source.db')
    try:
        first=legacy(broker,agent,db)
        if interrupt:
            body,stamp,_=body_for(rows(db)[0],'work/b')
            req=dict(source=dict(table='memory_items',id=1),body=body,scope='work/b',created_at=stamp,row_hash=body['metadata']['row_hash'])
            prepared=client.request(dict(op='import',**req))
            from agentmesh_log.events import EVENT_DOMAIN
            from agentmesh_log.encoding import typed
            assert client.request(dict(op='sign',signature=agent._private.sign(EVENT_DOMAIN+typed(prepared['unsigned'])).hex()))['stage']=='sign'
            broker.imports.release(client.session); client.session.pending=None
        assert not migrate(db,broker,client,'work/b')['errors']
        count=len(list(broker.replica.keys()))
        for _ in range(2):
            assert migrate(db,broker,client,'work/b')['outcomes']==dict(unchanged=1)
            assert len(list(broker.replica.keys()))==count
        entries=broker.imports.ledger(broker.state.grants[agent.agent_id])
        old=next(e for e in entries if 'device' not in e['source'])
        new=next(e for e in entries if 'device' in e['source'])
        assert old['status']=='retired' and len(old['chain'])==2 and first['id'] in old['chain']
        assert new['status']=='created' and len(new['chain'])==1 and new['scope']=='work/b'
        assert client.import_row(source=old['source'],scope='work/a',created_at=0,row_hash='0'*64,body=None)['outcome']=='unchanged'
        assert len(list(broker.replica.keys()))==count
    finally: broker.close()


def test_p7_3_rebind_during_scan(tmp_path,monkeypatch):
    import importlib
    module=importlib.import_module('agentmesh_log.migrate.run')
    broker,_,client=make(tmp_path); db=database(tmp_path/'source.db')
    original=module.rows
    def rebound(path,**kwargs):
        with sqlite3.connect(path) as c: c.execute("UPDATE agentmesh_staging_identity SET node='foreign'")
        return original(path,**kwargs)
    monkeypatch.setattr(module,'rows',rebound)
    try:
        with pytest.raises(ValueError,match='binding'): migrate(db,broker,client)
        assert not broker.imports.entries
    finally: broker.close()


@pytest.mark.parametrize('action',['run','rows','catch-up'])
def test_p7_3_cli_binding(tmp_path,action,capsys):
    import json
    from agentmesh_log.cli import main
    db=database(tmp_path/'source.db'); route=tmp_path/'mapping.json'
    route.write_text(json.dumps(mapping().value),encoding='utf-8')
    argv=['migrate',action,'--db',str(db),'--mapping',str(route),'--agent-key',str(tmp_path/'absent.key')]
    if action=='rows': argv+=['--ref','memory_items:1']
    assert main(argv)==1
    assert json.loads(capsys.readouterr().out)==dict(error='staging_binding_missing')
