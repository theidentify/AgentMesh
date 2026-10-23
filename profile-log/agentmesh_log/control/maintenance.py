"""Maintenance mode: a hold file job_notify.py reads before every wrapped job. Only `"on": true` holds."""
from datetime import datetime, timezone
from .layout import read_json, write_json

REASON_MAX=120


def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='seconds')


def status(layout):
    value=read_json(layout.maintenance) or {}
    on=value.get('on') is True
    return dict(on=on,since=value.get('since') if on else None,reason=value.get('reason') if on else None)


def turn_on(layout, reason, now=now_iso):
    if type(reason) is not str or not 1<=len(reason)<=REASON_MAX or not reason.isprintable(): raise ValueError('bad_args')
    current=status(layout)
    if current['on']: return current
    layout.ensure(); write_json(layout.maintenance,dict(on=True,since=now(),reason=reason))
    return status(layout)


def turn_off(layout):
    layout.ensure(); write_json(layout.maintenance,dict(on=False))
    return status(layout)
