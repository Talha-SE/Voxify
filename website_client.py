"""Client helpers for website-backed API bootstrap and licensing."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

DEFAULT_WEBSITE_URL = os.getenv("VOXIFY_WEBSITE_URL") or os.getenv("SONUS_WEBSITE_URL", "http://127.0.0.1:5050")
STATUS_TIMEOUT = 2.0
BOOTSTRAP_TIMEOUT = 5.0
UPDATE_TIMEOUT = 5.0
RUNTIME_CONFIG_TIMEOUT = 4.0
RELIABILITY_TIMEOUT = 3.0
LICENSE_TIMEOUT = 8.0
TRANSCRIBE_TIMEOUT = 75.0
DOWNLOAD_TIMEOUT = 15.0
DEFAULT_MODEL = "voxtral-mini-2507"


class WebsiteAPIError(RuntimeError):
    """Raised when the website cannot provide runtime API settings."""


@dataclass(frozen=True)
class DesktopBootstrap:
    api_key: str
    model: str
    entitlement: dict[str, Any]


@dataclass(frozen=True)
class UpdateInfo:
    update_available: bool
    latest_version: str
    download_url: str
    notes: str
    mandatory: bool
    published_at: str
    asset_type: str
    sha256: str
    installer_args: str
    restart_required: bool


@dataclass(frozen=True)
class RuntimeConfig:
    channel: str
    platform: str
    in_rollout: bool
    rollout_percent: int
    feature_flags: dict
    live_retry_limit: int
    command_set_version: str
    silence_trim_enabled: bool
    endpointing_mode: str


@dataclass(frozen=True)
class LicenseEntitlement:
    license_id: str
    status: str
    plan: str
    quota_chars: int
    bonus_chars: int
    used_chars: int
    used_words: int
    remaining_chars: int
    seat_limit: int
    active_seats: int
    is_subscription: bool
    can_transcribe: bool


@dataclass(frozen=True)
class LicenseSession:
    token: str
    refresh_at: str
    expires_at: str
    entitlement: LicenseEntitlement
    live_api_key: str
    live_model: str


@dataclass(frozen=True)
class ProxyTranscriptionResult:
    text: str
    usage: LicenseEntitlement
    quota_limited: bool


def _normalize_base_url(base_url: str | None = None) -> str:
    cleaned = (base_url or DEFAULT_WEBSITE_URL).strip().rstrip("/")
    return cleaned or DEFAULT_WEBSITE_URL


def _parse_entitlement(raw: dict[str, Any]) -> LicenseEntitlement:
    return LicenseEntitlement(
        license_id=(raw.get("licenseId") or "").strip(),
        status=(raw.get("status") or "").strip().lower(),
        plan=(raw.get("plan") or "starter").strip().lower(),
        quota_chars=int(raw.get("quotaChars") or 0),
        bonus_chars=int(raw.get("bonusChars") or 0),
        used_chars=int(raw.get("usedChars") or 0),
        used_words=int(raw.get("usedWords") or 0),
        remaining_chars=int(raw.get("remainingChars") or 0),
        seat_limit=int(raw.get("seatLimit") or 1),
        active_seats=int(raw.get("activeSeats") or 0),
        is_subscription=bool(raw.get("isSubscription", False)),
        can_transcribe=bool(raw.get("canTranscribe", False)),
    )


def get_site_status(base_url: str | None = None) -> dict:
    try:
        response = requests.get(
            f"{_normalize_base_url(base_url)}/api/site-status",
            timeout=STATUS_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise WebsiteAPIError("Unable to reach the website. Start the website server first.") from exc
    except ValueError as exc:
        raise WebsiteAPIError("The website returned an invalid status response.") from exc

    if not isinstance(payload, dict):
        raise WebsiteAPIError("The website returned an unexpected status payload.")
    return payload


def activate_license(
    license_key: str,
    device_id: str,
    device_name: str = "",
    product_id: str = "",
    base_url: str | None = None,
) -> LicenseSession:
    payload = {
        "licenseKey": (license_key or "").strip(),
        "deviceId": (device_id or "").strip(),
        "deviceName": (device_name or "").strip(),
    }
    clean_product_id = (product_id or "").strip()
    if clean_product_id:
        payload["productId"] = clean_product_id
    try:
        response = requests.post(
            f"{_normalize_base_url(base_url)}/api/license/activate",
            json=payload,
            timeout=LICENSE_TIMEOUT,
        )
        data = response.json() if response.content else {}
    except requests.RequestException as exc:
        raise WebsiteAPIError("Unable to activate license right now.") from exc
    except ValueError as exc:
        raise WebsiteAPIError("License activation returned invalid JSON.") from exc

    if response.status_code >= 400 or not isinstance(data, dict) or not data.get("success"):
        raise WebsiteAPIError((data or {}).get("message") or "License activation failed.")

    entitlement_raw = data.get("entitlement") if isinstance(data.get("entitlement"), dict) else {}
    return LicenseSession(
        token=(data.get("token") or "").strip(),
        refresh_at=(data.get("refreshAt") or "").strip(),
        expires_at=(data.get("expiresAt") or "").strip(),
        entitlement=_parse_entitlement(entitlement_raw),
        live_api_key=(data.get("liveApiKey") or "").strip(),
        live_model=(data.get("liveModel") or DEFAULT_MODEL).strip(),
    )


def refresh_license(
    token: str,
    device_id: str,
    device_name: str = "",
    license_key: str = "",
    product_id: str = "",
    base_url: str | None = None,
) -> LicenseSession:
    payload = {
        "token": (token or "").strip(),
        "deviceId": (device_id or "").strip(),
        "deviceName": (device_name or "").strip(),
        "licenseKey": (license_key or "").strip(),
    }
    clean_product_id = (product_id or "").strip()
    if clean_product_id:
        payload["productId"] = clean_product_id
    try:
        response = requests.post(
            f"{_normalize_base_url(base_url)}/api/license/refresh",
            json=payload,
            timeout=LICENSE_TIMEOUT,
        )
        data = response.json() if response.content else {}
    except requests.RequestException as exc:
        raise WebsiteAPIError("Unable to refresh license right now.") from exc
    except ValueError as exc:
        raise WebsiteAPIError("License refresh returned invalid JSON.") from exc

    if response.status_code >= 400 or not isinstance(data, dict) or not data.get("success"):
        raise WebsiteAPIError((data or {}).get("message") or "License refresh failed.")

    entitlement_raw = data.get("entitlement") if isinstance(data.get("entitlement"), dict) else {}
    return LicenseSession(
        token=(data.get("token") or "").strip(),
        refresh_at=(data.get("refreshAt") or "").strip(),
        expires_at=(data.get("expiresAt") or "").strip(),
        entitlement=_parse_entitlement(entitlement_raw),
        live_api_key=(data.get("liveApiKey") or "").strip(),
        live_model=(data.get("liveModel") or DEFAULT_MODEL).strip(),
    )


def get_license_status(token: str, device_id: str, base_url: str | None = None) -> LicenseEntitlement:
    params = {"token": (token or "").strip(), "deviceId": (device_id or "").strip()}
    try:
        response = requests.get(
            f"{_normalize_base_url(base_url)}/api/license/status",
            params=params,
            timeout=LICENSE_TIMEOUT,
        )
        data = response.json() if response.content else {}
    except requests.RequestException as exc:
        raise WebsiteAPIError("Unable to load license status right now.") from exc
    except ValueError as exc:
        raise WebsiteAPIError("License status returned invalid JSON.") from exc

    if response.status_code >= 400 or not isinstance(data, dict) or not data.get("success"):
        raise WebsiteAPIError((data or {}).get("message") or "License status lookup failed.")
    entitlement_raw = data.get("entitlement") if isinstance(data.get("entitlement"), dict) else {}
    return _parse_entitlement(entitlement_raw)


def consume_license_usage(
    token: str,
    device_id: str,
    chars_used: int,
    words_used: int,
    mode: str,
    session_id: str,
    idempotency_key: str,
    detail: str = "",
    base_url: str | None = None,
) -> LicenseEntitlement:
    payload = {
        "token": (token or "").strip(),
        "deviceId": (device_id or "").strip(),
        "charsUsed": max(0, int(chars_used or 0)),
        "wordsUsed": max(0, int(words_used or 0)),
        "mode": (mode or "batch").strip().lower(),
        "sessionId": (session_id or "").strip(),
        "idempotencyKey": (idempotency_key or "").strip(),
        "detail": (detail or "").strip(),
    }
    try:
        response = requests.post(
            f"{_normalize_base_url(base_url)}/api/license/consume",
            json=payload,
            timeout=LICENSE_TIMEOUT,
        )
        data = response.json() if response.content else {}
    except requests.RequestException as exc:
        raise WebsiteAPIError("Unable to record usage right now.") from exc
    except ValueError as exc:
        raise WebsiteAPIError("Usage response returned invalid JSON.") from exc

    if response.status_code >= 400 or not isinstance(data, dict) or not data.get("success"):
        raise WebsiteAPIError((data or {}).get("message") or "Usage update failed.")
    entitlement_raw = data.get("entitlement") if isinstance(data.get("entitlement"), dict) else {}
    return _parse_entitlement(entitlement_raw)


def get_desktop_bootstrap(token: str, device_id: str, base_url: str | None = None) -> DesktopBootstrap:
    params = {
        "token": (token or "").strip(),
        "deviceId": (device_id or "").strip(),
    }
    try:
        response = requests.get(
            f"{_normalize_base_url(base_url)}/api/desktop-bootstrap",
            params=params,
            timeout=BOOTSTRAP_TIMEOUT,
        )
        payload = response.json() if response.content else {}
    except requests.RequestException as exc:
        raise WebsiteAPIError("Unable to fetch desktop bootstrap right now.") from exc
    except ValueError as exc:
        raise WebsiteAPIError("Desktop bootstrap returned invalid JSON.") from exc

    if response.status_code >= 400 or not isinstance(payload, dict) or not payload.get("success"):
        raise WebsiteAPIError((payload or {}).get("message") or "Desktop bootstrap failed.")

    entitlement_raw = payload.get("license") if isinstance(payload.get("license"), dict) else {}
    return DesktopBootstrap(
        api_key=(payload.get("apiKey") or "").strip(),
        model=(payload.get("model") or DEFAULT_MODEL).strip(),
        entitlement=entitlement_raw,
    )


def transcribe_via_proxy(
    wav_path: str,
    token: str,
    device_id: str,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
    prompt: str | None = None,
    session_id: str = "",
    idempotency_key: str = "",
    base_url: str | None = None,
) -> ProxyTranscriptionResult:
    file_path = Path(wav_path)
    if not file_path.exists():
        raise WebsiteAPIError(f"Audio file not found: {wav_path}")

    payload = {
        "token": (token or "").strip(),
        "deviceId": (device_id or "").strip(),
        "model": (model or DEFAULT_MODEL).strip(),
        "sessionId": (session_id or "").strip(),
        "idempotencyKey": (idempotency_key or "").strip(),
    }
    if language:
        payload["language"] = language.strip()
    if prompt:
        payload["prompt"] = prompt.strip()

    try:
        with file_path.open("rb") as audio_file:
            response = requests.post(
                f"{_normalize_base_url(base_url)}/api/transcribe",
                data=payload,
                files={"file": (file_path.name, audio_file, "audio/wav")},
                timeout=TRANSCRIBE_TIMEOUT,
            )
            data = response.json() if response.content else {}
    except requests.RequestException as exc:
        raise WebsiteAPIError("Unable to transcribe through website proxy.") from exc
    except ValueError as exc:
        raise WebsiteAPIError("Proxy transcription returned invalid JSON.") from exc

    if response.status_code >= 400 or not isinstance(data, dict) or not data.get("success"):
        raise WebsiteAPIError((data or {}).get("message") or "Proxy transcription failed.")

    usage_raw = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return ProxyTranscriptionResult(
        text=(data.get("text") or "").strip(),
        usage=_parse_entitlement(usage_raw),
        quota_limited=bool(data.get("quotaLimited", False)),
    )


def _parse_version(value: str) -> tuple[int, int, int]:
    chunks = (value or "").strip().split(".")
    numeric: list[int] = []
    for item in chunks[:3]:
        digits = "".join(ch for ch in item if ch.isdigit())
        numeric.append(int(digits) if digits else 0)
    while len(numeric) < 3:
        numeric.append(0)
    return tuple(numeric)


def is_newer_version(current_version: str, latest_version: str) -> bool:
    return _parse_version(latest_version) > _parse_version(current_version)


def get_update_info(
    current_version: str,
    platform: str = "windows",
    channel: str = "stable",
    base_url: str | None = None,
) -> UpdateInfo:
    params = {
        "currentVersion": (current_version or "").strip(),
        "platform": (platform or "windows").strip().lower(),
        "channel": (channel or "stable").strip().lower(),
    }
    try:
        response = requests.get(
            f"{_normalize_base_url(base_url)}/api/app-update",
            params=params,
            timeout=UPDATE_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise WebsiteAPIError("Unable to check for updates right now.") from exc
    except ValueError as exc:
        raise WebsiteAPIError("Update check returned an invalid response.") from exc

    if not isinstance(payload, dict):
        raise WebsiteAPIError("Update check returned an unexpected payload.")

    latest_version = (payload.get("latestVersion") or "").strip()
    download_url = (payload.get("downloadUrl") or "").strip()
    server_update_available = payload.get("updateAvailable")
    if isinstance(server_update_available, bool):
        update_available = server_update_available
    else:
        update_available = bool(latest_version and download_url and is_newer_version(current_version, latest_version))

    return UpdateInfo(
        update_available=update_available,
        latest_version=latest_version or current_version,
        download_url=download_url,
        notes=(payload.get("notes") or "").strip(),
        mandatory=bool(payload.get("mandatory")),
        published_at=(payload.get("publishedAt") or "").strip(),
        asset_type=(payload.get("assetType") or "exe").strip().lower(),
        sha256=(payload.get("sha256") or "").strip().lower(),
        installer_args=(payload.get("installerArgs") or "").strip(),
        restart_required=bool(payload.get("restartRequired", True)),
    )


def get_runtime_config(
    channel: str = "stable",
    platform: str = "windows",
    device_id: str = "",
    base_url: str | None = None,
) -> RuntimeConfig:
    params = {
        "channel": (channel or "stable").strip().lower(),
        "platform": (platform or "windows").strip().lower(),
        "deviceId": (device_id or "").strip(),
    }
    try:
        response = requests.get(
            f"{_normalize_base_url(base_url)}/api/runtime-config",
            params=params,
            timeout=RUNTIME_CONFIG_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise WebsiteAPIError("Unable to load runtime configuration right now.") from exc
    except ValueError as exc:
        raise WebsiteAPIError("Runtime configuration response is invalid.") from exc

    if not isinstance(payload, dict):
        raise WebsiteAPIError("Runtime configuration payload is unexpected.")

    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    return RuntimeConfig(
        channel=(payload.get("channel") or "stable").strip().lower(),
        platform=(payload.get("platform") or "windows").strip().lower(),
        in_rollout=bool(payload.get("inRollout", True)),
        rollout_percent=int(payload.get("rolloutPercent") or 100),
        feature_flags=payload.get("featureFlags") if isinstance(payload.get("featureFlags"), dict) else {},
        live_retry_limit=int(runtime.get("liveRetryLimit") or 2),
        command_set_version=(runtime.get("commandSetVersion") or "v1").strip(),
        silence_trim_enabled=bool(runtime.get("silenceTrimEnabled", True)),
        endpointing_mode=(runtime.get("endpointingMode") or "adaptive").strip().lower(),
    )


def post_reliability_event(event: dict, base_url: str | None = None) -> bool:
    try:
        response = requests.post(
            f"{_normalize_base_url(base_url)}/api/reliability-event",
            json=event,
            timeout=RELIABILITY_TIMEOUT,
        )
        if response.status_code >= 400:
            return False
        payload = response.json() if response.content else {}
        return bool(payload.get("success", False))
    except Exception:
        return False


def download_update_asset(info: UpdateInfo, dest_dir: str | None = None) -> str:
    if not info.download_url:
        raise WebsiteAPIError("Update download URL is missing.")

    target_dir = Path(dest_dir or tempfile.gettempdir()) / "voxify_updates"
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(info.download_url.split("?")[0]).name or f"voxify-{info.latest_version}.exe"
    target_path = target_dir / filename

    try:
        with requests.get(info.download_url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
            response.raise_for_status()
            with target_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        handle.write(chunk)
    except requests.RequestException as exc:
        raise WebsiteAPIError("Failed to download update package.") from exc

    expected_sha = (info.sha256 or "").lower()
    if expected_sha:
        digest = hashlib.sha256(target_path.read_bytes()).hexdigest().lower()
        if digest != expected_sha:
            raise WebsiteAPIError("Downloaded update failed checksum verification.")
    return str(target_path)


def launch_update_installer(file_path: str, info: UpdateInfo) -> str:
    path = Path(file_path)
    if not path.exists():
        raise WebsiteAPIError("Update package was not found on disk.")

    args = [part for part in (info.installer_args or "").split(" ") if part]
    if os.name == "nt":
        if info.asset_type == "msi":
            cmd = ["msiexec", "/i", str(path)] + args
        elif info.asset_type == "zip":
            cmd = ["explorer", str(path.parent)]
        else:
            cmd = [str(path)] + args
        subprocess.Popen(cmd, cwd=str(path.parent))
        return "Installer launched."

    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
        return "Opened downloaded update."

    subprocess.Popen(["xdg-open", str(path)])
    return "Opened downloaded update."
