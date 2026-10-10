@echo off
setlocal
cd /d "%~dp0"
echo AgentMesh RC.5 - existing Windows installation only
echo This installs program files only. It does not enable logon startup or restart a worker.
echo The runtime path below is a candidate; a missing or unsafe runtime is refused.
"%~dp0agentmesh.exe" windows-install --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --dry-run
if errorlevel 1 goto finish
"%~dp0agentmesh.exe" windows-install --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json"
:finish
echo.
pause
endlocal
