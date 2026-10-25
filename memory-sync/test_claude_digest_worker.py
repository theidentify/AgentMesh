import json

import pytest

from bounded_protocol import build_request

AUTH_EXPIRED_RESULT = {
    'is_error': True, 'subtype': 'success', 'terminal_reason': 'api_error',
    'api_error_status': None,
    'result': 'Failed to authenticate: OAuth session expired and could not be refreshed',
}


def request():
    batch = [{'id': 7, 'kind': 'user_request', 'project': 'demo', 'content': 'use sqlite',
              'occurred_at': '2026-10-13T00:00:00Z'}]
    return build_request(batch, [])


def test_command_is_noninteractive_and_uses_request_schema():
    import claude_digest_worker as worker
    req = request()
    args = worker.command(req, executable='claude', model='claude-sonnet-5-5')
    assert args[args.index('--tools') + 1] == ''
    assert '--safe-mode' in args and '--print' in args and '--no-session-persistence' in args
    assert '--dangerously-skip-permissions' not in args
    assert args[args.index('--system-prompt') + 1] == req['instructions']
    assert json.loads(args[args.index('--json-schema') + 1]) == req['text']['format']['schema']
    assert args[args.index('--model') + 1] == 'claude-sonnet-5-5'
    assert args[args.index('--effort') + 1] == 'low'


def test_schema_patterns_are_unicode_js_regex_and_keep_python_meaning():
    import re
    import claude_digest_worker as worker
    batch = [{'id': 7, 'kind': 'user_request', 'project': 'Agent-Mesh.claude-wt 1', 'content': 'use sqlite',
              'occurred_at': '2026-10-13T00:00:00Z'}]
    args = worker.command(build_request(batch, []), model='claude-haiku-5-5')
    schema = args[args.index('--json-schema') + 1]
    patterns = re.findall(r'"pattern": "((?:[^"\\]|\\.)*)"', schema)
    assert patterns
    for raw in patterns:
        pattern = json.loads('"' + raw + '"')
        assert '\\-' not in pattern and '\\ ' not in pattern
        if 'project' in pattern:
            assert re.match(pattern, 'project.Agent-Mesh.claude-wt 1.constraint.x')
            assert not re.match(pattern, 'project.AgentXMesh.claude-wt 1.constraint.x')


def test_rejects_requests_with_extra_options():
    import claude_digest_worker as worker
    req = dict(request(), tools=[])
    with pytest.raises(ValueError):
        worker.check_request(req)
    req = dict(request(), store=True)
    with pytest.raises(ValueError):
        worker.check_request(req)


def test_decode_result_returns_bounded_envelope():
    import claude_digest_worker as worker
    payload = {'items': [], 'reviewed_event_ids': [7], 'conflicts': []}
    result = {'is_error': False, 'subtype': 'success', 'structured_output': payload,
              'modelUsage': {'claude-sonnet-5-5': {}}, 'total_cost_usd': 0.01,
              'usage': {'input_tokens': 10, 'cache_read_input_tokens': 5,
                        'cache_creation_input_tokens': 2, 'output_tokens': 3}}
    out = worker.decode_result(result, 'claude-sonnet-5-5')
    assert set(out) == {'payload', 'provider', 'model', 'api_calls', 'usage'}
    assert out['payload'] == payload and out['model'] == 'claude-sonnet-5-5'
    assert out['provider'] == 'anthropic/claude-code'
    assert out['usage']['input_total_tokens'] == 17 and out['usage']['cache_read_tokens'] == 5
    with pytest.raises(ValueError):
        worker.decode_result(result, 'claude-opus-5-5')
    with pytest.raises(ValueError):
        worker.decode_result(dict(result, is_error=True), 'claude-sonnet-5-5')


def test_main_never_echoes_cli_output(tmp_path, capsys, monkeypatch):
    import claude_digest_worker as worker
    fake = tmp_path / 'claude'
    fake.write_text('#!/bin/sh\necho SECRET-EVIDENCE; exit 3\n')
    fake.chmod(0o755)
    monkeypatch.setattr('sys.stdin', __import__('io').StringIO(json.dumps(request())))
    assert worker.main(['--model', 'claude-sonnet-5-5', '--claude', str(fake)]) == 1
    out = capsys.readouterr().out
    assert 'SECRET' not in out and json.loads(out) == {'error_class': 'ValueError'}


@pytest.mark.parametrize('stdout, code, reason', [
    (AUTH_EXPIRED_RESULT, 1, 'claude login expired'),
    (AUTH_EXPIRED_RESULT, 0, 'claude login expired'),
    ({'is_error': True, 'api_error_status': 401}, 1, 'claude auth failed'),
    ({'is_error': True, 'api_error_status': 403, 'result': 'OAuth expired'}, 1, 'claude auth failed'),
    ({'is_error': True, 'result': 'FAILED TO AUTHENTICATE: SECRET'}, 1, 'claude auth failed'),
    ({'is_error': True, 'result': 'Invalid API Key: SECRET'}, 1, 'claude auth failed'),
    ({'is_error': True, 'result': 'Use /LOGIN: SECRET'}, 1, 'claude auth failed'),
    ({'is_error': True, 'api_error_status': 429, 'result': {'oauth': 'expired'}}, 1, 'api_error 429'),
    ({'type': 'result', 'is_error': True, 'subtype': 'success', 'api_error_status': 529,
      'terminal_reason': 'api_error', 'result': 'SECRET provider text'}, 1, 'api_error 529'),
    ({'type': 'result', 'is_error': True, 'subtype': 'success', 'terminal_reason': 'api_error',
      'result': 'SECRET provider text'}, 1, 'api_error'),
    ({'type': 'result', 'is_error': True, 'subtype': 'error_max_budget_usd'}, 0, 'error_max_budget_usd'),
    ({'type': 'result', 'is_error': True, 'terminal_reason': 'SECRET Free Text!'}, 1, None),
])
def test_main_reports_only_a_fixed_cli_failure_reason(tmp_path, capsys, monkeypatch, stdout, code, reason):
    import claude_digest_worker as worker
    fake = tmp_path / 'claude'
    fake.write_text("#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' '" + json.dumps(stdout) + "'\nexit " + str(code) + "\n")
    fake.chmod(0o755)
    monkeypatch.setattr('sys.stdin', __import__('io').StringIO(json.dumps(request())))
    assert worker.main(['--model', 'claude-haiku-5-5', '--claude', str(fake)]) == 1
    out = capsys.readouterr().out
    expected = {'error_class': 'ValueError'} if reason is None else {'error_class': 'ValueError', 'reason': reason}
    assert 'SECRET' not in out and json.loads(out) == expected
    if isinstance(stdout.get('result'), str):
        assert stdout['result'] not in out


def test_expired_login_detail_end_to_end(tmp_path):
    import sys
    from pathlib import Path
    import bounded_digest
    import claude_digest_worker
    from bounded_protocol import ValidationError, error_detail

    fake = tmp_path / 'claude'
    fake.write_text("#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' '" + json.dumps(AUTH_EXPIRED_RESULT) + "'\nexit 1\n")
    fake.chmod(0o755)
    with pytest.raises(ValidationError) as caught:
        bounded_digest.extract_command(
            request(), model='claude-haiku-5-5',
            command=[sys.executable, str(Path(claude_digest_worker.__file__)), '--claude', str(fake)],
        )
    detail = error_detail(caught.value)
    assert detail == 'model worker failed: claude login expired; coverage unchanged'
    assert AUTH_EXPIRED_RESULT['result'] not in detail


@pytest.mark.parametrize('suffix', ['cmd', 'bat', 'CMD', 'BAT'])
def test_windows_batch_shim_rejected(suffix, monkeypatch, capsys):
    import io
    from types import SimpleNamespace
    import claude_digest_worker as worker
    monkeypatch.setattr(worker, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(worker.sys, 'stdin', io.StringIO(json.dumps(request())))
    monkeypatch.setattr(worker.subprocess, 'run', lambda *a, **k: pytest.fail('batch shim must not run'))
    assert worker.main(['--model', 'model', '--claude', 'C:\\Program Files\\Claude\\claude.'+suffix]) == 1
    assert json.loads(capsys.readouterr().out) == dict(error_class='ValueError', reason='Windows batch shims are unsupported; pass --claude claude.exe')


def test_windows_exe_runs_without_shell(monkeypatch, capsys):
    import io
    from types import SimpleNamespace
    import claude_digest_worker as worker
    req = request(); calls = []
    executable = r'C:\Program Files\Claude\claude.exe'
    monkeypatch.setattr(worker, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(worker.sys, 'stdin', io.StringIO(json.dumps(req)))
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(structured_output={}, modelUsage={'model': {}})))
    monkeypatch.setattr(worker.subprocess, 'run', run)
    assert worker.main(['--model', 'model', '--claude', executable]) == 0
    argv, opts = calls[0]
    assert argv == worker.command(req, executable=executable, model='model')
    assert opts == dict(input=req['input'][0]['content'], text=True, encoding='utf-8', capture_output=True, timeout=110, check=False)
    assert json.loads(capsys.readouterr().out)['provider'] == 'anthropic/claude-code'


def test_posix_batch_suffix_is_unchanged(monkeypatch):
    from types import SimpleNamespace
    import claude_digest_worker as worker
    monkeypatch.setattr(worker, 'os', SimpleNamespace(name='posix'))
    assert worker.command(request(), executable='/bin/claude.cmd', model='model')[0] == '/bin/claude.cmd'
