"""Run one scheduled job and post a Discord webhook message on state changes.

Alerts only when the job starts failing and when it recovers, never on every
tick. Messages carry the job name, exit code and the job's own JSON
`error_class`/`status` fields; other output is never forwarded because it may
contain source evidence. Without a webhook file the run is logged only.

  python job_notify.py --name summary --state-dir status \
      --webhook-file ~/.config/agentmesh/discord-webhook -- python job.py
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
try:
    import resource
except ImportError:
    resource = None
import urllib.request

WEBHOOK = re.compile(r'^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$')
SAFE = re.compile(r'^[A-Za-z0-9_.:-]{1,80}$')
# Fixed protocol messages such as "unsupported evidence quote" or "oversize event 7; ...".
DETAIL = re.compile(r'^[A-Za-z0-9 _.,:;/()=-]{1,200}$')
SYSTEM_CA = ('/etc/ssl/cert.pem', '/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt')


def summary_fields(text):
    """Only allow-listed short identifiers from the job's JSON status lines."""
    fields = {}
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            for key in ('status', 'action', 'error_class'):
                if isinstance(value.get(key), str) and SAFE.match(value[key]):
                    fields[key] = value[key]
            if isinstance(value.get('detail'), str) and DETAIL.match(value['detail']):
                fields['detail'] = value['detail']
    return fields


def read_webhook(path):
    if not path:
        return None
    path = Path(path).expanduser()
    if not path.is_file():
        return None
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise PermissionError('webhook file must not be group/world readable')
    url = path.read_text(encoding='utf-8').strip()
    if not WEBHOOK.match(url):
        raise ValueError('not a Discord webhook URL')
    return url


def tls_context():
    """Verified TLS; python.org builds may lack a CA bundle, so fall back to the OS one."""
    context = ssl.create_default_context()
    if not context.get_ca_certs() and not os.path.exists(ssl.get_default_verify_paths().cafile or ''):
        for bundle in SYSTEM_CA:
            if os.path.exists(bundle):
                context.load_verify_locations(bundle)
                break
    return context


def post(url, content, timeout=15):
    data = json.dumps({'content': content, 'allowed_mentions': {'parse': []}}).encode('utf-8')
    request = urllib.request.Request(url, data=data, method='POST',
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'AgentMesh-job-notify/1'})
    with urllib.request.urlopen(request, timeout=timeout, context=tls_context()) as response:
        return response.status


def transition(previous, ok, now):
    """Return (event, since, alerted): alert a failure until it is delivered once,
    and a recovery only for a failure that was alerted."""
    failing = bool(previous) and not previous.get('ok', True)
    if not ok:
        since = previous.get('since', now) if failing else now
        alerted = bool(previous.get('alerted')) if failing else False
        return (None if alerted else 'failed'), since, alerted
    return ('recovered' if failing and previous.get('alerted') else None), now, False


def message(name, event, code, fields, since, now):
    if event == 'failed':
        detail = ', '.join('%s=%s' % kv for kv in sorted(fields.items()) if kv[0] != 'detail') or 'no status'
        if fields.get('detail'):
            detail += ': ' + fields['detail']
        return '🔴 AgentMesh `%s` failing (exit %d, %s) since %s' % (name, code, detail, since)
    return '🟢 AgentMesh `%s` recovered at %s (failing since %s)' % (name, now, since)


def run(name, argv, state_dir, webhook_file=None, timeout=3000, sender=post, now=None):
    state_path = Path(state_dir) / ('job-%s.json' % name)
    try:
        previous = json.loads(state_path.read_text())
    except (OSError, ValueError):
        previous = None
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    try:
        process = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        code, out, err = process.returncode, process.stdout, process.stderr
    except subprocess.TimeoutExpired:
        code, out, err = 124, '', json.dumps({'error_class': 'Timeout'})
    except OSError:
        code, out, err = 127, '', json.dumps({'error_class': 'LaunchError'})
    wall = round(time.monotonic()-started, 3)
    after = resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    cpu = round(max(0, after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime), 3) if after else None
    rss = round(after.ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024), 3) if after else None
    now = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    ok = code == 0
    fields = summary_fields(out + '\n' + err)
    event, since, alerted = transition(previous, ok, now)
    state = dict(ok=ok, exit=code, at=now, fields=fields, since=since, alerted=alerted)
    notified = None
    if event:
        failed_since = since if event == 'failed' else previous.get('since', 'unknown')
        try:
            url = read_webhook(webhook_file)
            if url:
                sender(url, message(name, event, code, fields, failed_since, now))
                notified = event
                state['alerted'] = event == 'failed'
        except Exception as exc:
            # Undelivered alerts retry next tick: a failure stays unalerted, and a
            # recovery keeps the previous (alerted, failing) state.
            print(json.dumps({'job': name, 'notify_error': type(exc).__name__}), file=sys.stderr)
            if event == 'recovered':
                state = dict(previous, at=now)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, sort_keys=True))
    os.replace(tmp, state_path)
    print(json.dumps(dict(job=name, at=now, exit=code, event=event, notified=notified, wall_s=wall, cpu_s=cpu, maxrss_mb=rss, **fields), sort_keys=True))
    return code


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--name', required=True)
    p.add_argument('--state-dir', required=True)
    p.add_argument('--webhook-file')
    p.add_argument('--timeout', type=int, default=3000)
    p.add_argument('command', nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or not SAFE.match(args.name):
        p.error('job name and command after -- are required')
    return run(args.name, command, args.state_dir, args.webhook_file, args.timeout)


if __name__ == '__main__':
    raise SystemExit(main())
