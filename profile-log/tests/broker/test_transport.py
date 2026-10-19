import os
import tempfile
import shutil
import pytest
from agentmesh_log.broker.transport import UnixServer, UnixClient, PipeServer, PipeClient


def test_real_transport():
    root = tempfile.mkdtemp(prefix='am', dir=None if os.name == 'nt' else '/tmp')
    address = (r'\\.\pipe\agentmesh-test-' + str(os.getpid())) if os.name == 'nt' else root + '/s'
    server = None
    try:
        try: server = (PipeServer if os.name == 'nt' else UnixServer)(address)
        except PermissionError: pytest.fail('IPC acceptance blocked: sandbox denies socket binding', pytrace=False)
        client = (PipeClient if os.name == 'nt' else UnixClient)(address)
        conn = server.accept(); client.send({'hello': 1})
        assert conn.receive() == {'hello': 1}
        conn.close(); client.close()
    finally:
        if server: server.close()
        shutil.rmtree(root)


def test_foreign_uid():
    if os.name == 'nt': return
    class Peer:
        closed = False
        def close(self): self.closed = True
        def settimeout(self, value): pass
    peer = Peer()
    server = UnixServer.__new__(UnixServer); server.peer_uid = lambda sock: os.getuid()+1
    class Listener:
        def accept(self): return peer, None
    server.sock=Listener()
    with pytest.raises(ValueError, match='unauthenticated'): server.accept()
    assert peer.closed


def test_pipe_bytes():
    from agentmesh_log.broker.transport import PipeConnection
    from agentmesh_log.broker.framing import encode
    class Pipe:
        data=None
        def send_bytes(self,data): self.data=data
        def recv_bytes(self,limit):
            assert limit==1048580
            return self.data
        def poll(self,timeout):
            assert timeout==30
            return True
        def close(self): pass
        def send(self,obj): raise AssertionError('pickle forbidden')
        def recv(self): raise AssertionError('pickle forbidden')
    pipe=Pipe(); conn=PipeConnection(pipe)
    conn.send({'fixture':1}); assert pipe.data==encode({'fixture':1})
    assert conn.receive()=={'fixture':1}
