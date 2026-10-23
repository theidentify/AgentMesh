"""The supervisor: one owner-only control socket, one thread and one request per connection."""
import threading
from ..broker.transport import UnixServer, UnixClient
from .peer import peer_pid, executable


class Supervisor:
    def __init__(self, control, layout, server_factory=UnixServer):
        self.control,self.layout,self.server_factory=control,layout,server_factory
        self.stopped=threading.Event(); self.server=None

    def serve(self):
        self.layout.ensure()
        self.server=self.server_factory(str(self.layout.socket))
        while not self.stopped.is_set():
            try: conn=self.server.accept()
            except ValueError: continue  # foreign peer UID; the transport already closed it
            except OSError:
                if self.stopped.is_set(): break
                raise
            threading.Thread(target=self.connection,args=(conn,),daemon=True).start()

    def connection(self, conn):
        try:
            sock=getattr(conn,'sock',None); pid=peer_pid(sock) if sock is not None else None
            request=conn.receive()
            conn.send(self.control.handle(request,dict(pid=pid,exe=executable(pid))))
        except (OSError,ValueError,EOFError): pass
        finally: conn.close()

    def close(self):
        self.stopped.set()
        if self.server:
            # Closing a listening socket does not wake a thread blocked in accept() on macOS or Linux.
            try: UnixClient(str(self.layout.socket)).close()
            except OSError: pass
            self.server.close()
