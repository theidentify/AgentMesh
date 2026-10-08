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


def test_cli_demo_exercises_isolated_exchange_and_refuses_reusing_workspace(tmp_path):
    root = tmp_path / 'demo'
    result = successful(root, 'alpha', 'demo')
    assert result['checks'] and all(result['checks'].values())
    assert result['retrieval']['keyword-exact']['top1_correct'] is True
    assert not (root / 'receiver-node' / 'profiles' / 'alpha').exists()
    assert invoke(root, 'alpha', 'demo').returncode == 1


def test_cli_authorized_exchange_uses_separate_mirror_and_replay_receipts(tmp_path):
    source_root, receiver_root = tmp_path / 'source-node', tmp_path / 'receiver-node'
    successful(source_root, 'alpha', 'init')
    successful(receiver_root, 'beta', 'init')
    key_path = tmp_path / 'channel.key'
    successful(source_root, 'alpha', 'keygen', '--output', str(key_path))
    assert len(key_path.read_bytes()) == 32
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'content': 'Portable provider rule.',
                                  'policy': {'read': ['alpha', 'beta'], 'export': ['beta']}}))
    original = successful(source_root, 'alpha', 'remember', '--input', str(request))
    output = tmp_path / 'bundle.json'
    exported = successful(source_root, 'alpha', 'export', '--recipient', 'beta', '--id', original['id'],
                          '--key-file', str(key_path), '--output', str(output))
    assert exported['records'] == 1
    result = successful(receiver_root, 'beta', 'apply', '--issuer', 'alpha', '--key-file', str(key_path), '--input', str(output))
    assert result['status'] == 'applied'
    replay = successful(receiver_root, 'beta', 'apply', '--issuer', 'alpha', '--key-file', str(key_path), '--input', str(output))
    assert replay['status'] == 'duplicate'
    readback = successful(receiver_root, 'beta', 'get', '--owner', 'alpha', '--id', original['id'])
    assert readback['revision'] == original['revision']
    assert readback['owner'] == 'alpha'
    status = successful(receiver_root, 'beta', 'status', '--issuer', 'alpha')
    assert status['pending'] == 0
    assert status['receipts'] == {'applied': 1}
    assert successful(receiver_root, 'beta', 'retry', '--issuer', 'alpha', '--key-file', str(key_path)) == []
    assert not (receiver_root / 'profiles' / 'alpha').exists()
    assert (receiver_root / 'mirrors' / 'beta' / 'alpha' / 'knowledge' / 'knowledge.sqlite3').exists()
    assert 'content' not in status
    again = invoke(source_root, 'alpha', 'keygen', '--output', str(key_path))
    assert again.returncode == 1


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
        partition = successful(root, 'beta', 'index', '--issuer', 'alpha', '--endpoint', url)
        assert partition['indexed'] == 1
        assert (root / 'profiles' / 'alpha' / 'indexes' / 'vectors-beta.sqlite3').exists()
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
