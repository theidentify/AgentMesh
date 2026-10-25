"""Bounded digest worker backed by Claude Code; no filesystem/shell/MCP agent tools.

Drop-in replacement for digest_worker.py: reads one bounded request on stdin,
calls `claude --print` once with the request's instructions and JSON schema,
and prints the same {payload, usage, provider, model, api_calls} envelope.
Authentication is the local Claude Code login; no credentials are stored here.
"""
import argparse
import json
import os
import re
import subprocess
import sys

from memory_sync import canonical, load_packet

EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
JS_SYNTAX = '^$\\.*+?()[]{}|/'


def check_request(request):
    # Requests are built by the bounded protocol. Disallow tools/loop options.
    if not isinstance(request, dict) or set(request) != {'instructions', 'input', 'store', 'reasoning', 'text'} \
            or request['store'] is not False:
        raise ValueError('invalid bounded request')
    messages = request['input']
    if not isinstance(messages, list) or len(messages) != 1 or messages[0].get('role') != 'user' \
            or not isinstance(messages[0].get('content'), str):
        raise ValueError('invalid bounded input')
    schema = request['text']['format']['schema']
    if not isinstance(schema, dict):
        raise ValueError('invalid bounded schema')
    return messages[0]['content'], schema


def js_pattern(pattern):
    """Python re.escape output -> Unicode-mode JS regex (rejects escapes like `\\-`)."""
    return re.sub(r'\\(.)', lambda m: m.group(0) if m.group(1).isalnum() or m.group(1) in JS_SYNTAX
                  else m.group(1), pattern)


def js_schema(value):
    if isinstance(value, dict):
        return {k: js_pattern(v) if k == 'pattern' and isinstance(v, str) else js_schema(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [js_schema(v) for v in value]
    return value


def command(request, *, executable='claude', model, budget=1.0):
    if os.name == 'nt' and str(executable).lower().endswith(('.cmd', '.bat')):
        raise CLIFailure('Windows batch shims are unsupported; pass --claude claude.exe')
    _, schema = check_request(request)
    schema = js_schema(schema)
    args = [executable, '--print', '--safe-mode', '--tools', '',
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--no-session-persistence', '--no-chrome', '--disable-slash-commands',
            '--permission-prompts', 'none', '--output-format', 'json',
            '--system-prompt', request['instructions'], '--json-schema', json.dumps(schema),
            '--max-budget-usd', str(budget), '--model', model]
    effort = (request.get('reasoning') or {}).get('effort')
    if effort in EFFORTS:
        args += ['--effort', effort]
    return args


def normalize_usage(usage, cost=None):
    usage = usage or {}
    uncached = usage.get('input_tokens')
    cached = usage.get('cache_read_input_tokens')
    created = usage.get('cache_creation_input_tokens')
    parts = [v for v in (uncached, cached, created) if v is not None]
    return {'input_total_tokens': sum(parts) if parts else None,
            'input_uncached_tokens': uncached + (created or 0) if uncached is not None else None,
            'cache_read_tokens': cached, 'output_tokens': usage.get('output_tokens'),
            'reasoning_tokens': None, 'actual_cost_usd': cost}


class CLIFailure(ValueError):
    def __init__(self, reason=None):
        super().__init__('Claude command failed')
        self.reason = reason


def failure_reason(stdout):
    """A fixed reason from the CLI's result line (HTTP status or reason code), never its text."""
    try:
        result = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(result, dict):
        return None
    status = result.get('api_error_status')
    if type(status) is int and status in (401, 403):
        return 'claude auth failed'
    text = result.get('result')
    if isinstance(text, str):
        text = text.lower()
        if any(marker in text for marker in ('oauth', 'failed to authenticate', 'invalid api key', '/login')):
            return 'claude login expired' if 'expired' in text else 'claude auth failed'
    if type(status) is int and 100 <= status <= 599:
        return 'api_error %d' % status
    for key in ('terminal_reason', 'subtype'):
        value = result.get(key)
        if isinstance(value, str) and value != 'success' and re.fullmatch(r'[a-z][a-z0-9_]{0,39}', value):
            return value
    return None


def decode_result(result, model):
    if not isinstance(result, dict) or result.get('is_error') or result.get('subtype', 'success') != 'success':
        raise ValueError('Claude reported an unsuccessful result')
    payload = result.get('structured_output')
    models = result.get('modelUsage')
    if not isinstance(payload, dict) or not isinstance(models, dict) or model not in models:
        raise ValueError('Claude did not return structured output from the requested model')
    return dict(payload=payload, usage=normalize_usage(result.get('usage'), result.get('total_cost_usd')),
                provider='anthropic/claude-code', model=model, api_calls=1)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--model', required=True, help='full Claude model ID, e.g. claude-sonnet-5-5')
    p.add_argument('--claude', default='claude', help='Claude Code executable')
    p.add_argument('--budget', type=float, default=1.0)
    p.add_argument('--timeout', type=int, default=110)
    args = p.parse_args(argv)
    try:
        request = load_packet(sys.stdin.read())
        content, _ = check_request(request)
        process = subprocess.run(command(request, executable=args.claude, model=args.model, budget=args.budget),
                                 input=content, text=True, encoding='utf-8', capture_output=True,
                                 timeout=args.timeout, check=False)
        if process.returncode:
            raise CLIFailure(failure_reason(process.stdout))
        result = json.loads(process.stdout)
        if isinstance(result, dict) and result.get('is_error'):
            raise CLIFailure(failure_reason(process.stdout))
        print(canonical(decode_result(result, args.model)))
        return 0
    except CLIFailure as exc:
        # Never echo CLI output: it may contain source-bearing evidence.
        print(canonical(dict(error_class='ValueError', **({'reason': exc.reason} if exc.reason else {}))))
        return 1
    except Exception as exc:
        print(canonical(dict(error_class=type(exc).__name__)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
