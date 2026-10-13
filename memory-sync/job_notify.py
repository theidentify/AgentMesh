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
import subprocess
import sys
import urllib.request

WEBHOOK = re.compile(r'^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$')
SAFE = re.compile(r'^[A-Za-z0-9_.:-]{1,80}$')


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


def post(url, content, timeout=15):
    data = json.dumps({'content': content, 'allowed_mentions': {'parse': []}}).encode('utf-8')
    request = urllib.request.Request(url, data=data, method='POST',
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'AgentMesh-job-notify/1'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def transition(previous, ok):
    """Return 'failed', 'recovered' or None for a state change worth alerting."""
    if not ok and (previous is None or previous.get('ok', True)):
        return 'failed'
    if ok and previous is not None and not previous.get('ok', True):
        return 'recovered'
    return None


def message(name, event, code, fields, previous, now):
    if event == 'failed':
        detail = ', '.join('%s=%s' % kv for kv in sorted(fields.items())) or 'no status'
        return '🔴 AgentMesh `%s` failed (exit %d, %s) at %s' % (name, code, detail, now)
    since = (previous or {}).get('since', 'unknown')
    return '🟢 AgentMesh `%s` recovered at %s (failing since %s)' % (name, now, since)


def run(name, argv, state_dir, webhook_file=None, timeout=3000, sender=post, now=None):
    state_path = Path(state_dir) / ('job-%s.json' % name)
    try:
        previous = json.loads(state_path.read_text())
    except (OSError, ValueError):
        previous = None
    try:
        process = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        code, out, err = process.returncode, process.stdout, process.stderr
    except subprocess.TimeoutExpired:
        code, out, err = 124, '', json.dumps({'error_class': 'Timeout'})
    except OSError:
        code, out, err = 127, '', json.dumps({'error_class': 'LaunchError'})
    now = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    ok = code == 0
    fields = summary_fields(out + '\n' + err)
    event = transition(previous, ok)
    state = dict(ok=ok, exit=code, at=now, fields=fields,
                 since=(previous or {}).get('since', now) if not ok and previous and not previous.get('ok', True) else now)
    notified = None
    if event:
        try:
            url = read_webhook(webhook_file)
            if url:
                sender(url, message(name, event, code, fields, previous, now))
                notified = event
        except Exception as exc:
            # A failed alert retries on the next tick: keep the previous state.
            print(json.dumps({'job': name, 'notify_error': type(exc).__name__}), file=sys.stderr)
            state = dict(previous or {}, at=now) if previous else dict(state, ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, sort_keys=True))
    os.replace(tmp, state_path)
    print(json.dumps(dict(job=name, at=now, exit=code, event=event, notified=notified, **fields), sort_keys=True))
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
