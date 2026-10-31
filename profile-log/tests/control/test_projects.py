import json
import os
import stat
import pytest
from agentmesh_log.control import projects
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.moves import Moves, MoveError
from agentmesh_log.control.ops import Control
from agentmesh_log.control.projects import Mappings
from agentmesh_log.control.store import Store
from agentmesh_log.encoding import wire
from .support import FakeBackend

MAPPING=dict(format='agentmesh-migration-mapping-v1',null_project='work/common',prefixes=[{'prefix':'AgentMesh','target':'work/common'}],
    projects={'home':'personal/common'},**{'global':'work/common'})


class Sheet:
    def __init__(self, answer=True, available=True): self.answer,self._available,self.seen=answer,available,[]
    def available(self): return self._available
    def confirm(self, action, summary): self.seen.append((action,summary)); return self.answer


def setup(tmp_path, unmapped=('learn-x',), sheet=None, clock=None):
    layout=Layout(tmp_path).ensure()
    layout.mapping.write_bytes(wire(MAPPING)); layout.mapping.chmod(0o600)
    layout.profile_status.write_text(json.dumps(dict(last_catch_up=dict(unmapped=list(unmapped)))))
    sheet=sheet or Sheet()
    moves=Moves(layout,Store(layout.state),sheet,**({'clock':clock} if clock else {}))
    return layout,sheet,moves,Mappings(layout,sheet,moves)


def mapped(layout): return json.loads(layout.mapping.read_bytes())['projects']


def test_pending_and_scopes(tmp_path):
    layout,*_=setup(tmp_path,unmapped=('learn-x','home','AgentMesh-tools','',7))
    assert projects.pending(layout)==['learn-x']  # covered by a project or a prefix, or not a name: not pending
    assert projects.scopes(layout)==['personal/common','work/common','skip']


def test_unreadable_state_means_none(tmp_path):
    layout,*_=setup(tmp_path); layout.profile_status.write_text('{')
    assert projects.pending(layout)==[]
    layout.mapping.unlink(); assert projects.scopes(layout)==[]


def test_set_approved(tmp_path):
    layout,sheet,moves,mappings=setup(tmp_path)
    assert mappings.set('learn-x','personal/common')=={'project':'learn-x','target':'personal/common'}
    assert mapped(layout)=={'home':'personal/common','learn-x':'personal/common'}
    assert sheet.seen==[('migrate','Map new project learn-x to personal/common. Agents that may read personal/common will see its memory.')]
    assert projects.pending(layout)==[] and moves.active is None
    if os.name!='nt': assert stat.S_IMODE(layout.mapping.stat().st_mode)==0o600


def test_skip(tmp_path):
    layout,sheet,moves,mappings=setup(tmp_path)
    mappings.set('learn-x','skip')
    assert mapped(layout)['learn-x']=='skip' and 'stays out of the profile log' in sheet.seen[0][1]


def test_denied_writes_nothing(tmp_path):
    layout,sheet,moves,mappings=setup(tmp_path,sheet=Sheet(answer=False))
    before=layout.mapping.read_bytes()
    with pytest.raises(MoveError) as error: mappings.set('learn-x','work/common')
    assert error.value.code=='presence_required' and layout.mapping.read_bytes()==before and moves.active is None


@pytest.mark.parametrize('project', ['home','AgentMesh-x','never-seen'], ids=['mapped','prefix','unknown'])
def test_only_pending_projects(tmp_path, project):
    # mapping.set adds, it never re-routes: an existing project cannot be moved to a wider scope.
    layout,sheet,moves,mappings=setup(tmp_path,unmapped=('learn-x','home','AgentMesh-x'))
    with pytest.raises(MoveError) as error: mappings.set(project,'work/common')
    assert error.value.code=='not_pending' and sheet.seen==[] and mapped(layout)=={'home':'personal/common'}


@pytest.mark.parametrize('target', ['work/secret','work','',None], ids=['new','partial','empty','none'])
def test_only_known_scopes(tmp_path, target):
    layout,sheet,moves,mappings=setup(tmp_path)
    with pytest.raises(MoveError) as error: mappings.set('learn-x',target)
    assert error.value.code=='bad_args' and sheet.seen==[]


def test_unavailable(tmp_path):
    layout,sheet,moves,mappings=setup(tmp_path,sheet=Sheet(available=False))
    with pytest.raises(MoveError) as error: mappings.set('learn-x','personal/common')
    assert error.value.code=='presence_unavailable' and sheet.seen==[] and moves.active is None


def test_busy_during_move(tmp_path):
    layout,sheet,moves,mappings=setup(tmp_path)
    moves.active='some-move'
    with pytest.raises(MoveError) as error: mappings.set('learn-x','personal/common')
    assert error.value.code=='busy' and sheet.seen==[] and moves.active=='some-move'


def test_shares_rate_limit(tmp_path):
    now=[1000.0]
    layout,sheet,moves,mappings=setup(tmp_path,sheet=Sheet(answer=False),clock=lambda:now[0])
    for _ in range(3):
        with pytest.raises(MoveError): mappings.set('learn-x','personal/common')
    with pytest.raises(MoveError) as error: moves.request('ingest','native')
    assert error.value.code=='rate_limited' and len(sheet.seen)==3


def test_op_audited(tmp_path):
    layout,sheet,moves,mappings=setup(tmp_path)
    control=Control(layout,FakeBackend(),moves=moves,mappings=mappings)
    request=dict(v=1,id='r',op='mapping.set',args={'project':'learn-x','target':'personal/common'})
    assert control.handle(request,dict(pid=7,exe='/app'))['result']=={'project':'learn-x','target':'personal/common'}
    rows=[json.loads(line) for line in layout.audit.read_bytes().splitlines()]
    assert [r['outcome'] for r in rows]==['requested','ok'] and rows[0]['caller_exe']=='/app'
    status=control.handle(dict(v=1,id='s',op='status',args={}),{})['result']['projects']
    assert status=={'pending':[],'scopes':['personal/common','work/common','skip']}
    assert Control(layout,FakeBackend()).handle(request,{})['error']['code']=='presence_unavailable'
