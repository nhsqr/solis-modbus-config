#!/bin/bash
# Double-click launcher for macOS (Finder → Start.command)
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  osascript -e 'display dialog "Python 3 was not found.\n\nInstall from https://www.python.org/downloads/ or: brew install python" buttons {"OK"} default button 1 with title "Solis Modbus Config"'
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment in .venv ..."
  "$PY" -m venv .venv
fi

echo "Installing / updating dependencies ..."
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt

if ! .venv/bin/python -m app; then
  echo
  echo "Application exited with an error."
  read -r -p "Press Enter to close..."
  exit 1
fi
