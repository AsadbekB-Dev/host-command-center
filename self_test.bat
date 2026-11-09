@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" self_test.py
) else (
  python self_test.py
)
pause
