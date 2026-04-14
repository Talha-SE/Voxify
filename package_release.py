"""Package built desktop artifacts into standardized release zips."""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import app_info

ROOT_DIR = Path(__file__).resolve().parent
DIST_DIR = ROOT_DIR / "dist"
RELEASE_DIR = ROOT_DIR / "release"


def _normalize_platform(value: str) -> str:
    normalized = (value or "").strip().lower()
    aliases = {
        "win": "windows",
        "win32": "windows",
        "windows": "windows",
        "mac": "macos",
        "darwin": "macos",
        "macos": "macos",
        "osx": "macos",
        "linux": "linux",
        "linux2": "linux",
        "gnu/linux": "linux",
        "ubuntu": "linux",
        "debian": "linux",
        "auto": "auto",
    }
    return aliases.get(normalized, normalized)


def _detect_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    raise RuntimeError("Automatic platform detection is supported only for Windows, macOS, and Linux.")


def _resolve_artifact(platform_name: str) -> Path:
    candidates = {
        "windows": [DIST_DIR / "Voxify.exe", DIST_DIR / "SONUS.exe", DIST_DIR / "BreviosChipVoxtral.exe"],
        "macos": [DIST_DIR / "Voxify.app", DIST_DIR / "Voxify"],
        "linux": [DIST_DIR / "Voxify", DIST_DIR / "SONUS", DIST_DIR / "BreviosChipVoxtral"],
    }.get(platform_name, [])

    for candidate in candidates:
        if candidate.exists():
            return candidate

    checked = ", ".join(str(path) for path in candidates) or "<none>"
    raise FileNotFoundError(f"No built artifact found for {platform_name}. Checked: {checked}")


def _zip_path(version: str, platform_name: str) -> Path:
    return RELEASE_DIR / f"Voxify-v{version}-{platform_name}.zip"


def _ensure_artifact_is_packagable(artifact_path: Path, target_zip: Path) -> None:
    artifact_resolved = artifact_path.resolve()
    target_resolved = target_zip.resolve()

    if artifact_resolved == target_resolved:
        raise RuntimeError("Refusing to package a release zip into itself.")
    if artifact_path.suffix.lower() == ".zip":
        raise RuntimeError(
            f"Expected a built binary/app artifact but got a zip file: {artifact_path}. "
            "Build the application first and retry packaging."
        )


def _add_path_to_zip(handle: zipfile.ZipFile, artifact_path: Path) -> None:
    if artifact_path.is_dir():
        # Keep macOS app bundles intact but flatten generic build directories.
        keep_top_level_dir = artifact_path.suffix.lower() == ".app"
        base_dir = artifact_path.parent if keep_top_level_dir else artifact_path
        for item in artifact_path.rglob("*"):
            if item.is_file():
                handle.write(item, item.relative_to(base_dir))
        return

    handle.write(artifact_path, artifact_path.name)


def package_release(platform_name: str, version: str) -> Path:
    artifact = _resolve_artifact(platform_name)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    target_zip = _zip_path(version, platform_name)
    _ensure_artifact_is_packagable(artifact, target_zip)

    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        _add_path_to_zip(archive, artifact)

    return target_zip


def main() -> int:
    parser = argparse.ArgumentParser(description="Create standardized release zip from built artifacts.")
    parser.add_argument(
        "--platform",
        default="auto",
        help="Target platform: windows, macos, linux, or auto (default: auto)",
    )
    parser.add_argument(
        "--version",
        default="",
        help="Release version; defaults to app_info.APP_VERSION",
    )
    args = parser.parse_args()

    selected_platform = _normalize_platform(args.platform)
    if selected_platform == "auto":
        selected_platform = _detect_platform()

    if selected_platform not in {"windows", "macos", "linux"}:
        print("Unsupported platform. Use windows, macos, linux, or auto.")
        return 2

    version = (args.version or app_info.APP_VERSION or "1.0.0").strip()

    try:
        zip_path = package_release(selected_platform, version)
    except Exception as exc:
        print(f"Release packaging failed: {exc}")
        return 1

    print(f"Created release package: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
