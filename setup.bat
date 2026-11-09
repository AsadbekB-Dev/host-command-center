@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ===============================================
echo   Host Command Center v0.4 - Setup
echo ===============================================

where py >nul 2>&1
if not errorlevel 1 (
  set "PY=py -3"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Python 3 was not found.
    echo Install Python 3.11+ from python.org and enable "Add Python to PATH".
    pause
    exit /b 1
  )
  set "PY=python"
)

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
  echo Python 3.11 or newer is required.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating private Python environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv
    pause
    exit /b 1
  )
)

if not exist "data\outbox" mkdir "data\outbox"
if not exist "data\webhook_inbox" mkdir "data\webhook_inbox"
if not exist "data\webhook_processed" mkdir "data\webhook_processed"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $desk=[Environment]::GetFolderPath('Desktop'); $s=$ws.CreateShortcut((Join-Path $desk 'Host Command Center.lnk')); $s.TargetPath=(Join-Path '%~dp0' 'launch.vbs'); $s.WorkingDirectory='%~dp0'; $s.Description='Host Command Center'; $s.IconLocation=(Join-Path '%~dp0' 'assets\host_command_center.ico'); $s.Save()"

if errorlevel 1 (
  echo Warning: Desktop shortcut could not be created.
) else (
  echo Desktop shortcut created.
)

echo.
echo Setup complete. Launch "Host Command Center" from your desktop.
echo Live Shadow Mode can read/sync and send host emails. Remote write actions remain locked OFF.
pause
endlocal
