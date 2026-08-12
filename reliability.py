"""Local and optional remote reliability event reporting."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
import threading
import uuid

import website_client

EVENT_LOG_FILE = Path(__file__).with_name("reliability_events.jsonl")

# Keep the local event log bounded: rotate once it exceeds these limits.
MAX_EVENT_LOG_BYTES = 2 * 1024 * 1024   # 2 MB
MAX_EVENT_LOG_LINES = 5000


ERROR_HINT_MAP = {
    "busy": "audio_device_busy",
    "stream": "stream_drop",
    "timeout": "network_timeout",
    "typing": "typing_failed",
    "clipboard": "typing_failed",
}


def new_session_id() -> str:
    return uuid.uuid4().hex


def normalize_error_code(message: str) -> str:
    lowered = (message or "").lower()
    for key, code in ERROR_HINT_MAP.items():
        if key in lowered:
            return code
    return "unknown_error"


def build_event(
    session_id: str,
    mode: str,
    source: str,
    event_type: str,
    latency_ms: int | None = None,
    error_code: str = "",
    detail: str = "",
) -> dict:
    return {
        "sessionId": session_id or "",
        "mode": (mode or "").lower(),
        "source": (source or "").lower(),
        "eventType": (event_type or "").lower(),
        "latencyMs": int(latency_ms or 0),
        "errorCode": (error_code or "").strip().lower(),
        "detail": (detail or "")[:240],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _rotate_event_log_if_needed() -> None:
    """Trim the local event log once it grows beyond the size/line limits."""
    try:
        if not EVENT_LOG_FILE.exists():
            return
        if EVENT_LOG_FILE.stat().st_size <= MAX_EVENT_LOG_BYTES:
            return
        lines = EVENT_LOG_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_EVENT_LOG_LINES:
            lines = lines[-MAX_EVENT_LOG_LINES:]
        EVENT_LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def log_local_event(event: dict) -> None:
    EVENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _rotate_event_log_if_needed()
    with EVENT_LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True) + "\n")


def log_event_async(event: dict, send_remote: bool = False) -> None:
    def _run() -> None:
        try:
            log_local_event(event)
        except Exception:
            pass
        if send_remote:
            try:
                website_client.post_reliability_event(event)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()

