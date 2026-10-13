"""Digest worker that forwards to a digest_api.py service.

Use as the scheduler's worker argv; the model flag it receives is the routed
`provider:model` spec, so switching models is a config change only:
  command: [python, digest_api_client.py, --url, http://127.0.0.1:8765]
  model:   claude-code:claude-haiku-5-5
"""
import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

from memory_sync import canonical, load_packet

ENVELOPE = ('payload', 'provider', 'model', 'api_calls', 'usage')


def post(url, path, body, *, token=None, timeout=310):
    request = urllib.request.Request(url.rstrip('/') + path, data=canonical(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    if token:
        request.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return load_packet(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            error = load_packet(exc.read().decode('utf-8')).get('error')
        except Exception:
            error = None
        raise RuntimeError('digest API %d: %s' % (exc.code, error or 'error')) from None


def digest(url, model, request, *, token=None, timeout=310):
    return post(url, '/digest', {'model': model, 'request': request}, token=token, timeout=timeout)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--url', default='http://127.0.0.1:8765')
    p.add_argument('--model', required=True, help='provider:model')
    p.add_argument('--token-file')
    p.add_argument('--timeout', type=int, default=310)
    args = p.parse_args(argv)
    try:
        token = Path(args.token_file).read_text(encoding='utf-8').strip() if args.token_file else None
        result = digest(args.url, args.model, load_packet(sys.stdin.read()), token=token, timeout=args.timeout)
        print(canonical({k: result[k] for k in ENVELOPE}))
        return 0
    except Exception as exc:
        print(canonical(dict(error_class=type(exc).__name__)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
