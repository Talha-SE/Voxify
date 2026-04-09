@echo off
echo Building SONUS standalone EXE...
echo.

python -m pip install pyinstaller -q

python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "SONUS" ^
    --add-data "config.json;." ^
    --collect-all flet ^
    --hidden-import flet_settings ^
    --hidden-import customtkinter ^
    --hidden-import sounddevice ^
    --hidden-import soundcard ^
    --hidden-import scipy ^
    --hidden-import numpy ^
    --hidden-import pyperclip ^
    --hidden-import pyautogui ^
    --hidden-import requests ^
    app.py

echo.
echo Done! Executable is in: dist\SONUS.exe
pause
