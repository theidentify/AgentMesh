"""Native Windows ACL provisioning against disposable storage only."""
import base64
import json
import os
from pathlib import Path
import subprocess
import time

import pytest
import windows_acl

native = pytest.mark.skipif(os.name != 'nt', reason='requires native Windows ACLs')

# Test-only reference: the former PowerShell/.NET projection (verify only), used to
# prove the in-process decoder reports the same owner, DACL and kind.
REFERENCE = r'''
$ErrorActionPreference = 'Stop'
$path = $env:AGENTMESH_ACL_PATH
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$item = Get-Item -LiteralPath $path -Force
$acl = Get-Acl -LiteralPath $path
$raw = [System.Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
$rules = @()
foreach ($ace in $raw.DiscretionaryAcl) {
    $rules += @{sid=$ace.SecurityIdentifier.Value; type=[int]$ace.AceType; flags=[int]$ace.AceFlags; mask=[int]$ace.AccessMask}
}
@{sid=$sid.Value; owner=$raw.Owner.Value; control=[int]$raw.ControlFlags; directory=[bool]$item.PSIsContainer; rules=@($rules)} | ConvertTo-Json -Depth 5 -Compress
'''
# DaclPresent, DaclDefaulted, DaclAutoInheritRequired, DaclAutoInherited, DaclProtected.
DACL_CONTROL = 0x150c


def reference(path):
    executable = Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    env = {key: value for key, value in os.environ.items() if key.casefold() != 'psmodulepath'}
    env.update(AGENTMESH_ACL_PATH=str(path), PSModulePath=str(executable.parent / 'Modules'))
    result = subprocess.run([str(executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                             base64.b64encode(REFERENCE.encode('utf-16le')).decode('ascii')],
                            env=env, text=True, capture_output=True, timeout=60, check=True)
    return json.loads(result.stdout)


def projection(report):
    rules = report['rules'] if type(report['rules']) is list else [report['rules']]
    return {**report, 'control': report['control'] & DACL_CONTROL,
            'rules': sorted(rules, key=lambda rule: json.dumps(rule, sort_keys=True))}


def describe(error):
    chain = []
    while error is not None:
        chain.append(type(error).__name__ + ': ' + str(error))
        error = error.__cause__
    return ' <- '.join(chain)


@native
@pytest.mark.parametrize('directory', [True, False])
def test_native_private_acl_provision(tmp_path, directory):
    private = tmp_path / 'private'
    private.mkdir() if directory else private.write_bytes(b'disposable')
    try:
        windows_acl.apply(private, provision=True)
    except ValueError as exc:
        # CI-only diagnostic: no keys or real deployment paths are involved.
        pytest.fail('Disposable native ACL diagnostic: ' + describe(exc))
    assert windows_acl.apply(private)['directory'] is directory


@native
@pytest.mark.parametrize('provision', [True, False])
@pytest.mark.parametrize('directory', [True, False])
def test_native_report_matches_powershell_reference(tmp_path, directory, provision):
    target = tmp_path / 'target'
    target.mkdir() if directory else target.write_bytes(b'disposable')
    if provision: windows_acl.apply(target, provision=True)
    # Unprovisioned targets carry inherited ACEs: compared, never accepted.
    assert projection(windows_acl.native_report(str(target), False)) == projection(reference(target))
    if not provision:
        with pytest.raises(ValueError): windows_acl.apply(target)


@native
def test_native_verification_is_in_process_and_fast(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    windows_acl.apply(private, provision=True)
    started = time.perf_counter()
    for _ in range(50): windows_acl.apply(private)
    elapsed = time.perf_counter() - started
    print(f'50 native ACL verifications: {elapsed:.3f}s')
    # One PowerShell child per check took seconds; generous bound for loaded CI hosts.
    assert elapsed < 5
