"""Native Windows ACL provisioning against disposable storage only."""
import base64
import os
from pathlib import Path
import subprocess

import pytest
import windows_acl


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows ACLs')
def test_native_private_acl_provision(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    try:
        windows_acl.apply(private, provision=True)
    except ValueError:
        # CI-only diagnostic: no keys or real deployment paths are involved.
        diagnostic = windows_acl.SCRIPT.replace(
            "[Console]::Error.WriteLine('Private ACL unavailable or unsupported')",
            "[Console]::Error.WriteLine($_.Exception.Message + ' at line ' + $_.InvocationInfo.ScriptLineNumber)")
        env = dict(os.environ, AGENTMESH_ACL_PATH=str(private), AGENTMESH_ACL_OPERATION='provision')
        executable = Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        result = subprocess.run([str(executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                                 base64.b64encode(diagnostic.encode('utf-16le')).decode('ascii')],
                                env=env, text=True, capture_output=True, timeout=30)
        pytest.fail('Disposable native ACL diagnostic: ' + result.stderr)
    windows_acl.apply(private)
