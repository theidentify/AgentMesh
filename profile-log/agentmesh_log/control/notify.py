"""One-line Discord alerts from the supervisor, under the same webhook rules as memory-sync/job_notify.py.

Owner-only webhook file, Discord webhook URLs only, no mentions, verified TLS. Failures never stop the supervisor.
"""
import json
import os
from pathlib import Path
import re
import ssl
import sys
import urllib.request

WEBHOOK=re.compile(r'^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$')
SYSTEM_CA=('/etc/ssl/cert.pem','/etc/ssl/certs/ca-certificates.crt','/etc/pki/tls/certs/ca-bundle.crt')


def read_webhook(path):
    path=Path(path)
    if not path.is_file(): return None
    if os.name!='nt' and path.stat().st_mode&0o077: raise PermissionError('webhook file must not be group/world readable')
    url=path.read_text(encoding='utf-8').strip()
    if not WEBHOOK.match(url): raise ValueError('not a Discord webhook URL')
    return url


def tls_context():
    """Verified TLS; python.org builds may lack a CA bundle, so fall back to the OS one (as job_notify.py does)."""
    context=ssl.create_default_context()
    if not context.get_ca_certs() and not os.path.exists(ssl.get_default_verify_paths().cafile or ''):
        for bundle in SYSTEM_CA:
            if os.path.exists(bundle): context.load_verify_locations(bundle); break
    return context


def post(url, content, timeout=15):
    data=json.dumps({'content':content,'allowed_mentions':{'parse':[]}}).encode('utf-8')
    request=urllib.request.Request(url,data=data,method='POST',
        headers={'Content-Type':'application/json','User-Agent':'AgentMesh-control/1'})
    with urllib.request.urlopen(request,timeout=timeout,context=tls_context()) as response: return response.status


def notifier(layout, sender=post):
    def notify(text):
        try:
            url=read_webhook(layout.webhook)
            if url: sender(url,text)
        except Exception as exc:
            print(json.dumps({'notify_error':type(exc).__name__}),file=sys.stderr,flush=True)
    return notify
