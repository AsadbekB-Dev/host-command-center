@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" start_webhook_dev.py
) else (
  python start_webhook_dev.py
)
pause
