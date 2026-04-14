#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This script must run on Linux."
  exit 1
fi

echo "Building Voxify Linux artifact..."

python3 -m pip install pyinstaller -q

python3 -m PyInstaller \
  --onefile \
  --name "Voxify" \
  --add-data "config.json:." \
  --add-data "assets/voxify-logo.png:assets" \
  --collect-all flet \
  --collect-all flet_desktop \
  --hidden-import flet_settings \
  --hidden-import flet_desktop \
  --hidden-import customtkinter \
  --hidden-import sounddevice \
  --hidden-import soundcard \
  --hidden-import scipy \
  --hidden-import numpy \
  --hidden-import pyperclip \
  --hidden-import pyautogui \
  --hidden-import requests \
  app.py

python3 package_release.py --platform linux

echo "Done. Check dist/ and release/."
