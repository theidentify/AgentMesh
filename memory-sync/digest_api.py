"""Loopback HTTP digest service: one bounded request, model chosen per call.

POST /digest {"model": "<provider>:<model>", "request": <bounded request>}
GET  /models  -> configured providers

Each provider is a worker argv speaking the digest worker protocol
(stdin bounded request, `--model` appended, stdout result envelope), e.g.
claude_digest_worker.py or digest_worker.py. The service never echoes worker
output on failure and binds to loopback only.
"""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from memory_sync import canonical, load_packet

HERE = Path(__file__).resolve().parent
ENVELOPE = {'payload', 'provider', 'model', 'api_calls', 'usage'}
MAX_BODY = 4_000_000
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')
LOOPBACK = ('127.0.0.1', '::1', 'localhost')


class DigestError(Exception):
    def __init__(self, status, error):
        super().__init__(error)
        self.status, self.error = status, error


def default_providers():
    return {'claude-code': [sys.executable, str(HERE / 'claude_digest_worker.py')]}


def load_providers(path=None):
    if path is None:
        return default_providers()
    providers = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(providers, dict) or not providers:
        raise ValueError('providers file must map names to worker argv')
    for name, argv in providers.items():
        if not NAME.match(name) or ':' in name or not isinstance(argv, list) or not argv \
                or any(not isinstance(a, str) or not a or '\x00' in a for a in argv):
            raise ValueError('invalid provider ' + repr(name))
    return providers


def split_model(spec, providers):
    if not isinstance(spec, str) or spec.count(':') != 1:
        raise DigestError(400, 'model must be provider:model')
    provider, model = spec.split(':')
    if provider not in providers:
        raise DigestError(404, 'unknown provider')
    if not NAME.match(model):
        raise DigestError(400, 'invalid model name')
    return provider, model


def run_digest(body, providers, timeout=300):
    """Route one bounded request to provider:model; returns envelope + duration."""
    if not isinstance(body, dict) or set(body) != {'model', 'request'} or not isinstance(body['request'], dict):
        raise DigestError(400, 'body must be {model, request}')
    provider, model = split_model(body['model'], providers)
    start = time.monotonic()
    try:
        process = subprocess.run([*providers[provider], '--model', model], input=canonical(body['request']),
                                 text=True, encoding='utf-8', capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DigestError(504, 'worker timeout') from None
    except OSError:
        raise DigestError(502, 'worker unavailable') from None
    duration = round(time.monotonic() - start, 3)
    if process.returncode:
        raise DigestError(502, 'worker failed')
    try:
        envelope = load_packet(process.stdout)
    except ValueError:
        raise DigestError(502, 'worker returned invalid JSON') from None
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE or envelope['model'] != model:
        raise DigestError(502, 'invalid worker envelope')
    # The routed spec is the model identity the caller asked for and records.
    return dict(envelope, model=body['model'], duration_seconds=duration)


def make_handler(providers, *, token=None, timeout=300):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'AgentMeshDigest/1'

        def log_message(self, fmt, *args):
            # Path and status only; bodies may carry source evidence.
            sys.stderr.write('%s %s\n' % (self.command, args[1] if len(args) > 1 else ''))

        def reply(self, status, value):
            data = canonical(value).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            if token is None:
                return True
            supplied = self.headers.get('Authorization', '')
            return hmac.compare_digest(supplied.encode(), ('Bearer ' + token).encode())

        def do_GET(self):
            if not self.authorized():
                return self.reply(401, {'error': 'unauthorized'})
            if self.path != '/models':
                return self.reply(404, {'error': 'not found'})
            self.reply(200, {'providers': sorted(providers), 'model_format': 'provider:model'})

        def do_POST(self):
            if not self.authorized():
                return self.reply(401, {'error': 'unauthorized'})
            if self.path != '/digest':
                return self.reply(404, {'error': 'not found'})
            try:
                length = int(self.headers.get('Content-Length', ''))
            except ValueError:
                return self.reply(411, {'error': 'length required'})
            if not 0 < length <= MAX_BODY:
                return self.reply(413, {'error': 'body too large'})
            try:
                body = load_packet(self.rfile.read(length).decode('utf-8'))
                self.reply(200, run_digest(body, providers, timeout))
            except DigestError as exc:
                self.reply(exc.status, {'error': exc.error})
            except (ValueError, UnicodeError):
                self.reply(400, {'error': 'invalid JSON'})
    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', default='127.0.0.1', choices=LOOPBACK)
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--providers', help='JSON file {provider: worker argv}; default: claude-code only')
    p.add_argument('--token-file', help='require Authorization: Bearer <file contents>')
    p.add_argument('--timeout', type=int, default=300)
    args = p.parse_args(argv)
    providers = load_providers(args.providers)
    token = Path(args.token_file).read_text(encoding='utf-8').strip() if args.token_file else None
    server = ThreadingHTTPServer((args.host, args.port), make_handler(providers, token=token, timeout=args.timeout))
    print(canonical({'listening': 'http://%s:%d' % (args.host, args.port), 'providers': sorted(providers)}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
