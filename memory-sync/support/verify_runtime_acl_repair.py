"""Native disposable verification of the narrow operator-confirmed ACL helper."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

source = Path(__file__).with_name('protect-runtime-acl.ps1').read_text()
encoded = base64.b64encode(source.encode('utf-16le')).decode()
command_line = '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoLogo -NoProfile -EncodedCommand ' + encoded
assert len(command_line) < 8191, len(command_line)
assert os.name == 'nt', 'real Windows verification required'
powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
env = {k:v for k,v in os.environ.items() if k.casefold() != 'psmodulepath'}
env['PSModulePath'] = str(powershell.parent / 'Modules')

def ps(script, input_text=None):
    args = [str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
            base64.b64encode(script.encode('utf-16le')).decode()]
    return subprocess.run(args, input=input_text, capture_output=True, text=True, env=env, timeout=60)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import windows_acl
with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as directory:
    root = Path(directory)
    env['LOCALAPPDATA'] = str(root)
    data = root / 'AgentMesh/data'
    data.mkdir(parents=True)
    runtime = data / 'runtime.json'
    runtime.write_text('{"fixture":"existing-runtime-bytes"}')
    db = data / 'windows.db'
    db.write_bytes(b'database-bytes-must-not-change')
    identity = root / 'AgentMesh/identity'
    identity.mkdir()
    key = identity / 'identity.json'
    key.write_bytes(b'fixture-key-bytes-must-not-change')
    baseline = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (runtime,db,key)}
    prepare = ps("$ErrorActionPreference='Stop';$p=Join-Path $env:LOCALAPPDATA 'AgentMesh\\data\\runtime.json';$a=Get-Acl -LiteralPath $p;$a.SetOwner([Security.Principal.WindowsIdentity]::GetCurrent().User);$a.SetAccessRuleProtection($false,$false);Set-Acl -LiteralPath $p -AclObject $a; (Get-Acl -LiteralPath $p).Sddl")
    assert prepare.returncode == 0, prepare.stderr
    sddl = prepare.stdout.strip()
    declined = ps(source, 'NO\n')
    assert declined.returncode == 2, (declined.stdout,declined.stderr)
    assert not (data / 'acl-repair-backups').exists()
    current = ps("(Get-Acl -LiteralPath (Join-Path $env:LOCALAPPDATA 'AgentMesh\\data\\runtime.json')).Sddl")
    assert current.stdout.strip() == sddl
    accepted = ps(source, 'PROTECT\n')
    assert accepted.returncode == 0 and 'SUCCESS:' in accepted.stdout, (accepted.stdout,accepted.stderr)
    report = windows_acl.apply(runtime)
    assert report['owner'] == report['sid'] and report['control'] & 0x1004 == 0x1004
    assert {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (runtime,db,key)} == baseline
    backups = list((data / 'acl-repair-backups').glob('*.json'))
    assert len(backups) == 1
    backup = json.loads(backups[0].read_text(encoding='utf-8-sig'))
    assert backup['sddl'] == sddl and backup['sha256'].lower() == baseline[str(runtime)]
    # The saved ACL is usable for permissions-only rollback; no DB restore.
    rollback = ps("$ErrorActionPreference='Stop';$p=Join-Path $env:LOCALAPPDATA 'AgentMesh\\data\\runtime.json';$b=Get-ChildItem -LiteralPath (Join-Path (Split-Path $p) 'acl-repair-backups');$j=Get-Content -LiteralPath $b.FullName -Raw|ConvertFrom-Json;$a=[Security.AccessControl.FileSecurity]::new();$a.SetSecurityDescriptorSddlForm($j.sddl);Set-Acl -LiteralPath $p -AclObject $a;(Get-Acl -LiteralPath $p).Sddl")
    assert rollback.returncode == 0 and rollback.stdout.strip() == sddl, rollback.stderr
    assert {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (runtime,db,key)} == baseline
print(json.dumps({'native_confirmation_cancel_protect_readback_acl_backup_rollback':'passed', 'runtime_db_key_bytes':'unchanged','cmd_line_length':len(command_line)}))
