import struct
import pytest
from agentmesh_log.broker.framing import encode, decode, receive


def test_frames():
    assert decode(encode({'x': 1})) == {'x': 1}
    for raw in (b'\0'*4, struct.pack('!I', 1048577), struct.pack('!I', 1)+b'{',
                struct.pack('!I', 13)+b'{"x":1,"x":2}'):
        with pytest.raises(ValueError): decode(raw)
    called = []
    def read(n):
        called.append(n); return struct.pack('!I', 1048577)
    with pytest.raises(ValueError): receive(read)
    assert called == [4]
