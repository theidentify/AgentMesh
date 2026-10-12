import io
import json
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

FAKE_WORKER = r'''
import json, sys
args = sys.argv[1:]
model = args[args.index('--model') + 1]
request = json.load(sys.stdin)
if model == 'boom':
    print('SECRET-EVIDENCE'); sys.exit(3)
if model == 'liar':
    print(json.dumps(dict(payload={}, usage={}, provider='p', model='other', api_calls=1))); sys.exit(0)
ids = [e['id'] for e in json.loads(request['input'][0]['content'])['events']]
print(json.dumps(dict(payload={'items': [], 'reviewed_event_ids': ids, 'conflicts': []},
                      usage={'actual_cost_usd': 0.001, 'input_total_tokens': 10, 'output_tokens': 2},
                      provider='fake', model=model, api_calls=1)))
'''

EVENTS = [{'id': 7, 'kind': 'user_request', 'project': 'demo', 'content': 'hello',
           'occurred_at': '2026-10-13T00:00:00Z', 'source_session_id': 's1'}]


@pytest.fixture
def service(tmp_path):
    import digest_api
    worker = tmp_path / 'fake_worker.py'
    worker.write_text(FAKE_WORKER)
    providers = {'fake': [sys.executable, str(worker)]}

    def start(token=None):
        server = ThreadingHTTPServer(('127.0.0.1', 0), digest_api.make_handler(providers, token=token, timeout=30))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return 'http://127.0.0.1:%d' % server.server_address[1]
    servers = []
    yield start
    for server in servers:
        server.shutdown()


def request():
    from bounded_protocol import build_request
    return build_request(EVENTS, [])


def test_digest_routes_by_provider_and_reports_routed_model(service):
    from digest_api_client import digest
    url = service()
    result = digest(url, 'fake:model-a', request())
    assert result['model'] == 'fake:model-a' and result['provider'] == 'fake'
    assert result['payload']['reviewed_event_ids'] == [7]
    assert result['duration_seconds'] >= 0


@pytest.mark.parametrize('model, message', [('nope:x', '404'), ('fake', '400'), ('fake:bad name', '400'),
                                            ('fake:boom', '502'), ('fake:liar', '502')])
def test_digest_rejects_bad_routes_and_never_echoes_worker_output(service, model, message):
    from digest_api_client import digest
    url = service()
    with pytest.raises(RuntimeError) as info:
        digest(url, model, request())
    assert message in str(info.value) and 'SECRET' not in str(info.value)


def test_token_is_required_when_configured(service):
    from digest_api_client import digest, post
    url = service(token='t0k')
    with pytest.raises(RuntimeError, match='401'):
        digest(url, 'fake:m', request())
    assert digest(url, 'fake:m', request(), token='t0k')['model'] == 'fake:m'


def test_client_worker_emits_scheduler_envelope(service, capsys, monkeypatch):
    import digest_api_client
    url = service()
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(request())))
    assert digest_api_client.main(['--url', url, '--model', 'fake:m']) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {'payload', 'provider', 'model', 'api_calls', 'usage'} and out['model'] == 'fake:m'


def test_client_envelope_satisfies_bounded_extract_command(service):
    from bounded_digest import extract_command
    url = service()
    command = [sys.executable, 'digest_api_client.py', '--url', url]
    assert extract_command(request(), model='fake:m', command=command)['model'] == 'fake:m'


def test_load_providers_rejects_bad_entries(tmp_path):
    import digest_api
    path = tmp_path / 'p.json'
    for bad in ({}, {'a:b': ['x']}, {'ok': []}, {'ok': ['', 'x']}):
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            digest_api.load_providers(path)


def test_bench_reports_validity_stability_and_cost(service):
    import digest_bench
    from digest_api_client import digest
    url = service()
    case = {'name': 'c', 'events': EVENTS}
    call = lambda model, req: digest(url, model, req)
    rows = [dict(digest_bench.run_case(call, m, case), trial=t) for m in ('fake:a', 'fake:boom') for t in range(2)]
    report = {r['model']: r for r in digest_bench.summarize(rows)}
    assert report['fake:a']['valid'] == 2 and report['fake:a']['key_stable_cases'] == '1/1'
    assert report['fake:a']['total_cost_usd'] == 0.002
    assert report['fake:boom']['valid'] == 0
