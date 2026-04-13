from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from itsdangerous import BadData, URLSafeTimedSerializer
import requests
from werkzeug.security import check_password_hash

try:
    from .license_store import MongoLicenseStore
    from .secure_api import get_masked_api_key, get_mistral_api_key, get_mistral_model
except ImportError:
    from license_store import MongoLicenseStore
    from secure_api import get_masked_api_key, get_mistral_api_key, get_mistral_model

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "replace-me-in-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_SECURE_COOKIE", "false").lower() == "true"

LOGIN_WINDOW_SECONDS = 300
MAX_LOGIN_ATTEMPTS = 5
_failed_attempts: dict[str, list[float]] = {}

GUMROAD_VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"
RELEASE_INFO_FILE = Path(__file__).with_name("release_info.json")
RUNTIME_CONFIG_FILE = Path(__file__).with_name("runtime_config.json")
RELIABILITY_EVENTS_FILE = Path(__file__).with_name("reliability_events.jsonl")

LICENSE_TOKEN_MAX_AGE_SEC = max(3600, int(os.getenv("VOXIFY_LICENSE_TOKEN_MAX_AGE_SEC") or os.getenv("SONUS_LICENSE_TOKEN_MAX_AGE_SEC", "604800")))
LICENSE_REFRESH_INTERVAL_SEC = max(600, int(os.getenv("VOXIFY_LICENSE_REFRESH_INTERVAL_SEC") or os.getenv("SONUS_LICENSE_REFRESH_INTERVAL_SEC", "21600")))
ALLOW_CLIENT_LIVE_KEY = (os.getenv("VOXIFY_ALLOW_CLIENT_LIVE_KEY") or os.getenv("SONUS_ALLOW_CLIENT_LIVE_KEY", "true")).strip().lower() == "true"

_store: MongoLicenseStore | None = None
_store_error: str = ""
_token_serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="sonus-license-v1")

MEMBERSHIP_TIERS = [
    {
        "name": "Starter",
        "price": "$7",
        "period": "/ month",
        "characters": "50,000 characters",
        "tag": "Affordable",
        "benefits": [
            "Live + Batch transcription",
            "Mic and system audio capture",
            "Private processing workflow",
        ],
    },
    {
        "name": "Pro",
        "price": "$19",
        "period": "/ month",
        "characters": "500,000 characters",
        "tag": "Most Popular",
        "benefits": [
            "Priority processing speed",
            "Commercial usage rights",
            "Advanced language coverage",
        ],
    },
    {
        "name": "Team",
        "price": "$69",
        "period": "/ month",
        "characters": "2,000,000 characters",
        "tag": "Scale",
        "benefits": [
            "Up to 5 desktop seats",
            "Centralized team access",
            "Built for meetings and operations",
        ],
    },
]

ONE_TIME_OFFER = {
    "name": "One-Time Lifetime",
    "price": "$39",
    "period": " once",
    "characters": "No recurring billing",
    "benefits": [
        "Lifetime desktop unlock",
        "Live + Batch core features",
        "Great for occasional power users",
    ],
}

DEFAULT_RELEASE_INFO = {
    "latestVersion": "1.0.0",
    "downloadUrl": "",
    "notes": "",
    "mandatory": False,
    "publishedAt": "",
    "channel": "stable",
    "platform": "windows",
    "assetType": "exe",
    "sha256": "",
    "installerArgs": "/S",
    "restartRequired": True,
}

DEFAULT_RUNTIME_CONFIG = {
    "channels": {
        "stable": {
            "rolloutPercent": 100,
            "platform": "windows",
            "featureFlags": {
                "voiceCommands": True,
                "autoFallback": True,
                "reliabilityEvents": False,
            },
            "runtime": {
                "liveRetryLimit": 2,
                "commandSetVersion": "v1",
                "silenceTrimEnabled": True,
                "endpointingMode": "adaptive",
            },
        },
        "beta": {
            "rolloutPercent": 20,
            "platform": "windows",
            "featureFlags": {
                "voiceCommands": True,
                "autoFallback": True,
                "reliabilityEvents": True,
            },
            "runtime": {
                "liveRetryLimit": 3,
                "commandSetVersion": "v1",
                "silenceTrimEnabled": True,
                "endpointingMode": "adaptive",
            },
        },
    }
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _word_count(value: str) -> int:
    text = _text(value)
    if not text:
        return 0
    return len(re.findall(r"\b[\w']+\b", text, flags=re.UNICODE))


def _get_store() -> MongoLicenseStore | None:
    global _store
    global _store_error
    if _store is not None:
        return _store
    try:
        _store = MongoLicenseStore()
        _store.ping()
        _store_error = ""
    except Exception as exc:
        _store = None
        _store_error = str(exc)
    return _store


def _store_problem() -> tuple[dict[str, Any], int]:
    message = "License database is unavailable."
    if _store_error:
        message = f"{message} {_store_error}"
    return {"success": False, "message": message}, 503


def _normalize_checkout_url(value: str, fallback: str) -> str:
    cleaned = _text(value)
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    return fallback


def _is_rate_limited(client_ip: str) -> bool:
    now = time.time()
    entries = [ts for ts in _failed_attempts.get(client_ip, []) if now - ts < LOGIN_WINDOW_SECONDS]
    _failed_attempts[client_ip] = entries
    return len(entries) >= MAX_LOGIN_ATTEMPTS


def _register_failed_attempt(client_ip: str) -> None:
    _failed_attempts.setdefault(client_ip, []).append(time.time())


def _clear_failed_attempts(client_ip: str) -> None:
    _failed_attempts.pop(client_ip, None)


def _verify_admin_credentials(username: str, password: str) -> bool:
    env_user = _text(os.getenv("ADMIN_USERNAME", "admin"))
    password_hash = _text(os.getenv("ADMIN_PASSWORD_HASH", ""))

    if username != env_user:
        return False
    if not password_hash:
        return False
    return check_password_hash(password_hash, password)


def _parse_version(value: str) -> tuple[int, int, int]:
    match = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)", value or "")
    if not match:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


def _is_version_newer(current: str, latest: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def _load_release_info() -> dict:
    if RELEASE_INFO_FILE.exists():
        try:
            with RELEASE_INFO_FILE.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return {**DEFAULT_RELEASE_INFO, **data}
        except Exception:
            pass
    return dict(DEFAULT_RELEASE_INFO)


def _save_release_info(data: dict) -> None:
    merged = {**DEFAULT_RELEASE_INFO, **data}
    with RELEASE_INFO_FILE.open("w", encoding="utf-8") as handle:
        json.dump(merged, handle, indent=2)


def _load_runtime_config() -> dict:
    if RUNTIME_CONFIG_FILE.exists():
        try:
            with RUNTIME_CONFIG_FILE.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                channels = data.get("channels") if isinstance(data.get("channels"), dict) else {}
                merged_channels = {
                    "stable": {
                        **DEFAULT_RUNTIME_CONFIG["channels"]["stable"],
                        **(channels.get("stable") or {}),
                    },
                    "beta": {
                        **DEFAULT_RUNTIME_CONFIG["channels"]["beta"],
                        **(channels.get("beta") or {}),
                    },
                }
                for key in ("stable", "beta"):
                    feature_flags = merged_channels[key].get("featureFlags")
                    runtime = merged_channels[key].get("runtime")
                    merged_channels[key]["featureFlags"] = {
                        **DEFAULT_RUNTIME_CONFIG["channels"][key]["featureFlags"],
                        **(feature_flags if isinstance(feature_flags, dict) else {}),
                    }
                    merged_channels[key]["runtime"] = {
                        **DEFAULT_RUNTIME_CONFIG["channels"][key]["runtime"],
                        **(runtime if isinstance(runtime, dict) else {}),
                    }
                return {"channels": merged_channels}
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_RUNTIME_CONFIG))


def _save_runtime_config(data: dict) -> None:
    with RUNTIME_CONFIG_FILE.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def _event_rollout_bucket(value: str) -> int:
    if not value:
        return 100
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100 + 1


def _resolve_channel_runtime(channel: str) -> tuple[str, dict]:
    runtime_config = _load_runtime_config()
    channels = runtime_config.get("channels", {})
    normalized = _text(channel).lower() or "stable"
    if normalized not in channels:
        normalized = "stable"
    return normalized, channels.get(normalized, channels.get("stable", {}))


def _append_reliability_event(event: dict) -> None:
    RELIABILITY_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RELIABILITY_EVENTS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True) + "\n")


def _summarize_reliability_events(limit: int = 2000) -> dict:
    if not RELIABILITY_EVENTS_FILE.exists():
        return {
            "totalEvents": 0,
            "eventTypeCounts": {},
            "errorCodeCounts": {},
            "avgLatencyMs": 0,
            "lastTimestamp": "",
        }

    lines = RELIABILITY_EVENTS_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    event_type_counts = Counter()
    error_counts = Counter()
    latency_values = []
    last_ts = ""

    for line in lines:
        try:
            event = json.loads(line)
        except Exception:
            continue

        event_type = _text(event.get("eventType")).lower() or "unknown"
        error_code = _text(event.get("errorCode")).lower()
        latency = event.get("latencyMs")
        timestamp = _text(event.get("timestamp"))

        event_type_counts[event_type] += 1
        if error_code:
            error_counts[error_code] += 1
        if isinstance(latency, (int, float)) and latency > 0:
            latency_values.append(float(latency))
        if timestamp:
            last_ts = timestamp

    avg_latency = int(sum(latency_values) / len(latency_values)) if latency_values else 0
    return {
        "totalEvents": int(sum(event_type_counts.values())),
        "eventTypeCounts": dict(event_type_counts.most_common(8)),
        "errorCodeCounts": dict(error_counts.most_common(8)),
        "avgLatencyMs": avg_latency,
        "lastTimestamp": last_ts,
    }


def _gumroad_verify(license_key: str, product_id: str) -> tuple[dict[str, Any] | None, str]:
    verify_form = {
        "product_id": _text(product_id),
        "license_key": _text(license_key),
        "increment_uses_count": "false",
    }
    access_token = _text(os.getenv("GUMROAD_API_ACCESS_TOKEN", ""))
    if access_token:
        verify_form["access_token"] = access_token

    try:
        response = requests.post(GUMROAD_VERIFY_URL, data=verify_form, timeout=15)
        payload = response.json() if response.content else {}
    except requests.RequestException:
        return None, "Unable to verify license right now."
    except ValueError:
        return None, "Unexpected response from Gumroad."

    if response.status_code != 200 or not payload.get("success"):
        return None, "Invalid or inactive license."
    return payload, ""


def _mint_license_token(license_id: str, device_hash: str) -> str:
    return _token_serializer.dumps({"lid": license_id, "dh": device_hash, "iat": int(time.time())})


def _parse_license_token(raw_token: str) -> tuple[dict[str, Any] | None, str]:
    token = _text(raw_token)
    if not token:
        return None, "token is required."
    try:
        payload = _token_serializer.loads(token, max_age=LICENSE_TOKEN_MAX_AGE_SEC)
    except BadData:
        return None, "Invalid or expired token."
    if not isinstance(payload, dict):
        return None, "Invalid token payload."
    if not _text(payload.get("lid")) or not _text(payload.get("dh")):
        return None, "Token payload is incomplete."
    return payload, ""


def _entitlement_payload(entitlement: dict[str, Any], token: str, include_runtime_key: bool = False) -> dict[str, Any]:
    payload = {
        "success": True,
        "token": token,
        "entitlement": entitlement,
        "refreshAt": (_utc_now() + timedelta(seconds=LICENSE_REFRESH_INTERVAL_SEC)).isoformat(),
        "expiresAt": (_utc_now() + timedelta(seconds=LICENSE_TOKEN_MAX_AGE_SEC)).isoformat(),
    }
    if include_runtime_key and ALLOW_CLIENT_LIVE_KEY:
        try:
            payload["liveApiKey"] = get_mistral_api_key()
            payload["liveModel"] = get_mistral_model()
        except RuntimeError:
            payload["liveApiKey"] = ""
            payload["liveModel"] = get_mistral_model()
    return payload


def _validate_token_and_license(device_id: str, token: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    store = _get_store()
    if not store:
        return None, None, _store_problem()[0]["message"]

    payload, token_error = _parse_license_token(token)
    if not payload:
        return None, None, token_error

    device_hash = store.hash_device(device_id)
    if not secrets.compare_digest(device_hash, _text(payload.get("dh"))):
        return None, None, "Device does not match the active token."

    doc = store.get_license_by_id(_text(payload.get("lid")))
    if not doc:
        return None, None, "License not found."
    entitlement = store.build_entitlement(doc)
    return entitlement, payload, ""


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return view_func(*args, **kwargs)

    return wrapped_view


@app.context_processor
def inject_template_globals():
    return {
        "current_year": _utc_now().year,
        "membership_url": _normalize_checkout_url(
            os.getenv("GUMROAD_MEMBERSHIP_URL", ""),
            "https://gumroad.com",
        ),
        "onetime_url": _normalize_checkout_url(
            os.getenv("GUMROAD_ONETIME_URL", ""),
            "https://gumroad.com",
        ),
    }


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.get("/")
def home():
    return render_template(
        "index.html",
        membership_tiers=MEMBERSHIP_TIERS,
        one_time_offer=ONE_TIME_OFFER,
    )


@app.get("/privacy-policy")
def privacy_policy():
    return render_template("privacy.html")


@app.get("/terms-and-conditions")
def terms_and_conditions():
    return render_template("terms.html")


@app.get("/robots.txt")
def robots():
    return send_from_directory(app.static_folder, "robots.txt")


@app.get("/sitemap.xml")
def sitemap():
    return send_from_directory(app.static_folder, "sitemap.xml")


@app.route("/admin-brevios-login", methods=["GET", "POST"])
def admin_login():
    error_message = ""
    csrf_token = session.get("admin_csrf_token")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(24)
        session["admin_csrf_token"] = csrf_token

    if request.method == "POST":
        client_ip = request.remote_addr or "unknown"
        form_token = _text(request.form.get("csrf_token"))

        if not secrets.compare_digest(form_token, csrf_token):
            return render_template("admin_login.html", error_message="Invalid form token.", csrf_token=csrf_token), 400

        if _is_rate_limited(client_ip):
            error_message = "Too many attempts. Try again in a few minutes."
            return render_template("admin_login.html", error_message=error_message, csrf_token=csrf_token), 429

        username = _text(request.form.get("username"))
        password = request.form.get("password") or ""
        if _verify_admin_credentials(username, password):
            session["admin_logged_in"] = True
            session["admin_user"] = username
            session["admin_csrf_token"] = secrets.token_urlsafe(24)
            _clear_failed_attempts(client_ip)
            return redirect(url_for("admin_dashboard"))

        _register_failed_attempt(client_ip)
        error_message = "Invalid credentials."

    return render_template("admin_login.html", error_message=error_message, csrf_token=csrf_token)


@app.get("/admin/logout")
@login_required
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin-brevios-dashboard", methods=["GET", "POST"])
@login_required
def admin_dashboard():
    csrf_token = session.get("admin_csrf_token")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(24)
        session["admin_csrf_token"] = csrf_token

    dashboard_message = ""
    dashboard_error = ""
    release_info = _load_release_info()
    runtime_config = _load_runtime_config()
    reliability_summary = _summarize_reliability_events()
    selected_runtime_channel = _text(request.args.get("runtime_channel") or request.form.get("runtime_channel") or "stable").lower()
    if selected_runtime_channel not in {"stable", "beta"}:
        selected_runtime_channel = "stable"

    license_query = _text(request.args.get("license_query") or request.form.get("license_query") or "")
    store = _get_store()
    license_summary = {
        "totalLicenses": 0,
        "activeLicenses": 0,
        "revokedLicenses": 0,
        "activeDevices": 0,
        "totalCharsUsed": 0,
        "totalWordsUsed": 0,
    }

    if request.method == "POST":
        form_token = _text(request.form.get("csrf_token"))
        if not secrets.compare_digest(form_token, csrf_token):
            dashboard_error = "Invalid form token."
        else:
            form_type = _text(request.form.get("form_type")).lower() or "release"
            if form_type == "runtime":
                selected_runtime_channel = _text(request.form.get("runtime_channel") or "stable").lower()
                if selected_runtime_channel not in {"stable", "beta"}:
                    selected_runtime_channel = "stable"

                try:
                    rollout_percent = int(request.form.get("rollout_percent") or 100)
                except ValueError:
                    rollout_percent = 100
                rollout_percent = max(0, min(100, rollout_percent))
                try:
                    live_retry_limit = int(request.form.get("live_retry_limit") or 2)
                except ValueError:
                    live_retry_limit = 2
                live_retry_limit = max(0, min(10, live_retry_limit))
                command_set_version = _text(request.form.get("command_set_version") or "v1")
                endpointing_mode = _text(request.form.get("endpointing_mode") or "adaptive").lower()
                if endpointing_mode not in {"adaptive", "latency", "accuracy"}:
                    endpointing_mode = "adaptive"

                ch_data = runtime_config["channels"].setdefault(selected_runtime_channel, {})
                ch_data["rolloutPercent"] = rollout_percent
                ch_data["platform"] = _text(request.form.get("runtime_platform") or "windows").lower() or "windows"
                ch_data["featureFlags"] = {
                    "voiceCommands": _text(request.form.get("flag_voice_commands")).lower() == "on",
                    "autoFallback": _text(request.form.get("flag_auto_fallback")).lower() == "on",
                    "reliabilityEvents": _text(request.form.get("flag_reliability_events")).lower() == "on",
                }
                ch_data["runtime"] = {
                    "liveRetryLimit": live_retry_limit,
                    "commandSetVersion": command_set_version or "v1",
                    "silenceTrimEnabled": _text(request.form.get("silence_trim_enabled")).lower() == "on",
                    "endpointingMode": endpointing_mode,
                }
                _save_runtime_config(runtime_config)
                dashboard_message = f"Runtime configuration updated for {selected_runtime_channel}."
            elif form_type == "release":
                latest_version = _text(request.form.get("latest_version"))
                download_url = _text(request.form.get("download_url"))
                notes = _text(request.form.get("release_notes"))
                mandatory = _text(request.form.get("mandatory_update")).lower() == "on"
                channel = _text(request.form.get("channel") or "stable").lower() or "stable"
                platform = _text(request.form.get("platform") or "windows").lower() or "windows"
                asset_type = _text(request.form.get("asset_type") or "exe").lower() or "exe"
                sha256_value = _text(request.form.get("sha256"))
                installer_args = _text(request.form.get("installer_args") or "/S")
                restart_required = _text(request.form.get("restart_required")).lower() == "on"

                if not re.match(r"^\d+\.\d+\.\d+$", latest_version):
                    dashboard_error = "Version must be in semantic format, e.g. 1.4.2"
                elif download_url and not (download_url.startswith("http://") or download_url.startswith("https://")):
                    dashboard_error = "Download URL must start with http:// or https://"
                else:
                    release_info = {
                        "latestVersion": latest_version,
                        "downloadUrl": download_url,
                        "notes": notes,
                        "mandatory": mandatory,
                        "publishedAt": _utc_now().isoformat(),
                        "channel": channel,
                        "platform": platform,
                        "assetType": asset_type if asset_type in {"exe", "msi", "zip"} else "exe",
                        "sha256": sha256_value.lower(),
                        "installerArgs": installer_args,
                        "restartRequired": restart_required,
                    }
                    _save_release_info(release_info)
                    dashboard_message = "Release metadata updated successfully."
            elif form_type == "license_action":
                if not store:
                    dashboard_error = _store_problem()[0]["message"]
                else:
                    action = _text(request.form.get("license_action")).lower()
                    license_id = _text(request.form.get("license_id"))
                    if action == "revoke":
                        reason = _text(request.form.get("license_reason") or "admin_revoke")
                        if store.set_revoke_state(license_id, True, reason=reason):
                            dashboard_message = "License revoked."
                        else:
                            dashboard_error = "Unable to revoke license."
                    elif action == "unrevoke":
                        if store.set_revoke_state(license_id, False):
                            dashboard_message = "License restored."
                        else:
                            dashboard_error = "Unable to restore license."
                    elif action == "topup":
                        try:
                            amount = int(request.form.get("topup_chars") or 0)
                        except ValueError:
                            amount = 0
                        if amount <= 0:
                            dashboard_error = "Top-up amount must be greater than zero."
                        elif store.top_up_chars(license_id, amount):
                            dashboard_message = f"Added {amount:,} chars."
                        else:
                            dashboard_error = "Unable to apply top-up."
            elif form_type == "license_search":
                license_query = _text(request.form.get("license_query"))

    runtime_config = _load_runtime_config()
    reliability_summary = _summarize_reliability_events()
    release_info = _load_release_info()
    runtime_channel_data = runtime_config["channels"].get(selected_runtime_channel, runtime_config["channels"]["stable"])

    api_configured = True
    masked_key = "not-configured"
    try:
        _ = get_mistral_api_key()
        masked_key = get_masked_api_key()
    except RuntimeError:
        api_configured = False

    license_rows: list[dict[str, Any]] = []
    if store:
        try:
            license_summary = store.dashboard_summary()
            license_rows = store.list_licenses(limit=120, query=license_query)
        except Exception as exc:
            dashboard_error = dashboard_error or f"License store error: {exc}"
    else:
        dashboard_error = dashboard_error or _store_problem()[0]["message"]

    return render_template(
        "admin_dashboard.html",
        admin_user=session.get("admin_user", "admin"),
        csrf_token=csrf_token,
        api_configured=api_configured,
        model_name=get_mistral_model(),
        masked_key=masked_key,
        release_info=release_info,
        runtime_config=runtime_config,
        runtime_channel_data=runtime_channel_data,
        selected_runtime_channel=selected_runtime_channel,
        reliability_summary=reliability_summary,
        dashboard_message=dashboard_message,
        dashboard_error=dashboard_error,
        license_summary=license_summary,
        license_rows=license_rows,
        license_query=license_query,
    )


@app.get("/admin/licenses/export.csv")
@login_required
def admin_export_licenses():
    store = _get_store()
    if not store:
        payload, status = _store_problem()
        return jsonify(payload), status

    query = _text(request.args.get("license_query"))
    rows = store.list_licenses(limit=2000, query=query)
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "licenseHint",
            "purchaseEmail",
            "saleId",
            "plan",
            "billingCycle",
            "priceType",
            "variantLabel",
            "status",
            "activeSeats",
            "seatLimit",
            "purchasedSeats",
            "usedChars",
            "usedWords",
            "quotaChars",
            "bonusChars",
            "remainingChars",
            "updatedAt",
            "lastVerifiedAt",
        ],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=voxify-license-export.csv"
    return response


@app.get("/api/site-status")
def site_status():
    api_is_set = True
    try:
        _ = get_mistral_api_key()
    except RuntimeError:
        api_is_set = False

    store = _get_store()
    db_ok = bool(store is not None)
    return jsonify(
        {
            "service": "voxify-website",
            "status": "ok",
            "apiConfigured": api_is_set,
            "licenseDbConfigured": db_ok,
        }
    )


@app.post("/api/license/activate")
def license_activate():
    payload = request.get_json(silent=True) or {}
    license_key = _text(payload.get("licenseKey"))
    product_id = _text(payload.get("productId") or os.getenv("GUMROAD_PRODUCT_ID", ""))
    device_id = _text(payload.get("deviceId"))
    device_name = _text(payload.get("deviceName"))
    if not license_key or not device_id:
        return jsonify({"success": False, "message": "licenseKey and deviceId are required."}), 400
    if not product_id:
        return jsonify({"success": False, "message": "License verification is not configured on server."}), 503

    store = _get_store()
    if not store:
        data, status = _store_problem()
        return jsonify(data), status

    gumroad_payload, gumroad_error = _gumroad_verify(license_key, product_id)
    if not gumroad_payload:
        return jsonify({"success": False, "active": False, "message": gumroad_error}), 401

    purchase = gumroad_payload.get("purchase") or {}
    entitlement, activation_error = store.activate_from_purchase(
        license_key=license_key,
        product_id=product_id,
        device_id=device_id,
        device_name=device_name,
        purchase=purchase,
    )
    if not entitlement:
        message_map = {
            "license_revoked": "This license was revoked.",
            "license_inactive": "This license is inactive.",
            "seat_limit_reached": "Seat limit reached for this license.",
        }
        return jsonify({"success": False, "message": message_map.get(activation_error, "Unable to activate this license.")}), 403

    token = _mint_license_token(entitlement["licenseId"], store.hash_device(device_id))
    return jsonify(_entitlement_payload(entitlement, token, include_runtime_key=True))


@app.post("/api/license/refresh")
def license_refresh():
    payload = request.get_json(silent=True) or {}
    device_id = _text(payload.get("deviceId"))
    token = _text(payload.get("token"))
    device_name = _text(payload.get("deviceName"))
    if not token or not device_id:
        return jsonify({"success": False, "message": "token and deviceId are required."}), 400

    store = _get_store()
    if not store:
        data, status = _store_problem()
        return jsonify(data), status

    entitlement, _, error = _validate_token_and_license(device_id, token)
    if not entitlement:
        return jsonify({"success": False, "message": error}), 401

    sync_key = _text(payload.get("licenseKey"))
    sync_product_id = _text(payload.get("productId"))
    if sync_key:
        product_id_to_verify = sync_product_id
        if not product_id_to_verify:
            license_doc = store.get_license_by_id(entitlement["licenseId"])
            product_id_to_verify = _text((license_doc or {}).get("productId"))

        if product_id_to_verify:
            gumroad_payload, gumroad_error = _gumroad_verify(sync_key, product_id_to_verify)
        else:
            gumroad_payload, gumroad_error = None, ""

        if gumroad_payload:
            purchase = gumroad_payload.get("purchase") or {}
            synced_entitlement, synced_error = store.activate_from_purchase(
                license_key=sync_key,
                product_id=product_id_to_verify,
                device_id=device_id,
                device_name=device_name,
                purchase=purchase,
            )
            if synced_entitlement:
                entitlement = synced_entitlement
            elif synced_error == "license_inactive":
                return jsonify({"success": False, "message": "License is inactive."}), 403
        elif "invalid or inactive" in gumroad_error.lower():
            return jsonify({"success": False, "message": "License is inactive."}), 403

    store.touch_activation(entitlement["licenseId"], device_id, device_name=device_name)
    doc = store.get_license_by_id(entitlement["licenseId"])
    if not doc:
        return jsonify({"success": False, "message": "License not found."}), 404
    refreshed = store.build_entitlement(doc)
    if refreshed["status"] != "active":
        return jsonify({"success": False, "message": "License is inactive."}), 403

    next_token = _mint_license_token(refreshed["licenseId"], store.hash_device(device_id))
    return jsonify(_entitlement_payload(refreshed, next_token, include_runtime_key=True))


@app.get("/api/license/status")
def license_status():
    device_id = _text(request.args.get("deviceId"))
    token = _text(request.args.get("token"))
    if not device_id or not token:
        return jsonify({"success": False, "message": "token and deviceId are required."}), 400

    entitlement, _, error = _validate_token_and_license(device_id, token)
    if not entitlement:
        return jsonify({"success": False, "message": error}), 401
    return jsonify({"success": True, "entitlement": entitlement})


@app.post("/api/license/consume")
def license_consume():
    payload = request.get_json(silent=True) or {}
    token = _text(payload.get("token"))
    device_id = _text(payload.get("deviceId"))
    session_id = _text(payload.get("sessionId"))
    mode = _text(payload.get("mode") or "batch")
    detail = _text(payload.get("detail"))
    idempotency_key = _text(payload.get("idempotencyKey"))
    try:
        chars_used = int(payload.get("charsUsed") or 0)
    except ValueError:
        chars_used = 0
    try:
        words_used = int(payload.get("wordsUsed") or 0)
    except ValueError:
        words_used = 0

    if not token or not device_id or not idempotency_key:
        return jsonify({"success": False, "message": "token, deviceId and idempotencyKey are required."}), 400
    if chars_used < 0:
        return jsonify({"success": False, "message": "charsUsed must be >= 0."}), 400
    if words_used < 0:
        return jsonify({"success": False, "message": "wordsUsed must be >= 0."}), 400

    entitlement, _, error = _validate_token_and_license(device_id, token)
    if not entitlement:
        return jsonify({"success": False, "message": error}), 401

    store = _get_store()
    if not store:
        data, status = _store_problem()
        return jsonify(data), status

    updated, consume_error = store.consume_chars(
        license_id=entitlement["licenseId"],
        chars_used=chars_used,
        words_used=words_used,
        mode=mode,
        session_id=session_id,
        idempotency_key=idempotency_key,
        detail=detail,
    )
    if not updated:
        code = 402 if consume_error == "quota_exceeded" else 403
        message = (
            "Quota reached for this cycle. Upgrade or top up to continue."
            if consume_error == "quota_exceeded"
            else "Unable to record usage."
        )
        return jsonify({"success": False, "message": message, "reason": consume_error}), code
    return jsonify({"success": True, "entitlement": updated})


@app.post("/api/gumroad/webhook")
def gumroad_webhook():
    store = _get_store()
    if not store:
        data, status = _store_problem()
        return jsonify(data), status

    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form.to_dict(flat=True)
    if not isinstance(payload, dict):
        payload = {}

    event_type = _text(payload.get("event") or payload.get("type") or payload.get("action") or "unknown")
    sale_id = _text(payload.get("sale_id") or payload.get("saleId"))
    event_key = _text(payload.get("id") or payload.get("event_id") or payload.get("timestamp"))
    is_new_event = store.record_webhook_event(event_key=event_key, event_type=event_type, payload=payload)
    if not is_new_event:
        return jsonify({"success": True, "updatedLicenses": 0, "duplicate": True})

    lowered = event_type.lower()
    status = "active"
    if any(item in lowered for item in ("refund", "chargeback", "cancel", "ended", "revoked", "dispute")):
        status = "inactive"
    updates = store.apply_webhook_purchase_update(payload=payload, event_type=event_type, fallback_status=status)
    if updates <= 0 and sale_id:
        updates = store.apply_webhook_status_update(sale_id=sale_id, status=status)
    return jsonify({"success": True, "updatedLicenses": updates})


@app.get("/api/desktop-bootstrap")
def desktop_bootstrap():
    token = _text(request.args.get("token"))
    device_id = _text(request.args.get("deviceId"))
    if not token or not device_id:
        return jsonify({"success": False, "message": "token and deviceId are required."}), 400

    entitlement, _, error = _validate_token_and_license(device_id, token)
    if not entitlement:
        return jsonify({"success": False, "message": error}), 401
    if entitlement.get("status") != "active":
        return jsonify({"success": False, "message": "License is inactive."}), 403
    if not entitlement.get("canTranscribe"):
        return jsonify({"success": False, "message": "Quota reached for this cycle. Upgrade or top up to continue."}), 402

    live_key = ""
    if ALLOW_CLIENT_LIVE_KEY:
        try:
            live_key = get_mistral_api_key()
        except RuntimeError:
            live_key = ""
    return jsonify(
        {
            "success": True,
            "apiKey": live_key,
            "model": get_mistral_model(),
            "license": entitlement,
        }
    )


@app.post("/api/transcribe")
def proxy_transcribe():
    token = _text(request.form.get("token"))
    device_id = _text(request.form.get("deviceId"))
    model = _text(request.form.get("model") or get_mistral_model())
    language = _text(request.form.get("language"))
    prompt = _text(request.form.get("prompt"))
    session_id = _text(request.form.get("sessionId"))
    idempotency_key = _text(request.form.get("idempotencyKey"))

    if not token or not device_id:
        return jsonify({"success": False, "message": "token and deviceId are required."}), 400
    if "file" not in request.files:
        return jsonify({"success": False, "message": "Audio file is required."}), 400

    entitlement, _, error = _validate_token_and_license(device_id, token)
    if not entitlement:
        return jsonify({"success": False, "message": error}), 401
    if entitlement.get("status") != "active":
        return jsonify({"success": False, "message": "License is inactive."}), 403
    remaining_before = int(entitlement.get("remainingChars") or 0)
    if remaining_before <= 0:
        return jsonify(
            {
                "success": False,
                "message": "Quota reached for this cycle. Upgrade or top up to continue.",
                "reason": "quota_exceeded",
            }
        ), 402

    try:
        api_key = get_mistral_api_key()
    except RuntimeError:
        return jsonify({"success": False, "message": "Mistral API key is not configured."}), 503

    audio_file = request.files["file"]
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": (audio_file.filename or "audio.wav", audio_file.stream, audio_file.mimetype or "audio/wav")}
    data = {"model": model}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt

    try:
        response = requests.post(
            "https://api.mistral.ai/v1/audio/transcriptions",
            headers=headers,
            data=data,
            files=files,
            timeout=60,
        )
    except requests.RequestException:
        return jsonify({"success": False, "message": "Unable to reach transcription provider."}), 502

    if response.status_code != 200:
        try:
            detail = response.json()
            message = detail.get("message") or detail.get("error") or str(detail)
        except Exception:
            message = response.text or f"HTTP {response.status_code}"
        return jsonify({"success": False, "message": f"Provider error [{response.status_code}]: {message}"}), 502

    try:
        response_payload = response.json()
    except ValueError:
        return jsonify({"success": False, "message": "Provider returned invalid JSON."}), 502
    text = _text(response_payload.get("text"))
    chars_used = len(text)
    words_used = _word_count(text)
    charge_chars = min(chars_used, max(0, remaining_before))
    charge_words = words_used
    if chars_used > 0 and charge_chars < chars_used and words_used > 0:
        charge_words = max(1, int(round(words_used * (charge_chars / float(chars_used)))))
    if not idempotency_key:
        idempotency_key = hashlib.sha256(
            f"{session_id}:{device_id}:{model}:{chars_used}:{time.time_ns()}".encode("utf-8")
        ).hexdigest()

    store = _get_store()
    if not store:
        data, status = _store_problem()
        return jsonify(data), status

    updated, consume_error = store.consume_chars(
        license_id=entitlement["licenseId"],
        chars_used=charge_chars,
        words_used=charge_words,
        mode="batch",
        session_id=session_id,
        idempotency_key=idempotency_key,
        detail="proxy_transcribe",
    )
    if not updated:
        return jsonify({"success": False, "message": "Unable to record usage.", "reason": consume_error}), 500

    return jsonify(
        {
            "success": True,
            "text": text,
            "usage": updated,
            "quotaLimited": bool(chars_used > remaining_before),
        }
    )


@app.post("/api/verify-license")
def verify_license():
    payload = request.get_json(silent=True) or {}
    license_key = _text(payload.get("licenseKey"))
    product_id = _text(payload.get("productId") or os.getenv("GUMROAD_PRODUCT_ID", ""))
    device_id = _text(payload.get("deviceId") or "legacy-device")
    if not license_key:
        return jsonify({"success": False, "message": "licenseKey is required."}), 400
    if not product_id:
        return jsonify({"success": False, "message": "License verification is not configured on server."}), 503

    gumroad_payload, gumroad_error = _gumroad_verify(license_key, product_id)
    if not gumroad_payload:
        return jsonify({"success": False, "active": False, "message": gumroad_error}), 401
    purchase = gumroad_payload.get("purchase") or {}

    store = _get_store()
    if not store:
        data, status = _store_problem()
        return jsonify(data), status

    entitlement, activation_error = store.activate_from_purchase(
        license_key=license_key,
        product_id=product_id,
        device_id=device_id,
        device_name=_text(payload.get("deviceName")),
        purchase=purchase,
    )
    if not entitlement:
        return jsonify({"success": False, "active": False, "message": activation_error}), 403

    token = _mint_license_token(entitlement["licenseId"], store.hash_device(device_id))
    return jsonify(
        {
            "success": True,
            "active": entitlement["status"] == "active",
            "purchaseEmail": purchase.get("email"),
            "saleId": purchase.get("sale_id"),
            "isSubscription": bool(purchase.get("subscription_id")),
            "subscriptionEndedAt": purchase.get("subscription_ended_at"),
            "subscriptionCancelledAt": purchase.get("subscription_cancelled_at"),
            "token": token,
            "entitlement": entitlement,
        }
    )


@app.get("/api/app-update")
def app_update():
    release_info = _load_release_info()
    current_version = _text(request.args.get("currentVersion"))
    requested_channel = _text(request.args.get("channel") or "stable").lower()
    requested_platform = _text(request.args.get("platform") or "windows").lower()

    same_channel = requested_channel == _text(release_info.get("channel") or "stable").lower()
    same_platform = requested_platform == _text(release_info.get("platform") or "windows").lower()
    candidate_available = same_channel and same_platform and bool(release_info.get("downloadUrl"))
    has_newer = _is_version_newer(current_version, release_info.get("latestVersion", "0.0.0"))

    return jsonify(
        {
            "success": True,
            "app": "voxify-desktop",
            "channel": release_info.get("channel", "stable"),
            "platform": release_info.get("platform", "windows"),
            "latestVersion": release_info.get("latestVersion", "1.0.0"),
            "downloadUrl": release_info.get("downloadUrl", ""),
            "notes": release_info.get("notes", ""),
            "publishedAt": release_info.get("publishedAt", ""),
            "mandatory": bool(release_info.get("mandatory")),
            "assetType": release_info.get("assetType", "exe"),
            "sha256": release_info.get("sha256", ""),
            "installerArgs": release_info.get("installerArgs", ""),
            "restartRequired": bool(release_info.get("restartRequired", True)),
            "currentVersion": current_version,
            "updateAvailable": bool(candidate_available and has_newer),
        }
    )


@app.get("/api/runtime-config")
def runtime_config():
    requested_channel = _text(request.args.get("channel") or "stable").lower()
    requested_platform = _text(request.args.get("platform") or "windows").lower()
    device_id = _text(request.args.get("deviceId"))

    channel, channel_data = _resolve_channel_runtime(requested_channel)
    rollout_percent = int(channel_data.get("rolloutPercent") or 100)
    in_rollout = _event_rollout_bucket(device_id) <= max(0, min(100, rollout_percent))

    return jsonify(
        {
            "success": True,
            "channel": channel,
            "platform": requested_platform,
            "inRollout": bool(in_rollout),
            "rolloutPercent": rollout_percent,
            "featureFlags": channel_data.get("featureFlags", {}),
            "runtime": channel_data.get("runtime", {}),
            "updatedAt": _utc_now().isoformat(),
        }
    )


@app.post("/api/reliability-event")
def reliability_event():
    payload = request.get_json(silent=True) or {}
    session_id = _text(payload.get("sessionId"))
    mode = _text(payload.get("mode")).lower()
    source = _text(payload.get("source")).lower()
    event_type = _text(payload.get("eventType")).lower()

    if not session_id or not mode or not source or not event_type:
        return jsonify({"success": False, "message": "sessionId, mode, source and eventType are required."}), 400

    latency_raw = str(payload.get("latencyMs") or "").strip()
    latency_value = int(latency_raw) if latency_raw.lstrip("-").isdigit() else 0

    event = {
        "sessionId": session_id[:80],
        "mode": mode[:32],
        "source": source[:32],
        "eventType": event_type[:64],
        "latencyMs": latency_value,
        "errorCode": _text(payload.get("errorCode")).lower()[:64],
        "detail": _text(payload.get("detail"))[:240],
        "timestamp": _text(payload.get("timestamp")) or _utc_now().isoformat(),
    }
    _append_reliability_event(event)
    return jsonify({"success": True})


@app.errorhandler(404)
def page_not_found(_error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(_error):
    return render_template("500.html"), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5050"))
    app.run(host="127.0.0.1", port=port, debug=False)
