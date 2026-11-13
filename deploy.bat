@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" deploy_to_pi.py
pause
