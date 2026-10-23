"""Durable request accounting without plaintext queries or payload metadata."""
import hashlib
import hmac
import os
import stat
import threading
import time
from pathlib import Path
from ..encoding import wire
from ..storage.filesystem import _check


def append_row(path, row):
    """Append one canonical JSON line to an owner-only regular file (never through a symlink) and fsync it."""
    path=Path(path); data=wire(row)+b'\n'
    _check(path)
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'ab') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode): raise ValueError('unsafe audit file')
        if os.name!='nt' and (info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600):
            raise ValueError('unsafe audit file')
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


class Audit:
    def __init__(self, path, key):
        self.path, self.key, self.lock = path, key, threading.Lock()

    def append(self, *, agent_id, op, outcome, profiles=None, scopes=None, cross_profile=False,
               query=None, result_ids=None, redactions=None, label=None):
        query = query if type(query) is str else ''
        row = dict(ts=time.time_ns()//1000000,agent_id=agent_id,op=op,outcome=outcome,
            profiles=profiles or [],scopes=scopes or [],cross_profile=cross_profile,
            query_hmac=hmac.new(self.key,query.encode('utf-8'),hashlib.sha256).hexdigest(),
            query_chars=len(query),result_count=len(result_ids or []),result_ids=result_ids or [],
            redactions=redactions or {},label=label)
        with self.lock: append_row(self.path,row)
