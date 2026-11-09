"""The archives the SQLite retirement left behind (backups/archive/sqlite-<node>-<stamp>/).

R4 moved each device's staging database there; R5b removed the code that read it, so there is nothing left to roll back
to. `agentmesh-log control sqlite-archive list` shows them and `delete` removes one for good: owner-run in a terminal,
never over the socket, with typed console presence plus Touch ID (or the Mac password) through the supervisor's helper
on a Mac.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import time
from ..broker.audit import append_row


def now(): return datetime.now(timezone.utc)


def audit(layout, op, args, outcome):
    layout.ensure()
    append_row(layout.audit,dict(ts=time.time_ns()//1000000,op=op,args=args,outcome=outcome,caller_pid=os.getpid(),caller_exe=sys.executable))


def read(path):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value,dict): raise ValueError('invalid manifest')
    return value


def size(path):
    path=Path(path)
    if path.is_file(): return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink())


def present(console, helper, action, summary):
    """Tier 3: typed console confirmation, then the supervisor's helper (Touch ID or the Mac password) where there is one."""
    if not console.confirm(action,summary): return False
    return helper is None or helper.confirm(action,summary)


class Abort(Exception): pass


def archives(root):
    base=Path(root)/'backups'/'archive'
    rows=[]
    for path in sorted(base.glob('sqlite-*')) if base.is_dir() else []:
        try: manifest=read(path/'manifest.json')
        except (OSError,ValueError): continue
        created=datetime.fromisoformat(manifest['created_at'])
        rows.append(dict(archive=path.name,created_at=manifest['created_at'],age_days=(now()-created).days,bytes=size(path)))
    return rows


def find(root, name):
    path=Path(root)/'backups'/'archive'/str(name)
    if '/' in str(name) or '\\' in str(name) or not str(name).startswith('sqlite-') or not (path/'manifest.json').is_file():
        raise Abort('archive not found')
    return path,read(path/'manifest.json')


def delete(layout, root, name, *, console, helper):
    try: archive,manifest=find(root,name)
    except Abort as exc: return dict(error=str(exc))
    age=(now()-datetime.fromisoformat(manifest['created_at'])).days
    summary='Delete %s for good (%d days old). It is the last copy of the SQLite staging database and its export.'%(archive.name,age)
    if not present(console,helper,'delete',summary):
        audit(layout,'sqlite.archive.delete',{'archive':archive.name},'presence_denied'); return dict(error='presence_required')
    for path in sorted(archive.rglob('*'),reverse=True):
        os.chmod(path,0o700 if path.is_dir() else 0o600)
    os.chmod(archive,0o700); shutil.rmtree(archive)
    audit(layout,'sqlite.archive.delete',{'archive':archive.name},'ok')
    return dict(ok=True,deleted=archive.name)
