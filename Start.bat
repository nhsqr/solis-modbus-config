@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "PY_LAUNCH="
where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
  set "PY_LAUNCH=py -3"
) else (
  where python >nul 2>&1
  if %ERRORLEVEL% equ 0 (
    set "PY_LAUNCH=python"
  )
)

if not defined PY_LAUNCH (
  echo.
  echo ERROR: Python 3 was not found on PATH.
  echo.
  echo Install Python 3 from https://www.python.org/downloads/
  echo During setup, enable "Add python.exe to PATH".
  echo Then double-click Start.bat again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment in .venv ...
  %PY_LAUNCH% -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv
    pause
    exit /b 1
  )
)

echo Installing / updating dependencies ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install requirements.
  pause
  exit /b 1
)

if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m app
  exit /b 0
)

".venv\Scripts\python.exe" -m app
if errorlevel 1 (
  echo.
  echo Application exited with an error.
  pause
  exit /b 1
)
exit /b 0
