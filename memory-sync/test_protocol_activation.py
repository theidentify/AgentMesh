"""Operator capability activation, with no production test bypass."""
import importlib
import json
from pathlib import Path
import pytest
import memory_sync as sync
import resolution_store as store
from test_install_adopt import existing
from signed_packets import write_local

@pytest.fixture
def activation(): return importlib.import_module('protocol_activation')

def runtime_for(tmp_path):
    args=existing(tmp_path)
    write_local(args['runtime'],{k:str(v) for k,v in args.items() if k!='runtime'})
    return args

def inventory():
    return dict(inventory_complete=True,legacy_workers_drained=True,peers=[
        dict(node=n,capability=1,software='AgentMesh resolution-v1',offline=n!='mac')
        for n in ('mac','windows','linux')])

def enable(activation,runtime):
    plan=activation.activation_plan(runtime,inventory())
    activation.activate(runtime,plan['plan_digest'],confirm_word='ACTIVATE')
    return plan


def test_activation_requires_all_peer_inventory_and_typed_word(activation,tmp_path):
    args=runtime_for(tmp_path)
    with pytest.raises(ValueError): activation.activation_plan(args['runtime'],dict(inventory(),inventory_complete=False))
    plan=activation.activation_plan(args['runtime'],inventory())
    with sync.connect(args['database']) as c: assert not store.exists(c)
    with pytest.raises(ValueError): activation.activate(args['runtime'],plan['plan_digest'],confirm_word='yes')
    with sync.connect(args['database']) as c: assert not store.exists(c)
    activation.activate(args['runtime'],plan['plan_digest'],confirm_word='ACTIVATE')
    with sync.connect(args['database']) as c:
        assert activation.emission_enabled(c)
        record=c.execute('SELECT activation_manifest,minimum_capability FROM _sync_protocol').fetchone()
        assert json.loads(record[0])['inventory']['peers']==inventory()['peers']
        assert record[1]==1


def test_emission_disabled_before_activation(activation,tmp_path):
    args=runtime_for(tmp_path)
    from test_resolution_apply import edit
    assert edit(args['database'],dict(x=1))['format']==sync.FORMAT
    enable(activation,args['runtime'])
    assert edit(args['database'],dict(x=2))['format']=='omp-memory-changes-v2'
    assert sync.capture(args['database'])['changes']==0
    assert 'gate3-test-only' not in '\n'.join(p.read_text() for p in Path(__file__).parent.glob('*.py') if not p.name.startswith('test_'))


def test_old_readers_remain_after_activation(activation,tmp_path):
    from test_resolution_apply import peers, edit
    from test_resolution_e2e import put
    args=runtime_for(tmp_path); enable(activation,args['runtime'])
    other=tmp_path/'others'; other.mkdir(); _,b,_=peers(other)
    p=edit(b,dict(x=11)); put(args['exchange'],p)
    assert sync.receive(args['database'],args['exchange'])['applied']==1
    assert p['format']==sync.FORMAT


def test_managed_downgrade_refused_after_activation(activation,tmp_path):
    args=runtime_for(tmp_path); enable(activation,args['runtime'])
    with pytest.raises(ValueError,match='capability'):
        activation.require_capability({'protocol_capability':0},args['database'])
    activation.require_capability({'protocol_capability':1},args['database'])


@pytest.mark.parametrize('damage',['runtime','exchange','trust','inventory'])
def test_activation_rechecks_confirmation_inputs(activation,tmp_path,damage):
    args=runtime_for(tmp_path); plan=activation.activation_plan(args['runtime'],inventory())
    if damage=='runtime': args['runtime'].write_text(args['runtime'].read_text()+' ')
    elif damage=='exchange':
        args['exchange'].rename(tmp_path/'old-exchange'); (args['exchange']/'.stfolder').mkdir(parents=True)
    elif damage=='trust':
        args['security_dir'].mkdir(); (args['security_dir']/'trust.json').write_text('{}')
    else:
        path=activation.plan_path(args['runtime'],plan['plan_digest']); raw=json.loads(path.read_text()); raw['inventory']['peers'].pop(); write_local(path,raw)
    with pytest.raises(ValueError): activation.activate(args['runtime'],plan['plan_digest'],confirm_word='ACTIVATE')
    with sync.connect(args['database']) as c: assert not store.exists(c)


def test_activated_signed_capture_uses_real_v3_crypto(tmp_path):
    import signed_packets as signed
    import packet_versions as versions
    from test_resolution_apply import peers,edit,activate
    from test_memory_sync import GROUP
    a,b,_=peers(tmp_path)
    da,db=tmp_path/'a-keys',tmp_path/'b-keys'
    pa=signed.init_identity(da,GROUP,'mac');pb=signed.init_identity(db,GROUP,'windows')
    signed.approve(da,pb,pb['key_id'],GROUP,'windows',pb['sender']);signed.approve(db,pa,pa['key_id'],GROUP,'mac',pa['sender'])
    sa,sb=signed.Security(da),signed.Security(db)
    sync.capture(a,security=sa);activate(a,sa)
    p=edit(a,dict(x=8),sa)
    assert p['format']==versions.NEW_SIGNED and p['kind']=='change'
    assert sb.verify(p,GROUP,'mac')==p
    with sync.connect(b) as c,c:
        import resolution_apply
        assert resolution_apply.receive_resolution(c,p,security=sb).status=='applied'


def test_inventory_cannot_omit_observed_or_reserved_offline_peers(activation,tmp_path):
    args=runtime_for(tmp_path)
    (args['exchange']/'changes'/'windows').mkdir(parents=True)
    value=inventory();value['peers']=[value['peers'][0]]
    with pytest.raises(ValueError,match='omits'):activation.activation_plan(args['runtime'],value)


def test_activated_capture_self_replay_is_noop(activation,tmp_path):
    args=runtime_for(tmp_path);enable(activation,args['runtime'])
    from test_resolution_apply import edit
    edit(args['database'],dict(x=1))
    report=sync.cycle(args['database'],args['exchange'])
    assert report['receive']['conflict']==report['receive']['invalid']==0
    assert report['receive']['applied']==0


@pytest.mark.parametrize('forever',[False,True])
def test_snapshot_retries_writer_with_bounded_backoff(tmp_path,monkeypatch,forever):
    import sqlite3
    import protocol_activation as activation
    args=runtime_for(tmp_path);db=args['database']
    writer=sqlite3.connect(db)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('CREATE TABLE snapshot_probe(value INTEGER)')
    writer.execute('INSERT INTO snapshot_probe VALUES(0)');writer.commit()
    original=activation.shutil.copyfile;copy_calls=[];sleeps=[];expected=[];targets=[]
    def files(): return {p.name:p.read_bytes() for p in db.parent.glob(db.name+'*')}
    def copying(source,target):
        result=original(source,target)
        if str(source)==str(db):
            copy_calls.append(source);targets.append(target)
            if forever or len(copy_calls)<=4:
                writer.execute('UPDATE snapshot_probe SET value=value+1');writer.commit()
            expected[:]=[files()]
        return result
    monkeypatch.setattr(activation.shutil,'copyfile',copying)
    # Patch the existing stdlib module; the implementation must actually back off.
    import time
    monkeypatch.setattr(time,'sleep',sleeps.append)
    try:
        if forever:
            with pytest.raises(ValueError,match='^database changed during snapshot; retry$'):
                with activation.snapshot(db): pytest.fail('unstable pair must not be opened')
            assert len(copy_calls)==20 and len(sleeps)==19
        else:
            with activation.snapshot(db) as c:
                assert c.execute('SELECT value FROM snapshot_probe').fetchone()[0]==4
                assert c.execute('PRAGMA query_only').fetchone()[0]==1
                assert files()==expected[0]
            assert len(copy_calls)==5 and len(sleeps)==4
        assert all(0<delay<=0.25 for delay in sleeps) and sum(sleeps)<=5
        assert files()==expected[0]  # DB, WAL and SHM only changed by the injected writer
        assert all(not target.parent.exists() for target in targets)
    finally: writer.close()


@pytest.mark.parametrize('error',[FileNotFoundError,PermissionError])
def test_snapshot_retries_transient_copy_failure(tmp_path,monkeypatch,error):
    import protocol_activation as activation
    args=runtime_for(tmp_path);original=activation.shutil.copyfile;calls=[];sleeps=[]
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    def copying(source,target):
        calls.append(source)
        if len(calls)==1: raise error('simulated checkpoint/sharing race')
        return original(source,target)
    monkeypatch.setattr(activation.shutil,'copyfile',copying)
    monkeypatch.setattr(activation.time,'sleep',sleeps.append)
    with activation.snapshot(args['database']) as c: assert sync.config(c)['node']=='mac'
    assert len(sleeps)==1 and len(calls)>=2
    assert {p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}==before


def test_busy_snapshot_cli_reason_is_actionable():
    from cli_errors import report
    reason='database changed during snapshot; retry'
    assert report(ValueError(reason),'conflicts')['reason']==reason
