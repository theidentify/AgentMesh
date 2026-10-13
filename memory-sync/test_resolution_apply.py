"""Gate 3: real SQLite transactions and explicit, test-only activation records."""
import importlib
import json
import sqlite3
import uuid

import pytest
import memory_sync as sync
import packet_versions as versions
import resolution_store as store
import resolution_protocol as protocol
import sqlite_memory
from test_memory_sync import GROUP


@pytest.fixture
def app():
    return importlib.import_module('resolution_apply')


def peers(tmp_path, count=3):
    result = []
    for node in ('mac', 'windows', 'linux')[:count]:
        db = tmp_path / (node + '.db')
        sqlite_memory.init_database(db); sync.initialize(db, node, GROUP)
        result.append(db)
    return result


def activate(db, security=None):
    # Earlier-gate fixtures now use the same operator-approved public API.
    import protocol_activation as activation
    from signed_packets import write_local
    from test_protocol_activation import inventory
    with sync.connect(db) as c:
        if activation.emission_enabled(c): return
        cfg=sync.config(c)
    root=db.with_suffix('.activation'); root.mkdir(mode=0o700,exist_ok=True)
    exchange=db.parent/'activation-exchange'; (exchange/'.stfolder').mkdir(parents=True,exist_ok=True)
    runtime=root/'runtime.json'
    config=dict(app_root=str(root),database=str(db),exchange=str(exchange),node=cfg['node'])
    if security: config['security_dir']=str(security.directory)
    write_local(runtime,config)
    plan=activation.activation_plan(runtime,inventory())
    activation.activate(runtime,plan['plan_digest'],confirm_word='ACTIVATE')


def edit(db, values, security=None):
    with sync.connect(db) as c, c:
        for key, value in values.items():
            if value is None:
                c.execute('DELETE FROM summary_state WHERE consumer=?', (key,))
            else:
                c.execute("INSERT INTO summary_state VALUES(?,?,'fixture') ON CONFLICT(consumer) DO UPDATE SET last_event_id=excluded.last_event_id", (key,value))
    sync.capture(db, security=security)
    with sync.connect(db) as c:
        return json.loads(c.execute('SELECT packet FROM _sync_outbox ORDER BY rowid DESC LIMIT 1').fetchone()[0])


def retain(db, packets, security=None):
    with sync.connect(db) as c, c:
        store.migrate(c)
        # Index first, normalize second, as packets may arrive out of order.
        for p in packets:
            try: versions.decode_packet(c,p,security=security,folder=p['node'],filename=p['uuid']+'.json')
            except protocol.MissingEvidence: pass


def ref(p):
    return protocol.Ref(p['uuid'], versions.envelope_identity(p))


def choices(**values):
    return {protocol.Key('summary_state',(k,)): None if v is None else
            {'consumer':k,'last_event_id':v,'updated_at':'fixture'} for k,v in values.items()}


def resolve(app, db, packets, values, security=None, operation_id=None):
    retain(db, packets, security); activate(db,security)
    with sync.connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        plan = app.prepare_resolution(c, [ref(p) for p in packets], choices(**values), security=security)
        return app.commit_resolution(c,plan,security=security,operation_id=operation_id or str(uuid.uuid4()))


def receive(app, db, packet, security=None):
    with sync.connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        return app.receive_resolution(c, packet, security=security)


def vector(db):
    with sync.connect(db) as c:
        return {r[0]:r[1] for r in c.execute('SELECT consumer,last_event_id FROM summary_state')}


def snapshot(db):
    with sync.connect(db) as c: return '\n'.join(c.iterdump())


def branches(tmp_path):
    a,b,c = peers(tmp_path)
    p = edit(a, {'x':1,'y':2}); q = edit(b, {'x':3,'z':4})
    return a,b,c,p,q


def test_two_peers_companions_exactly_once(app, tmp_path):
    a,b,c,p,q = branches(tmp_path)
    r = resolve(app,a,[p,q],dict(x=5,y=2,z=4))
    retain(b,[p,q]); assert receive(app,b,r).status == 'applied'
    assert vector(a) == vector(b) == dict(x=5,y=2,z=4)
    before = snapshot(b)
    assert receive(app,b,r).status == 'noop'
    assert snapshot(b) == before
    assert sync.capture(b)['changes'] == 0
    with sync.connect(b) as con:
        assert con.execute('SELECT count(*) FROM _sync_packet_dispositions').fetchone()[0] == 2
        assert con.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0] == 1


def test_independent_resolutions_then_terminal_choice(app,tmp_path):
    a,b,c,p,q = branches(tmp_path)
    r = resolve(app,a,[p,q],dict(x=5,y=2,z=4))
    s = resolve(app,b,[p,q],dict(x=9,y=2,z=4))
    with pytest.raises(protocol.ResolutionConflict): receive(app,a,s)
    t = resolve(app,a,[p,q,r,s],dict(x=10,y=2,z=None))
    for db in (b,c):
        retain(db,[p,q,r,s]); receive(app,db,t)
        assert vector(db) == dict(x=10,y=2)


def test_offline_new_branch_is_not_lost(app,tmp_path):
    a,b,c,p,q = branches(tmp_path)
    d = edit(c,dict(x=7))
    r = resolve(app,a,[p,q],dict(x=5,y=2,z=4))
    retain(c,[p,q,d]); before = vector(c)
    with pytest.raises(protocol.ResolutionConflict): receive(app,c,r)
    assert vector(c) == before


def test_later_edit_precedes_resolution_delivery(app,tmp_path):
    a,b,c,p,q = branches(tmp_path)
    r = resolve(app,a,[p,q],dict(x=5,y=2,z=4))
    u = edit(a,dict(x=8))
    retain(b,[p,q,r,u]); receive(app,b,r)
    with sync.connect(b) as con, con:
        app.receive_resolution(con,u,security=None)
        con.execute('DELETE FROM _sync_receipts WHERE uuid=?',(r['uuid'],))
    assert receive(app,b,r).status == 'noop'
    assert vector(b)['x'] == 8


@pytest.mark.parametrize('boundary', ['rows','shadow','history','dispositions','receipt','outbox','operation'])
def test_transaction_faults_leave_no_partial_dispositions(app,tmp_path,monkeypatch,boundary):
    a,b,c,p,q = branches(tmp_path)
    retain(a,[p,q]); activate(a)
    with sync.connect(a) as con:
        plan = app.prepare_resolution(con,[ref(p),ref(q)],choices(x=5,y=2,z=4),security=None)
        before = '\n'.join(con.iterdump())
        class Crash(BaseException): pass
        def crash(name):
            if name == boundary: raise Crash()
        monkeypatch.setattr(app,'_checkpoint',crash)
        with pytest.raises(Crash): app.commit_resolution(con,plan,security=None,operation_id='fault')
        assert '\n'.join(con.iterdump()) == before
    assert snapshot(a) == before


def test_publish_retry_keeps_uuid(app,tmp_path,monkeypatch):
    a,b,c,p,q = branches(tmp_path)
    retain(a,[p,q]); activate(a)
    with sync.connect(a) as con:
        plan = app.prepare_resolution(con,[ref(p),ref(q)],choices(x=5,y=2,z=4),security=None)
        r = app.commit_resolution(con,plan,security=None,operation_id='retry')
        assert app.commit_resolution(con,plan,security=None,operation_id='retry') == r
    original = sync.os.link
    def fail(*args): raise OSError('publication failure')
    monkeypatch.setattr(sync.os,'link',fail)
    assert sync.publish(a,tmp_path/'exchange')['unavailable']
    monkeypatch.setattr(sync.os,'link',original)
    assert not sync.publish(a,tmp_path/'exchange')['unavailable']
    path = tmp_path/'exchange'/'changes'/'mac'/(r['uuid']+'.json')
    assert json.loads(path.read_text()) == r


def test_capture_before_resolve_invalidates_stale_plan(app,tmp_path):
    a,b,c,p,q = branches(tmp_path)
    retain(a,[p,q]); activate(a)
    with sync.connect(a) as con:
        plan = app.prepare_resolution(con,[ref(p),ref(q)],choices(x=5,y=2,z=4),security=None)
    edit(a,dict(x=11))
    before = snapshot(a)
    with sync.connect(a) as con, pytest.raises(ValueError,match='stale'):
        app.commit_resolution(con,plan,security=None,operation_id='stale')
    assert snapshot(a) == before


@pytest.mark.parametrize('failure',['unique','foreign-key','cascade'])
def test_relational_repairs_refused_atomically(app,tmp_path,failure):
    a,b,c,p,q = branches(tmp_path)
    retain(a,[p,q]); activate(a)
    if failure == 'cascade': edit(a,dict(off_packet=99))
    with sync.connect(a) as con, con:
        if failure == 'unique':
            con.execute('CREATE UNIQUE INDEX test_unique ON summary_state(last_event_id)')
        elif failure == 'foreign-key':
            con.execute('CREATE TABLE test_parent(id INTEGER PRIMARY KEY)')
            con.execute('CREATE TABLE test_child(id INTEGER REFERENCES test_parent(id) DEFERRABLE INITIALLY DEFERRED)')
            con.execute('CREATE TRIGGER test_fk AFTER UPDATE ON summary_state BEGIN INSERT INTO test_child VALUES(99); END')
        else:
            con.execute("CREATE TRIGGER test_cascade AFTER UPDATE ON summary_state BEGIN DELETE FROM summary_state WHERE consumer='off_packet'; END")
    with sync.connect(a) as con:
        plan = app.prepare_resolution(con,[ref(p),ref(q)],choices(x=2 if failure=='unique' else 5,y=2,z=4),security=None)
        before = '\n'.join(con.iterdump())
        with pytest.raises(ValueError,match='unsupported relational repair'):
            app.commit_resolution(con,plan,security=None,operation_id='repair')
        assert '\n'.join(con.iterdump()) == before


def test_authoring_requires_activation_and_capture_stays_legacy(app,tmp_path):
    a,b,c,p,q = branches(tmp_path); retain(a,[p,q])
    with sync.connect(a) as con, pytest.raises(ValueError,match='emission disabled'):
        app.prepare_resolution(con,[ref(p),ref(q)],choices(x=5,y=2,z=4),security=None)
    assert edit(a,dict(x=12))['format'] == sync.FORMAT


def test_gate2_schema_upgrade_is_atomic_and_lazy(app,tmp_path):
    a,b,c = peers(tmp_path)
    sync.capture(a); sync.receive(a,tmp_path/'empty'); sync.status(a)
    with sync.connect(a) as con:
        assert not store.exists(con)
        store.migrate(con)
        con.execute('DROP TABLE _sync_resolution_operations')
        con.execute('DROP TABLE _sync_packet_paths')
        con.execute('ALTER TABLE _sync_protocol DROP COLUMN activation_manifest')
        con.execute('ALTER TABLE _sync_protocol DROP COLUMN minimum_capability')
        con.execute('UPDATE _sync_protocol SET schema_version=1'); con.commit()
        before = '\n'.join(con.iterdump())
        con.set_authorizer(lambda action,name,*args: sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_CREATE_TABLE and name=='_sync_resolution_operations' else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError): store.migrate(con)
        con.set_authorizer(None)
        assert '\n'.join(con.iterdump()) == before
        store.migrate(con)
        assert con.execute('SELECT schema_version FROM _sync_protocol').fetchone()[0] == store.SCHEMA_VERSION


@pytest.mark.parametrize('boundary',['rows','shadow','history','dispositions','receipt','outbox','operation','committed'])
def test_process_crash_recovery(app,tmp_path,boundary):
    import os
    import subprocess
    import sys
    from pathlib import Path
    a,b,c,p,q=branches(tmp_path); retain(a,[p,q]); activate(a)
    with sync.connect(a) as con:
        plan=app.prepare_resolution(con,[ref(p),ref(q)],choices(x=5,y=2,z=4),security=None)
    plan_path=tmp_path/'plan.json'; plan_path.write_text(sync.canonical(vars(plan)))
    before=snapshot(a)
    script='''import json,os,sys
import memory_sync as sync
import resolution_apply as app
plan=app.ResolutionPlan(**json.load(open(sys.argv[2])))
def crash(point):
    if point==sys.argv[3]: os._exit(73)
app._checkpoint=crash
with sync.connect(sys.argv[1]) as c:
    app.commit_resolution(c,plan,security=None,operation_id='crash')
os._exit(73)
'''
    result=subprocess.run([sys.executable,'-c',script,str(a),str(plan_path),boundary],
                          cwd=Path(__file__).parent, env={**os.environ,'PYTHONPATH':str(Path(__file__).parent)})
    assert result.returncode==73
    if boundary!='committed': assert snapshot(a)==before
    with sync.connect(a) as con:
        packet=app.commit_resolution(con,plan,security=None,operation_id='crash')
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert con.execute('SELECT importing FROM _sync_config').fetchone()[0]==0
        assert con.execute('SELECT packet_uuid FROM _sync_resolution_operations').fetchone()[0]==packet['uuid']
    assert vector(a)==dict(x=5,y=2,z=4)


@pytest.mark.parametrize('boundary',['rows','shadow','history','dispositions','receipt'])
def test_receiver_fault_rolls_back_rows_receipt_and_diagnostic_cleanup(app,tmp_path,monkeypatch,boundary):
    a,b,c,p,q=branches(tmp_path); r=resolve(app,a,[p,q],dict(x=5,y=2,z=4))
    retain(b,[p,q,r])
    with sync.connect(b) as con, con:
        con.execute('INSERT INTO _sync_packet_paths VALUES(?,?,?)',('matching',p['uuid'],ref(p).digest))
        con.execute("INSERT INTO _sync_diagnostics VALUES('matching','conflict','old')")
    before=snapshot(b)
    def fail(point):
        if point==boundary: raise RuntimeError('injected receiver fault')
    monkeypatch.setattr(app,'_checkpoint',fail)
    with pytest.raises(RuntimeError): receive(app,b,r)
    assert snapshot(b)==before


def test_missing_baseline_witness_and_mismatched_baseline_refused(app,tmp_path):
    a,b,c=peers(tmp_path)
    with sync.connect(a) as con, con:
        con.execute("INSERT INTO summary_state VALUES('x',0,'fixture')")
        row=dict(con.execute('SELECT * FROM summary_state').fetchone())
        baseline='baseline:'+sync.digest(['summary_state',['x'],row])
        con.execute('INSERT INTO _sync_shadow VALUES(?,?,?,?)',('summary_state','["x"]',sync.canonical(row),baseline))
        con.execute('INSERT INTO _sync_history VALUES(?,?,?)',(baseline,'summary_state','["x"]'))
    activate(a)  # retain actual baseline witness before its payload is overwritten
    p=edit(a,dict(x=1)); retain(a,[p])
    r=resolve(app,a,[p],dict(x=5))
    retain(b,[p])
    with pytest.raises(protocol.MissingEvidence): receive(app,b,r)
    assert vector(b)=={}
    # A different baseline cannot be made comparable by choosing a final row.
    with sync.connect(c) as con, con:
        row={'consumer':'x','last_event_id':9,'updated_at':'fixture'}
        con.execute('INSERT INTO summary_state VALUES(?,?,?)',tuple(row.values()))
        other='baseline:'+sync.digest(['summary_state',['x'],row])
        con.execute('INSERT INTO _sync_shadow VALUES(?,?,?,?)',('summary_state','["x"]',sync.canonical(row),other))
        con.execute('INSERT INTO _sync_history VALUES(?,?,?)',(other,'summary_state','["x"]'))
    with pytest.raises(ValueError,match='baseline'): retain(c,[p])
    assert vector(c)==dict(x=9)


def test_replay_does_not_scan_rows_per_retained_packet(app,tmp_path,monkeypatch):
    a,b,c,p,q=branches(tmp_path); r=resolve(app,a,[p,q],dict(x=5,y=2,z=4))
    from test_resolution_e2e import put
    ex=tmp_path/'exchange'
    for packet in [r,p,q]: put(ex,packet)
    sync.receive(c,ex)
    calls=[]; original=sync.rows
    def counted(con): calls.append(1); return original(con)
    monkeypatch.setattr(sync,'rows',counted)
    assert sync.receive(c,ex)['applied']==0
    assert len(calls)==1  # initial legacy capture only; no per-evidence row scans


def test_crash_after_publish_link_reuses_committed_packet(app,tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    a,b,c,p,q=branches(tmp_path)
    r=resolve(app,a,[p,q],dict(x=5,y=2,z=4),operation_id='publish-crash')
    ex=tmp_path/'exchange'
    script='''import os,sys
import memory_sync as sync
original=sync.os.link
def link(source,target):
    original(source,target)
    if target.name==sys.argv[3]+'.json': os._exit(74)
sync.os.link=link
sync.publish(sys.argv[1],sys.argv[2])
'''
    result=subprocess.run([sys.executable,'-c',script,str(a),str(ex),r['uuid']],
                          cwd=Path(__file__).parent, env={**os.environ,'PYTHONPATH':str(Path(__file__).parent)})
    assert result.returncode==74
    path=ex/'changes'/'mac'/(r['uuid']+'.json'); original=path.read_bytes()
    with sync.connect(a) as con:
        assert con.execute('SELECT published FROM _sync_outbox WHERE uuid=?',(r['uuid'],)).fetchone()[0]==0
    assert sync.publish(a,ex)['published']==1
    assert path.read_bytes()==original
    assert json.loads(original)==r
