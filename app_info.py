"""Application identity and version metadata."""

from __future__ import annotations

import os

APP_NAME = "SONUS"
APP_VERSION = (os.getenv("SONUS_APP_VERSION") or "1.0.0").strip()
APP_PLATFORM = "windows"
APP_CHANNEL = "stable"

