from agentmesh_log.control.health import assess, observe
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.units import UNITS, get
from .support import FakeBackend, IDLE


def test_assess():
    job,service=get('ingest'),get('broker')
    assert assess(job,dict(IDLE,loaded=False),None)=='missing'
    assert assess(job,dict(IDLE,running=True,pid=1,runtime_s=3600),None)=='running'
    assert assess(job,dict(IDLE,running=True,pid=1,runtime_s=3601),None)=='stuck'
    assert assess(job,dict(IDLE,running=True,pid=1,runtime_s=None),None)=='running'
    assert assess(service,dict(IDLE,running=True,pid=1,runtime_s=10**7),None)=='running'
    assert assess(service,IDLE,None)=='down'
    assert assess(job,IDLE,dict(ok=False,at='t',since='t'))=='failing'
    assert assess(get('catch-up'),dict(IDLE,last_exit=1),None)=='failing'
    assert assess(job,IDLE,dict(ok=True,at='t',since='t'))=='idle'


def test_observe_reads_job_state(tmp_path):
    layout=Layout(tmp_path); layout.status.mkdir()
    (layout.status/'job-summary.json').write_text('{"ok": false, "at": "a", "since": "s", "alerted": true}')
    rows={row['name']:row for row in observe(layout,FakeBackend(),UNITS)}
    assert len(rows)==8
    assert rows['summary']['health']=='failing' and rows['summary']['job']==dict(ok=False,at='a',since='s')
    assert rows['broker']['health']=='down' and rows['catch-up']['job'] is None


def test_failed_wins():
    job=get('ingest')
    assert assess(job,dict(IDLE,failed=True),None)=='failed'
    assert assess(job,dict(IDLE,running=True,pid=1,runtime_s=1,failed=True),None)=='failed'
    assert assess(job,dict(IDLE,loaded=False,failed=True),None)=='missing'


def test_conflict_wins():
    job=get('ingest')
    assert assess(job,dict(IDLE,conflict=True),None)=='conflict'
    assert assess(job,dict(IDLE,running=True,pid=1,runtime_s=1,failed=True,conflict=True),None)=='conflict'
