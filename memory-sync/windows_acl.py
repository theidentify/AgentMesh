"""Fail-closed Windows private-storage ACL adapter (no localized command output).

Only newly created objects may be provisioned. Existing identities are verified,
not silently repaired. Native Windows execution requires an operator host gate.
"""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import re
import subprocess

# Path and operation are data in the child environment, never PowerShell source.
SCRIPT = r'''
$ErrorActionPreference = 'Stop'
try {
    $path = $env:AGENTMESH_ACL_PATH
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $item = Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'reparse point' }
    if ($env:AGENTMESH_ACL_OPERATION -eq 'provision') {
        if ($item.PSIsContainer) {
            $acl = [System.Security.AccessControl.DirectorySecurity]::new()
            $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
        } else {
            $acl = [System.Security.AccessControl.FileSecurity]::new()
            $inherit = [System.Security.AccessControl.InheritanceFlags]::None
        }
        $acl.SetOwner($sid)
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($principal in @($sid, [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
            $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($principal, [System.Security.AccessControl.FileSystemRights]::FullControl, $inherit, [System.Security.AccessControl.PropagationFlags]::None, [System.Security.AccessControl.AccessControlType]::Allow)
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $path -AclObject $acl
    } elseif ($env:AGENTMESH_ACL_OPERATION -ne 'verify') { throw 'invalid operation' }
    $acl = Get-Acl -LiteralPath $path
    $raw = [System.Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
    $rules = @()
    foreach ($ace in $raw.DiscretionaryAcl) {
        # Reject callback/object/conditional and other unsupported ACEs.
        if ($ace -isnot [System.Security.AccessControl.CommonAce] -or $ace.IsCallback) { throw 'unsupported ACE' }
        $rules += @{sid=$ace.SecurityIdentifier.Value; type=[int]$ace.AceType; flags=[int]$ace.AceFlags; mask=[int]$ace.AccessMask}
    }
    @{sid=$sid.Value; owner=$raw.Owner.Value; control=[int]$raw.ControlFlags; directory=[bool]$item.PSIsContainer; rules=@($rules)} | ConvertTo-Json -Depth 5 -Compress
} catch { [Console]::Error.WriteLine('Private ACL unavailable or unsupported'); exit 1 }
'''
FULL_CONTROL = 0x1f01ff
SYSTEM = 'S-1-5-18'


def validate(report, *, directory):
    """Accept only protected, explicit user/SYSTEM grants and current-user owner."""
    if type(report) is not dict or set(report) != {'sid', 'owner', 'control', 'directory', 'rules'}:
        raise ValueError('unverifiable Windows ACL')
    sid = report['sid']
    if type(sid) is not str or not re.fullmatch(r'S-1-5-21-(?:[0-9]+-){3}[0-9]+', sid):
        raise ValueError('unsupported Windows user SID')
    if report['owner'] != sid or type(report['directory']) is not bool or report['directory'] != directory:
        raise ValueError('Windows private owner/type mismatch')
    control = report['control']
    # DaclPresent and DaclProtected; reject null, inherited/unprotected DACLs.
    if type(control) is not int or control & 0x1004 != 0x1004:
        raise ValueError('Windows private ACL must be protected')
    if type(report['rules']) is not list or not report['rules']:
        raise ValueError('Windows private ACL has no explicit grants')
    user_full = False
    for ace in report['rules']:
        if type(ace) is not dict or set(ace) != {'sid', 'type', 'flags', 'mask'}:
            raise ValueError('unsupported Windows ACE')
        if ace['sid'] not in (sid, SYSTEM) or type(ace['type']) is not int or ace['type'] != 0:
            raise ValueError('unapproved Windows ACL principal or ACE type')
        # Only object/container inheritance, no inherited or inherit-only ACEs.
        if type(ace['flags']) is not int or ace['flags'] not in ((0, 3) if directory else (0,)):
            raise ValueError('unsupported or inherited Windows ACE')
        if type(ace['mask']) is not int or not 0 < ace['mask'] <= FULL_CONTROL or ace['mask'] & ~FULL_CONTROL:
            raise ValueError('unsupported Windows ACL rights')
        user_full |= ace['sid'] == sid and ace['mask'] == FULL_CONTROL
    if not user_full: raise ValueError('Windows private ACL requires user full control')
    return report


def apply(path, *, provision=False):
    if os.name != 'nt': raise ValueError('native Windows ACL adapter requires Windows')
    path = Path(path)
    system_root = os.environ.get('SystemRoot')
    if not system_root or not Path(system_root).is_absolute(): raise ValueError('Windows PowerShell unavailable')
    executable = Path(system_root) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    env = {key: value for key, value in os.environ.items() if key.casefold() != 'psmodulepath'}
    # The caller may be PowerShell 7; its modules cannot load in fixed Windows
    # PowerShell 5.1. Only search the OS-owned legacy module directory.
    env.update(AGENTMESH_ACL_PATH=str(path), AGENTMESH_ACL_OPERATION='provision' if provision else 'verify',
               PSModulePath=str(executable.parent / 'Modules'))
    encoded = base64.b64encode(SCRIPT.encode('utf-16le')).decode('ascii')
    try:
        result = subprocess.run([str(executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                env=env, capture_output=True, text=True, timeout=30, check=True)
        report = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError('Windows private ACL could not be verified') from exc
    return validate(report, directory=path.is_dir())
