@echo off
setlocal
cd /d "%~dp0"
echo AgentMesh - install program files for an existing Windows installation
echo This copies the program only. It does not enable start at login or restart a worker.
echo Uses %%LOCALAPPDATA%%\AgentMesh\data\runtime.json; a missing or unsafe runtime is refused.
echo Copying and verifying can take about a minute. Do not close this window.
"%~dp0agentmesh.exe" windows-install --runtime "%LOCALAPPDATA%\AgentMesh\data\runtime.json" --gui >nul
if errorlevel 2 (echo Cancelled. Nothing was changed.& goto finish)
if errorlevel 1 goto finish
echo.
echo Installed. Next: Enable-AgentMesh-Autostart.cmd to start the worker at sign-in.
:finish
echo.
pause
endlocal
