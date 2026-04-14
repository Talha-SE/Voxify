# Voxify Release Packaging

This project uses OS-specific desktop artifacts.

## Important

A single executable file does not run across Windows, macOS, and Linux.
For PyInstaller builds, you must build on each target OS separately.

## Artifact naming

- Windows: `release/Voxify-v<version>-windows.zip`
- macOS: `release/Voxify-v<version>-macos.zip`
- Linux: `release/Voxify-v<version>-linux.zip`

## Windows release

Run:

```bat
build.bat
```

Output:

- `dist/Voxify.exe`
- `release/Voxify-v<version>-windows.zip`

## macOS release

Run on a macOS machine:

```bash
chmod +x build_macos.sh
./build_macos.sh
```

Output:

- `dist/Voxify` or `dist/Voxify.app`
- `release/Voxify-v<version>-macos.zip`

## Linux release

Run on a Linux machine:

```bash
chmod +x build_linux.sh
./build_linux.sh
```

Output:

- `dist/Voxify`
- `release/Voxify-v<version>-linux.zip`

## Shared packaging utility

Both build scripts call:

```bash
python package_release.py --platform <windows|macos|linux>
```

This utility creates standardized release zip files used by website download endpoints.
