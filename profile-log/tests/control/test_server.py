import json
import os
import shutil
import stat
import tempfile
import threading
import time
import pytest
from agentmesh_log.control.client import call
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.ops import Control
from agentmesh_log.control.server import Supervisor
from .support import FakeBackend


def call_when_ready(layout, op, args):
    for _ in range(100):
        try: return call(layout,op,args)
        except (FileNotFoundError,ConnectionRefusedError): time.sleep(0.02)
    raise AssertionError('supervisor did not start')


@pytest.mark.skipif(os.name=='nt',reason='Unix socket')
def test_supervisor_round_trip():
    root=tempfile.mkdtemp(prefix='amc',dir='/tmp'); layout=Layout(root); backend=FakeBackend()
    supervisor=Supervisor(Control(layout,backend),layout)
    thread=threading.Thread(target=supervisor.serve,daemon=True); thread.start()
    try:
        response=call_when_ready(layout,'unit.restart',{'unit':'ingest'})
        assert response['ok'] and backend.calls==[('restart','ingest')]
        assert call(layout,'status',{})['ok']
        row=json.loads(layout.audit.read_bytes().splitlines()[0])
        assert row['caller_pid']==os.getpid()
        assert stat.S_IMODE(layout.socket.stat().st_mode)==0o600
    finally:
        supervisor.close(); thread.join(5); shutil.rmtree(root)
    assert not thread.is_alive()
