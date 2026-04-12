@echo off
echo Building Voxify standalone EXE...
echo.

if exist dist\SONUS.exe del /q dist\SONUS.exe
if exist dist\BreviosChipVoxtral.exe del /q dist\BreviosChipVoxtral.exe

python -m pip install pyinstaller -q

python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "Voxify" ^
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
echo Done! Executable is in: dist\Voxify.exe
pause
