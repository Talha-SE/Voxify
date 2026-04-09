import os
import re
import secrets
import time
import hashlib
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
import json
from collections import Counter

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for
import requests
from werkzeug.security import check_password_hash

from secure_api import get_masked_api_key, get_mistral_api_key, get_mistral_model

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "replace-me-in-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_SECURE_COOKIE", "false").lower() == "true"

LOGIN_WINDOW_SECONDS = 300
MAX_LOGIN_ATTEMPTS = 5
_failed_attempts = {}

GUMROAD_VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"
RELEASE_INFO_FILE = Path(__file__).with_name("release_info.json")
RUNTIME_CONFIG_FILE = Path(__file__).with_name("runtime_config.json")
RELIABILITY_EVENTS_FILE = Path(__file__).with_name("reliability_events.jsonl")

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


def _normalize_checkout_url(value: str, fallback: str) -> str:
    cleaned = (value or "").strip()
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
    env_user = (os.getenv("ADMIN_USERNAME") or "admin").strip()
    password_hash = (os.getenv("ADMIN_PASSWORD_HASH") or "").strip()

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
    normalized = (channel or "stable").strip().lower()
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

        event_type = (event.get("eventType") or "unknown").strip().lower()
        error_code = (event.get("errorCode") or "").strip().lower()
        latency = event.get("latencyMs")
        timestamp = (event.get("timestamp") or "").strip()

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
        "current_year": datetime.now(timezone.utc).year,
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
        form_token = request.form.get("csrf_token") or ""

        if not secrets.compare_digest(form_token, csrf_token):
            return render_template("admin_login.html", error_message="Invalid form token.", csrf_token=csrf_token), 400

        if _is_rate_limited(client_ip):
            error_message = "Too many attempts. Try again in a few minutes."
            return render_template("admin_login.html", error_message=error_message, csrf_token=csrf_token), 429

        username = (request.form.get("username") or "").strip()
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

    if request.method == "POST":
        form_token = request.form.get("csrf_token") or ""
        if not secrets.compare_digest(form_token, csrf_token):
            dashboard_error = "Invalid form token."
        else:
            form_type = (request.form.get("form_type") or "release").strip().lower()
            if form_type == "runtime":
                selected_channel = (request.form.get("runtime_channel") or "stable").strip().lower()
                if selected_channel not in {"stable", "beta"}:
                    selected_channel = "stable"

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
                command_set_version = (request.form.get("command_set_version") or "v1").strip()
                endpointing_mode = (request.form.get("endpointing_mode") or "adaptive").strip().lower()
                if endpointing_mode not in {"adaptive", "latency", "accuracy"}:
                    endpointing_mode = "adaptive"

                ch_data = runtime_config["channels"].setdefault(selected_channel, {})
                ch_data["rolloutPercent"] = rollout_percent
                ch_data["platform"] = (request.form.get("runtime_platform") or "windows").strip().lower() or "windows"
                ch_data["featureFlags"] = {
                    "voiceCommands": (request.form.get("flag_voice_commands") or "").strip().lower() == "on",
                    "autoFallback": (request.form.get("flag_auto_fallback") or "").strip().lower() == "on",
                    "reliabilityEvents": (request.form.get("flag_reliability_events") or "").strip().lower() == "on",
                }
                ch_data["runtime"] = {
                    "liveRetryLimit": live_retry_limit,
                    "commandSetVersion": command_set_version or "v1",
                    "silenceTrimEnabled": (request.form.get("silence_trim_enabled") or "").strip().lower() == "on",
                    "endpointingMode": endpointing_mode,
                }
                _save_runtime_config(runtime_config)
                dashboard_message = f"Runtime configuration updated for {selected_channel}."
            else:
                latest_version = (request.form.get("latest_version") or "").strip()
                download_url = (request.form.get("download_url") or "").strip()
                notes = (request.form.get("release_notes") or "").strip()
                mandatory = (request.form.get("mandatory_update") or "").strip().lower() == "on"
                channel = (request.form.get("channel") or "stable").strip().lower()
                platform = (request.form.get("platform") or "windows").strip().lower()

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
                        "publishedAt": datetime.now(timezone.utc).isoformat(),
                        "channel": channel or "stable",
                        "platform": platform or "windows",
                    }
                    _save_release_info(release_info)
                    dashboard_message = "Release metadata updated successfully."

    runtime_config = _load_runtime_config()
    reliability_summary = _summarize_reliability_events()

    api_configured = True
    masked_key = "not-configured"

    try:
        _ = get_mistral_api_key()
        masked_key = get_masked_api_key()
    except RuntimeError:
        api_configured = False

    return render_template(
        "admin_dashboard.html",
        admin_user=session.get("admin_user", "admin"),
        csrf_token=csrf_token,
        api_configured=api_configured,
        model_name=get_mistral_model(),
        masked_key=masked_key,
        release_info=release_info,
        runtime_config=runtime_config,
        reliability_summary=reliability_summary,
        dashboard_message=dashboard_message,
        dashboard_error=dashboard_error,
    )


@app.get("/api/site-status")
def site_status():
    api_is_set = True
    try:
        _ = get_mistral_api_key()
    except RuntimeError:
        api_is_set = False

    return jsonify(
        {
            "service": "sonus-website",
            "status": "ok",
            "apiConfigured": api_is_set,
        }
    )


@app.get("/api/desktop-bootstrap")
def desktop_bootstrap():
    try:
        api_key = get_mistral_api_key()
    except RuntimeError:
        return jsonify({"success": False, "message": "Mistral API key is not configured."}), 503

    return jsonify(
        {
            "success": True,
            "apiKey": api_key,
            "model": get_mistral_model(),
        }
    )


@app.post("/api/verify-license")
def verify_license():
    payload = request.get_json(silent=True) or {}
    license_key = (payload.get("licenseKey") or "").strip()
    product_id = (payload.get("productId") or os.getenv("GUMROAD_PRODUCT_ID", "")).strip()

    if not license_key or not product_id:
        return jsonify({"success": False, "message": "licenseKey and productId are required."}), 400

    verify_form = {
        "product_id": product_id,
        "license_key": license_key,
        "increment_uses_count": "false",
    }

    access_token = (os.getenv("GUMROAD_API_ACCESS_TOKEN") or "").strip()
    if access_token:
        verify_form["access_token"] = access_token

    try:
        response = requests.post(GUMROAD_VERIFY_URL, data=verify_form, timeout=15)
        data = response.json() if response.content else {}
    except requests.RequestException:
        return jsonify({"success": False, "message": "Unable to verify license right now."}), 502
    except ValueError:
        return jsonify({"success": False, "message": "Unexpected response from Gumroad."}), 502

    if response.status_code != 200 or not data.get("success"):
        return jsonify({"success": False, "active": False, "message": "Invalid or inactive license."}), 401

    purchase = data.get("purchase") or {}
    subscription_stopped = bool(
        purchase.get("subscription_cancelled_at") or purchase.get("subscription_ended_at")
    )

    return jsonify(
        {
            "success": True,
            "active": not subscription_stopped,
            "purchaseEmail": purchase.get("email"),
            "saleId": purchase.get("sale_id"),
            "isSubscription": bool(purchase.get("subscription_id")),
            "subscriptionEndedAt": purchase.get("subscription_ended_at"),
            "subscriptionCancelledAt": purchase.get("subscription_cancelled_at"),
        }
    )


@app.get("/api/app-update")
def app_update():
    release_info = _load_release_info()
    current_version = (request.args.get("currentVersion") or "").strip()
    requested_channel = (request.args.get("channel") or "stable").strip().lower()
    requested_platform = (request.args.get("platform") or "windows").strip().lower()

    same_channel = requested_channel == (release_info.get("channel") or "stable").lower()
    same_platform = requested_platform == (release_info.get("platform") or "windows").lower()
    candidate_available = same_channel and same_platform and bool(release_info.get("downloadUrl"))
    has_newer = _is_version_newer(current_version, release_info.get("latestVersion", "0.0.0"))

    payload = {
        "success": True,
        "app": "sonus-desktop",
        "channel": release_info.get("channel", "stable"),
        "platform": release_info.get("platform", "windows"),
        "latestVersion": release_info.get("latestVersion", "1.0.0"),
        "downloadUrl": release_info.get("downloadUrl", ""),
        "notes": release_info.get("notes", ""),
        "publishedAt": release_info.get("publishedAt", ""),
        "mandatory": bool(release_info.get("mandatory")),
        "currentVersion": current_version,
        "updateAvailable": bool(candidate_available and has_newer),
    }
    return jsonify(payload)


@app.get("/api/runtime-config")
def runtime_config():
    requested_channel = (request.args.get("channel") or "stable").strip().lower()
    requested_platform = (request.args.get("platform") or "windows").strip().lower()
    device_id = (request.args.get("deviceId") or "").strip()

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
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.post("/api/reliability-event")
def reliability_event():
    payload = request.get_json(silent=True) or {}
    session_id = (payload.get("sessionId") or "").strip()
    mode = (payload.get("mode") or "").strip().lower()
    source = (payload.get("source") or "").strip().lower()
    event_type = (payload.get("eventType") or "").strip().lower()

    if not session_id or not mode or not source or not event_type:
        return jsonify({"success": False, "message": "sessionId, mode, source and eventType are required."}), 400

    event = {
        "sessionId": session_id[:80],
        "mode": mode[:32],
        "source": source[:32],
        "eventType": event_type[:64],
        "latencyMs": int(payload.get("latencyMs") or 0) if str(payload.get("latencyMs", "")).strip("-").isdigit() else 0,
        "errorCode": (payload.get("errorCode") or "").strip().lower()[:64],
        "detail": (payload.get("detail") or "").strip()[:240],
        "timestamp": (payload.get("timestamp") or datetime.now(timezone.utc).isoformat()),
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
