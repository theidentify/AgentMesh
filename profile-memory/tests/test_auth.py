"""Fail-closed selection without a fabricated identity backend."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from agentmesh_memory.auth import load_backend
from agentmesh_memory.core import KnowledgeError
from test_cli import invoke, successful


@pytest.mark.parametrize('path', ['relative.py', '/nonexistent/agentmesh-backend.py'])
def test_backend_must_be_explicit_absolute_existing_file(path):
    with pytest.raises(KnowledgeError):
        load_backend(path)


@pytest.mark.parametrize('missing', ['signed-backend', 'peer-profile', 'peer-fingerprint', 'peer-group', 'peer-node', 'peer-sender'])
def test_cli_incomplete_signed_options_fail_without_hmac_fallback(tmp_path, missing):
    root = tmp_path / 'cli'
    successful(root, 'alpha', 'init')
    options = dict([('security-dir', str(tmp_path / 'no-identity')), ('signed-backend', '/nonexistent/backend.py'),
        ('peer-profile', 'beta'), ('peer-fingerprint', 'f' * 64), ('peer-group', '00000000-0000-4000-8000-000000000001'),
        ('peer-node', 'linux'), ('peer-sender', '00000000-0000-4000-8000-000000000002')])
    del options[missing]
    args = [v for k, value in options.items() for v in ('--' + k, value)]
    result = invoke(root, 'alpha', 'export', '--recipient', 'beta', '--id', 'urn:uuid:00000000-0000-4000-8000-000000000003',
                    '--output', str(tmp_path / 'no-bundle.json'), *args)
    assert result.returncode == 1 and result.stdout == ''
    assert json.loads(result.stderr)['error'] == 'KnowledgeError'
    assert not (tmp_path / 'no-bundle.json').exists()


def test_cli_cannot_use_hmac_with_signed_options(tmp_path):
    root = tmp_path / 'cli'
    successful(root, 'alpha', 'init')
    result = invoke(root, 'alpha', 'export', '--recipient', 'beta', '--id', 'urn:uuid:00000000-0000-4000-8000-000000000003',
        '--output', str(tmp_path / 'no-bundle.json'), '--key-file', str(tmp_path / 'absent.key'), '--peer-profile', 'beta')
    assert result.returncode == 1 and result.stdout == ''
    assert 'no HMAC fallback' in json.loads(result.stderr)['message']


def test_cli_missing_trusted_backend_is_clean_json(tmp_path):
    root = tmp_path / 'cli'
    successful(root, 'alpha', 'init')
    result = invoke(root, 'alpha', 'export', '--recipient', 'beta',
        '--id', 'urn:uuid:00000000-0000-4000-8000-000000000003', '--output', str(tmp_path / 'no-bundle.json'),
        '--security-dir', str(tmp_path / 'no-identity'), '--signed-backend', '/nonexistent/backend.py',
        '--peer-profile', 'beta', '--peer-fingerprint', 'f' * 64,
        '--peer-group', '00000000-0000-4000-8000-000000000001', '--peer-node', 'linux',
        '--peer-sender', '00000000-0000-4000-8000-000000000002')
    assert result.returncode == 1 and result.stdout == ''
    assert json.loads(result.stderr)['error'] == 'KnowledgeError'


def test_real_backend_without_crypto_fails_closed():
    path = os.environ.get('AGENTMESH_SIGNED_BACKEND')
    if not path:
        pytest.skip('optional backend integration; set AGENTMESH_SIGNED_BACKEND')
    source = str(Path(__file__).resolve().parents[1])
    # -S disables site-packages: actual missing cryptography, not a mocked API.
    script = ('import sys; sys.path.insert(0, sys.argv[1]); '
              'from agentmesh_memory.auth import load_backend; load_backend(sys.argv[2])')
    result = subprocess.run([sys.executable, '-S', '-c', script, source, path], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and result.stdout == ''
    assert 'signed identity backend unavailable or incompatible' in result.stderr
