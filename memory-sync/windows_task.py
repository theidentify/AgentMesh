"""Per-user logon launcher, not a worker supervisor. Native Windows only."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess

# Fixed code only: all names, bindings and compare-and-swap snapshots are data.
SCRIPT = r'''
$ErrorActionPreference = 'Stop'
try {
    $p = $env:AGENTMESH_TASK_PAYLOAD | ConvertFrom-Json
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $service.GetFolder('\')
    function ReadTask {
        try { $t = $folder.GetTask($p.name) } catch {
            if ($_.Exception.HResult -eq -2147024894) { return $null }
            throw
        }
        $d = $t.Definition
        $triggers = @(); foreach ($x in $d.Triggers) {
            $triggers += @{type=[int]$x.Type; user=$x.UserId; enabled=[bool]$x.Enabled; delay=$x.Delay; start=$x.StartBoundary; end=$x.EndBoundary; repeat=$x.Repetition.Interval; duration=$x.Repetition.Duration; stop=[bool]$x.Repetition.StopAtDurationEnd}
        }
        $actions = @(); foreach ($x in $d.Actions) {
            $actions += @{type=[int]$x.Type; path=$x.Path; arguments=$x.Arguments; directory=$x.WorkingDirectory}
        }
        $s = $d.Settings
        return @{sid=$sid; xml=$t.Xml; binding=@{marker=$d.RegistrationInfo.Description; user=$d.Principal.UserId; logon=[int]$d.Principal.LogonType; level=[int]$d.Principal.RunLevel; enabled=[bool]$s.Enabled; hidden=[bool]$s.Hidden; multiple=[int]$s.MultipleInstances; terminate=[bool]$s.AllowHardTerminate; restart=$s.RestartInterval; restart_count=[int]$s.RestartCount; battery_start=[bool]$s.DisallowStartIfOnBatteries; battery_stop=[bool]$s.StopIfGoingOnBatteries; limit=$s.ExecutionTimeLimit; demand=[bool]$s.AllowDemandStart; available=[bool]$s.StartWhenAvailable; idle=[bool]$s.RunOnlyIfIdle; network=[bool]$s.RunOnlyIfNetworkAvailable; wake=[bool]$s.WakeToRun; triggers=@($triggers); actions=@($actions)}}
    }
    $before = ReadTask
    if ($p.operation -ne 'read') {
        if ($sid -ne $p.sid) { throw 'SID changed' }
        if ($null -eq $p.expected) {
            if ($null -ne $before) { throw 'task already exists' }
        } elseif ($null -eq $before -or $before.xml -cne $p.expected) { throw 'task changed' }
        if ($p.operation -eq 'remove') { $folder.DeleteTask($p.name, 0) }
        elseif ($p.operation -eq 'register') {
            $b = $p.binding
            $d = $service.NewTask(0)
            $d.RegistrationInfo.Description = $b.marker
            $d.Principal.UserId = $sid
            $d.Principal.LogonType = 3
            $d.Principal.RunLevel = 0
            $t = $d.Triggers.Create(9)
            $t.UserId = $sid; $t.Enabled = $true; $t.Delay = 'PT0S'
            $a = $d.Actions.Create(0)
            $a.Path = $b.actions[0].path; $a.Arguments = $b.actions[0].arguments
            $a.WorkingDirectory = $b.actions[0].directory
            $s = $d.Settings
            $s.Enabled = $b.enabled; $s.Hidden = $false; $s.MultipleInstances = 2
            $s.AllowHardTerminate = $false; $s.RestartCount = 0
            # Leave RestartInterval absent: its legal minimum is one minute.
            # Zero count and no RestartOnFailure block mean no crash restart.
            $s.DisallowStartIfOnBatteries = $false; $s.StopIfGoingOnBatteries = $false
            $s.ExecutionTimeLimit = 'PT0S'; $s.AllowDemandStart = $false
            $s.StartWhenAvailable = $false; $s.RunOnlyIfIdle = $false
            $s.RunOnlyIfNetworkAvailable = $false; $s.WakeToRun = $false
            $flags = 2; if ($null -ne $before) { $flags = 4 }
            $null = $folder.RegisterTaskDefinition($p.name, $d, $flags, $sid, $null, 3, $null)
        } else { throw 'operation refused' }
    }
    @{sid=$sid; task=(ReadTask)} | ConvertTo-Json -Depth 10 -Compress
} catch { [Console]::Error.WriteLine('Scheduled Task operation refused'); exit 1 }
'''


def arguments(runtime, interval, timeout, legacy_drained):
    # subprocess.list2cmdline implements the Windows executable argument contract.
    args = ['worker-start', '--runtime', str(runtime), '--interval', str(interval), '--timeout', str(timeout)]
    if legacy_drained:
        args.append('--legacy-drained')
    return subprocess.list2cmdline(args)


def binding(sid, marker, binary, runtime, interval, timeout, legacy_drained, enabled):
    return dict(marker=marker, user=sid, logon=3, level=0, enabled=enabled,
                hidden=False, multiple=2, terminate=False, restart='', restart_count=0,
                battery_start=False, battery_stop=False, limit='PT0S', demand=False,
                available=False, idle=False, network=False, wake=False,
                triggers=[dict(type=9, user=sid, enabled=True, delay='PT0S', start='', end='', repeat='', duration='', stop=False)],
                actions=[dict(type=0, path=str(binary), arguments=arguments(runtime, interval, timeout, legacy_drained), directory=str(Path(binary).parent))])


class TaskAdapter:
    def __init__(self):
        if os.name != 'nt':
            raise ValueError('Windows installation requires native Windows')

    def call(self, name, operation='read', *, sid=None, expected=None, definition=None):
        if not re.fullmatch(r'AgentMesh-[A-Za-z0-9-]{1,100}', name):
            raise ValueError('invalid owned task name')
        system = os.environ.get('SystemRoot')
        if not system or not Path(system).is_absolute():
            raise ValueError('OS PowerShell unavailable')
        executable = Path(system) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        env = {k: v for k, v in os.environ.items() if k.casefold() not in ('psmodulepath', 'agentmesh_task_payload')}
        env['PSModulePath'] = str(executable.parent / 'Modules')
        env['AGENTMESH_TASK_PAYLOAD'] = json.dumps(dict(name=name, operation=operation, sid=sid, expected=expected, binding=definition))
        try:
            result = subprocess.run([str(executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                                     base64.b64encode(SCRIPT.encode('utf-16le')).decode('ascii')],
                                    env=env, text=True, capture_output=True, timeout=45, check=True)
            report = json.loads(result.stdout)
            if not re.fullmatch(r'S-1-5-21-(?:[0-9]+-){3}[0-9]+', report['sid']):
                raise ValueError('unsupported user SID')
            return report
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            raise ValueError('Scheduled Task could not be verified') from exc

    def read(self, name):
        return self.call(name)

    def register(self, name, sid, expected, definition):
        self.call(name, 'register', sid=sid, expected=expected, definition=definition)
        result = self.read(name)  # Independent exact-target readback, not API success.
        if result['sid'] != sid or result['task'] is None or result['task']['binding'] != definition:
            raise ValueError('Scheduled Task readback mismatch')
        return result['task']

    def remove(self, name, sid, expected):
        self.call(name, 'remove', sid=sid, expected=expected)
        result = self.read(name)
        if result['sid'] != sid or result['task'] is not None:
            raise ValueError('Scheduled Task removal readback mismatch')
