"""Local IPC. Windows uses byte messages only, never pickle or authkey."""
import ctypes
import os
import socket
import stat
import struct
from pathlib import Path
from . import framing


def peer_uid(sock):
    if hasattr(socket, 'SO_PEERCRED'):
        return struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.getpeereid(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise ValueError('unauthenticated')
    return uid.value


class UnixConnection:
    def __init__(self, sock):
        self.sock = sock; sock.settimeout(30)

    def send(self, obj): self.sock.sendall(framing.encode(obj))
    def receive(self): return framing.receive(self.sock.recv)
    def close(self): self.sock.close()


class UnixClient(UnixConnection):
    def __init__(self, address):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try: sock.settimeout(30); sock.connect(address)
        except Exception: sock.close(); raise
        super().__init__(sock)


class UnixServer:
    def __init__(self, address, peer_uid=peer_uid):
        self.address, self.peer_uid = address, peer_uid
        path = Path(address)
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError('unsafe socket path')
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(address)
            except ConnectionRefusedError: path.unlink()
            else: raise ValueError('broker already running')
            finally: probe.close()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.bind(address); os.chmod(address, 0o600); self.sock.listen(32)
        except Exception: self.sock.close(); raise

    def check_peer(self, sock):
        try:
            if self.peer_uid(sock) != os.getuid(): raise ValueError('unauthenticated')
        except Exception:
            sock.close(); raise ValueError('unauthenticated') from None

    def accept(self):
        sock, _ = self.sock.accept(); self.check_peer(sock)
        return UnixConnection(sock)

    def close(self):
        self.sock.close()
        try:
            info = os.lstat(self.address)
            if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid(): os.unlink(self.address)
        except FileNotFoundError: pass


class PipeConnection:
    def __init__(self, connection): self.connection = connection
    def send(self, obj): self.connection.send_bytes(framing.encode(obj))
    def receive(self):
        if not self.connection.poll(30): raise TimeoutError('connection timeout')
        return framing.decode(self.connection.recv_bytes(1048580))
    def close(self): self.connection.close()


class PipeClient(PipeConnection):
    def __init__(self, address):
        from multiprocessing.connection import Client
        super().__init__(Client(address, family='AF_PIPE', authkey=None))


class PipeServer:
    def __init__(self, address):
        from multiprocessing.connection import Listener
        self.listener = Listener(address, family='AF_PIPE', authkey=None)
    def accept(self): return PipeConnection(self.listener.accept())
    def close(self): self.listener.close()
