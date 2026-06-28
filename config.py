"""
Config manager — loads and saves settings to config.json
"""

import json
import os
import time
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"

# ── In-memory cache to avoid redundant disk I/O on repeated loads ──────────
_config_cache: dict | None = None
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL: float = 2.0  # seconds before re-reading from disk


def _invalidate_cache() -> None:
    global _config_cache, _config_cache_time
    _config_cache = None
    _config_cache_time = 0.0


DEFAULT_CONFIG = {
    "model": "voxtral-mini-2602",
    "api_key": "",
    "language": "",          # kept for runtime compatibility; empty means auto-detect
    "sample_rate": 16000,
    "channels": 1,
    "theme": "dark",         # dark | light
    "window_x": 100,
    "window_y": 100,
    "always_on_top": True,
    "auto_type_delay": 3,    # seconds countdown before auto-typing
    "hotkey": "ctrl+shift+space",  # global push-to-record hotkey
    "mode": "Live",         # Batch | Live
    "source": "mic",         # mic | system
    "mic_device": "",        # sounddevice device index (int) or name substring; empty = default
    "check_for_updates": True,
    "ignored_update_version": "",
    "dictation_profile": "notes",  # email | chat | docs | notes | code_notes
    "reliability_mode": "balanced",  # balanced | latency | accuracy
    "voice_commands_enabled": True,
    "command_prefix": "command",
    "personal_dictionary": [],
    "text_replacements": {},
    "auto_fallback_enabled": True,
    "silence_trim_enabled": True,
    "live_retry_limit": 2,
    "runtime_channel": "stable",
    "send_reliability_events": False,
    "device_id": "",
    "gemini_model": "gemini-3.1-flash-live-preview",
    "gemini_voice": "Puck",
    "gemini_use_ephemeral_tokens": True,
    "gemini_session_resumption": False,
    "gemini_context_compression": False,
    "gemini_idle_timeout": 300,
    "gemini_thinking_level": "minimal",
    "gemini_thinking_budget": 0,
    "screen_share_resolution": "",
    "screen_share_pause_on_idle": True,
    "auto_minimize": True,
    "minimize_timeout": 10,
    "pc_control_enabled": True,
    "auto_install_updates": True,
    "restart_after_update": True,
    # ── Gemini Chat: Security & Safety ────────────────────────────────────────
    "gemini_tool_confirmation_enabled": True,
    "gemini_tool_confirmation_timeout": 10,
    "gemini_shell_command_blocked": True,
    "gemini_shell_timeout_max": 30,
    "gemini_tool_rate_limit": 10,
    "gemini_tool_rate_window": 60,
    # ── Gemini Chat: UI & UX ──────────────────────────────────────────────────
    "gemini_transcript_max_messages": 20,
    "gemini_show_transcript_panel": False,
    "gemini_listen_only_mode": False,
}


def load() -> dict:
    """Return merged config (file values override defaults), cached in memory."""
    global _config_cache, _config_cache_time
    now = time.time()
    if _config_cache is not None and (now - _config_cache_time) < _CONFIG_CACHE_TTL:
        return _config_cache
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user = json.load(f)
            _config_cache = {**DEFAULT_CONFIG, **user}
            _config_cache_time = now
            return _config_cache
        except Exception:
            pass
    _config_cache = dict(DEFAULT_CONFIG)
    _config_cache_time = now
    return _config_cache


def save(cfg: dict) -> None:
    """Persist config to disk and invalidate in-memory cache."""
    _invalidate_cache()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get(key: str, default=None):
    """Quick single-key read."""
    return load().get(key, default)


def set_value(key: str, value) -> None:
    """Quick single-key write."""
    cfg = load()
    cfg[key] = value
    save(cfg)
