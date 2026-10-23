import os
import socket
import pytest
from agentmesh_log.control.peer import peer_pid, executable


@pytest.mark.skipif(os.name=='nt',reason='AF_UNIX peer credentials')
def test_peer_pid_of_socketpair():
    a,b=socket.socketpair(socket.AF_UNIX)
    try: assert peer_pid(a)==os.getpid()
    finally: a.close(); b.close()


def test_executable_uses_ps():
    class Done: returncode=0; stdout='/usr/bin/python3\n'
    seen=[]
    assert executable(42,runner=lambda argv,**kw:(seen.append(argv) or Done()))=='/usr/bin/python3'
    assert seen==[['/bin/ps','-o','comm=','-p','42']]
    assert executable(None) is None


def test_executable_failure_is_none():
    class Gone: returncode=1; stdout=''
    assert executable(42,runner=lambda argv,**kw:Gone()) is None
