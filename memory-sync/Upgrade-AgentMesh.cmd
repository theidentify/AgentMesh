@echo off
setlocal
cd /d "%~dp0"
echo AgentMesh - upgrade the installed program and restart the worker from it
echo Requires an earlier Install-AgentMesh.cmd. DB, identity and runtime are not changed.
echo The current worker is asked to stop (no process is killed), then the new version starts.
echo Legacy unsigned policy is kept as-is.
"%~dp0agentmesh.exe" windows-upgrade --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --replace --legacy-drained --dry-run
if errorlevel 1 goto finish
"%~dp0agentmesh.exe" windows-upgrade --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --replace --legacy-drained
:finish
echo.
pause
endlocal
