import json
import sys

import pytest

import job_notify

URL = 'https://discord.com/api/webhooks/123/abc-DEF_9'


def job(tmp_path, code, stderr=''):
    script = tmp_path / ('job%d.py' % code)
    script.write_text('import sys\nprint("SECRET-EVIDENCE")\nsys.stderr.write(%r)\nsys.exit(%d)\n' % (stderr, code))
    return [sys.executable, str(script)]


@pytest.fixture
def webhook(tmp_path):
    path = tmp_path / 'hook'
    path.write_text(URL + '\n')
    path.chmod(0o600)
    return path


def test_alerts_once_on_failure_and_once_on_recovery(tmp_path, webhook):
    sent = []
    sender = lambda url, content: sent.append((url, content))
    fail = job(tmp_path, 1, '{"status": "blocked", "action": "summary", "error_class": "ValidationError"}')
    for i in range(3):
        job_notify.run('summary', fail, tmp_path, webhook, sender=sender, now='t%d' % i)
    assert len(sent) == 1 and sent[0][0] == URL
    assert 'error_class=ValidationError' in sent[0][1] and 'SECRET' not in sent[0][1]
    job_notify.run('summary', job(tmp_path, 0), tmp_path, webhook, sender=sender, now='t3')
    job_notify.run('summary', job(tmp_path, 0), tmp_path, webhook, sender=sender, now='t4')
    assert len(sent) == 2 and 'recovered' in sent[1][1] and 'failing since t0' in sent[1][1]


def test_failed_alert_is_retried_next_tick(tmp_path, webhook):
    calls = []
    def flaky(url, content):
        calls.append(content)
        if len(calls) == 1:
            raise OSError('network')
    fail = job(tmp_path, 1)
    job_notify.run('ingest', fail, tmp_path, webhook, sender=flaky, now='t0')
    job_notify.run('ingest', fail, tmp_path, webhook, sender=flaky, now='t1')
    job_notify.run('ingest', fail, tmp_path, webhook, sender=flaky, now='t2')
    assert len(calls) == 2


def test_failure_before_webhook_is_alerted_once_configured(tmp_path, webhook):
    sent = []
    sender = lambda url, content: sent.append(content)
    fail = job(tmp_path, 1, '{"error_class": "ValidationError"}')
    job_notify.run('summary', fail, tmp_path, tmp_path / 'missing', sender=sender, now='t0')
    job_notify.run('summary', fail, tmp_path, tmp_path / 'missing', sender=sender, now='t1')
    assert sent == []
    job_notify.run('summary', fail, tmp_path, webhook, sender=sender, now='t2')
    job_notify.run('summary', fail, tmp_path, webhook, sender=sender, now='t3')
    assert len(sent) == 1 and 'since t0' in sent[0]
    job_notify.run('summary', job(tmp_path, 0), tmp_path, webhook, sender=sender, now='t4')
    assert len(sent) == 2 and 'recovered' in sent[1]


def test_legacy_failing_state_without_alerted_flag_alerts(tmp_path, webhook):
    (tmp_path / 'job-summary.json').write_text(json.dumps({'ok': False, 'exit': 1, 'since': 'old', 'at': 'old'}))
    sent = []
    job_notify.run('summary', job(tmp_path, 1), tmp_path, webhook, sender=lambda u, c: sent.append(c), now='t1')
    assert len(sent) == 1 and 'since old' in sent[0]


def test_no_webhook_file_logs_only(tmp_path):
    sent = []
    assert job_notify.run('ingest', job(tmp_path, 1), tmp_path, tmp_path / 'missing',
                          sender=lambda *a: sent.append(a)) == 1
    assert sent == [] and json.loads((tmp_path / 'job-ingest.json').read_text())['ok'] is False


def test_webhook_file_must_be_private_and_a_discord_url(tmp_path, webhook):
    webhook.chmod(0o644)
    with pytest.raises(PermissionError):
        job_notify.read_webhook(webhook)
    webhook.chmod(0o600)
    webhook.write_text('https://example.com/api/webhooks/1/x')
    with pytest.raises(ValueError):
        job_notify.read_webhook(webhook)


def test_only_allow_listed_identifiers_are_forwarded():
    fields = job_notify.summary_fields('{"error_class": "ValueError", "message": "secret"}\n'
                                       '{"status": "has spaces and secrets"}\nplain text')
    assert fields == {'error_class': 'ValueError'}


def test_protocol_detail_is_forwarded_but_free_text_is_not():
    fields = job_notify.summary_fields('{"error_class": "ValidationError", "detail": "unsupported evidence quote"}')
    assert fields['detail'] == 'unsupported evidence quote'
    assert 'detail' not in job_notify.summary_fields('{"detail": "token=sk-ant-abc \\"quoted\\" <html>"}')
    text = job_notify.message('summary', 'failed', 1, fields, 't0', 't1')
    assert 'error_class=ValidationError: unsupported evidence quote' in text


def test_resource_fields(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    ticks=iter([10.,14.]); usage=iter([SimpleNamespace(ru_utime=1.,ru_stime=2.,ru_maxrss=0),SimpleNamespace(ru_utime=2.,ru_stime=2.5,ru_maxrss=8*1024*1024)])
    monkeypatch.setattr(job_notify.time,'monotonic',lambda:next(ticks))
    monkeypatch.setattr(job_notify.resource,'getrusage',lambda who:next(usage))
    monkeypatch.setattr(job_notify.sys,'platform','darwin')
    monkeypatch.setattr(job_notify.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout='',stderr=''))
    assert job_notify.run('summary',['fake'],tmp_path,now='fixed')==0
    row=json.loads(capsys.readouterr().out)
    assert (row['wall_s'],row['cpu_s'],row['maxrss_mb'])==(4,1.5,8)
    assert row['job']=='summary' and row['at']=='fixed' and row['exit']==0


@pytest.mark.parametrize('code', [0, 1])
def test_retention_summary_and_exit(tmp_path, webhook, code, capsys):
    sent = []
    payload = json.dumps(dict(deleted=2, candidates=3, content='SECRET-EVIDENCE'))
    argv = [sys.executable, '-c', f'import sys; print({payload!r}); sys.exit({code})']
    assert job_notify.run('retention', argv, tmp_path, webhook, sender=lambda u,c: sent.append(c)) == code
    state = json.loads((tmp_path/'job-retention.json').read_text())
    assert state['ok'] is (code == 0) and state['exit'] == code
    assert state['fields'] == {'summary': 'deleted=2 candidates=3'}
    assert len(sent) == code
    assert 'SECRET-EVIDENCE' not in capsys.readouterr().out + ''.join(sent)


def test_retention_summary_rejects_non_counts():
    for value in ('secret', True, -1, 1.5, None):
        assert job_notify.summary_fields(json.dumps(dict(deleted=value, candidates=2))) == {}


NOW = '2026-10-23T10:00:00+00:00'


def control_home(tmp_path):
    (tmp_path / 'status').mkdir(); (tmp_path / 'control').mkdir()
    return tmp_path / 'status', tmp_path / 'control'


def touch(path):
    return [sys.executable, '-c', 'open(%r, "w").close()' % str(path)]


def test_maintenance_holds_wrapped_job(tmp_path, capsys):
    status, control = control_home(tmp_path)
    (control / 'maintenance.json').write_text('{"on": true, "reason": "upgrade"}')
    assert job_notify.run('ingest', touch(tmp_path / 'ran'), status, now=NOW) == 0
    assert not (tmp_path / 'ran').exists() and not (status / 'job-ingest.json').exists()
    assert json.loads(capsys.readouterr().out) == {'at': NOW, 'held': 'maintenance', 'job': 'ingest'}


def test_maintenance_off_runs(tmp_path):
    status, control = control_home(tmp_path)
    (control / 'maintenance.json').write_text('{"on": "yes"}')
    job_notify.run('ingest', touch(tmp_path / 'ran'), status, now=NOW)
    assert (tmp_path / 'ran').exists()


def test_without_control_dir_nothing_changes(tmp_path):
    status = tmp_path / 'status'; status.mkdir()
    assert job_notify.run('summary', job(tmp_path, 0), status, now=NOW) == 0
    assert not (tmp_path / 'control').exists()
