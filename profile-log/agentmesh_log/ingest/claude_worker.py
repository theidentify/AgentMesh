"""Bounded digest worker backed by Claude Code; no filesystem/shell/MCP agent tools.

Drop-in replacement for digest_worker.py: reads one bounded request on stdin,
calls `claude --print` once with the request's instructions and JSON schema,
and prints the same {payload, usage, provider, model, api_calls} envelope.
Authentication is the local Claude Code login; no credentials are stored here.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from digest_io import canonical, load_packet, utf8_stdio

EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
JS_SYNTAX = '^$\\.*+?()[]{}|/'
ERROR_LOG_ENV = 'AGENTMESH_WORKER_ERROR_LOG'  # set by job_notify for scheduled jobs; unset means no log
ERROR_LOG_MAX = 64 * 1024
STDERR_LINES = 20
LINE_CHARS = 300
# Only lines that read like a CLI, auth, network or OS error are kept; anything else may echo the request.
ERROR_LINE = re.compile(r'error|fail|denied|not found|unauthori|forbidden|timed? ?out|login|auth|credential|keychain|'
                        r'token|rate limit|quota|network|ECONN|ENOTFOUND|EACCES|EPERM|ENOENT|certificate|proxy|'
                        r'permission|\bstatus \d{3}\b|\b[45]\d\d\b', re.IGNORECASE)
SECRET = re.compile(r'sk-ant-[A-Za-z0-9_-]+|(?i:bearer)\s+\S+|[A-Za-z0-9_-]{40,}')
# Which of these are set says a lot about a task that fails only right after logon; values are never logged.
ENV_NAMES = ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'PATH', 'CLAUDE_CONFIG_DIR',
             'ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN', 'HTTPS_PROXY')


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


def failure_reason(stdout, stderr='', code=0):
    reason = json_failure_reason(stdout)
    try:
        result = json.loads(stdout)
        message = result.get('result', '') if isinstance(result, dict) else stdout
        status = result.get('api_error_status') if isinstance(result, dict) else None
    except ValueError: message = stdout; status = None
    text = ((message if isinstance(message, str) else '') + '\n' + stderr).lower()
    if status == 429: return 'claude rate limited'
    if any(marker in text for marker in ('401', 'oauth', 'failed to authenticate', '/login')):
        return 'claude login expired' if 'expired' in text else 'claude auth failed'
    if any(marker in text for marker in ('429', 'rate limit', 'usage limit')):
        return 'claude rate limited'
    return reason or ('claude exit %d' % code if type(code) is int and code else None)


def json_failure_reason(stdout):
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
        if value in ('api_error', 'error_max_budget_usd', 'error_max_turns', 'error_during_execution', 'error_max_structured_output_retries'):
            return value
    return None


def stderr_tail(stderr):
    """The last error-looking stderr lines, cut short and with anything token-like masked; never other text."""
    lines = [line.strip()[:LINE_CHARS] for line in (stderr or '').splitlines() if line.strip()]
    return [SECRET.sub('[masked]', line) for line in lines[-STDERR_LINES:] if ERROR_LINE.search(line)]


def log_failure(executable, code, stderr, reason, path=None):
    """Append one redacted record for a failed CLI run, so a failure seen only by a scheduled task can be read later."""
    path = path or os.environ.get(ERROR_LOG_ENV)
    if not path:
        return
    try:
        path = Path(path)
        record = dict(at=datetime.now(timezone.utc).isoformat(timespec='seconds'), exit=code, reason=reason,
                      executable=Path(str(executable)).name, cwd_is_home=Path.cwd() == Path.home(),
                      env_set=sorted(n for n in ENV_NAMES if os.environ.get(n)), stderr=stderr_tail(stderr))
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n'
        if path.is_symlink():
            return
        if path.exists() and path.stat().st_size + len(line) > ERROR_LOG_MAX:
            kept = path.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)
            path.write_text(''.join(kept[len(kept) // 2:]), encoding='utf-8')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as handle:
            handle.write(line)
    except (OSError, ValueError):
        pass  # the log is a diagnostic; it never changes the worker's result


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
    p.add_argument('--error-log', help='append a redacted record of each failed CLI run here (default: $%s)' % ERROR_LOG_ENV)
    args = p.parse_args(argv)
    try:
        utf8_stdio()
        request = load_packet(sys.stdin.read())
        content, _ = check_request(request)
        process = subprocess.run(command(request, executable=args.claude, model=args.model, budget=args.budget),
                                 input=content, text=True, encoding='utf-8', capture_output=True,
                                 timeout=args.timeout, check=False)
        def failed():
            reason = failure_reason(process.stdout, process.stderr, process.returncode)
            log_failure(args.claude, process.returncode, process.stderr, reason, args.error_log)
            return CLIFailure(reason)
        if process.returncode:
            raise failed()
        try: result = json.loads(process.stdout)
        except ValueError: raise failed() from None
        if isinstance(result, dict) and result.get('is_error'):
            raise failed()
        print(canonical(decode_result(result, args.model)))
        return 0
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as exc:
        reason = {subprocess.TimeoutExpired: 'claude timeout', FileNotFoundError: 'claude executable missing',
                  PermissionError: 'claude executable denied'}[type(exc)]
        log_failure(args.claude, None, getattr(exc, 'stderr', None) if isinstance(getattr(exc, 'stderr', None), str) else '',
                    reason, args.error_log)
        print(canonical(dict(error_class=type(exc).__name__, reason=reason)))
        return 1
    except CLIFailure as exc:
        # Never echo CLI output: it may contain source-bearing evidence.
        print(canonical(dict(error_class='ValueError', **({'reason': exc.reason} if exc.reason else {}))))
        return 1
    except Exception as exc:
        print(canonical(dict(error_class=type(exc).__name__)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
