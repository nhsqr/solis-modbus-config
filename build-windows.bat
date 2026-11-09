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
  echo ERROR: Python 3 not found. Install from https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv ...
  %PY_LAUNCH% -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv
    pause
    exit /b 1
  )
)

echo Installing dependencies + PyInstaller ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo Failed to install build dependencies.
  pause
  exit /b 1
)

echo Building SolisModbusConfig.exe (onefile) ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm solis-modbus-config.spec
if errorlevel 1 (
  echo PyInstaller build failed.
  pause
  exit /b 1
)

echo.
echo Done: dist\SolisModbusConfig.exe
echo.
pause
exit /b 0
