from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any


STATE_FILE = Path(__file__).with_name("license_state.json")


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
    return data


def save_state(
    token: str,
    license_key: str = "",
    entitlement: dict[str, Any] | None = None,
) -> None:
    payload = {
        "tokenEnc": _protect(token or ""),
        "licenseKeyEnc": _protect(license_key or ""),
        "entitlement": entitlement or {},
    }
    STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_state() -> None:
    try:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
    except Exception:
        pass
