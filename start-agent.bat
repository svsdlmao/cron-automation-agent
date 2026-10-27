@echo off
rem cron-agent for Windows: double-click to start.
rem First run creates .venv and installs everything; later runs start instantly.
setlocal
cd /d "%~dp0"
title cron-agent

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required: https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [setup] First run: creating .venv and installing cron-agent. This takes a few minutes...
  python -m venv .venv
  if errorlevel 1 ( echo Could not create the virtual environment. & pause & exit /b 1 )
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -e ".[dev]"
  if errorlevel 1 ( echo Installing the dependencies failed. & pause & exit /b 1 )
)

rem Windows has no cron, so jobs go in a file crontab and our own daemon runs them.
set "CRON_AGENT_BACKEND=file"
if not defined CRON_AGENT_SCRIPT_DIRS set "CRON_AGENT_SCRIPT_DIRS=%~dp0demo\scripts;%USERPROFILE%\scripts;%USERPROFILE%\bin"

if not "%CRON_AGENT_NO_DAEMON%"=="1" (
  start "cron-agent daemon (close to stop scheduled jobs)" cmd /k ""%~dp0.venv\Scripts\python.exe" -m cron_agent daemon"
)

".venv\Scripts\python.exe" -m cron_agent %*

echo.
echo The "cron-agent daemon" window keeps running your jobs. Close it to stop them.
pause
