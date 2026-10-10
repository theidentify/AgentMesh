@echo off
setlocal
cd /d "%~dp0"
echo AgentMesh - stop the current worker and enable start at login
echo Requires an earlier Install-AgentMesh.cmd. DB, identity and runtime are not changed.
set "RT=%LOCALAPPDATA%\AgentMesh\data\runtime.json"
echo.
echo Step 1 of 2: ask the current worker to stop. No process is killed.
echo This can take up to 2 minutes while the current sync cycle finishes. Do not close this window.
echo Sync pauses until you sign out and sign back in.
choice /m "Stop the current worker now"
if errorlevel 2 goto finish
"%~dp0agentmesh.exe" worker-stop --runtime "%RT%" --timeout 120 >nul
if errorlevel 1 goto finish
echo.
echo Step 2 of 2: enable start at login (legacy unsigned policy is kept as-is).
"%~dp0agentmesh.exe" windows-autostart --runtime "%RT%" --enable --legacy-drained --gui >nul
if errorlevel 2 (echo Cancelled. Start at login was not changed; sign out and in, or run worker-start, to resume sync.& goto finish)
if errorlevel 1 goto finish
echo.
echo Done. Sign out of Windows and sign back in (no reboot needed),
echo wait about 2 minutes, then run Diagnose-AgentMesh.cmd.
:finish
echo.
pause
endlocal
