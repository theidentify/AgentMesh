import functools
import json
import threading
import pytest
from agentmesh_log.control import migrate
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.moves import Moves, MoveError
from agentmesh_log.control.ops import Control
from agentmesh_log.control.store import Store
from .support import FakeBackend, FakeLaunchd, write_plists

NOSLEEP=lambda seconds:None
CALLER=dict(pid=7,exe='/usr/bin/agent')


class Sheet:
    """The supervisor's presence: available(), then a verdict per confirm()."""
    def __init__(self, answer=True, available=True, block=None):
        self.answer,self._available,self.block,self.seen=answer,available,block,[]
    def available(self): return self._available
    def confirm(self, action, summary):
        self.seen.append((action,summary))
        if self.block is not None: self.block.wait(5)
        return self.answer


class Inline:
    """A thread that runs its target at start(), so tests are deterministic."""
    def __init__(self, target, args, daemon): self.target,self.args=target,args
    def start(self): self.target(*self.args)


def setup(tmp_path, sheet=None, thread=Inline, busy=None, clock=None, **kw):
    layout=Layout(tmp_path).ensure(); agents=tmp_path/'agents'; agents.mkdir(); write_plists(agents)
    store,launchd=Store(layout.state),FakeLaunchd(busy=busy)
    sheet=sheet or Sheet()
    moves=Moves(layout,store,sheet,launchd=launchd,agents=agents,thread=thread,
        adopt=functools.partial(migrate.adopt,sleep=NOSLEEP),release=functools.partial(migrate.release,sleep=NOSLEEP),
        **({'clock':clock} if clock else {}),**kw)
    return layout,store,launchd,sheet,moves


def outcomes(layout, op):
    return [r['outcome'] for r in map(json.loads,layout.audit.read_bytes().splitlines()) if r['op']==op]


def test_adopt_approved(tmp_path):
    layout,store,launchd,sheet,moves=setup(tmp_path)
    result=moves.request('ingest','native')
    assert result['accepted'] and store.backend('ingest')=='native'
    assert ('disable','ingest') in launchd.calls and ('bootout','ingest') in launchd.calls
    assert sheet.seen[0][0]=='migrate' and 'Undo: agentmesh-log control release ingest' in sheet.seen[0][1]
    assert moves.get(result['move_id'])=={'move_id':result['move_id'],'unit':'ingest','to':'native','state':'ok'}
    assert outcomes(layout,'unit.adopt')==['requested','ok'] and moves.current('ingest') is None


def test_release_approved(tmp_path):
    layout,store,launchd,sheet,moves=setup(tmp_path)
    moves.request('ingest','native'); result=moves.request('ingest','launchd')
    assert result['accepted'] and store.backend('ingest')=='launchd' and ('enable','ingest') in launchd.calls


def test_denied(tmp_path):
    layout,store,launchd,sheet,moves=setup(tmp_path,Sheet(answer=False))
    with pytest.raises(MoveError) as error: moves.request('ingest','native')
    assert error.value.code=='presence_required' and store.backend('ingest')=='launchd'
    assert ('bootout','ingest') not in launchd.calls and outcomes(layout,'unit.adopt')==['presence_denied']


def test_unavailable_never_prompts(tmp_path):
    layout,store,launchd,sheet,moves=setup(tmp_path,Sheet(available=False))
    with pytest.raises(MoveError) as error: moves.request('ingest','native')
    assert error.value.code=='presence_unavailable' and sheet.seen==[] and store.backend('ingest')=='launchd'


@pytest.mark.parametrize('name,to', [('broker','native'),('nope','native'),('ingest','elsewhere')],
    ids=['service','unknown','bad-to'])
def test_bad_args(tmp_path, name, to):
    layout,store,launchd,sheet,moves=setup(tmp_path)
    with pytest.raises(MoveError) as error: moves.request(name,to)
    assert error.value.code=='bad_args' and sheet.seen==[]


def test_early_error_reported(tmp_path):
    # summary has no plist in the fixture: adopt fails before it prompts.
    layout,store,launchd,sheet,moves=setup(tmp_path)
    with pytest.raises(MoveError) as error: moves.request('summary','native')
    assert error.value.code=='unsupported_plist' and sheet.seen==[]
    with pytest.raises(MoveError) as error: moves.request('ingest','launchd')
    assert error.value.code=='not_native' and sheet.seen==[]


def test_busy(tmp_path):
    gate=threading.Event(); sheet=Sheet(block=gate)
    layout,store,launchd,sheet,moves=setup(tmp_path,sheet,thread=threading.Thread)
    first={}
    worker=threading.Thread(target=lambda:first.update(moves.request('ingest','native'))); worker.start()
    for _ in range(100):
        if sheet.seen: break
        threading.Event().wait(0.01)
    assert moves.current('ingest')['state']=='deciding'
    with pytest.raises(MoveError) as error: moves.request('retention','native')
    assert error.value.code=='busy'
    gate.set(); worker.join(5)
    assert first['accepted'] and len(sheet.seen)==1


def test_rate_limit_counts_denials(tmp_path):
    now=[1000.0]
    layout,store,launchd,sheet,moves=setup(tmp_path,Sheet(answer=False),clock=lambda:now[0])
    for _ in range(3):
        with pytest.raises(MoveError) as error: moves.request('ingest','native')
        assert error.value.code=='presence_required'
    with pytest.raises(MoveError) as error: moves.request('ingest','native')
    assert error.value.code=='rate_limited' and len(sheet.seen)==3
    now[0]+=601; sheet.answer=True
    assert moves.request('ingest','native')['accepted']


def test_never_decides(tmp_path):
    gate=threading.Event()
    layout,store,launchd,sheet,moves=setup(tmp_path,Sheet(block=gate),thread=threading.Thread,decide_s=0.2)
    with pytest.raises(MoveError) as error: moves.request('ingest','native')
    assert error.value.code=='presence_required'
    gate.set()


def test_waits_for_busy_launchd(tmp_path):
    layout,store,launchd,sheet,moves=setup(tmp_path,busy={'ingest':2})
    result=moves.request('ingest','native')
    assert result['accepted'] and moves.get(result['move_id'])['state']=='ok' and store.backend('ingest')=='native'


def test_unknown_move(tmp_path):
    with pytest.raises(MoveError) as error: setup(tmp_path)[4].get('nope')
    assert error.value.code=='unknown_move'


def request(op, args): return dict(v=1,id='r1',op=op,args=args)


def test_op_audited_with_caller(tmp_path):
    layout,store,launchd,sheet,moves=setup(tmp_path)
    control=Control(layout,FakeBackend(),moves=moves)
    response=control.handle(request('unit.backend',{'unit':'ingest','to':'native'}),CALLER)
    assert response['ok'] and response['result']['accepted']
    rows=[r for r in map(json.loads,layout.audit.read_bytes().splitlines()) if r['op']=='unit.backend']
    assert [r['outcome'] for r in rows]==['requested','ok'] and rows[0]['caller_exe']=='/usr/bin/agent'
    got=control.handle(request('moves.get',{'move_id':response['result']['move_id']}),CALLER)
    assert got['result']['state']=='ok'


def test_op_errors(tmp_path):
    layout=Layout(tmp_path).ensure()
    control=Control(layout,FakeBackend())
    assert control.handle(request('unit.backend',{'unit':'ingest','to':'native'}),CALLER)['error']['code']=='presence_unavailable'
    assert control.handle(request('unit.backend',{'unit':'nope','to':'native'}),CALLER)['error']['code']=='presence_unavailable'
    assert control.handle(request('moves.get',{'move_id':'x'}),CALLER)['error']['code']=='unknown_move'
    moves=setup(tmp_path/'m',Sheet(answer=False))[4]
    control=Control(layout,FakeBackend(),moves=moves)
    assert control.handle(request('unit.backend',{'unit':'nope','to':'native'}),CALLER)['error']['code']=='bad_args'
    assert control.handle(request('unit.backend',{'unit':'ingest','to':'native'}),CALLER)['error']['code']=='presence_required'


def test_status_move_column(tmp_path):
    layout=Layout(tmp_path).ensure()
    rows=Control(layout,FakeBackend()).handle(request('status',{}),CALLER)['result']['units']
    assert all(row['move'] is None for row in rows)
