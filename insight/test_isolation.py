"""Default paths in tests always point to disposable, absent fixture sources."""
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

_stack=[]

def setUpModule():
    temp=tempfile.TemporaryDirectory(); root=Path(temp.name)
    (root/'agentmesh').mkdir()
    (root/'agentmesh/packet-sync-status.json').write_text('{"format":"agentmesh-packet-sync-status-v1","packet_sync":"enabled"}',encoding='utf-8')
    env=patch.dict(os.environ,{'AGENTMESH_HOME':str(root/'agentmesh'),'INSIGHT_SYNCTHING_CONFIG':str(root/'config.xml')})
    env.start(); _stack.append((temp,env))


def tearDownModule():
    temp,env=_stack.pop(); env.stop(); temp.cleanup()
