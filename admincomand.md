# Admin Commands

## Run desktop app
Purpose: Start the main Voxify desktop widget.

```powershell
python app.py
```

## Run website server
Purpose: Start the Flask website and admin dashboard.

```powershell
python website/server.py
```

## Open desktop settings directly
Purpose: Launch the Flet settings window without opening the full app.

```powershell
python app.py --settings
```

## Build Windows release
Purpose: Build Windows binary and package release zip.

```powershell
build.bat
```

Optional signing (recommended for trusted publisher):

```powershell
$env:SIGN_PFX_PATH="C:\path\to\your-certificate.pfx"
$env:SIGN_PFX_PASSWORD="your-pfx-password"
build.bat
```

## Build macOS release
Purpose: Build macOS artifact and package release zip (run on macOS).

```bash
chmod +x build_macos.sh
./build_macos.sh
```

Optional signing and notarization prep:

```bash
export APPLE_CODESIGN_IDENTITY="Developer ID Application: Your Company (TEAMID)"
./build_macos.sh
```

## Build Linux release
Purpose: Build Linux artifact and package release zip (run on Linux).

```bash
chmod +x build_linux.sh
./build_linux.sh
```

## Cross-platform build rule
Purpose: Keep release artifacts valid and trusted per operating system.

```text
Windows zip must be built on Windows.
macOS zip must be built on macOS.
Linux zip must be built on Linux.
```

## Trust and warning behavior
Purpose: Understand what users will see on each OS if app is unsigned.

```text
Windows: SmartScreen warns with "Unknown publisher" until Authenticode signing reputation is established.
macOS: Gatekeeper blocks/unwarns unless app is signed (Developer ID) and notarized.
Linux: Usually no publisher pop-up, but package signatures/checksums are still recommended.
```

## Package release from existing dist artifact
Purpose: Create standardized release zip for a specific platform.

```powershell
python package_release.py --platform windows
python package_release.py --platform macos
python package_release.py --platform linux
```

## Syntax check critical modules
Purpose: Catch syntax issues before build or deployment.

```powershell
python -m py_compile app.py flet_main.py flet_settings.py website/server.py
```

## Quick download endpoint smoke test
Purpose: Verify website platform routes and update API output.

```powershell
python -c "from website.server import app; c=app.test_client(); print(c.get('/download').status_code, c.get('/download/windows').status_code, c.get('/download/macos').status_code, c.get('/download/linux').status_code)"
python -c "from website.server import app; c=app.test_client(); print(c.get('/api/app-update?currentVersion=0.0.1&platform=windows').status_code)"
```

## Run GitHub Actions release
Purpose: Build Windows, macOS, and Linux release zips in CI.

```text
Push a tag like v1.0.0 to trigger the workflow automatically.
Or open the GitHub Actions tab and run "Build and Publish Release" manually.
```

## GitHub Actions signing secrets
Purpose: Enable professional signing on CI when you have certificates.

```text
Windows: WINDOWS_SIGN_PFX_B64 and WINDOWS_SIGN_PFX_PASSWORD
macOS: MACOS_CERT_BASE64 and MACOS_CERT_PASSWORD
```
