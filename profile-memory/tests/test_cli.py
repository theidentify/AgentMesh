import json
from pathlib import Path
import os
import subprocess
import sys

ENTRY = Path(__file__).resolve().parents[1] / 'agentmesh-memory.py'


def invoke(root, profile, *args):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable, str(ENTRY), '--root', str(root), '--profile', profile, *args],
                          capture_output=True, text=True, env=environment, timeout=30)


def successful(root, profile, *args):
    result = invoke(root, profile, *args)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    assert len(result.stdout.strip().splitlines()) == 1
    return json.loads(result.stdout)


def test_cli_builds_real_http_fixture_index_and_switches_profile_default(tmp_path):
    from test_embeddings import server
    root = tmp_path / 'workspace'
    successful(root, 'alpha', 'init')
    successful(root, 'beta', 'init')
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'content': 'Configurable provider.', 'policy': {'read': ['alpha', 'beta'], 'embed': ['local']}}))
    record = successful(root, 'alpha', 'remember', '--input', str(request))
    with server() as (url, state):
        state['response']['embeddings'] = [[1, 0]]
        indexed = successful(root, 'alpha', 'index', '--endpoint', url)
        assert indexed['indexed'] == 1
        successful(root, 'beta', 'configure', '--mode', 'hybrid', '--engine', 'hnsw')
        response = successful(root, 'beta', 'search', 'provider', '--endpoint', url)
        assert response['effective_mode'] == 'hybrid'
        assert response['vector_engine'] == 'hnsw'
        assert response['results'][0]['id'] == record['id']
        assert state['calls'][-1][2]['truncate'] is False
    # A keyword override never contacts the unavailable embedding endpoint.
    plain = successful(root, 'beta', 'search', 'provider', '--mode', 'keyword')
    assert plain['effective_mode'] == 'keyword'


def test_cli_rejects_identity_payloads_duplicate_keys_and_uninitialized_reads(tmp_path):
    root = tmp_path / 'workspace'
    unopened = invoke(root, 'alpha', 'search', 'provider', '--mode', 'keyword')
    assert unopened.returncode == 1
    assert not (root / 'profiles' / 'alpha').exists()
    successful(root, 'alpha', 'init')
    request = tmp_path / 'request.json'
    request.write_text('{"content":"one","content":"two"}')
    duplicate = invoke(root, 'alpha', 'remember', '--input', str(request))
    assert duplicate.returncode == 1
    assert json.loads(duplicate.stderr)['error'] == 'KnowledgeError'
    request.write_text(json.dumps({'content': 'Forged.', 'owner': 'beta'}))
    forged = invoke(root, 'alpha', 'remember', '--input', str(request))
    assert forged.returncode == 1
    assert forged.stdout == ''


def test_cli_revision_retention_and_revocation_trace_exact_provenance(tmp_path):
    root = tmp_path / 'workspace'
    successful(root, 'alpha', 'init')
    successful(root, 'beta', 'init')
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'content': 'Provider A required.', 'policy': {'read': ['alpha', 'beta'], 'retain': ['beta']}}))
    original = successful(root, 'alpha', 'remember', '--input', str(request))
    derived_request = tmp_path / 'derived.json'
    derived_request.write_text(json.dumps({'content': 'My interpretation needs provider A.'}))
    derived = successful(root, 'beta', 'retain', '--owner', 'alpha', '--id', original['id'],
                         '--revision', original['revision'], '--input', str(derived_request))
    update = tmp_path / 'update.json'
    update.write_text(json.dumps({'content': 'Any configured provider is allowed.'}))
    correction = successful(root, 'alpha', 'revise', '--id', original['id'], '--expected', original['revision'], '--input', str(update))
    assert correction['parents'] == [original['revision']]
    reviewed = successful(root, 'beta', 'get', '--owner', 'beta', '--id', derived['id'])
    assert reviewed['review_required'] is True
    related = successful(root, 'beta', 'related', '--owner', 'beta', '--id', derived['id'])
    assert related[0]['knowledge']['revision'] == original['revision']
    revoked = successful(root, 'alpha', 'revoke', '--id', original['id'], '--expected', correction['revision'])
    assert revoked['status'] == 'revoked'
    denied = invoke(root, 'beta', 'get', '--owner', 'alpha', '--id', original['id'], '--revision', original['revision'])
    assert denied.returncode == 1
    assert denied.stdout == ''


def test_cli_remembers_searches_and_denies_unpermitted_profile_with_clean_json(tmp_path):
    root = tmp_path / 'workspace'
    assert successful(root, 'alpha', 'init')['profile'] == 'alpha'
    successful(root, 'beta', 'init')
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'content': 'Configurable provider requirement.', 'kind': 'requirement',
                                  'project': 'demo', 'policy': {'read': ['alpha', 'beta']}}))
    record = successful(root, 'alpha', 'remember', '--input', str(request))
    got = successful(root, 'beta', 'get', '--owner', 'alpha', '--id', record['id'])
    assert got['revision'] == record['revision']
    response = successful(root, 'beta', 'search', 'provider', '--mode', 'keyword', '--project', 'demo')
    assert response['results'][0]['id'] == record['id']
    private_request = tmp_path / 'private.json'
    private_request.write_text(json.dumps({'content': 'PRIVATE-SENTINEL provider.'}))
    private = successful(root, 'alpha', 'remember', '--input', str(private_request))
    denied = invoke(root, 'beta', 'get', '--owner', 'alpha', '--id', private['id'])
    assert denied.returncode == 1
    assert denied.stdout == ''
    assert json.loads(denied.stderr)['error'] == 'AccessDenied'
    assert 'PRIVATE-SENTINEL' not in denied.stderr
