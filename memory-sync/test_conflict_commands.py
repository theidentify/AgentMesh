"""Read-only inspection and confirmed, locked local recovery commands."""
import importlib
import io
import json
import pytest
import memory_sync as sync
from test_protocol_activation import runtime_for, inventory, enable
from test_resolution_apply import peers, edit
from test_resolution_e2e import put

@pytest.fixture
def commands(): return importlib.import_module('conflict_commands')


def setup_conflict(tmp_path):
    args=runtime_for(tmp_path); other=tmp_path/'others'; other.mkdir(); _,b,_=peers(other)
    p=edit(args['database'],dict(x=1,y=2)); q=edit(b,dict(x=3,z=4))
    put(args['exchange'],p); put(args['exchange'],q)
    sync.receive(args['database'],args['exchange'])
    return args,p,q


def files(root): return {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_conflicts_is_read_only_and_redacts_public_output(commands,tmp_path):
    args,p,q=setup_conflict(tmp_path); before=files(tmp_path)
    report=commands.list_conflicts(args['database'],args['exchange'])
    assert report['conflicts'] and 'last_event_id' not in json.dumps(report)
    detailed=commands.list_conflicts(args['database'],args['exchange'],show_content=True)
    assert 'last_event_id' in json.dumps(detailed)
    assert files(tmp_path)==before
    with sync.connect(args['database']) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_protocol'").fetchone()


@pytest.mark.parametrize('choice',['mine','theirs','row','delete'])
def test_mine_theirs_explicit_row_and_delete_preview(commands,tmp_path,choice):
    activation=importlib.import_module('protocol_activation')
    args,p,q=setup_conflict(tmp_path); enable(activation,args['runtime'])
    selected={'mode':choice}
    if choice=='row': selected['rows']=[dict(table='summary_state',key=['x'],row=dict(consumer='x',last_event_id=9,updated_at='fixture'))]
    plan=commands.preview_resolution(args['runtime'],[q['uuid']],selected)
    report=commands.resolve(args['runtime'],plan['plan_digest'],confirm_word='RESOLVE',operation_id='choice')
    assert report['status']=='resolved' and report['next_action']=='worker-start'
    with sync.connect(args['database']) as c:
        values=dict(c.execute('SELECT consumer,last_event_id FROM summary_state'))
    assert values.get('x')=={'mine':1,'theirs':3,'row':9,'delete':None}[choice]
    assert values.get('y')==2 and values.get('z')==4


def test_wrong_word_eof_and_stale_plan_write_nothing(commands,tmp_path,monkeypatch):
    from protocol_activation import activation_plan,activate
    args,p,q=setup_conflict(tmp_path)
    act=activation_plan(args['runtime'],inventory()); activate(args['runtime'],act['plan_digest'],confirm_word='ACTIVATE')
    plan=commands.preview_resolution(args['runtime'],[q['uuid']],{'mode':'mine'})
    before=files(tmp_path)
    with pytest.raises(ValueError): commands.resolve(args['runtime'],plan['plan_digest'],confirm_word='yes',operation_id='bad')
    assert files(tmp_path)==before
    import agentmesh
    monkeypatch.setattr('sys.stdin',io.StringIO(''))
    assert agentmesh.main(['resolve','--runtime',str(args['runtime']),'--plan-digest',plan['plan_digest'],'--operation-id','eof'])==1
    assert files(tmp_path)==before
    edit(args['database'],dict(x=11)); before=files(tmp_path)
    with pytest.raises(ValueError,match='stale|changed'): commands.resolve(args['runtime'],plan['plan_digest'],confirm_word='RESOLVE',operation_id='stale')
    with sync.connect(args['database']) as c: assert dict(c.execute('SELECT consumer,last_event_id FROM summary_state'))['x']==11


def test_resolve_commit_publish_retry_uses_same_operation(commands,tmp_path,monkeypatch):
    activation=importlib.import_module('protocol_activation')
    args,p,q=setup_conflict(tmp_path); enable(activation,args['runtime'])
    plan=commands.preview_resolution(args['runtime'],[q['uuid']],{'mode':'theirs'})
    original=sync.publish
    monkeypatch.setattr(sync,'publish',lambda *a,**k:dict(unavailable=True,published=0))
    first=commands.resolve(args['runtime'],plan['plan_digest'],confirm_word='RESOLVE',operation_id='retry')
    monkeypatch.setattr(sync,'publish',original)
    second=commands.resolve(args['runtime'],plan['plan_digest'],confirm_word='RESOLVE',operation_id='retry')
    assert first['packet_uuid']==second['packet_uuid'] and not second['publish']['unavailable']


def test_stopped_worker_receive_only_then_explicit_restart(commands,tmp_path,monkeypatch):
    from test_worker_lifecycle import bound
    import worker_lifecycle as worker
    import sync_worker
    args=bound(tmp_path,monkeypatch)
    original=sync_worker.run_once
    monkeypatch.setattr(sync_worker,'run_once',lambda *a,**k:(_ for _ in ()).throw(ValueError('unrelated failure')))
    assert worker.run(args['runtime'],once=True,legacy_drained=True)==1
    old=worker.status(args['runtime'])
    report=commands.receive_only(args['runtime'])
    assert report['next_action']=='worker-start'
    assert worker.status(args['runtime'])==old
    monkeypatch.setattr(sync_worker,'run_once',original)
    worker.start(args['runtime'],interval=60,legacy_drained=True,timeout=30)
    try:
        assert worker.status(args['runtime'])['nonce']!=old['nonce']
        assert worker.status(args['runtime'])['cycles']>=1
    finally: worker.stop(args['runtime'],timeout=30)


@pytest.mark.parametrize('secure',[False,True])
def test_legacy_only_and_upgraded_peers_interoperate_before_activation(tmp_path,secure):
    import resolution_store as store
    import sqlite_memory
    import signed_packets as signed
    from test_memory_sync import GROUP
    a,b,_=peers(tmp_path); ex=tmp_path/'exchange';ex.mkdir();(ex/'.stfolder').mkdir()
    sa=sb=None
    if secure:
        da,db=tmp_path/'keys-a',tmp_path/'keys-b'
        pa=signed.init_identity(da,GROUP,'mac');pb=signed.init_identity(db,GROUP,'windows')
        signed.approve(da,pb,pb['key_id'],GROUP,'windows',pb['sender']);signed.approve(db,pa,pa['key_id'],GROUP,'mac',pa['sender'])
        sa,sb=signed.Security(da),signed.Security(db)
    with sync.connect(b) as c: store.migrate(c)
    p=edit(a,dict(x=1),sa);put(ex,p)
    assert sync.receive(b,ex,security=sb)['applied']==1
    q=edit(b,dict(x=2),sb);put(ex,q)
    assert sync.receive(a,ex,security=sa)['applied']==1
    assert p['format']==q['format']==('agentmesh-signed-changes-v2' if secure else sync.FORMAT)
    with sync.connect(a) as c: assert not store.exists(c)
    with sync.connect(b) as c:
        assert c.execute('SELECT emission_enabled FROM _sync_protocol').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM _sync_packet_paths').fetchone()[0]>=1


def test_listing_live_wal_and_signed_telemetry_are_unchanged(commands,tmp_path):
    args,p,q=setup_conflict(tmp_path)
    with sync.connect(args['database']) as c,c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE _sync_verification(attempts INTEGER)')
        c.execute('INSERT INTO _sync_verification VALUES(7)'); c.commit()
        before=files(tmp_path)
        assert commands.list_conflicts(args['database'],args['exchange'])['conflicts']
        assert files(tmp_path)==before
        assert c.execute('SELECT attempts FROM _sync_verification').fetchone()[0]==7


def test_stale_preview_does_not_create_worker_control_files(commands,tmp_path):
    import protocol_activation as activation
    args,p,q=setup_conflict(tmp_path);enable(activation,args['runtime'])
    plan=commands.preview_resolution(args['runtime'],[q['uuid']],{'mode':'mine'})
    # Remove only fixture lock artifacts left by activation before checking resolve.
    import shutil
    shutil.rmtree(args['database'].parent/'.agentmesh-worker')
    (args['database'].parent/'worker.lock').unlink()
    edit(args['database'],dict(x=10));before=files(tmp_path)
    with pytest.raises(ValueError,match='stale'):commands.resolve(args['runtime'],plan['plan_digest'],confirm_word='RESOLVE',operation_id='stale')
    assert files(tmp_path)==before


def test_upgraded_legacy_receive_preserves_intervening_local_edit(tmp_path,monkeypatch):
    import resolution_apply as apply
    import resolution_store as store
    a,b,_=peers(tmp_path);ex=tmp_path/'exchange'
    p=edit(a,dict(x=1));put(ex,p);sync.receive(b,ex)
    with sync.connect(b) as c:store.migrate(c)
    q=edit(a,dict(x=2));put(ex,q)
    original=apply.receive_indexed;changed=[]
    def race(c,p,path,**kwargs):
        if p['uuid']==q['uuid'] and not changed:
            c.execute("UPDATE summary_state SET last_event_id=99 WHERE consumer='x'");changed.append(True)
        return original(c,p,path,**kwargs)
    monkeypatch.setattr(apply,'receive_indexed',race)
    report=sync.receive(b,ex)
    assert report['conflict']==1
    with sync.connect(b) as c:
        assert c.execute("SELECT last_event_id FROM summary_state WHERE consumer='x'").fetchone()[0]==99


def test_pre_activation_diagnostics_clear_in_resolution_commit(commands,tmp_path,monkeypatch):
    import protocol_activation as activation
    args,p,q=setup_conflict(tmp_path);enable(activation,args['runtime'])
    with sync.connect(args['database']) as c,c:c.execute("INSERT INTO _sync_diagnostics VALUES('unrelated','invalid','keep')")
    plan=commands.preview_resolution(args['runtime'],[q['uuid']],{'mode':'theirs'})
    def crash(*a,**k): raise RuntimeError('crash after commit before publication')
    monkeypatch.setattr(sync,'publish',crash)
    with pytest.raises(RuntimeError):commands.resolve(args['runtime'],plan['plan_digest'],confirm_word='RESOLVE',operation_id='committed')
    with sync.connect(args['database']) as c:
        assert [r[0] for r in c.execute('SELECT path FROM _sync_diagnostics')]==['unrelated']
        assert c.execute('SELECT count(*) FROM _sync_resolution_operations').fetchone()[0]==1


def test_preview_refuses_uncaptured_edits_and_relational_repairs(commands,tmp_path):
    import protocol_activation as activation
    args,p,q=setup_conflict(tmp_path);enable(activation,args['runtime'])
    with sync.connect(args['database']) as c,c:c.execute("UPDATE summary_state SET last_event_id=12 WHERE consumer='x'")
    before=files(tmp_path)
    with pytest.raises(ValueError,match='uncaptured'):commands.preview_resolution(args['runtime'],[q['uuid']],{'mode':'mine'})
    assert files(tmp_path)==before
    with sync.connect(args['database']) as c,c:
        c.execute("UPDATE summary_state SET last_event_id=1 WHERE consumer='x'")
        c.execute('CREATE UNIQUE INDEX preview_unique ON summary_state(last_event_id)')
    before=files(tmp_path)
    rows=[dict(table='summary_state',key=['x'],row=dict(consumer='x',last_event_id=2,updated_at='fixture'))]
    with pytest.raises(ValueError,match='unsupported relational repair'):commands.preview_resolution(args['runtime'],[q['uuid']],dict(mode='row',rows=rows))
    assert files(tmp_path)==before


def test_pending_receive_does_not_recommend_worker_restart(commands,tmp_path):
    from test_packet_versions import new_packet
    args=runtime_for(tmp_path)
    packet=new_packet(kind='resolution');packet['node']='windows'
    import packet_versions,resolution_protocol
    packet['checksum']=resolution_protocol.payload_digest(packet_versions.semantic(packet))
    put(args['exchange'],packet)
    report=commands.receive_only(args['runtime'])
    assert report['receive']['pending']==1
    assert report['next_action']=='review-diagnostics'


def protected_conflict(tmp_path):
    import signed_packets as signed
    import key_protection as kp
    import protocol_activation as activation
    from test_key_protection import PASS, FAST
    from test_memory_sync import GROUP
    args=runtime_for(tmp_path)
    other=tmp_path/'others';other.mkdir();_,b,_=peers(other)
    remote=tmp_path/'remote-key'
    pa=signed.init_identity(args['security_dir'],GROUP,'mac')
    pb=signed.init_identity(remote,GROUP,'windows')
    signed.approve(args['security_dir'],pb,pb['key_id'],GROUP,'windows',pb['sender'])
    signed.approve(remote,pa,pa['key_id'],GROUP,'mac',pa['sender'])
    sa,sb=signed.Security(args['security_dir']),signed.Security(remote)
    p=edit(args['database'],dict(x=1,y=2),sa);q=edit(b,dict(x=3,z=4),sb)
    put(args['exchange'],p);put(args['exchange'],q)
    sync.receive(args['database'],args['exchange'],security=sa)
    enable(activation,args['runtime'])
    kp.protect_identity(args['security_dir'],lambda prompt:PASS,**FAST)
    return args,p,q


def test_locked_protected_key_cannot_sign_or_commit_resolution(tmp_path):
    import signed_packets as signed
    import resolution_apply as apply
    import key_protection as kp
    from test_key_protection import PASS
    from test_resolution_apply import ref,choices,snapshot
    args,p,q=protected_conflict(tmp_path)
    unlocked=signed.Security(args['security_dir'],unlock=lambda:PASS)
    locked=signed.Security(args['security_dir'])
    with sync.connect(args['database']) as c,c:
        plan=apply.prepare_resolution(c,[ref(p),ref(q)],choices(x=3,y=2,z=4),security=unlocked)
    before=snapshot(args['database'])
    with sync.connect(args['database']) as c,c:
        with pytest.raises(ValueError) as error:
            locked._sign_resolution(c,json.loads(plan.payload_json))
        assert str(error.value)==kp.LOCKED
        with pytest.raises(ValueError) as error:
            apply.commit_resolution(c,plan,security=locked,operation_id='locked')
        assert str(error.value)==kp.LOCKED
    assert snapshot(args['database'])==before
    # Real positive control: the same protected identity can sign after unlocking.
    with sync.connect(args['database']) as c,c:
        packet=apply.commit_resolution(c,plan,security=unlocked,operation_id='unlocked')
        unlocked.verify(packet,packet['group'],packet['node'])


def test_resolve_preview_reports_locked_reason_without_writes(tmp_path,capsys):
    import agentmesh
    import key_protection as kp
    args,p,q=protected_conflict(tmp_path);before=files(tmp_path)
    assert agentmesh.main(['resolve','--runtime',str(args['runtime']),'--packet',q['uuid'],
                           '--choice','theirs','--preview'])==1
    error=json.loads(capsys.readouterr().err)
    assert error['reason']==kp.LOCKED
    assert files(tmp_path)==before


def test_protected_resolution_uses_os_unlock_and_locked_commit_is_write_free(tmp_path,monkeypatch,capsys):
    import agentmesh
    import conflict_commands as commands
    import key_protection as kp
    import os_unlock as ou
    from test_os_unlock import FakeBackend
    from test_key_protection import PASS
    args,p,q=protected_conflict(tmp_path)
    backend=FakeBackend();ou.reset_cache()
    monkeypatch.setattr(ou,'current_backend',lambda:backend)
    ou.enable(args['security_dir'],ask=lambda prompt:PASS,backend=backend)
    try:
        plan=commands.preview_resolution(args['runtime'],[q['uuid']],{'mode':'theirs'})
        # Loss of the OS secret does not change identity/trust or stale the plan.
        backend.fail['load']=ou.MISSING;ou.reset_cache();before=files(tmp_path)
        argv=['resolve','--runtime',str(args['runtime']),'--plan-digest',plan['plan_digest'],
              '--operation-id','protected','--yes','--confirm','RESOLVE']
        assert agentmesh.main(argv)==1
        assert json.loads(capsys.readouterr().err)['reason']==ou.MISSING
        assert files(tmp_path)==before
        backend.fail.clear();ou.reset_cache()
        assert agentmesh.main(argv)==0
        assert json.loads(capsys.readouterr().out)['status']=='resolved'
    finally: ou.reset_cache()


def test_locked_signed_conflicts_cli_is_read_only_without_os_access(tmp_path,monkeypatch,capsys):
    import agentmesh
    import os_unlock as ou
    args,p,q=protected_conflict(tmp_path)
    def forbidden(): raise AssertionError('listing must not access OS key store')
    monkeypatch.setattr(ou,'current_backend',forbidden)
    before=files(tmp_path)
    assert agentmesh.main(['conflicts','--runtime',str(args['runtime']),'--json'])==0
    report=json.loads(capsys.readouterr().out)
    assert report['conflicts'] and report['content_shown'] is False
    assert 'last_event_id' not in json.dumps(report)
    assert files(tmp_path)==before


@pytest.mark.parametrize('action',['receive-only','activate-protocol'])
def test_signed_recovery_requires_unlock_before_control_or_database_writes(tmp_path,monkeypatch,capsys,action):
    import agentmesh
    import key_protection as kp
    import os_unlock as ou
    import protocol_activation as activation
    import signed_packets as signed
    from test_memory_sync import GROUP
    from test_key_protection import PASS,FAST
    from test_os_unlock import FakeBackend
    args=runtime_for(tmp_path)
    signed.init_identity(args['security_dir'],GROUP,'mac')
    security=signed.Security(args['security_dir'])
    sync.capture(args['database'],security=security)
    kp.protect_identity(args['security_dir'],lambda prompt:PASS,**FAST)
    argv=[action,'--runtime',str(args['runtime'])]
    if action=='activate-protocol':
        plan=activation.activation_plan(args['runtime'],inventory())
        argv+=['--plan-digest',plan['plan_digest'],'--yes','--confirm','ACTIVATE']
    before=files(tmp_path)
    assert agentmesh.main(argv)==1
    assert json.loads(capsys.readouterr().err)['reason']==kp.LOCKED
    assert files(tmp_path)==before
    backend=FakeBackend();ou.reset_cache()
    monkeypatch.setattr(ou,'current_backend',lambda:backend)
    ou.enable(args['security_dir'],ask=lambda prompt:PASS,backend=backend)
    try:
        # Enrolling changes identity bytes, so activation must be previewed again.
        if action=='activate-protocol':
            plan=activation.activation_plan(args['runtime'],inventory())
            argv[argv.index('--plan-digest')+1]=plan['plan_digest']
        assert agentmesh.main(argv)==0
        assert json.loads(capsys.readouterr().out)['status'] in ('received','activated')
    finally: ou.reset_cache()
