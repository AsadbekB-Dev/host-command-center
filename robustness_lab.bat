@echo off
cd /d "%~dp0"
py robustness_lab.py
if errorlevel 1 (
  echo.
  echo Robustness lab FAILED.
) else (
  echo.
  echo Robustness lab PASSED.
)
pause
