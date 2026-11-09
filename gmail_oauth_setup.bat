@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag your Google OAuth client_secret JSON file onto this BAT file,
  echo or run: gmail_oauth_setup.bat path\to\client_secret.json
  pause
  exit /b 1
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m providers.gmail_oauth_setup "%~1" --out "data\gmail_auth.json"
) else (
  python -m providers.gmail_oauth_setup "%~1" --out "data\gmail_auth.json"
)
pause
endlocal
