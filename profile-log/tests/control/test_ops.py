import json
import os
import pytest
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.ops import Control
from .support import FakeBackend

CALLER=dict(pid=7,exe='/usr/bin/agent')


def request(op, args=None, rid='r1'): return dict(v=1,id=rid,op=op,args=args or {})


def audit_rows(tmp_path):
    return [json.loads(line) for line in Layout(tmp_path).audit.read_bytes().splitlines()]


def test_status_is_tier0_and_not_audited(tmp_path):
    response=Control(Layout(tmp_path),FakeBackend()).handle(request('status'),CALLER)
    assert response['ok'] and response['id']=='r1' and len(response['result']['units'])==8
    assert response['result']['maintenance']['on'] is False and response['result']['quota']['calls']==0
    assert not Layout(tmp_path).audit.exists()


@pytest.mark.parametrize('bad',[None,[],dict(v=1,id='r',op='status'),dict(v=True,id='r',op='status',args={}),
    dict(v=2,id='r',op='status',args={}),dict(v=1,id='',op='status',args={}),dict(v=1,id='x'*65,op='status',args={}),
    dict(v=1,id='r',op='status',args={},extra=1),dict(v=1,id='r',op='status',args=[])],
    ids=['none','list','no-args','v-bool','v-2','id-empty','id-long','extra-key','args-list'])
def test_envelope_refused(tmp_path, bad):
    assert Control(Layout(tmp_path),FakeBackend()).handle(bad,CALLER)['error']['code']=='bad_args'


def test_unknown_op(tmp_path):
    assert Control(Layout(tmp_path),FakeBackend()).handle(request('unit.delete'),CALLER)['error']['code']=='unknown_op'


def test_exact_arguments(tmp_path):
    control=Control(Layout(tmp_path),FakeBackend())
    assert control.handle(request('status',{'x':1}),CALLER)['error']['code']=='bad_args'
    assert control.handle(request('unit.restart'),CALLER)['error']['code']=='bad_args'
    assert control.handle(request('unit.restart',{'unit':'ingest','force':True}),CALLER)['error']['code']=='bad_args'
    assert control.handle(request('unit.restart',{'unit':'sync'}),CALLER)['error']==dict(code='bad_args',message='unknown_unit')
    assert control.handle(request('unit.restart',{'unit':['ingest']}),CALLER)['error']['code']=='bad_args'


def test_restart_audits_request_and_outcome(tmp_path):
    backend=FakeBackend(); control=Control(Layout(tmp_path),backend)
    assert control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)['result']==dict(unit='ingest',action='restart')
    assert backend.calls==[('restart','ingest')]
    rows=audit_rows(tmp_path)
    assert [row['outcome'] for row in rows]==['requested','ok']
    assert rows[0]['op']=='unit.restart' and rows[0]['args']=={'unit':'ingest'}
    assert rows[0]['caller_pid']==7 and rows[0]['caller_exe']=='/usr/bin/agent'


@pytest.mark.skipif(os.name=='nt',reason='symlink refusal')
def test_audit_failure_fails_closed(tmp_path):
    layout=Layout(tmp_path).ensure()
    (tmp_path/'elsewhere').write_bytes(b''); layout.audit.symlink_to(tmp_path/'elsewhere')
    backend=FakeBackend(); control=Control(layout,backend)
    response=control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)
    assert response['error']==dict(code='failed',message='audit_unavailable') and backend.calls==[]
    assert control.handle(request('maintenance.on',{'reason':'x'}),CALLER)['error']['message']=='audit_unavailable'
    assert not layout.maintenance.exists()
    assert control.handle(request('status'),CALLER)['ok']
    assert (tmp_path/'elsewhere').read_bytes()==b''


def test_rate_limit_per_unit_per_hour(tmp_path):
    now=[0.0]; backend=FakeBackend(); control=Control(Layout(tmp_path),backend,clock=lambda:now[0])
    for _ in range(3): assert control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)['ok']
    assert control.handle(request('unit.start',{'unit':'ingest'}),CALLER)['error']['code']=='rate_limited'
    assert control.handle(request('unit.restart',{'unit':'summary'}),CALLER)['ok']
    now[0]=3599.0
    assert control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)['error']['code']=='rate_limited'
    now[0]=3600.0
    assert control.handle(request('unit.start',{'unit':'ingest'}),CALLER)['ok']
    assert audit_rows(tmp_path)[-3]['outcome']=='rate_limited'


def test_maintenance_blocks_wrapped_starts(tmp_path):
    backend=FakeBackend(); control=Control(Layout(tmp_path),backend)
    assert control.handle(request('maintenance.on',{'reason':'upgrade'}),CALLER)['result']['on'] is True
    for op,unit in (('unit.start','summary'),('unit.restart','ingest'),('unit.start','retention')):
        assert control.handle(request(op,{'unit':unit}),CALLER)['error']['code']=='maintenance'
    assert control.handle(request('unit.restart',{'unit':'broker'}),CALLER)['ok']
    assert control.handle(request('maintenance.off'),CALLER)['result']['on'] is False
    assert control.handle(request('unit.start',{'unit':'summary'}),CALLER)['ok']
    assert backend.calls==[('restart','broker'),('start','summary')]


def test_maintenance_reason_validated(tmp_path):
    assert Control(Layout(tmp_path),FakeBackend()).handle(request('maintenance.on',{'reason':''}),CALLER)['error']['code']=='bad_args'


def test_stop_only_jobs(tmp_path):
    backend=FakeBackend(); control=Control(Layout(tmp_path),backend)
    assert control.handle(request('unit.stop',{'unit':'broker'}),CALLER)['error']==dict(code='bad_args',message='service_keepalive')
    assert control.handle(request('unit.stop',{'unit':'ingest'}),CALLER)['ok'] and backend.calls==[('stop','ingest')]


def test_backend_failure_audited(tmp_path):
    control=Control(Layout(tmp_path),FakeBackend(fail=True))
    assert control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)['error']==dict(code='failed',message='')
    assert [row['outcome'] for row in audit_rows(tmp_path)]==['requested','failed']


def test_audit_tail(tmp_path):
    control=Control(Layout(tmp_path),FakeBackend())
    assert control.handle(request('audit.tail',{'limit':5}),CALLER)['result']==[]
    control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)
    rows=control.handle(request('audit.tail',{'limit':1}),CALLER)['result']
    assert len(rows)==1 and rows[0]['outcome']=='ok'
    for bad in (0,201,True,'5'):
        assert control.handle(request('audit.tail',{'limit':bad}),CALLER)['error']['code']=='bad_args'


def test_units_and_diagnose(tmp_path):
    control=Control(Layout(tmp_path),FakeBackend())
    units=control.handle(request('units'),CALLER)['result']
    assert [u['name'] for u in units][:2]==['ingest','summary'] and units[0]['wrapped'] is True
    report=control.handle(request('diagnose'),CALLER)['result']
    assert set(report)=={'status','units','python','platform'}
