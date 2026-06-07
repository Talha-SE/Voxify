"""
Config manager — loads and saves settings to config.json
"""

import json
import os
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "model": "voxtral-mini-2507",
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
    "screen_share_quality": 70,
    "screen_share_pause_on_idle": True,
    "auto_minimize": True,
    "minimize_timeout": 10,
    "pc_control_enabled": True,
    "auto_install_updates": True,
    "restart_after_update": True,
}


def load() -> dict:
    """Return merged config (file values override defaults)."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user = json.load(f)
            return {**DEFAULT_CONFIG, **user}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save(cfg: dict) -> None:
    """Persist config to disk."""
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
