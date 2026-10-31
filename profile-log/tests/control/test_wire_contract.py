"""The status wire shape the menu bar app decodes (control-app reads the same fixture)."""
import json
from pathlib import Path
from agentmesh_log.control.layout import Layout, write_json
from agentmesh_log.control.moves import Moves
from agentmesh_log.control.ops import Control
from agentmesh_log.control.store import Store
from .support import FakeBackend

FIXTURE=Path(__file__).parent/'fixtures'/'status.json'


def live(tmp_path):
    layout=Layout(tmp_path).ensure(); layout.status.mkdir(exist_ok=True)
    write_json(layout.status/'job-ingest.json',dict(ok=True,at='2026-10-29T05:00:00+00:00',since='2026-10-29T05:00:00+00:00'))
    moves=Moves(layout,Store(layout.state),None)
    moves.moves['m']=dict(move_id='m',unit='catch-up',to='launchd',state='moving',started_at=0,ended_at=None); moves.active='m'
    return Control(layout,FakeBackend(),moves=moves).handle(dict(v=1,id='fixture',op='status',args={}),{})


def first(rows, key): return next(r[key] for r in rows if r[key] is not None)


def test_status_shape(tmp_path):
    want,got=json.loads(FIXTURE.read_text()),live(tmp_path)
    assert set(got)==set(want) and set(got['result'])==set(want['result'])
    for section in ('maintenance','quota','projects'): assert set(got['result'][section])==set(want['result'][section])
    rows,fixture_rows=got['result']['units'],want['result']['units']
    assert [r['name'] for r in rows]==[r['name'] for r in fixture_rows]
    assert all(set(r)==set(fixture_rows[0]) for r in rows+fixture_rows)
    for nested in ('job','move'): assert set(first(rows,nested))==set(first(fixture_rows,nested))
