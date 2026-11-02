"""A minimal client for the control socket: one request per connection."""
import uuid
from ..broker.transport import UnixClient


# Ops that wait for the supervisor's presence sheet (75 s) and then work; the default socket timeout is 30 s.
SLOW={'memory.export':900}


def call(layout, op, args, client_factory=UnixClient):
    conn=client_factory(str(layout.socket))
    if op in SLOW and getattr(conn,'sock',None) is not None: conn.sock.settimeout(SLOW[op])
    try:
        conn.send(dict(v=1,id=uuid.uuid4().hex,op=op,args=args))
        return conn.receive()
    finally: conn.close()
