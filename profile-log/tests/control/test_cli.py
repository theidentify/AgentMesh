import os
import plistlib
import sys
import pytest
from agentmesh_log.cli import parser
from agentmesh_log.control.cli import execute
from agentmesh_log.control.layout import Layout


def fake(response, seen=None):
    def call(layout, op, args):
        if seen is not None: seen.append((op,args))
        return response
    return call


def test_calls_socket_with_exact_op(tmp_path):
    seen=[]
    args=parser().parse_args(['control','restart','ingest'])
    result=execute(args,layout=Layout(tmp_path),call=fake(dict(id='x',ok=True,result={'unit':'ingest'}),seen))
    assert result=={'unit':'ingest'} and seen==[('unit.restart',{'unit':'ingest'})]


def test_maintenance_and_error_mapping(tmp_path):
    seen=[]
    args=parser().parse_args(['control','maintenance','on','--reason','upgrade'])
    result=execute(args,layout=Layout(tmp_path),call=fake(dict(id='x',ok=False,error=dict(code='maintenance',message='')),seen))
    assert result=={'error':'maintenance'} and seen==[('maintenance.on',{'reason':'upgrade'})]


def test_list_results_are_wrapped(tmp_path):
    args=parser().parse_args(['control','audit','--limit','3'])
    assert execute(args,layout=Layout(tmp_path),call=fake(dict(id='x',ok=True,result=[{'op':'a'}])))=={'items':[{'op':'a'}]}


@pytest.mark.skipif(os.name=='nt',reason='Unix socket client')
def test_unreachable_supervisor(tmp_path):
    args=parser().parse_args(['control','status'])
    assert execute(args,layout=Layout(tmp_path))=={'error':'supervisor_unavailable'}


def test_serve_refuses_other_platforms(tmp_path, monkeypatch):
    monkeypatch.setattr(sys,'platform','linux')
    assert execute(parser().parse_args(['control','serve']),layout=Layout(tmp_path))=={'error':'unsupported_platform'}


def test_plist_written_once(tmp_path):
    out=tmp_path/'org.agentmesh.control.plist'
    args=parser().parse_args(['control','plist','--out',str(out),'--python','/venv/bin/python'])
    assert execute(args,layout=Layout(tmp_path))=={'ok':True,'path':str(out)}
    assert plistlib.loads(out.read_bytes())['Label']=='org.agentmesh.control'
    assert execute(args,layout=Layout(tmp_path))=={'error':'exists'}


from .support import FakeLaunchd, Presence, ok_call, write_plists


def test_reset_over_socket(tmp_path):
    seen=[]
    result=execute(parser().parse_args(['control','reset','ingest']),layout=Layout(tmp_path),
        call=fake(dict(id='x',ok=True,result={'unit':'ingest','cleared':True}),seen))
    assert result=={'unit':'ingest','cleared':True} and seen==[('unit.reset',{'unit':'ingest'})]


def test_adopt_release_panic_locally(tmp_path):
    agents=tmp_path/'agents'; agents.mkdir(); write_plists(agents); launchd=FakeLaunchd(); layout=Layout(tmp_path)
    run=lambda *argv,answer=True: execute(parser().parse_args(['control',*argv]),layout=layout,call=ok_call,
        console=Presence(answer),launchd=launchd,agents=agents)
    assert run('adopt','ingest')=={'ok':True,'unit':'ingest','backend':'native'}
    assert run('release','ingest',answer=False)=={'error':'presence_required'}
    assert run('panic-revert')=={'ok':True,'reverted':['ingest'],'stopped':[],'errors':[]}
    assert run('release','ingest')=={'error':'not_native'}
