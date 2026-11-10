"""Task Scheduler backend: observe and control AgentMesh's scheduled tasks, the Windows counterpart of LaunchdBackend.

Every call is one `powershell -NoProfile -NonInteractive` run of a fixed script. Task names, folders and XML reach the
script through environment variables and stdin, never through the script text, so nothing a task is called can become
code. Output is UTF-8 JSON ([Console]::OutputEncoding), so a non-ASCII path survives the console code page.

Only tasks whose action mentions agentmesh are listed, and with `folder` only those in that Task Scheduler folder: a test
home passes its own folder and never sees the real install's tasks.
"""
import json
import os
import subprocess
import time
from . import wintasks

POWERSHELL=('powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-Command')
PRELUDE='$ErrorActionPreference="Stop"; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); '
LIST=PRELUDE+r'''
$folder=$env:AGENTMESH_TASK_FOLDER
$rows=@(Get-ScheduledTask | Where-Object {
    (-not $folder -or $_.TaskPath -eq $folder) -and
    ($_.Actions | Where-Object { ("" + $_.Execute + " " + $_.Arguments) -match "agentmesh" })
} | ForEach-Object {
    [pscustomobject]@{ path=$_.TaskPath; name=$_.TaskName; state=[string]$_.State;
        xml=[string](Export-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath) }
})
ConvertTo-Json -InputObject $rows -Depth 3 -Compress
'''
ONE=PRELUDE+'$t=Get-ScheduledTask -TaskName $env:AGENTMESH_TASK_NAME -TaskPath $env:AGENTMESH_TASK_PATH; '
ACTIONS={
    'enable':ONE+'Enable-ScheduledTask -InputObject $t | Out-Null',
    'disable':ONE+'Disable-ScheduledTask -InputObject $t | Out-Null',
    'start':ONE+'Start-ScheduledTask -InputObject $t',
    'stop':ONE+'Stop-ScheduledTask -InputObject $t',
    'delete':ONE+'Unregister-ScheduledTask -InputObject $t -Confirm:$false',
    'register':PRELUDE+'$xml=[Console]::In.ReadToEnd(); Register-ScheduledTask -Xml $xml -TaskName $env:AGENTMESH_TASK_NAME '
        '-TaskPath $env:AGENTMESH_TASK_PATH | Out-Null',
}
CACHE_S=10
# A task registered from an elevated window refuses Enable/Disable/Unregister to the user's normal token, while Start and
# Stop still work. PowerShell prints the failure with FullyQualifiedErrorId "HRESULT 0x80070005" (E_ACCESSDENIED); the
# message text is localized, so the HRESULT is what counts, the English text a fallback.
DENIED=(b'0x80070005',b'access is denied',b'e_accessdenied')
ELEVATE=('Task Scheduler refused access: a task registered from an elevated window can only be disabled or deleted from '
    'one. Re-run this command from an elevated PowerShell (Run as administrator).')


def denied(process):
    text=(process.stderr or b'')+b'\n'+(process.stdout or b'')
    return any(marker in text.lower() for marker in DENIED)


def run(argv, *, env=None, input=None):
    return subprocess.run(argv,capture_output=True,env=env,input=input,timeout=120)


def split(full):
    """\\Folder\\Name -> ('\\Folder\\', 'Name'); Task Scheduler paths always end in a backslash."""
    full=full if full.startswith('\\') else '\\'+full
    folder,_,name=full.rpartition('\\')
    return folder+'\\',name


class TaskSchedulerBackend:
    def __init__(self, runner=run, folder=None, clock=time.monotonic):
        self.runner,self.folder,self.clock=runner,folder,clock
        self.cached,self.cached_at=None,None

    def powershell(self, script, task=None, data=None):
        env=dict(os.environ)
        if self.folder: env['AGENTMESH_TASK_FOLDER']=self.folder
        if task is not None: env['AGENTMESH_TASK_PATH'],env['AGENTMESH_TASK_NAME']=split(task)
        try: process=self.runner([*POWERSHELL,script],env=env,
                                 input=data.encode('utf-8') if data is not None else None)
        except (OSError,subprocess.SubprocessError): raise RuntimeError('task_scheduler_failed') from None
        if process.returncode!=0: raise RuntimeError('needs_elevation' if denied(process) else 'task_scheduler_failed')
        return process.stdout

    def tasks(self, fresh=False):
        """[{task, state, xml, unit}] for every AgentMesh task; `unit` is None for a task that runs something else."""
        if not fresh and self.cached is not None and self.clock()-self.cached_at<CACHE_S: return self.cached
        raw=self.powershell(LIST)
        rows=json.loads(raw.decode('utf-8-sig') or '[]') if raw.strip() else []
        if isinstance(rows,dict): rows=[rows]
        found=[]
        for row in rows:
            task=row['path']+row['name']
            try: unit=wintasks.unit_of(wintasks.command(wintasks.parse(row['xml']))[0])
            except (ValueError,KeyError,TypeError,SyntaxError): unit=None
            found.append(dict(task=task,state=row.get('state'),xml=row['xml'],unit=unit))
        self.cached,self.cached_at=found,self.clock()
        return found

    def act(self, action, task, xml=None):
        self.powershell(ACTIONS[action],task,xml); self.cached=None
        return True

    def find(self, unit):
        """The one task that runs unit, or None (none, or more than one, which only the owner can sort out)."""
        rows=[row for row in self.tasks() if row['unit']==unit.name]
        return rows[0] if len(rows)==1 else None

    # The Router's backend interface, for a unit that is not native yet.
    def status(self, unit):
        try: row=self.find(unit)
        except RuntimeError: row=None
        loaded=row is not None and row['state'] not in (None,'Disabled')
        return dict(loaded=loaded,running=row is not None and row['state']=='Running',pid=None,last_exit=None,runtime_s=None)

    def _on(self, action, unit):
        row=self.find(unit)
        if row is None: raise RuntimeError('task_not_found')
        return self.act(action,row['task'])

    def start(self, unit): return self._on('start',unit)
    def stop(self, unit): return self._on('stop',unit)

    def restart(self, unit):
        self._on('stop',unit); return self._on('start',unit)
