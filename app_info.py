"""Application identity and version metadata."""

from __future__ import annotations

import os
import sys


def _detect_platform() -> str:
	if sys.platform.startswith("win"):
		return "windows"
	if sys.platform == "darwin":
		return "mac"
	if sys.platform.startswith("linux"):
		return "linux"
	return "windows"


def _resolve_platform() -> str:
	override = (os.getenv("VOXIFY_APP_PLATFORM") or os.getenv("SONUS_APP_PLATFORM") or "").strip().lower()
	return override or _detect_platform()

APP_NAME = "Voxify"
APP_VERSION = (os.getenv("VOXIFY_APP_VERSION") or os.getenv("SONUS_APP_VERSION") or "1.0.0").strip()
APP_PLATFORM = _resolve_platform()
APP_CHANNEL = "stable"

