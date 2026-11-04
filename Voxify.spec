# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('config.json', '.'), ('assets\\voxify-logo.png', 'assets')],
    hiddenimports=[
        'flet_settings', 'flet_desktop', 'sounddevice', 'soundcard',
        'scipy', 'numpy', 'pyperclip', 'pyautogui', 'mss', 'requests',
        'mistralai', 'mistralai.client', 'mistralai.client.models',
        'mistralai.extra.realtime', 'gemini_live_client', 'websockets',
        'cv2', 'PIL', 'pyaudio', 'psutil', 'keyboard', 'pynput',
        'website_client', 'license_cache', 'dictation_features',
        'realtime_transcriber', 'transcriber', 'recorder'
    ],
    hookspath=['pyinstaller-hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Voxify',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
