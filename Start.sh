#!/bin/bash
# Double-click / Terminal launcher for Linux (Zorin, Ubuntu, Debian, …)
set -e
cd "$(dirname "$0")"

need_pkgs=()
if ! command -v python3 >/dev/null 2>&1; then
  need_pkgs+=(python3)
fi
if ! python3 -c "import venv" 2>/dev/null; then
  need_pkgs+=(python3-venv)
fi
if ! python3 -c "import tkinter" 2>/dev/null; then
  need_pkgs+=(python3-tk)
fi

if [ ${#need_pkgs[@]} -gt 0 ]; then
  echo "Missing packages: ${need_pkgs[*]}"
  echo "Install with:"
  echo "  sudo apt-get update && sudo apt-get install -y ${need_pkgs[*]}"
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="Solis Modbus Config" \
      --text="Missing: ${need_pkgs[*]}\n\nRun in Terminal:\nsudo apt-get install -y ${need_pkgs[*]}" || true
  fi
  read -r -p "Press Enter to close..."
  exit 1
fi

PY=python3

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
