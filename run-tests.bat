@echo off
rem Runs the automated test suite (run start-agent.bat once first to set up .venv).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pytest -q
pause
