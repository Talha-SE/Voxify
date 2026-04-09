"""Client helpers for website-backed API bootstrap."""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

DEFAULT_WEBSITE_URL = os.getenv("SONUS_WEBSITE_URL", "http://127.0.0.1:5050")
STATUS_TIMEOUT = 2.0
BOOTSTRAP_TIMEOUT = 4.0
UPDATE_TIMEOUT = 4.0
RUNTIME_CONFIG_TIMEOUT = 4.0
RELIABILITY_TIMEOUT = 3.0
DEFAULT_MODEL = "voxtral-mini-2507"


class WebsiteAPIError(RuntimeError):
    """Raised when the website cannot provide runtime API settings."""


@dataclass(frozen=True)
class DesktopBootstrap:
    api_key: str
    model: str


@dataclass(frozen=True)
class UpdateInfo:
    update_available: bool
    latest_version: str
    download_url: str
    notes: str
    mandatory: bool
    published_at: str


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


def _normalize_base_url(base_url: str | None = None) -> str:
    cleaned = (base_url or DEFAULT_WEBSITE_URL).strip().rstrip("/")
    return cleaned or DEFAULT_WEBSITE_URL


def get_site_status(base_url: str | None = None) -> dict:
    """Return parsed JSON from the website status endpoint."""
    try:
        response = requests.get(
            f"{_normalize_base_url(base_url)}/api/site-status",
            timeout=STATUS_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise WebsiteAPIError(
            "Unable to reach the website. Start the website server first."
        ) from exc
    except ValueError as exc:
        raise WebsiteAPIError(
            "The website returned an invalid status response."
        ) from exc

    if not isinstance(payload, dict):
        raise WebsiteAPIError("The website returned an unexpected status payload.")
    return payload


def get_desktop_bootstrap(base_url: str | None = None) -> DesktopBootstrap:
    """Fetch runtime API key/model from the website for desktop transcription."""
    status = get_site_status(base_url)
    if not bool(status.get("apiConfigured")):
        raise WebsiteAPIError(
            "The website API key is missing. Configure it on the website first."
        )

    try:
        response = requests.get(
            f"{_normalize_base_url(base_url)}/api/desktop-bootstrap",
            timeout=BOOTSTRAP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise WebsiteAPIError("Unable to fetch the API key from the website.") from exc
    except ValueError as exc:
        raise WebsiteAPIError(
            "The website returned an invalid API bootstrap response."
        ) from exc

    if not isinstance(payload, dict):
        raise WebsiteAPIError("The website returned an unexpected API bootstrap payload.")

    api_key = (payload.get("apiKey") or "").strip()
    model = (payload.get("model") or DEFAULT_MODEL).strip()
    if not api_key:
        raise WebsiteAPIError("The website did not return an API key.")
    return DesktopBootstrap(api_key=api_key, model=model)


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
    update_available = bool(payload.get("updateAvailable"))
    if latest_version and download_url:
        update_available = update_available or is_newer_version(current_version, latest_version)

    return UpdateInfo(
        update_available=update_available,
        latest_version=latest_version or current_version,
        download_url=download_url,
        notes=(payload.get("notes") or "").strip(),
        mandatory=bool(payload.get("mandatory")),
        published_at=(payload.get("publishedAt") or "").strip(),
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
