"""A minimal client for the control socket: one request per connection."""
import uuid
from ..broker.transport import UnixClient


def call(layout, op, args, client_factory=UnixClient):
    conn=client_factory(str(layout.socket))
    try:
        conn.send(dict(v=1,id=uuid.uuid4().hex,op=op,args=args))
        return conn.receive()
    finally: conn.close()
