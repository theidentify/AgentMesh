"""Who is calling: peer pid and executable. Audit context only, never authority (same-user code can lie)."""
import socket
import struct
import subprocess
import sys

SOL_LOCAL,LOCAL_PEERPID=0,2  # <sys/un.h> on macOS


def peer_pid(sock):
    try:
        if hasattr(socket,'SO_PEERCRED'):
            return struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[0]
        if sys.platform=='darwin':
            return struct.unpack('i',sock.getsockopt(SOL_LOCAL,LOCAL_PEERPID,4))[0]
    except OSError: pass
    return None


def executable(pid, runner=subprocess.run):
    if pid is None: return None
    try: process=runner(['/bin/ps','-o','comm=','-p',str(pid)],capture_output=True,text=True,timeout=5)
    except (OSError,subprocess.SubprocessError): return None
    if process.returncode!=0: return None
    return process.stdout.strip()[:256] or None
