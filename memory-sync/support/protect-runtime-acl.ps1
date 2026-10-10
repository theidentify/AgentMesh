$ErrorActionPreference='Stop'
try {
 $p=Join-Path $env:LOCALAPPDATA 'AgentMesh\data\runtime.json'
 $i=Get-Item -LiteralPath $p -Force
 if($i.PSIsContainer){throw 'Runtime is not a file'}
 if($i.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse path refused'}
 for($d=$i.Directory;$null -ne $d;$d=$d.Parent){if($d.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse path refused'}}
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
 $old=Get-Acl -LiteralPath $p
 if($old.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value){throw 'Runtime owner is another account; no ownership takeover'}
 $sddl=$old.Sddl
 $hash=(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash
 Write-Host 'Protect ONLY existing runtime.json: current user and SYSTEM full control.'
 Write-Host 'No database, key, file content or worker will be changed.'
 Write-Host $p
 Write-Host 'Type PROTECT to confirm:'
 if([Console]::ReadLine() -cne 'PROTECT'){Write-Host 'Cancelled; no changes';exit 2}
 if((Get-Acl -LiteralPath $p).Sddl -ne $sddl -or (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash -ne $hash){throw 'Runtime changed; no permission change'}
 $dir=Join-Path $i.Directory.FullName 'acl-repair-backups'
 if(Test-Path -LiteralPath $dir){$b=Get-Item -LiteralPath $dir -Force;if(!$b.PSIsContainer -or ($b.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Unsafe backup directory'}}else{New-Item -ItemType Directory -Path $dir | Out-Null}
 $backup=Join-Path $dir (([guid]::NewGuid().ToString())+'.json')
 @{path=$p;sddl=$sddl;sha256=$hash}|ConvertTo-Json|Set-Content -LiteralPath $backup -Encoding UTF8
 $acl=[Security.AccessControl.FileSecurity]::new()
 $acl.SetOwner($sid)
 $acl.SetAccessRuleProtection($true,$false)
 foreach($who in @($sid,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'))){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($who,'FullControl','Allow'))}
 try {
  Set-Acl -LiteralPath $p -AclObject $acl
  $check=Get-Acl -LiteralPath $p
  if(!$check.AreAccessRulesProtected -or $check.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value){throw 'ACL verification failed'}
  $rules=@($check.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
  if($rules.Count -ne 2){throw 'Unexpected ACL grants'}
  foreach($r in $rules){if($r.IsInherited -or $r.IdentityReference.Value -notin @($sid.Value,'S-1-5-18') -or $r.AccessControlType -ne 'Allow' -or $r.FileSystemRights -ne 'FullControl'){throw 'Unexpected ACL rights'}}
  if((Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash -ne $hash){throw 'Runtime bytes changed'}
 } catch {Set-Acl -LiteralPath $p -AclObject $old;throw}
 Write-Host 'SUCCESS: runtime ACL protected; file content unchanged.'
 Write-Host 'Previous ACL backup:' $backup
 Write-Host 'Now open Diagnose-AgentMesh.cmd from your extracted RC.2 folder.'
} catch {Write-Host ('BLOCKED: '+$_.Exception.Message);exit 1}
