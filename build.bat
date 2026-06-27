@echo off
echo Building Voxify standalone EXE...
echo.

if exist dist\Voxify.exe del /q dist\Voxify.exe

where python3.14 >nul 2>&1
if errorlevel 1 (
    set PY=python
) else (
    set PY=python3.14
)

%PY% -m pip install pyinstaller -q

%PY% -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "Voxify" ^
    --add-data "config.json;." ^
    --add-data "assets\voxify-logo.png;assets" ^
    --additional-hooks-dir "pyinstaller-hooks" ^
    --hidden-import flet_settings ^
    --hidden-import flet_desktop ^
    --hidden-import customtkinter ^
    --hidden-import sounddevice ^
    --hidden-import soundcard ^
    --hidden-import scipy ^
    --hidden-import numpy ^
    --hidden-import pyperclip ^
    --hidden-import pyautogui ^
    --hidden-import mss ^
    --hidden-import requests ^
    --hidden-import mistralai ^
    --hidden-import mistralai.client ^
    --hidden-import mistralai.client.models ^
    --hidden-import mistralai.extra.realtime ^
    app.py

if errorlevel 1 goto :build_failed

if not "%SIGN_PFX_PATH%"=="" (
    echo.
    echo Signing Windows executable...
    where signtool >nul 2>&1
    if errorlevel 1 (
        echo signtool.exe not found. Install Windows SDK or remove SIGN_PFX_PATH.
        goto :build_failed
    )
    signtool sign /fd SHA256 /f "%SIGN_PFX_PATH%" /p "%SIGN_PFX_PASSWORD%" /tr http://timestamp.digicert.com /td SHA256 dist\Voxify.exe
    if errorlevel 1 goto :build_failed
)

%PY% package_release.py --platform windows

if errorlevel 1 goto :build_failed

echo.
echo Done! Windows artifact is in: dist\Voxify.exe
echo Release package is in: release\Voxify-v^<version^>-windows.zip
pause
exit /b 0

:build_failed
echo.
echo Build failed.
pause
exit /b 1
