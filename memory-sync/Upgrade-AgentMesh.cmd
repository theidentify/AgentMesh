@echo off
setlocal
cd /d "%~dp0"
echo AgentMesh - upgrade the installed program and restart the worker from it
echo Requires an earlier Install-AgentMesh.cmd. DB, identity and runtime are not changed.
echo The current worker is asked to stop (no process is killed), then the new version starts.
echo After you confirm, this can take up to about 5 minutes. Do not close this window.
"%~dp0agentmesh.exe" windows-upgrade --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --replace --legacy-drained >nul
if errorlevel 2 (echo Cancelled. The installed version and worker were not changed.& goto finish)
if errorlevel 1 goto finish
echo.
echo Upgrade finished. Run Diagnose-AgentMesh.cmd to confirm the worker is healthy.
:finish
echo.
pause
endlocal
