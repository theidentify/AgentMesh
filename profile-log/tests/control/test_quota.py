from agentmesh_log.control import quota
from agentmesh_log.control.layout import Layout


def test_defaults_without_files(tmp_path):
    assert quota.status(Layout(tmp_path))==dict(day=None,calls=0,daily_llm_calls=None,backoff_s=0,backoff_until=None)


def test_reads_job_notify_state(tmp_path):
    layout=Layout(tmp_path).ensure()
    layout.quota.write_text('{"day": "2026-10-23", "calls": 4, "backoff_s": 600, "backoff_until": "2026-10-23T10:10:00+00:00"}')
    layout.quota_policy.write_text('{"daily_llm_calls": 50}')
    assert quota.status(layout)==dict(day='2026-10-23',calls=4,daily_llm_calls=50,backoff_s=600,backoff_until='2026-10-23T10:10:00+00:00')


def test_invalid_values_ignored(tmp_path):
    layout=Layout(tmp_path).ensure()
    layout.quota.write_text('{"calls": "4", "backoff_s": true}'); layout.quota_policy.write_text('{"daily_llm_calls": -1}')
    status=quota.status(layout)
    assert status['calls']==0 and status['backoff_s']==0 and status['daily_llm_calls'] is None
