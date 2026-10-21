"""Default paths in tests always point to disposable, absent fixture sources."""
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

_stack=[]

def setUpModule():
    temp=tempfile.TemporaryDirectory(); root=Path(temp.name)
    env=patch.dict(os.environ,{'AGENTMESH_HOME':str(root/'agentmesh'),'INSIGHT_SYNCTHING_CONFIG':str(root/'config.xml')})
    env.start(); _stack.append((temp,env))


def tearDownModule():
    temp,env=_stack.pop(); env.stop(); temp.cleanup()
