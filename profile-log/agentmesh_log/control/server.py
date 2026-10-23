"""The supervisor: one owner-only control socket, one thread and one request per connection, and a ticker
thread (the native backend's once-a-second tick). close() is idempotent and safe from a SIGTERM handler."""
import json
import sys
import threading
from ..broker.transport import UnixServer, UnixClient
from .peer import peer_pid, executable


class Supervisor:
    def __init__(self, control, layout, server_factory=UnixServer, *, ticker=None, on_close=None, tick_s=1.0):
        self.control,self.layout,self.server_factory=control,layout,server_factory
        self.ticker,self.on_close,self.tick_s=ticker,on_close,tick_s
        self.stopped=threading.Event(); self.server=None; self.tick_thread=None
        self.closed=False; self.close_lock=threading.Lock()

    def serve(self):
        self.layout.ensure()
        self.server=self.server_factory(str(self.layout.socket))
        if self.ticker is not None:
            self.tick_thread=threading.Thread(target=self.tick_loop,daemon=True); self.tick_thread.start()
        while not self.stopped.is_set():
            try: conn=self.server.accept()
            except ValueError: continue  # foreign peer UID; the transport already closed it
            except OSError:
                if self.stopped.is_set(): break
                raise
            threading.Thread(target=self.connection,args=(conn,),daemon=True).start()

    def tick_loop(self):
        while not self.stopped.wait(self.tick_s):
            try: self.ticker()
            except Exception as exc: print(json.dumps({'tick_error':type(exc).__name__}),file=sys.stderr,flush=True)

    def connection(self, conn):
        try:
            sock=getattr(conn,'sock',None); pid=peer_pid(sock) if sock is not None else None
            request=conn.receive()
            conn.send(self.control.handle(request,dict(pid=pid,exe=executable(pid))))
        except (OSError,ValueError,EOFError): pass
        finally: conn.close()

    def close(self):
        with self.close_lock:
            if self.closed: return
            self.closed=True
        self.stopped.set()
        if self.tick_thread is not None and self.tick_thread is not threading.current_thread(): self.tick_thread.join(30)
        if self.on_close is not None: self.on_close()
        if self.server:
            # Closing a listening socket does not wake a thread blocked in accept() on macOS or Linux.
            try: UnixClient(str(self.layout.socket)).close()
            except OSError: pass
            self.server.close()
