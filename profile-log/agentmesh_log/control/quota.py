"""Read-only view of the summary quota guard that job_notify.py maintains (control/quota.json)."""
from .layout import read_json


def count(value): return value if type(value) is int and value>=0 else None


def status(layout):
    state=read_json(layout.quota) or {}; policy=read_json(layout.quota_policy) or {}
    return dict(day=state.get('day') if type(state.get('day')) is str else None,
        calls=count(state.get('calls')) or 0,daily_llm_calls=count(policy.get('daily_llm_calls')),
        backoff_s=count(state.get('backoff_s')) or 0,
        backoff_until=state.get('backoff_until') if type(state.get('backoff_until')) is str else None)
