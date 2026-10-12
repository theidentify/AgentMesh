import json

import pytest

from bounded_protocol import build_request


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
