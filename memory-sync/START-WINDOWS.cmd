@echo off
setlocal
echo AgentMesh - checking Python, then starting installation...
cd /d "%~dp0"
py -3 --version >nul 2>&1
if %errorlevel% equ 0 goto run_py
python --version >nul 2>&1
if %errorlevel% equ 0 goto run_python
echo Python 3.10 or newer is required. Install Python and run this file again.
pause
exit /b 1
:run_py
py -3 -u "%~dp0bootstrap_windows.py" --exchange "%~dp0."
goto finish
:run_python
python -u "%~dp0bootstrap_windows.py" --exchange "%~dp0."
:finish
pause
