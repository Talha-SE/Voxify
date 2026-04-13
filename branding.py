"""Shared brand asset helpers for desktop app and website integrations."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

_LOGO_RELATIVE_PATH = Path("assets") / "voxify-logo.png"
_LEGACY_LOGO_RELATIVE_PATH = Path("website") / "Voxify.png"
_LOGO_BASE64_CACHE: str | None = None


def _runtime_root() -> Path:
    """Return the runtime root for source and PyInstaller onefile modes."""
    frozen_root = getattr(sys, "_MEIPASS", "")
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parent


def resolve_logo_path() -> Path | None:
    """Return the best-available logo path."""
    source_root = Path(__file__).resolve().parent
    candidates = (
        _runtime_root() / _LOGO_RELATIVE_PATH,
        source_root / _LOGO_RELATIVE_PATH,
        source_root / _LEGACY_LOGO_RELATIVE_PATH,
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def load_logo_base64() -> str | None:
    """Load and cache logo bytes as base64 for controls that need inline image data."""
    global _LOGO_BASE64_CACHE
    if _LOGO_BASE64_CACHE is not None:
        return _LOGO_BASE64_CACHE

    logo_path = resolve_logo_path()
    if not logo_path:
        return None

    try:
        _LOGO_BASE64_CACHE = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    except Exception:
        _LOGO_BASE64_CACHE = None

    return _LOGO_BASE64_CACHE
