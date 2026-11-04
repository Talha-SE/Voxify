from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_GRACE_PERIOD_HOURS = 48.0


def _resolve_state_file() -> Path:
    custom = os.getenv("VOXIFY_LICENSE_STATE_PATH")
    if custom:
        return Path(custom)

    if sys.platform.startswith("win"):
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        user_dir = Path(base) / "Voxify"
    elif sys.platform == "darwin":
        user_dir = Path.home() / "Library" / "Application Support" / "Voxify"
    else:
        user_dir = Path.home() / ".config" / "voxify"

    user_dir.mkdir(parents=True, exist_ok=True)
    target = user_dir / "license_state.json"

    if not target.exists():
        candidates = [
            Path(__file__).resolve().parent / "license_state.json",
            Path(getattr(sys, "_MEIPASS", "")) / "license_state.json" if getattr(sys, "_MEIPASS", "") else None,
        ]
        for c in candidates:
            if c and c.exists() and c.resolve() != target.resolve():
                try:
                    shutil.copy2(c, target)
                    break
                except Exception:
                    pass
    return target


STATE_FILE = _resolve_state_file()


def _protect_windows(value: str) -> str:
    if not value:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        raw = value.encode("utf-8")
        in_blob = DATA_BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw), ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "Voxify",
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        ):
            raise OSError("CryptProtectData failed")
        try:
            protected = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return "dpapi:" + base64.b64encode(protected).decode("ascii")
        finally:
            if out_blob.pbData:
                kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return "b64:" + base64.b64encode(value.encode("utf-8")).decode("ascii")


def _unprotect_windows(value: str) -> str:
    if not value:
        return ""
    if value.startswith("b64:"):
        try:
            return base64.b64decode(value[4:].encode("ascii")).decode("utf-8")
        except Exception:
            return ""
    if not value.startswith("dpapi:"):
        return value
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        raw = base64.b64decode(value[6:].encode("ascii"))
        in_blob = DATA_BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw), ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        ):
            raise OSError("CryptUnprotectData failed")
        try:
            decrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return decrypted.decode("utf-8")
        finally:
            if out_blob.pbData:
                kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return ""


def _protect(value: str) -> str:
    if os.name == "nt":
        return _protect_windows(value)
    return "b64:" + base64.b64encode((value or "").encode("utf-8")).decode("ascii")


def _unprotect(value: str) -> str:
    if os.name == "nt":
        return _unprotect_windows(value)
    if value.startswith("b64:"):
        try:
            return base64.b64decode(value[4:].encode("ascii")).decode("utf-8")
        except Exception:
            return ""
    return value


def load_state() -> dict[str, Any]:
    global STATE_FILE
    if not STATE_FILE.exists():
        STATE_FILE = _resolve_state_file()
    if not STATE_FILE.exists():
        return {}
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    data = dict(raw)
    data["licenseKey"] = _unprotect(str(raw.get("licenseKeyEnc") or ""))
    data["token"] = _unprotect(str(raw.get("tokenEnc") or ""))
    data["runtimeApiKey"] = _unprotect(str(raw.get("runtimeApiKeyEnc") or ""))
    data["geminiApiKey"] = _unprotect(str(raw.get("geminiApiKeyEnc") or ""))
    data["lastVerifiedAt"] = float(raw.get("lastVerifiedAt") or 0.0)
    data["graceExpiresAt"] = float(raw.get("graceExpiresAt") or 0.0)
    return data


def save_state(
    token: str,
    license_key: str = "",
    entitlement: dict[str, Any] | None = None,
    runtime_api_key: str = "",
    gemini_api_key: str = "",
    grace_duration_hours: float = DEFAULT_GRACE_PERIOD_HOURS,
) -> None:
    global STATE_FILE
    now = time.time()
    existing = load_state() if STATE_FILE.exists() else {}

    rk = runtime_api_key if runtime_api_key else str(existing.get("runtimeApiKey") or "")
    gk = gemini_api_key if gemini_api_key else str(existing.get("geminiApiKey") or "")
    ent = entitlement if entitlement is not None else (existing.get("entitlement") or {})
    lk = license_key if license_key else str(existing.get("licenseKey") or "")
    tok = token if token else str(existing.get("token") or "")

    is_active = str(ent.get("status") or "").lower() == "active"
    grace_expires = (now + (grace_duration_hours * 3600.0)) if is_active else 0.0

    payload = {
        "tokenEnc": _protect(tok),
        "licenseKeyEnc": _protect(lk),
        "runtimeApiKeyEnc": _protect(rk),
        "geminiApiKeyEnc": _protect(gk),
        "entitlement": ent,
        "lastVerifiedAt": now,
        "graceExpiresAt": grace_expires,
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def is_in_grace_period(state: dict[str, Any] | None = None) -> bool:
    """Return True if the cached license state is within the valid 24-48h offline grace window."""
    if state is None:
        state = load_state()
    if not state:
        return False
    ent = state.get("entitlement") or {}
    status = str(ent.get("status") or "").lower()
    if status != "active":
        return False
    if not ent.get("can_transcribe", True) and not ent.get("canTranscribe", True):
        return False
    grace_expires = float(state.get("graceExpiresAt") or 0.0)
    return time.time() < grace_expires


def get_grace_remaining_seconds(state: dict[str, Any] | None = None) -> float:
    """Return remaining seconds of offline grace period, or 0.0 if expired/inactive."""
    if state is None:
        state = load_state()
    grace_expires = float(state.get("graceExpiresAt") or 0.0)
    return max(0.0, grace_expires - time.time())


def clear_state() -> None:
    try:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
    except Exception:
        pass
