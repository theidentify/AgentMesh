"""Observe-only health from launchctl and job state files. M1 reports; it never restarts anything."""
from .layout import read_json


def job_state(layout, unit):
    if not unit.wrapped: return None
    value=read_json(layout.status/('job-%s.json'%unit.name))
    if value is None: return None
    return dict(ok=value['ok'] if type(value.get('ok')) is bool else None,at=value.get('at'),since=value.get('since'))


def assess(unit, launchd, job):
    """One of: missing, failed, running, stuck, down, failing, idle."""
    if not launchd['loaded']: return 'missing'
    if launchd.get('failed'): return 'failed'
    if launchd['running']:
        limit,runtime=unit.max_runtime_s,launchd['runtime_s']
        return 'stuck' if unit.kind=='job' and limit and runtime is not None and runtime>limit else 'running'
    if unit.kind=='service': return 'down'
    if job is not None: return 'failing' if job['ok'] is False else 'idle'
    return 'failing' if launchd['last_exit'] not in (None,0) else 'idle'


def observe(layout, backend, units):
    rows=[]
    for unit in units:
        launchd=backend.status(unit); job=job_state(layout,unit)
        rows.append(dict(name=unit.name,kind=unit.kind,backend=launchd.get('backend','launchd'),health=assess(unit,launchd,job),pid=launchd['pid'],
            runtime_s=launchd['runtime_s'],last_exit=launchd['last_exit'],job=job))
    return rows
