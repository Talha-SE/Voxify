#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script must run on macOS (Darwin)."
  exit 1
fi

echo "Building Voxify macOS artifact..."

python3 -m pip install pyinstaller -q

python3 -m PyInstaller \
  --onefile \
  --windowed \
  --name "Voxify" \
  --add-data "config.json:." \
  --add-data "assets/voxify-logo.png:assets" \
  --additional-hooks-dir "pyinstaller-hooks" \
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
  --hidden-import mistralai \
  --hidden-import mistralai.client \
  --hidden-import mistralai.client.models \
  --hidden-import mistralai.extra.realtime \
  app.py

if [[ -n "${APPLE_CODESIGN_IDENTITY:-}" ]]; then
  echo "Signing macOS artifact with identity: ${APPLE_CODESIGN_IDENTITY}"
  if [[ -d "dist/Voxify.app" ]]; then
    codesign --force --deep --options runtime --sign "$APPLE_CODESIGN_IDENTITY" "dist/Voxify.app"
  elif [[ -f "dist/Voxify" ]]; then
    codesign --force --options runtime --sign "$APPLE_CODESIGN_IDENTITY" "dist/Voxify"
  fi
fi

python3 package_release.py --platform macos

echo "Done. Check dist/ and release/."
