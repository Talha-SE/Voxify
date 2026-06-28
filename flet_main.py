from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
import threading
import time
import uuid
import hashlib
from pathlib import Path
from typing import Optional

import flet as ft

# ── Robust Logging Setup ───────────────────────────────────────────────────
# Use basicConfig with force=True to ensure Flet subprocess writes to our file
_log_dir = Path(__file__).parent
_debug_log = _log_dir / "voxify_debug.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    filename=str(_debug_log),
    filemode="a",
    force=True,
)

# Suppress noisy flet/websocket transport debug messages
logging.getLogger("flet").setLevel(logging.WARNING)
logging.getLogger("flet_core").setLevel(logging.WARNING)
logging.getLogger("flet_transport").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("websockets.client").setLevel(logging.WARNING)
logging.getLogger("websockets.server").setLevel(logging.WARNING)

logger = logging.getLogger("VoxifyApp")

# Also add a stream handler for console visibility
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
_stream_handler.setLevel(logging.INFO)
logger.addHandler(_stream_handler)

logger.info("=" * 60)
logger.info("VoxifyApp initializing — logging configured")
logger.info("=" * 60)

# Thread safety lock shared across callbacks
_live_lock = threading.Lock()

import config
import app_info
import branding
import dictation_features
import license_cache
import output_handler
import website_client


# ── Lazy module loaders (heavy imports deferred until needed) ──────────────
_recorder_module = None

def _get_recorder():
    global _recorder_module
    if _recorder_module is None:
        import recorder as _recorder_module
    return _recorder_module


def _import_reliability():
    import reliability
    return reliability


def _import_realtime_transcriber():
    import realtime_transcriber as rt_module
    return rt_module


def _import_transcriber():
    import transcriber as tr_module
    return tr_module


def _import_gemini_live():
    import gemini_live_client
    return gemini_live_client


# ── Gemini Chat: Security Constants ─────────────────────────────────────────

# Tools that ALWAYS require user confirmation before execution
CONFIRMATION_REQUIRED_TOOLS: set[str] = {
    "run_shell_command", "kill_process", "write_file_content",
    "file_operation", "system_action", "open_app",
    "press_key_combination", "create_folder",
}

# Shell command patterns that are ALWAYS blocked (even with confirmation)
BLOCKED_SHELL_PATTERNS: list[str] = [
    "Remove-Item", "del ", "format ", "shutdown",
    "bcdedit", "reg delete", "reg add",
    "rd /s", "rmdir /s", "icacls",
    "net user", "net localgroup",
    "Set-MpPreference", "Stop-Service",
    "Disable-ComputerRestore", "taskkill /f",
]

# File paths that are NEVER writable by the AI
PROTECTED_PATHS: list[str] = [
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\System32", "C:\\SysWOW64",
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/boot",
]

APP_TITLE = "Voxify"
ACCENT = "#2563EB"
ACCENT_GLOW = "#22D3EE"
ACCENT_HOVER = "#1D4ED8"
BG = "#040914"
CARD = "#060D1F"
CARD_SOFT = "#0F172A"
CARD_ACTIVE = "#131C31"
BORDER = "#274F85"
TEXT = "#F8FBFF"
MUTED = "#89A2C7"
MUTED_SOFT = "#5D7598"
DANGER = "#FF453A"
DANGER_HOVER = "#E03B32"
SUCCESS = "#32D74B"
ACTIVE_DOT = "#67E8F9"
INACTIVE_DOT = "#60A5FA"
BAR_ACTIVE = "#22D3EE"
BAR_INACTIVE = "#335780"
WIDGET_GRADIENT_END = "#0B1A32"
SETTINGS_ICON = "#60A5FA"
CLOSE_ICON = "#93A8C6"
AUX_BG = "#B45309"
AUX_BORDER = "#FB923C"
MODE_BATCH_BG = "#1D4ED8"
MODE_BATCH_BORDER = "#60A5FA"
MODE_BATCH_TEXT = "#93C5FD"
BATCH_MODEL = "voxtral-mini-2602"
LIVE_MODELS = {"voxtral-mini-transcribe-realtime-2602"}
# Maps the user-facing config model to the actual realtime model for live mode.
LIVE_MODEL_MAP = {
    "voxtral-mini-2602": "voxtral-mini-transcribe-realtime-2602",
}
WIDGET_FULL_WIDTH = 200
WIDGET_FULL_HEIGHT = 160
WIDGET_MINI_WIDTH = 56
WIDGET_MINI_HEIGHT = 56

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "ACCENT": "#2563EB",
        "ACCENT_GLOW": "#22D3EE",
        "BG": "#040914",
        "CARD": "#060D1F",
        "CARD_SOFT": "#0F172A",
        "CARD_ACTIVE": "#131C31",
        "BORDER": "#274F85",
        "TEXT": "#F8FBFF",
        "MUTED": "#89A2C7",
        "MUTED_SOFT": "#5D7598",
        "DANGER": "#FF453A",
        "SUCCESS": "#32D74B",
        "ACTIVE_DOT": "#67E8F9",
        "INACTIVE_DOT": "#60A5FA",
        "BAR_ACTIVE": "#22D3EE",
        "BAR_INACTIVE": "#335780",
        "WIDGET_GRADIENT_END": "#0B1A32",
        "SETTINGS_ICON": "#60A5FA",
        "CLOSE_ICON": "#93A8C6",
        "AUX_BG": "#B45309",
        "AUX_BORDER": "#FB923C",
        "MODE_BATCH_BG": "#1D4ED8",
        "MODE_BATCH_BORDER": "#60A5FA",
        "MODE_BATCH_TEXT": "#93C5FD",
    },
    "light": {
        "ACCENT": "#2563EB",
        "ACCENT_GLOW": "#0891B2",
        "BG": "#EAF4FF",
        "CARD": "#F8FCFF",
        "CARD_SOFT": "#E7F0FD",
        "CARD_ACTIVE": "#DDEBFD",
        "BORDER": "#9AB8E8",
        "TEXT": "#0B1A32",
        "MUTED": "#3B5B88",
        "MUTED_SOFT": "#5E7DAA",
        "DANGER": "#DC2626",
        "SUCCESS": "#16A34A",
        "ACTIVE_DOT": "#0EA5E9",
        "INACTIVE_DOT": "#3B82F6",
        "BAR_ACTIVE": "#0EA5E9",
        "BAR_INACTIVE": "#A8C4EB",
        "WIDGET_GRADIENT_END": "#DDEBFD",
        "SETTINGS_ICON": "#2563EB",
        "CLOSE_ICON": "#3B5B88",
        "AUX_BG": "#F59E0B",
        "AUX_BORDER": "#D97706",
        "MODE_BATCH_BG": "#BFDBFE",
        "MODE_BATCH_BORDER": "#93C5FD",
        "MODE_BATCH_TEXT": "#1E3A8A",
    },
}


class VoxifyApp:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.cfg = config.load()

        self._recorder: Optional = None
        self._rt_transcriber: Optional = None
        self._click_listener = None
        self._waiting_click = False
        self._is_recording = False
        self._is_chatting = False
        self._chat_starting = False
        self._is_sharing_screen = False
        self._screen_share_paused = False
        self._screen_capture_thread: Optional[threading.Thread] = None
        self._last_frame_bytes: Optional[bytes] = None
        self._last_frame_pixels: Optional["np.ndarray"] = None
        self._stopping = False
        self._gemini_live_client = None
        self._live_raw_text: str = ""           # accumulated text from deltas (corrected by segments)
        self._live_segments: list[str] = []     # authoritative segment texts
        self._live_processed_marker: int = 0    # how many chars of processed text already typed
        self._live_paste_job = None
        self._runtime_api_key = ""
        self._api_last_check_at = 0.0
        self._api_cache_ttl_sec = 120.0
        self._gemini_api_key = ""
        self._gemini_api_last_check = 0.0
        self._api_check_in_flight = False
        self._api_lock = threading.Lock()
        self._license_token = ""
        self._license_entitlement: Optional[website_client.LicenseEntitlement] = None
        self._license_refresh_at = 0.0
        self._license_lock = threading.Lock()
        self._runtime_config: Optional[website_client.RuntimeConfig] = None
        self._runtime_config_last_fetch = 0.0
        self._runtime_config_ttl_sec = 300.0
        self._session_id = ""
        self._active_source = self.cfg.get("source", "mic")
        self._last_raw_transcript = ""
        self._typing_failed_pending = False
        self._live_retry_count = 0
        self._live_source_candidates: list[str] = []
        self._live_source_index = 0
        self._live_type_lock = threading.Lock()
        self._last_live_typed_char = ""
        self._is_minimized = False
        self._auto_minimize_timer: Optional[threading.Timer] = None
        # ── Gemini Chat: Modernization state ─────────────────────────────────
        self._transcript_history: list[dict] = []   # [{"role": "user"|"ai", "text": str, "ts": float}]
        self._transcript_panel_visible = False
        self._listen_only_mode = False
        self._tool_call_timestamps: list[float] = []  # for rate limiting
        self._pending_tool_confirmations: dict = {}   # call_id -> threading.Event
        self._chat_elapsed_start: float = 0.0

        self._wave_anim_running = False
        self._wave_anim_thread: Optional[threading.Thread] = None
        self._settings_process: Optional[subprocess.Popen] = None
        self._settings_opening = False
        self._config_mtime = self._get_config_mtime()
        self._theme_name = "dark"
        self._device_id = self._get_or_create_device_id()

        self._apply_theme_globals()

        self._setup_page()
        self._build_ui()
        threading.Thread(target=self._warmup_startup, daemon=True).start()
        threading.Thread(target=self._watch_config_changes, daemon=True).start()

    def _window_bgcolor(self) -> str:
        if sys.platform.startswith("win") or sys.platform == "darwin":
            return ft.Colors.TRANSPARENT
        return BG

    def _desired_theme_name(self) -> str:
        requested = (self.cfg.get("theme", "dark") or "dark").strip().lower()
        return requested if requested in THEMES else "dark"

    def _apply_theme_globals(self) -> bool:
        global ACCENT, ACCENT_GLOW, BG, CARD, CARD_SOFT, CARD_ACTIVE
        global BORDER, TEXT, MUTED, MUTED_SOFT, DANGER, SUCCESS
        global ACTIVE_DOT, INACTIVE_DOT, BAR_ACTIVE, BAR_INACTIVE
        global WIDGET_GRADIENT_END, SETTINGS_ICON, CLOSE_ICON
        global AUX_BG, AUX_BORDER, MODE_BATCH_BG, MODE_BATCH_BORDER, MODE_BATCH_TEXT

        theme_name = self._desired_theme_name()
        palette = THEMES[theme_name]

        ACCENT = palette["ACCENT"]
        ACCENT_GLOW = palette["ACCENT_GLOW"]
        BG = palette["BG"]
        CARD = palette["CARD"]
        CARD_SOFT = palette["CARD_SOFT"]
        CARD_ACTIVE = palette["CARD_ACTIVE"]
        BORDER = palette["BORDER"]
        TEXT = palette["TEXT"]
        MUTED = palette["MUTED"]
        MUTED_SOFT = palette["MUTED_SOFT"]
        DANGER = palette["DANGER"]
        SUCCESS = palette["SUCCESS"]
        ACTIVE_DOT = palette["ACTIVE_DOT"]
        INACTIVE_DOT = palette["INACTIVE_DOT"]
        BAR_ACTIVE = palette["BAR_ACTIVE"]
        BAR_INACTIVE = palette["BAR_INACTIVE"]
        WIDGET_GRADIENT_END = palette["WIDGET_GRADIENT_END"]
        SETTINGS_ICON = palette["SETTINGS_ICON"]
        CLOSE_ICON = palette["CLOSE_ICON"]
        AUX_BG = palette["AUX_BG"]
        AUX_BORDER = palette["AUX_BORDER"]
        MODE_BATCH_BG = palette["MODE_BATCH_BG"]
        MODE_BATCH_BORDER = palette["MODE_BATCH_BORDER"]
        MODE_BATCH_TEXT = palette["MODE_BATCH_TEXT"]

        changed = self._theme_name != theme_name
        self._theme_name = theme_name
        return changed

    def _apply_theme_to_controls(self) -> None:
        self.page.theme_mode = ft.ThemeMode.DARK if self._theme_name == "dark" else ft.ThemeMode.LIGHT
        window_bg = self._window_bgcolor()
        self.page.bgcolor = window_bg
        self.page.window.bgcolor = window_bg

        self.title_text.color = TEXT
        if not self._is_recording and not self._waiting_click and not self._api_check_in_flight:
            self.status_text.color = MUTED
        self.aux_chip_text.color = TEXT
        self.aux_chip.bgcolor = ft.Colors.with_opacity(0.28, AUX_BG)
        self.aux_chip.border = ft.Border.all(1, ft.Colors.with_opacity(0.55, AUX_BORDER))

        self.settings_icon.color = ft.Colors.with_opacity(0.8, SETTINGS_ICON)
        self.minimize_icon.color = ft.Colors.with_opacity(0.9, CLOSE_ICON)
        self.settings_btn.bgcolor = ft.Colors.with_opacity(0.2, CARD_ACTIVE)
        self.settings_btn.border = ft.Border.all(1, ft.Colors.with_opacity(0.2, BORDER))
        self.minimize_btn.bgcolor = ft.Colors.with_opacity(0.18, DANGER)
        self.minimize_btn.border = ft.Border.all(1, ft.Colors.with_opacity(0.45, DANGER))

        if self._is_chatting:
            self.chat_icon.name = ft.Icons.AUTO_AWESOME
            self.chat_icon.color = TEXT
            self.chat_btn.bgcolor = ACCENT
            self.chat_btn.gradient = ft.LinearGradient(
                begin=ft.Alignment(-1, 0),
                end=ft.Alignment(1, 0),
                colors=[ACCENT, ACCENT_GLOW],
            )
            self.chat_btn.shadow = ft.BoxShadow(
                blur_radius=16,
                spread_radius=0,
                color=ft.Colors.with_opacity(0.45, ACCENT_GLOW),
            )
            self.chat_btn.border = ft.Border.all(1, ft.Colors.with_opacity(0.5, ACCENT_GLOW))
            
            self.action_button.visible = False
            self.video_btn.visible = True
            # Show quick actions and transcript panel in chat mode
            self._quick_actions_row.visible = True
            self.mute_btn.visible = True
            self.history_btn.visible = True
            
            self.minimized_action_button.opacity = 0.4
            self.minimized_action_button.disabled = True
            self.minimized_action_button.bgcolor = ft.Colors.with_opacity(0.1, MUTED)
            self.minimized_action_icon.color = MUTED_SOFT
        else:
            self.chat_icon.name = ft.Icons.AUTO_AWESOME_ROUNDED
            self.chat_icon.color = ACCENT
            self.chat_btn.bgcolor = ft.Colors.with_opacity(0.2, CARD_ACTIVE)
            self.chat_btn.gradient = None
            self.chat_btn.shadow = None
            self.chat_btn.border = ft.Border.all(1, ft.Colors.with_opacity(0.2, BORDER))
            
            self.action_button.visible = True
            self.video_btn.visible = False
            # Hide quick actions and transcript panel when not in chat mode
            self._quick_actions_row.visible = False
            self._transcript_panel_container.visible = False
            self._transcript_panel_visible = False
            
            self.action_button.opacity = 1.0
            self.action_button.disabled = False
            self.minimized_action_button.opacity = 1.0
            self.minimized_action_button.disabled = False

        self.controls_group.border = None
        self.widget_shell.gradient = ft.LinearGradient(
            begin=ft.Alignment(-1, -1),
            end=ft.Alignment(1, 1),
            colors=[CARD, WIDGET_GRADIENT_END],
        )
        self.widget_shell.padding = (
            ft.Padding.all(0)
            if self._is_minimized
            else ft.Padding.symmetric(horizontal=10, vertical=6)
        )
        self.widget_shell.border = None
        self.widget_shell.shadow = None

        self._set_mode_badge()
        self._sync_action_visual(fg=ACCENT, border=ACCENT, text_color=self.action_label.color or TEXT)
        self.page.update()

    def _setup_page(self) -> None:
        self.page.title = APP_TITLE
        window_bg = self._window_bgcolor()
        self.page.bgcolor = window_bg
        self.page.padding = 0
        self.page.spacing = 0
        self.page.theme_mode = ft.ThemeMode.DARK if self._theme_name == "dark" else ft.ThemeMode.LIGHT
        self.page.window.bgcolor = window_bg
        self.page.window.width = WIDGET_FULL_WIDTH
        self.page.window.height = WIDGET_FULL_HEIGHT
        self.page.window.min_width = WIDGET_FULL_WIDTH
        self.page.window.max_width = WIDGET_FULL_WIDTH
        self.page.window.min_height = WIDGET_FULL_HEIGHT
        self.page.window.max_height = WIDGET_FULL_HEIGHT
        self.page.window.resizable = False
        self.page.window.shadow = False
        self.page.window.title_bar_hidden = True
        self.page.window.title_bar_buttons_hidden = True
        self.page.window.frameless = True
        self.page.window.prevent_close = True
        self.page.window.always_on_top = bool(self.cfg.get("always_on_top", True))
        self.page.window.movable = True
        try:
            logo_path = branding.resolve_window_icon_path()
            if logo_path is not None:
                self.page.window.icon = str(logo_path.resolve())
        except Exception:
            pass

        def _on_window_event(event) -> None:
            event_type = str(getattr(event, "type", "")).lower()
            event_data = str(getattr(event, "data", "")).lower()
            if "close" in event_type or event_data == "close":
                self._close_app(None)

        try:
            self.page.window.on_event = _on_window_event
        except Exception:
            pass

    def _auto_minimize_enabled(self) -> bool:
        return bool(self.cfg.get("auto_minimize", True))

    def _auto_minimize_timeout_sec(self) -> int:
        try:
            timeout = int(self.cfg.get("minimize_timeout", 10))
        except (TypeError, ValueError):
            timeout = 10
        return max(5, min(120, timeout))

    def _cancel_auto_minimize_timer(self) -> None:
        if self._auto_minimize_timer:
            try:
                self._auto_minimize_timer.cancel()
            except Exception:
                pass
            self._auto_minimize_timer = None

    def _schedule_auto_minimize_timer(self) -> None:
        self._cancel_auto_minimize_timer()
        if not self._auto_minimize_enabled() or self._is_minimized:
            return

        timeout = self._auto_minimize_timeout_sec()

        def _minimize() -> None:
            try:
                self.page.run_thread(self._set_minimized, True)
            except RuntimeError:
                return

        timer = threading.Timer(timeout, _minimize)
        timer.daemon = True
        self._auto_minimize_timer = timer
        timer.start()

    def _register_widget_interaction(self) -> None:
        if self._is_minimized:
            self._set_minimized(False)
        self._schedule_auto_minimize_timer()

    def _on_widget_hover(self, event: ft.ControlEvent) -> None:
        if str(event.data).lower() == "true":
            self._register_widget_interaction()

    def _set_minimized(self, minimized: bool) -> None:
        if minimized == self._is_minimized:
            return

        self._is_minimized = minimized
        self.full_mode_container.visible = not minimized
        self.minimized_mode_container.visible = minimized

        try:
            if minimized:
                self.widget_shell.padding = ft.Padding.all(0)
                self.page.window.min_width = WIDGET_MINI_WIDTH
                self.page.window.max_width = WIDGET_MINI_WIDTH
                self.page.window.min_height = WIDGET_MINI_HEIGHT
                self.page.window.max_height = WIDGET_MINI_HEIGHT
                self.page.window.width = WIDGET_MINI_WIDTH
                self.page.window.height = WIDGET_MINI_HEIGHT
                self._cancel_auto_minimize_timer()
            else:
                self.widget_shell.padding = ft.Padding.symmetric(horizontal=10, vertical=6)
                self.page.window.min_width = WIDGET_FULL_WIDTH
                self.page.window.max_width = WIDGET_FULL_WIDTH
                self.page.window.min_height = WIDGET_FULL_HEIGHT
                self.page.window.max_height = WIDGET_FULL_HEIGHT
                self.page.window.width = WIDGET_FULL_WIDTH
                self.page.window.height = WIDGET_FULL_HEIGHT
                self._schedule_auto_minimize_timer()
        except Exception:
            pass

        try:
            self.page.update()
        except Exception:
            pass

    def _build_ui(self) -> None:
        self.title_text = ft.Text("Voxify", size=10, weight=ft.FontWeight.W_900, color=TEXT)
        self.status_text = ft.Text("Standby", size=7, weight=ft.FontWeight.W_700, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.mode_badge_text = ft.Text("LIVE", size=6, weight=ft.FontWeight.W_800, color=ACCENT_GLOW)
        self.mode_badge = ft.Container(padding=ft.Padding.symmetric(horizontal=5, vertical=1), border_radius=999, bgcolor=ft.Colors.with_opacity(0.15, ACCENT), border=ft.Border.all(1, ft.Colors.with_opacity(0.4, ACCENT)), content=self.mode_badge_text)
        self.aux_chip_text = ft.Text("", size=7, weight=ft.FontWeight.W_700, color=TEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.aux_chip = ft.Container(visible=False, padding=ft.Padding.symmetric(horizontal=6, vertical=2), border_radius=999, bgcolor=ft.Colors.with_opacity(0.28, AUX_BG), border=ft.Border.all(1, ft.Colors.with_opacity(0.55, AUX_BORDER)), content=self.aux_chip_text)

        self.pulse_ring = ft.Container(width=8, height=8, border_radius=4, bgcolor=ft.Colors.with_opacity(0.3, INACTIVE_DOT), alignment=ft.Alignment(0, 0), animate=120)
        self.pulse_core = ft.Container(width=5, height=5, border_radius=2.5, bgcolor=INACTIVE_DOT, animate=120)
        indicator = ft.Container(width=12, height=12, alignment=ft.Alignment(0, 0), content=ft.Stack(width=12, height=12, controls=[ft.Container(alignment=ft.Alignment(0, 0), content=self.pulse_ring), ft.Container(alignment=ft.Alignment(0, 0), content=self.pulse_core)]))

        self.wave_bars: list[ft.Container] = []
        for _ in range(5):
            bar = ft.Container(width=2, height=5, border_radius=2, bgcolor=BAR_INACTIVE, animate=120)
            self.wave_bars.append(bar)
        waveform = ft.Container(width=44, height=16, alignment=ft.Alignment(0, 0), content=ft.Row(spacing=2, alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.END, controls=self.wave_bars))

        self.action_label = ft.Text("Start", color=TEXT)
        self.action_icon = ft.Icon(ft.Icons.MIC_NONE_ROUNDED, size=16, color=ACCENT)
        self.action_button = ft.Container(width=36, height=36, border_radius=18, alignment=ft.Alignment(0, 0), bgcolor=CARD_ACTIVE, border=ft.Border.all(1, ft.Colors.with_opacity(0.45, ACCENT)), content=self.action_icon, on_click=self._on_action_click, animate=160, shadow=ft.BoxShadow(blur_radius=14, spread_radius=0, color=ft.Colors.with_opacity(0.22, ACCENT)))
        self.minimized_action_icon = ft.Icon(ft.Icons.MIC_NONE_ROUNDED, size=20, color=ACCENT)
        self.minimized_action_button = ft.Container(width=56, height=56, border_radius=28, alignment=ft.Alignment(0, 0), bgcolor=CARD_ACTIVE, border=ft.Border.all(1, ft.Colors.with_opacity(0.45, ACCENT)), content=self.minimized_action_icon, on_click=self._on_action_click, animate=160, shadow=ft.BoxShadow(blur_radius=16, spread_radius=0, color=ft.Colors.with_opacity(0.24, ACCENT)))

        self.settings_icon = ft.Icon(ft.Icons.SETTINGS_ROUNDED, size=14, color=ft.Colors.with_opacity(0.8, SETTINGS_ICON))
        self.settings_btn = ft.Container(width=28, height=28, border_radius=14, alignment=ft.Alignment(0, 0), bgcolor=ft.Colors.with_opacity(0.2, CARD_ACTIVE), border=ft.Border.all(1, ft.Colors.with_opacity(0.2, BORDER)), content=self.settings_icon, on_click=self._open_settings, ink=True)
        self.minimize_icon = ft.Icon(ft.Icons.CLOSE_ROUNDED, size=14, color=ft.Colors.with_opacity(0.9, CLOSE_ICON))
        self.minimize_btn = ft.Container(width=28, height=28, border_radius=14, alignment=ft.Alignment(0, 0), bgcolor=ft.Colors.with_opacity(0.18, DANGER), border=ft.Border.all(1, ft.Colors.with_opacity(0.45, DANGER)), content=self.minimize_icon, on_click=self._close_app, ink=True)
        self.chat_icon = ft.Icon(ft.Icons.AUTO_AWESOME_ROUNDED, size=14, color=ACCENT)
        self.chat_btn = ft.Container(width=28, height=28, border_radius=14, alignment=ft.Alignment(0, 0), bgcolor=ft.Colors.with_opacity(0.2, CARD_ACTIVE), border=ft.Border.all(1, ft.Colors.with_opacity(0.2, BORDER)), content=self.chat_icon, on_click=self._on_chat_click, ink=True)
        
        self.video_icon = ft.Icon(ft.Icons.VIDEOCAM_OUTLINED, size=14, color=ACCENT)
        self.video_btn = ft.Container(width=36, height=36, border_radius=18, alignment=ft.Alignment(0, 0), bgcolor=ft.Colors.with_opacity(0.2, CARD_ACTIVE), border=ft.Border.all(1, ft.Colors.with_opacity(0.2, BORDER)), content=self.video_icon, on_click=self._on_video_click, visible=False, ink=True)

        # ── Quick action buttons (visible only in chat mode) ──────────────
        self.mute_icon = ft.Icon(ft.Icons.MIC_ROUNDED, size=12, color=ACCENT)
        self.mute_btn = ft.Container(width=24, height=24, border_radius=12, alignment=ft.Alignment(0, 0), bgcolor=ft.Colors.with_opacity(0.2, CARD_ACTIVE), border=ft.Border.all(1, ft.Colors.with_opacity(0.2, BORDER)), content=self.mute_icon, on_click=lambda _: self._toggle_listen_only(), visible=False, ink=True)
        self.history_icon = ft.Icon(ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED, size=12, color=ACCENT)
        self.history_btn = ft.Container(width=24, height=24, border_radius=12, alignment=ft.Alignment(0, 0), bgcolor=ft.Colors.with_opacity(0.2, CARD_ACTIVE), border=ft.Border.all(1, ft.Colors.with_opacity(0.2, BORDER)), content=self.history_icon, on_click=lambda _: self._toggle_transcript_panel(), visible=False, ink=True)

        # ── Transcript history panel ─────────────────────────────────────
        self._transcript_list = ft.Column(spacing=4, controls=[])
        self._transcript_chevron = ft.Icon(ft.Icons.KEYBOARD_ARROW_UP_ROUNDED, size=12, color=MUTED)
        transcript_header = ft.Container(
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            on_click=lambda _: self._toggle_transcript_panel(),
            content=ft.Row(
                spacing=4,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text("Transcript", size=7, weight=ft.FontWeight.W_700, color=MUTED),
                    self._transcript_chevron,
                ]
            )
        )
        self._transcript_panel_container = ft.Container(
            visible=False,
            height=120,
            padding=ft.Padding.symmetric(horizontal=4),
            content=ft.Column(
                spacing=4,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
                controls=[transcript_header, self._transcript_list]
            )
        )

        brand_block = ft.Column(
            spacing=0, 
            tight=True, 
            horizontal_alignment=ft.CrossAxisAlignment.START,
            controls=[
                ft.Row(spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[self.title_text, self.mode_badge]), 
                self.status_text, 
                self.aux_chip
            ]
        )
        self.controls_group = ft.Container(padding=ft.Padding.symmetric(vertical=4), content=ft.Row(spacing=6, alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[self.action_button, self.video_btn, self.chat_btn, self.settings_btn, self.minimize_btn]))

        # Quick actions row (visible only during chat mode)
        self._quick_actions_row = ft.Row(
            spacing=4,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[self.mute_btn, self.history_btn],
            visible=False,
        )

        self.full_mode_container = ft.Container(visible=True, content=ft.Column(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8, controls=[ft.Row(alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8, controls=[indicator, brand_block]), ft.Column(horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4, controls=[waveform, self.aux_chip]), self._quick_actions_row, self._transcript_panel_container, self.controls_group]))
        self.minimized_mode_container = ft.Container(visible=False, alignment=ft.Alignment(0, 0), content=self.minimized_action_button)

        self.widget_shell = ft.Container(expand=True, margin=ft.Margin.all(0), padding=ft.Padding.symmetric(horizontal=12, vertical=12), border_radius=20, gradient=ft.LinearGradient(begin=ft.Alignment(-1, -1), end=ft.Alignment(1, 1), colors=[CARD, WIDGET_GRADIENT_END]), on_hover=self._on_widget_hover, content=ft.Stack(expand=True, controls=[self.full_mode_container, self.minimized_mode_container]))
        root = ft.WindowDragArea(maximizable=False, content=self.widget_shell)

        # ── Set initial UI state before first paint ──────────────────────
        mode_value = (self.cfg.get("mode") or "Batch").strip().lower()
        mode_label = "Live" if mode_value == "live" else "Batch"
        self.mode_badge_text.value = mode_label.upper()
        if mode_label == "Live":
            self.mode_badge.bgcolor = ft.Colors.with_opacity(0.16, ACCENT)
            self.mode_badge.border = ft.Border.all(1, ft.Colors.with_opacity(0.45, ACCENT))
            self.mode_badge_text.color = ACCENT_GLOW
        else:
            self.mode_badge.bgcolor = ft.Colors.with_opacity(0.16, MODE_BATCH_BG)
            self.mode_badge.border = ft.Border.all(1, ft.Colors.with_opacity(0.4, MODE_BATCH_BORDER))
            self.mode_badge_text.color = MODE_BATCH_TEXT
        self.action_label.value = "Start"
        self.action_label.color = TEXT
        self.status_text.value = "Ready"

        self.page.add(root)
        self.page.update()

        self._start_wave_animation()
        self._schedule_auto_minimize_timer()

    def _set_mode_badge(self) -> None:
        mode_value = (self.cfg.get("mode") or "Batch").strip().lower()
        mode_label = "Live" if mode_value == "live" else "Batch"
        self.mode_badge_text.value = mode_label.upper()
        if mode_label == "Live":
            self.mode_badge.bgcolor = ft.Colors.with_opacity(0.16, ACCENT)
            self.mode_badge.border = ft.Border.all(1, ft.Colors.with_opacity(0.45, ACCENT))
            self.mode_badge_text.color = ACCENT_GLOW
        else:
            self.mode_badge.bgcolor = ft.Colors.with_opacity(0.16, MODE_BATCH_BG)
            self.mode_badge.border = ft.Border.all(1, ft.Colors.with_opacity(0.4, MODE_BATCH_BORDER))
            self.mode_badge_text.color = MODE_BATCH_TEXT
        self.page.update()

    def _is_live_mode(self) -> bool:
        return (self.cfg.get("mode") or "Live").strip().lower() == "live"

    def _selected_batch_model(self) -> str:
        """Return the model to use for batch transcription.

        If the user's selected model supports batch (it's a valid Mistral
        transcription model), use it as-is. Otherwise fall back to the
        default batch model.
        """
        selected = (self.cfg.get("model") or BATCH_MODEL).strip().lower()
        if selected in LIVE_MODELS:
            return selected
        return BATCH_MODEL

    def _set_action(self, label: str, fg: str, border: str, text_color: str, on_click) -> None:
        self.action_label.value = label
        self.action_label.color = text_color
        self.action_button.on_click = on_click
        self._sync_action_visual(fg=fg, border=border, text_color=text_color)
        self.page.update()

    def _sync_action_visual(self, fg: str, border: str, text_color: str) -> None:
        label = (self.action_label.value or "").strip().lower()
        is_busy = any(token in label for token in ("checking", "preparing", "stopping"))
        is_stop = "stop" in label
        is_cancel = "cancel" in label
        is_copy = "copy" in label
        is_settings = "settings" in label
        is_retry = "retry" in label

        icon_name = ft.Icons.MIC_NONE_ROUNDED
        icon_color = ACCENT
        button_bg = CARD_ACTIVE
        button_border = ft.Colors.with_opacity(0.45, border)
        button_gradient = None
        glow_color = ft.Colors.with_opacity(0.2, ACCENT)

        if is_stop:
            icon_name = ft.Icons.MIC_ROUNDED
            icon_color = TEXT
            button_bg = ACCENT
            button_border = ft.Colors.with_opacity(0.5, ACCENT_GLOW)
            button_gradient = ft.LinearGradient(begin=ft.Alignment(-1, 0), end=ft.Alignment(1, 0), colors=[ACCENT, ACCENT_GLOW])
            glow_color = ft.Colors.with_opacity(0.45, ACCENT_GLOW)
        elif is_cancel:
            icon_name = ft.Icons.CANCEL_ROUNDED
            icon_color = DANGER
            button_bg = CARD_ACTIVE
            button_border = ft.Colors.with_opacity(0.55, DANGER)
            glow_color = ft.Colors.with_opacity(0.2, DANGER)
        elif is_copy:
            icon_name = ft.Icons.CONTENT_COPY_ROUNDED
            icon_color = ACCENT_GLOW
            button_bg = CARD_ACTIVE
            button_border = ft.Colors.with_opacity(0.5, ACCENT)
            glow_color = ft.Colors.with_opacity(0.25, ACCENT)
        elif is_settings:
            icon_name = ft.Icons.SETTINGS_ROUNDED
            icon_color = ACCENT
            button_bg = CARD_ACTIVE
            button_border = ft.Border.all(1, ft.Colors.with_opacity(0.5, ACCENT))
            glow_color = ft.Colors.with_opacity(0.24, ACCENT)
        elif is_retry:
            icon_name = ft.Icons.REFRESH_ROUNDED
            icon_color = ACCENT_GLOW
            button_bg = CARD_ACTIVE
            button_border = ft.Border.all(1, ft.Colors.with_opacity(0.5, ACCENT))
            glow_color = ft.Colors.with_opacity(0.24, ACCENT)
        elif is_busy:
            icon_name = ft.Icons.HOURGLASS_TOP_ROUNDED
            icon_color = MUTED
            button_bg = CARD_SOFT
            button_border = ft.Colors.with_opacity(0.45, BORDER)
            glow_color = ft.Colors.with_opacity(0.0, ACCENT)
        elif "start" in label:
            icon_name = ft.Icons.MIC_NONE_ROUNDED
            icon_color = ACCENT
            button_bg = CARD_ACTIVE
            button_border = ft.Colors.with_opacity(0.45, ACCENT)
            glow_color = ft.Colors.with_opacity(0.22, ACCENT)
        else:
            icon_name = ft.Icons.RADIO_BUTTON_CHECKED_ROUNDED
            icon_color = text_color
            button_bg = fg
            button_border = ft.Colors.with_opacity(0.45, border)
            glow_color = ft.Colors.with_opacity(0.15, border)

        self.action_icon.name = icon_name
        self.action_icon.color = MUTED_SOFT if self._is_chatting else icon_color
        self.action_button.bgcolor = ft.Colors.with_opacity(0.1, MUTED) if self._is_chatting else button_bg
        self.action_button.gradient = None if self._is_chatting else button_gradient
        self.action_button.border = ft.Border.all(1, button_border) if not isinstance(button_border, ft.Border) else button_border
        self.action_button.shadow = ft.BoxShadow(blur_radius=16, spread_radius=0, color=ft.Colors.with_opacity(0.0 if self._is_chatting else 1.0, glow_color) if isinstance(glow_color, str) else glow_color)

        self.minimized_action_icon.name = icon_name
        self.minimized_action_icon.color = MUTED_SOFT if self._is_chatting else icon_color
        self.minimized_action_button.bgcolor = ft.Colors.with_opacity(0.1, MUTED) if self._is_chatting else button_bg
        self.minimized_action_button.gradient = None if self._is_chatting else button_gradient
        self.minimized_action_button.border = ft.Border.all(1, button_border) if not isinstance(button_border, ft.Border) else button_border
        self.minimized_action_button.shadow = ft.BoxShadow(blur_radius=16, spread_radius=0, color=ft.Colors.with_opacity(0.0 if self._is_chatting else 1.0, glow_color) if isinstance(glow_color, str) else glow_color)

    def _set_aux_chip(self, text: str = "", active: bool = False) -> None:
        self.aux_chip.visible = active
        if active:
            self.aux_chip_text.value = text
        self.page.update()

    def _is_visual_active(self) -> bool:
        return self._is_recording

    def _start_wave_animation(self) -> None:
        if self._wave_anim_running:
            return
        self._wave_anim_running = True
        wave_heights = ((0.2, 0.6, 0.3, 0.8, 0.2), (0.2, 0.8, 0.4, 1.0, 0.2), (0.2, 0.5, 0.9, 0.4, 0.2), (0.2, 1.0, 0.5, 0.7, 0.2), (0.2, 0.7, 0.3, 0.9, 0.2))

        def _animate() -> None:
            step = 0
            while self._wave_anim_running:
                try:
                    active = self._is_visual_active()
                    pattern = wave_heights[step % len(wave_heights)] if active else wave_heights[0]
                    pulse = (math.sin(step * 0.6) + 1.0) / 2.0
                    for index, bar in enumerate(self.wave_bars):
                        bar.height = int(4 + pattern[index] * 10)
                        bar.bgcolor = BAR_ACTIVE if active else BAR_INACTIVE
                    ring_size = int(8 + (4 * pulse if active else 2 * pulse))
                    ring_alpha = 0.45 if active else (0.2 + 0.12 * pulse)
                    ring_color = ACCENT_GLOW if active else INACTIVE_DOT
                    self.pulse_ring.width = ring_size
                    self.pulse_ring.height = ring_size
                    self.pulse_ring.border_radius = ring_size / 2
                    self.pulse_ring.bgcolor = ft.Colors.with_opacity(ring_alpha, ring_color)
                    self.pulse_core.bgcolor = ACTIVE_DOT if active else INACTIVE_DOT
                    self.page.update()
                except Exception:
                    self._wave_anim_running = False
                    break
                step += 1
                time.sleep(0.15 if active else 0.28)

        threading.Thread(target=_animate, daemon=True).start()

    def _on_chat_click(self, _event) -> None:
        self._register_widget_interaction()
        if self._is_chatting:
            self._stop_chat_mode()
            return
        # Guard against rapid double-click
        if hasattr(self, '_chat_starting') and self._chat_starting:
            return
        self._chat_starting = True
        try:
            self._api_check_in_flight = False
            self._waiting_click = False
            self._stop_target_listener()
            self.page.window.opacity = 1.0
            if self._is_recording:
                self._is_recording = False
                if self._rt_transcriber: self._rt_transcriber.stop(); self._rt_transcriber = None
                if self._recorder: self._recorder = None
            self._start_chat_mode()
        finally:
            self._chat_starting = False

    def _start_chat_mode(self) -> None:
        self._is_chatting = True
        self._set_status("Connecting to Gemini...", ACCENT, animate=True)
        self._apply_theme_to_controls()
        try:
            gemini_key = ""
            ephemeral_token = None
            gemini_model = self.cfg.get("gemini_model", "gemini-3.1-flash-live-preview")
            gemini_voice = self.cfg.get("gemini_voice", "Puck")
            use_ephemeral = self.cfg.get("gemini_use_ephemeral_tokens", True)

            if use_ephemeral:
                try:
                    ephemeral_result = self._get_gemini_ephemeral_token()
                    ephemeral_token = ephemeral_result.token
                    gemini_model = ephemeral_result.model
                    logger.info("Using ephemeral token for Gemini connection.")
                except Exception as exc:
                    logger.warning(f"Ephemeral token failed, falling back to API key: {exc}")
                    use_ephemeral = False

            if not use_ephemeral or not ephemeral_token:
                gemini_key = self._get_gemini_api_key()

            system_instruction = (
                "You are Voxify, a highly intelligent and autonomous PC voice assistant.\n\n"
                "## CRITICAL VOICE ONLY RULES (MUST OBEY):\n"
                "1. NO MARKDOWN OR BOLD TEXT: You must NEVER use bold markdown formatting (like '**') or headers in your speech. Speak strictly in normal plain-text conversational English sentences. Never output strings like '**Initiating Contact Sequence**' or '**Acquiring Window Data**'.\n"
                "2. NO TOOL NARRATION: You must NEVER speak about your tool calls, thoughts, plans, or step-by-step progress. Do not say 'I am opening...', 'Let me check...', 'I've retrieved...', or explain your math/calculations. Do not announce context gathering.\n"
                "3. BE EXTREMELY CONCISE: Speak only one or two short sentences. Answer directly and wait for the user.\n"
                "4. SILENT CONTEXT GATHERING: When you gather context using 'get_active_window_info' or 'read_clipboard', do it completely silently. Just call the tools and use the data to respond directly to the user's query.\n"
                "5. READING/LOOKING AT THE SCREEN: If the user asks you to read, locate, click, or analyze text/buttons on the screen, call the 'parse_screen_text' tool immediately. Never say you cannot read the screen or that tools are insufficient.\n"
                "6. ALARMS AND TIMERS: If the user asks you to set an alarm, timer, or reminder, calculate the duration in seconds and call the 'set_timer' tool.\n"
                "7. VERIFICATION: Use the screen share (if enabled) to verify visual actions (typing, clicking) succeeded. If they failed, try an alternative silently or report the failure concisely.\n"
                "8. language: Speak only in English."
            )

            SILENT = "SILENT EXECUTION. "
            tools = [{"functionDeclarations": [
                {"name": "open_app", "description": SILENT + "Searches for and opens an application on the computer.", "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "App name (e.g., 'chrome', 'notepad')."}}, "required": ["query"]}},
                {"name": "mouse_click", "description": SILENT + "Clicks at specific screen coordinates.", "parameters": {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}, "button": {"type": "STRING", "enum": ["left", "right", "middle"], "default": "left"}, "double": {"type": "BOOLEAN", "default": False}}, "required": ["x", "y"]}},
                {"name": "move_mouse", "description": SILENT + "Moves the mouse cursor to specific screen coordinates.", "parameters": {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}, "duration": {"type": "NUMBER", "default": 0.2}}, "required": ["x", "y"]}},
                {"name": "move_mouse_relative", "description": SILENT + "Moves the mouse cursor by a pixel offset from its current position.", "parameters": {"type": "OBJECT", "properties": {"dx": {"type": "INTEGER"}, "dy": {"type": "INTEGER"}}, "required": ["dx", "dy"]}},
                {"name": "mouse_drag", "description": SILENT + "Drags the mouse from one point to another.", "parameters": {"type": "OBJECT", "properties": {"x1": {"type": "INTEGER"}, "y1": {"type": "INTEGER"}, "x2": {"type": "INTEGER"}, "y2": {"type": "INTEGER"}, "button": {"type": "STRING", "enum": ["left", "right"], "default": "left"}}, "required": ["x1", "y1", "x2", "y2"]}},
                {"name": "type_text", "description": SILENT + "Types text at the current cursor position or at specific coordinates.", "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "press_enter": {"type": "BOOLEAN", "default": True}, "x": {"type": "INTEGER", "description": "Optional x coordinate to click before typing."}, "y": {"type": "INTEGER", "description": "Optional y coordinate to click before typing."}}, "required": ["text"]}},
                {"name": "smooth_scroll", "description": SILENT + "Scrolls the screen up or down smoothly.", "parameters": {"type": "OBJECT", "properties": {"direction": {"type": "STRING", "enum": ["up", "down"]}, "clicks": {"type": "INTEGER", "description": "Scroll distance.", "default": 3}}, "required": ["direction"]}},
                {"name": "start_scrolling", "description": SILENT + "Starts scrolling the page continuously.", "parameters": {"type": "OBJECT", "properties": {"direction": {"type": "STRING", "enum": ["up", "down"]}, "speed": {"type": "NUMBER", "description": "Seconds between scroll steps (smaller is faster).", "default": 0.5}}, "required": ["direction"]}},
                {"name": "stop_scrolling", "description": SILENT + "Stops the continuous scrolling action.", "parameters": {"type": "OBJECT", "properties": {}}},
                {"name": "press_key", "description": SILENT + "Presses a specific keyboard key.", "parameters": {"type": "OBJECT", "properties": {"key": {"type": "STRING", "description": "Key like 'enter', 'win', 'tab'."}}, "required": ["key"]}},
                {"name": "press_key_combination", "description": SILENT + "Presses multiple keyboard keys simultaneously (hotkeys).", "parameters": {"type": "OBJECT", "properties": {"keys": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "List of key names to press (e.g., ['ctrl', 'shift', 'p'])."}}, "required": ["keys"]}},
                {"name": "parse_screen_text", "description": SILENT + "Captures the screen and uses OCR to find all visible text and their coordinates.", "parameters": {"type": "OBJECT", "properties": {}}},
                {"name": "set_timer", "description": SILENT + "Sets a timer/alarm for a specified duration in seconds.", "parameters": {"type": "OBJECT", "properties": {"duration_seconds": {"type": "INTEGER", "description": "The timer duration in seconds."}, "label": {"type": "STRING", "description": "Optional name or label for the timer."}}, "required": ["duration_seconds"]}},
                {"name": "list_windows", "description": SILENT + "Returns titles of all currently visible windows.", "parameters": {"type": "OBJECT", "properties": {}}},
                {"name": "manage_window", "description": SILENT + "Activates, minimizes, maximizes, or closes a specific window.", "parameters": {"type": "OBJECT", "properties": {"title": {"type": "STRING", "description": "Full or partial window title."}, "action": {"type": "STRING", "enum": ["activate", "minimize", "maximize", "close"]}}, "required": ["title", "action"]}},
                {"name": "get_system_status", "description": SILENT + "Retrieves system resource usage (CPU, RAM, Battery) and local time.", "parameters": {"type": "OBJECT", "properties": {}}},
                {"name": "read_clipboard", "description": SILENT + "Reads the current text content from the system clipboard.", "parameters": {"type": "OBJECT", "properties": {}}},
                {"name": "get_active_window_info", "description": SILENT + "Returns title and process name of the active window.", "parameters": {"type": "OBJECT", "properties": {}}},
                {"name": "system_action", "description": SILENT + "Locks the PC or puts it to sleep.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "enum": ["lock", "sleep", "empty_trash"]}}, "required": ["action"]}},
                {"name": "set_volume", "description": SILENT + "Sets the system volume to a specific percentage (0-100).", "parameters": {"type": "OBJECT", "properties": {"level": {"type": "INTEGER", "description": "Volume percentage (0-100)."}}, "required": ["level"]}},
                {"name": "set_brightness", "description": SILENT + "Sets the screen brightness to a specific percentage (0-100).", "parameters": {"type": "OBJECT", "properties": {"level": {"type": "INTEGER", "description": "Brightness percentage (0-100)."}}, "required": ["level"]}},
                {"name": "run_shell_command", "description": SILENT + "Executes a PowerShell command and returns the output.", "parameters": {"type": "OBJECT", "properties": {"command": {"type": "STRING"}}, "required": ["command"]}},
                {"name": "list_files", "description": SILENT + "Lists recent files in a specific user directory.", "parameters": {"type": "OBJECT", "properties": {"directory": {"type": "STRING", "enum": ["downloads", "documents", "desktop", "pictures", "videos"], "default": "downloads"}}, "required": ["directory"]}},
                {"name": "read_file", "description": SILENT + "Reads the first 5000 characters of a text file.", "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING", "description": "Full file path."}}, "required": ["path"]}},
                {"name": "write_file_content", "description": SILENT + "Creates or overwrites a text file with the specified content.", "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING", "description": "Full file path."}, "content": {"type": "STRING", "description": "Content to write."}}, "required": ["path", "content"]}},
                {"name": "file_operation", "description": SILENT + "Copies, moves, deletes, or renames files or folders.", "parameters": {"type": "OBJECT", "properties": {"source": {"type": "STRING", "description": "Source path."}, "target": {"type": "STRING", "description": "Target path (optional)."}, "action": {"type": "STRING", "enum": ["copy", "move", "delete", "rename"]}}, "required": ["source", "action"]}},
                {"name": "create_folder", "description": SILENT + "Creates a new folder at the specified path.", "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING", "description": "Folder path to create."}}, "required": ["path"]}},
                {"name": "list_running_processes", "description": SILENT + "Returns running processes with PIDs, names, CPU and memory usage.", "parameters": {"type": "OBJECT", "properties": {}}},
                {"name": "kill_process", "description": SILENT + "Terminates a process by its PID or name.", "parameters": {"type": "OBJECT", "properties": {"pid_or_name": {"type": "STRING", "description": "The PID or process name."}}, "required": ["pid_or_name"]}},
                {"name": "get_screens_info", "description": SILENT + "Returns details about all connected monitors.", "parameters": {"type": "OBJECT", "properties": {}}},
                {"name": "media_control", "description": SILENT + "Controls system media playback (play/pause, next, previous, volume, mute).", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "enum": ["play_pause", "next", "previous", "volume_up", "volume_down", "mute"]}}, "required": ["action"]}},
                {"name": "search_web", "description": SILENT + "Opens the default browser and searches for a specific query.", "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}, "mode": {"type": "STRING", "enum": ["tab", "window"], "default": "tab"}}, "required": ["query"]}},
                {"name": "web_search", "description": SILENT + "Performs a background web search and returns text snippets. Use this to answer questions without opening a browser.", "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}},
                {"name": "open_url", "description": SILENT + "Opens a specific URL in the default browser.", "parameters": {"type": "OBJECT", "properties": {"url": {"type": "STRING"}, "mode": {"type": "STRING", "enum": ["tab", "window"], "default": "tab"}}, "required": ["url"]}},
                {"name": "get_local_time", "description": SILENT + "Returns the current local date and time.", "parameters": {"type": "OBJECT", "properties": {}}},
                {"name": "get_mouse_position", "description": SILENT + "Returns the current (x, y) coordinates of the mouse cursor.", "parameters": {"type": "OBJECT", "properties": {}}}
            ]}]

            session_resumption = self.cfg.get("gemini_session_resumption", False)
            context_compression = self.cfg.get("gemini_context_compression", False)
            idle_timeout = int(self.cfg.get("gemini_idle_timeout", 300))

            self._gemini_live_client = _import_gemini_live().GeminiLiveClient(
                api_key=gemini_key if gemini_key else None,
                ephemeral_token=ephemeral_token,
                model=gemini_model,
                voice_name=gemini_voice,
                tools=tools,
                system_instruction=system_instruction,
                session_resumption=session_resumption,
                context_compression=context_compression,
                idle_timeout=idle_timeout,
                on_status=lambda s: self.page.run_thread(self._set_gemini_status, s),
                on_error=lambda e: self.page.run_thread(self._on_gemini_error, e),
                on_transcript=lambda t: self.page.run_thread(self._on_gemini_transcript, t),
                on_user_transcript=lambda t: self.page.run_thread(self._on_gemini_user_transcript, t),
                on_tool_call=self._handle_ai_tool_call,
            )
            self._gemini_live_client.start()
            self._is_recording = True
        except Exception as exc:
            self._is_chatting = False
            self._is_recording = False
            self._on_transcription_error(f"Gemini Live failed: {exc}")
            self._apply_theme_to_controls()
            return
        self.page.update()

        # Apply gemini_show_transcript_panel config at start
        if config.load().get("gemini_show_transcript_panel", False):
            self._transcript_panel_visible = True
            if hasattr(self, '_transcript_panel_container'):
                self._transcript_panel_container.visible = True
                if hasattr(self, '_transcript_chevron'):
                    self._transcript_chevron.name = ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
            self.page.update()

    # ── Gemini Chat: Security & Safety ───────────────────────────────────────

    def _is_tool_confirmation_required(self, name: str) -> bool:
        """Check if a tool requires user confirmation before execution."""
        cfg = config.load()
        if not cfg.get("gemini_tool_confirmation_enabled", True):
            return False
        return name in CONFIRMATION_REQUIRED_TOOLS

    def _is_shell_command_blocked(self, command: str) -> bool:
        """Check if a shell command matches any blocked pattern."""
        cfg = config.load()
        if not cfg.get("gemini_shell_command_blocked", True):
            return False
        cmd_lower = command.lower()
        for pattern in BLOCKED_SHELL_PATTERNS:
            if pattern.lower() in cmd_lower:
                logger.warning(f"Blocked shell command pattern: {pattern} in '{command}'")
                return True
        return False

    def _is_path_protected(self, path: str) -> bool:
        """Check if a file path points to a protected system directory."""
        path_lower = path.lower().replace("/", "\\").strip()
        for protected in PROTECTED_PATHS:
            if path_lower.startswith(protected.lower().replace("/", "\\")):
                logger.warning(f"Protected path access denied: {path}")
                return True
        return False

    def _check_tool_rate_limit(self) -> bool:
        """Return True if the tool call is within rate limits."""
        cfg = config.load()
        limit = cfg.get("gemini_tool_rate_limit", 10)
        window = cfg.get("gemini_tool_rate_window", 60)
        now = time.time()
        # Prune old timestamps outside the window
        self._tool_call_timestamps = [
            ts for ts in self._tool_call_timestamps if (now - ts) < window
        ]
        if len(self._tool_call_timestamps) >= limit:
            logger.warning(f"Tool rate limit exceeded: {len(self._tool_call_timestamps)}/{limit} in {window}s")
            return False
        self._tool_call_timestamps.append(now)
        return True

    def _validate_tool_args(self, name: str, args: dict) -> dict | None:
        """Validate tool arguments for security. Returns error dict if blocked, None if OK."""
        # Rate limiting
        if not self._check_tool_rate_limit():
            return {"success": False, "error": "Rate limit exceeded. Please slow down."}

        # Shell command blocklist
        if name == "run_shell_command":
            cmd = args.get("command", "")
            if self._is_shell_command_blocked(cmd):
                return {"success": False, "error": "This command is blocked for safety."}

        # File path protection
        if name in ("write_file_content", "file_operation", "create_folder"):
            path = args.get("path", "") or args.get("source", "")
            if self._is_path_protected(path):
                return {"success": False, "error": "Writing to this path is not allowed."}

        if name == "read_file":
            path = args.get("path", "")
            if self._is_path_protected(path):
                return {"success": False, "error": "Reading from this path is not allowed."}

        return None

    def _request_tool_confirmation(self, name: str, args: dict) -> bool:
        """Show a confirmation dialog for dangerous tools. Returns True if approved."""
        cfg = config.load()
        timeout = cfg.get("gemini_tool_confirmation_timeout", 10)

        # Build a human-readable description of what the tool will do
        desc = name.replace("_", " ").title()
        if name == "run_shell_command":
            desc = f"Run shell: {args.get('command', '?')[:80]}"
        elif name == "kill_process":
            desc = f"Kill process: {args.get('pid_or_name', '?')}"
        elif name == "write_file_content":
            desc = f"Write file: {args.get('path', '?')}"
        elif name == "file_operation":
            desc = f"{args.get('action', '?')} file: {args.get('source', '?')}"
        elif name == "system_action":
            desc = f"System action: {args.get('action', '?')}"
        elif name == "open_app":
            desc = f"Open app: {args.get('query', '?')}"

        approval_event = threading.Event()
        result = {"approved": False}

        def _show_toast():
            def _on_approve(e):
                result["approved"] = True
                approval_event.set()
                toast.open = False
                self.page.update()

            def _on_deny(e):
                result["approved"] = False
                approval_event.set()
                toast.open = False
                self.page.update()

            toast = ft.SnackBar(
                content=ft.Column(
                    spacing=4,
                    controls=[
                        ft.Text(f"AI wants to: {desc}", size=10, color=TEXT, weight=ft.FontWeight.W_600),
                        ft.Row(
                            spacing=8,
                            controls=[
                                ft.ElevatedButton("Approve", on_click=_on_approve, bgcolor=SUCCESS, color=BG,
                                                   style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(8))),
                                ft.ElevatedButton("Deny", on_click=_on_deny, bgcolor=DANGER, color=TEXT,
                                                   style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(8))),
                            ]
                        ),
                    ]
                ),
                bgcolor=CARD_SOFT,
                duration=timeout * 1000,
                open=True,
            )
            self.page.snack_bar = toast
            self.page.update()

        try:
            self.page.run_thread(_show_toast)
        except Exception:
            pass

        approved = approval_event.wait(timeout=timeout)
        if not approved:
            logger.info(f"Tool confirmation timed out for {name}")
            return False
        return result.get("approved", False)

    def _handle_ai_tool_call(self, name: str, args: dict) -> dict:
        self.page.run_thread(self._set_status, f"AI {name}...", SUCCESS)

        # Security/Privacy Check: Read fresh configuration from disk
        pc_control_enabled = bool(config.load().get("pc_control_enabled", True))

        # Tools allowed even if PC Control is OFF (strictly passive/read-only)
        allowed_tools = [
            "web_search", "get_local_time", "get_system_status", "read_clipboard",
            "get_active_window_info", "list_windows", "list_files", "parse_screen_text",
            "list_running_processes"
        ]

        if not pc_control_enabled and name not in allowed_tools:
            return {"success": False, "error": "PC Control is currently disabled by the user in Settings. You can only chat and search the web."}

        # Security validation (rate limit, blocked commands, protected paths)
        validation_error = self._validate_tool_args(name, args)
        if validation_error is not None:
            return validation_error

        # Confirmation prompt for dangerous tools
        if self._is_tool_confirmation_required(name):
            if not self._request_tool_confirmation(name, args):
                return {"success": False, "error": "User denied this action."}

        try:
            if name == "open_app": return {"success": output_handler.open_application(args.get("query", ""))}
            elif name == "mouse_click": return {"success": output_handler.mouse_click(x=args.get("x"), y=args.get("y"), button=args.get("button", "left"), double=args.get("double", False))}
            elif name == "move_mouse": return {"success": output_handler.move_mouse(x=args.get("x"), y=args.get("y"), duration=args.get("duration", 0.2))}
            elif name == "move_mouse_relative": return {"success": output_handler.move_mouse_relative(dx=args.get("dx", 0), dy=args.get("dy", 0), duration=args.get("duration", 0.1))}
            elif name == "mouse_drag": return {"success": output_handler.mouse_drag(x1=args.get("x1"), y1=args.get("y1"), x2=args.get("x2"), y2=args.get("y2"), button=args.get("button", "left"))}
            elif name == "type_text":
                text = args.get("text", "")
                success = output_handler.type_text(text)
                if success and args.get("press_enter", True):
                    success = output_handler.send_shortcut("enter")
                return {"success": success}
            elif name == "smooth_scroll": return {"success": output_handler.smooth_scroll(direction=args.get("direction", "down"), clicks=args.get("clicks", 3))}
            elif name == "start_scrolling": return {"success": output_handler.start_continuous_scroll(direction=args.get("direction", "down"), speed=args.get("speed", 0.5))}
            elif name == "stop_scrolling": return {"success": output_handler.stop_continuous_scroll()}
            elif name == "press_key": return {"success": output_handler.send_shortcut(args.get("key", ""))}
            elif name == "press_key_combination": return {"success": output_handler.press_key_combination(args.get("keys", []))}
            elif name == "parse_screen_text": return {"success": True, "text_elements": output_handler.parse_screen_text()}
            elif name == "set_timer": return {"success": output_handler.set_timer(duration_seconds=args.get("duration_seconds"), label=args.get("label", "Timer"))}
            elif name == "list_windows": return {"success": True, "windows": output_handler.list_windows()}
            elif name == "manage_window": return {"success": output_handler.manage_window(title=args.get("title", ""), action=args.get("action", "activate"))}
            elif name == "get_system_status": return {"success": True, "status": output_handler.get_system_status()}
            elif name == "read_clipboard": return {"success": True, "content": output_handler.read_clipboard()}
            elif name == "get_active_window_info": return {"success": True, "info": output_handler.get_active_window_info()}
            elif name == "system_action": return {"success": output_handler.system_action(action=args.get("action", ""))}
            elif name == "list_files": return {"success": True, "files": output_handler.list_files(directory=args.get("directory", "downloads"))}
            elif name == "read_file": return {"success": True, "content": output_handler.read_file_content(file_path=args.get("path", ""))}
            elif name == "write_file_content": return {"success": output_handler.write_file_content(path=args.get("path", ""), content=args.get("content", ""))}
            elif name == "file_operation": return {"success": output_handler.file_operation(source=args.get("source", ""), target=args.get("target", ""), action=args.get("action", "copy"))}
            elif name == "create_folder": return {"success": output_handler.create_folder(path=args.get("path", ""))}
            elif name == "list_running_processes": return {"success": True, "processes": output_handler.list_running_processes()}
            elif name == "kill_process": return {"success": output_handler.kill_process(pid_or_name=args.get("pid_or_name", ""))}
            elif name == "get_screens_info": return {"success": True, "screens": output_handler.get_screens_info()}
            elif name == "set_volume": return {"success": output_handler.set_system_volume(level=args.get("level", 50))}
            elif name == "set_brightness": return {"success": output_handler.set_screen_brightness(level=args.get("level", 50))}
            elif name == "media_control": return {"success": output_handler.media_control(action=args.get("action", ""))}
            elif name == "search_web": return {"success": output_handler.search_web(query=args.get("query", ""), mode=args.get("mode", "tab"))}
            elif name == "web_search": return {"success": True, "results": output_handler.web_search(query=args.get("query", ""))}
            elif name == "open_url": return {"success": output_handler.open_url(url=args.get("url", ""), mode=args.get("mode", "tab"))}
            elif name == "get_local_time": return {"success": True, "time": output_handler.get_local_time()}
        except Exception as exc: return {"success": False, "error": str(exc)}
        return {"success": False, "error": "Unknown tool"}

    # ── Gemini Chat: Transcript History ──────────────────────────────────────

    def _add_transcript_message(self, role: str, text: str) -> None:
        """Append a message to the transcript history and update the panel."""
        if not text or not text.strip():
            return
        cfg = config.load()
        max_msgs = cfg.get("gemini_transcript_max_messages", 20)
        self._transcript_history.append({
            "role": role,
            "text": text.strip(),
            "ts": time.time(),
        })
        # Trim to max size
        if len(self._transcript_history) > max_msgs:
            self._transcript_history = self._transcript_history[-max_msgs:]
        # Update the panel if visible
        self._refresh_transcript_panel()

    def _refresh_transcript_panel(self) -> None:
        """Rebuild the transcript panel controls from the history."""
        if not hasattr(self, '_transcript_list') or self._transcript_list is None:
            return
        controls = []
        for msg in self._transcript_history[-20:]:
            is_user = msg["role"] == "user"
            role_color = ACCENT if is_user else ACCENT_GLOW
            role_label = "You" if is_user else "Voxify"
            controls.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.08, ACCENT if is_user else MUTED),
                    content=ft.Column(
                        spacing=1,
                        controls=[
                            ft.Text(role_label, size=7, weight=ft.FontWeight.W_800, color=role_color),
                            ft.Text(msg["text"], size=8, color=TEXT, selectable=True, max_lines=4, overflow=ft.TextOverflow.ELLIPSIS),
                        ]
                    )
                )
            )
        self._transcript_list.controls = controls
        # Auto-scroll to bottom
        try:
            self._transcript_list.scroll_to(offset=-1)
        except Exception:
            pass
        try:
            self.page.update()
        except Exception:
            pass

    def _toggle_transcript_panel(self) -> None:
        """Toggle the transcript history panel visibility."""
        self._transcript_panel_visible = not self._transcript_panel_visible
        if hasattr(self, '_transcript_panel_container'):
            self._transcript_panel_container.visible = self._transcript_panel_visible
            if hasattr(self, '_transcript_chevron'):
                self._transcript_chevron.name = (
                    ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED if self._transcript_panel_visible
                    else ft.Icons.KEYBOARD_ARROW_UP_ROUNDED
                )
            self.page.update()

    # ── Gemini Chat: Rich Status ─────────────────────────────────────────────

    def _set_gemini_status(self, status: str) -> None:
        """Map Gemini states to rich UI indicators."""
        STATE_COLORS = {
            "Connected": SUCCESS,
            "Listening\u2026": ACCENT_GLOW,
            "Listening...": ACCENT_GLOW,
            "Speaking\u2026": ACCENT,
            "Speaking...": ACCENT,
            "Interrupted": MUTED,
            "Tool executing\u2026": AUX_BG,
            "Tool executing...": AUX_BG,
            "Reconnecting\u2026": "#F59E0B",
            "Reconnecting...": "#F59E0B",
            "Idle timeout": "#EAB308",
            "Error": DANGER,
            "Mic unavailable - listen-only mode": MUTED,
            "Audio muted - session active": MUTED,
        }
        color = STATE_COLORS.get(status, TEXT)

        # Update pulse dot color based on state
        if hasattr(self, 'pulse_core'):
            if "Listening" in status:
                self.pulse_core.bgcolor = ACCENT_GLOW
            elif "Speaking" in status:
                self.pulse_core.bgcolor = ACCENT
            elif "Connected" in status:
                self.pulse_core.bgcolor = SUCCESS
            elif "Error" in status:
                self.pulse_core.bgcolor = DANGER
            elif "Reconnecting" in status:
                self.pulse_core.bgcolor = "#F59E0B"
            elif "Tool" in status:
                self.pulse_core.bgcolor = AUX_BG
            elif "timeout" in status.lower():
                self.pulse_core.bgcolor = "#EAB308"
            else:
                self.pulse_core.bgcolor = INACTIVE_DOT

        # Map display text
        mapping = {
            "Connected": "Gemini Live ready",
            "Listening\u2026": "Listening...",
            "Listening...": "Listening...",
            "Speaking\u2026": "AI speaking...",
            "Speaking...": "AI speaking...",
            "Interrupted": "You interrupted",
            "Tool executing\u2026": "Executing tool...",
            "Tool executing...": "Executing tool...",
            "Reconnecting\u2026": "Reconnecting...",
            "Reconnecting...": "Reconnecting...",
            "Idle timeout": "Idle timeout",
            "Idle timeout\u2026": "Idle timeout",
            "Error": "Error occurred",
        }
        display = mapping.get(status, status)
        self._set_status(display, color)

    # ── Gemini Chat: Transcript callbacks ────────────────────────────────────

    def _on_gemini_user_transcript(self, transcript: str) -> None:
        logger.info(f"User said: {transcript}")
        self._add_transcript_message("user", transcript)

    def _on_gemini_transcript(self, transcript: str) -> None:
        """Handle AI transcript - accumulate and show in status + history."""
        # Show in status bar
        preview = (transcript[:50] + "..") if len(transcript) > 50 else transcript
        self._set_status(preview, TEXT)
        # Add to transcript history
        self._add_transcript_message("ai", transcript)

    # ── Gemini Chat: Quick Actions ───────────────────────────────────────────

    def _toggle_listen_only(self) -> None:
        """Toggle listen-only mode (mute mic input)."""
        self._listen_only_mode = not self._listen_only_mode
        # Sync to GeminiLiveClient if active
        if self._gemini_live_client is not None:
            try:
                self._gemini_live_client.set_mute_input(self._listen_only_mode)
            except Exception:
                pass
        if hasattr(self, 'mute_icon'):
            if self._listen_only_mode:
                self.mute_icon.name = ft.Icons.MIC_OFF_ROUNDED
                self.mute_icon.color = DANGER
                self.mute_btn.bgcolor = ft.Colors.with_opacity(0.3, DANGER)
            else:
                self.mute_icon.name = ft.Icons.MIC_ROUNDED
                self.mute_icon.color = ACCENT
                self.mute_btn.bgcolor = ft.Colors.with_opacity(0.2, CARD_ACTIVE)
            self.page.update()

    def _stop_chat_mode(self) -> None:
        if not self._is_chatting:
            return
        self._is_chatting = False
        self._is_recording = False
        self._listen_only_mode = False
        self._transcript_history.clear()
        self._transcript_panel_visible = False
        self._tool_call_timestamps.clear()
        self._chat_elapsed_start = 0.0
        self._stop_screen_sharing()
        if self._gemini_live_client:
            try:
                self._gemini_live_client.stop()
            except Exception:
                pass
            self._gemini_live_client = None
        self._set_status("Chat closed", MUTED)
        self._apply_theme_to_controls()
        self._reset_to_ready()

    def _on_gemini_error(self, error: str) -> None:
        if not self._is_chatting:
            return
        # Show error toast
        try:
            toast = ft.SnackBar(
                content=ft.Text(f"Gemini Live error: {error}", size=10, color=TEXT),
                bgcolor=DANGER,
                duration=5000,
                open=True,
            )
            self.page.snack_bar = toast
            self.page.update()
        except Exception:
            pass
        self._is_chatting = False
        self._is_recording = False
        if self._gemini_live_client:
            try:
                self._gemini_live_client.stop()
            except Exception:
                pass
            self._gemini_live_client = None
        self._apply_theme_to_controls()
        self._on_transcription_error(f"Gemini Live: {error}")

    RESOLUTION_MAP = {
        "low": 480,
        "medium": 768,
        "high": 1024,
    }

    # Google Gemini Live API spec: max 1 FPS for video frames
    # FPS_TABLE: (change_threshold, frames_per_second)
    FPS_TABLE = [
        (0.30, 0.5),   # lots of motion → 0.5 FPS (2s interval)
        (0.10, 0.67),  # moderate motion → 0.67 FPS (1.5s)
        (0.00, 1.0),   # low motion → 1.0 FPS (1s)
    ]
    # Minimum interval between frames = 1 / max_fps = 1.0s

    def _on_video_click(self, _event) -> None:
        self._register_widget_interaction()
        if self._is_sharing_screen:
            self._stop_screen_sharing()
        else:
            self._start_screen_sharing()

    def _start_screen_sharing(self) -> None:
        if not self._is_chatting or not self._gemini_live_client:
            return
        self._is_sharing_screen = True
        self._screen_share_paused = False
        self._last_frame_bytes = None
        self._last_frame_pixels = None
        self._update_video_btn_ui(sharing=True)
        self._set_status("Sharing screen", SUCCESS)
        self._screen_capture_thread = threading.Thread(target=self._run_screen_capture, daemon=True)
        self._screen_capture_thread.start()
        self.page.update()

    def _stop_screen_sharing(self) -> None:
        self._is_sharing_screen = False
        self._screen_share_paused = False
        self._last_frame_bytes = None
        self._last_frame_pixels = None
        # Wait for the capture thread to finish so the finally block runs cleanly
        thread = getattr(self, '_screen_capture_thread', None)
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._screen_capture_thread = None
        self._update_video_btn_ui(sharing=False)
        if self._is_chatting:
            self._set_status("Listening...", SUCCESS)

    def _update_video_btn_ui(self, sharing: bool) -> None:
        if sharing:
            self.video_icon.name = ft.Icons.VIDEOCAM_ROUNDED
            self.video_icon.color = TEXT
            self.video_btn.bgcolor = ACCENT
            self.video_btn.gradient = ft.LinearGradient(
                begin=ft.Alignment(-1, -1), end=ft.Alignment(1, 1),
                colors=[ACCENT, ACCENT_GLOW],
            )
            self.video_btn.shadow = ft.BoxShadow(
                blur_radius=14, spread_radius=2,
                color=ft.Colors.with_opacity(0.5, ACCENT_GLOW),
            )
            self.video_btn.border = ft.Border.all(1, ft.Colors.with_opacity(0.6, ACCENT_GLOW))
        else:
            self.video_icon.name = ft.Icons.VIDEOCAM_OUTLINED
            self.video_icon.color = ACCENT
            self.video_btn.bgcolor = ft.Colors.with_opacity(0.2, CARD_ACTIVE)
            self.video_btn.gradient = None
            self.video_btn.shadow = None
            self.video_btn.border = ft.Border.all(1, ft.Colors.with_opacity(0.2, BORDER))
        self.page.update()

    def _run_screen_capture(self) -> None:
        from PIL import Image as PILImage
        import io

        # ── Import guard ─────────────────────────────────────────────
        try:
            import cv2
            import numpy as np
        except ImportError:
            self._is_sharing_screen = False
            self._screen_share_paused = False
            try: self.page.run_thread(lambda: self._update_video_btn_ui(False))
            except Exception: pass
            return

        try:
            import mss
            _use_mss = True
        except ImportError:
            _use_mss = False

        if not _use_mss:
            try:
                import pyautogui
            except ImportError:
                self._is_sharing_screen = False
                self._screen_share_paused = False
                try: self.page.run_thread(lambda: self._update_video_btn_ui(False))
                except Exception: pass
                return

        resolution_key = self.cfg.get("screen_share_resolution", "medium")
        max_dim = self.RESOLUTION_MAP.get(resolution_key, 768)
        MIN_INTERVAL = 1.0

        sct = mss.mss() if _use_mss else None
        try:
            while self._is_sharing_screen and self._is_chatting:
                if self._screen_share_paused:
                    time.sleep(0.3)
                    continue

                # ── Capture ──────────────────────────────────────────────
                if _use_mss:
                    raw = sct.grab(sct.monitors[1])
                    screenshot = PILImage.frombytes("RGB", raw.size, raw.rgb)
                else:
                    screenshot = pyautogui.screenshot()

                # ── Resize ───────────────────────────────────────────────
                w, h = screenshot.size
                scale = min(max_dim / max(w, h), 1.0)
                if scale < 1.0:
                    new_w, new_h = int(w * scale), int(h * scale)
                    screenshot = screenshot.resize(
                        (new_w, new_h), PILImage.Resampling.LANCZOS
                    )

                frame_pixels = np.array(screenshot)

                # ── Skip identical frames ────────────────────────────────
                if self._last_frame_pixels is not None:
                    diff = cv2.absdiff(frame_pixels, self._last_frame_pixels)
                    changed = float(np.count_nonzero(diff)) / frame_pixels.size
                    if changed < 0.005:
                        time.sleep(MIN_INTERVAL)
                        continue

                # ── Encode JPEG ──────────────────────────────────────────
                img_buf = io.BytesIO()
                screenshot.save(img_buf, format="JPEG", quality=85)
                frame_bytes = img_buf.getvalue()

                # ── Send if changed ──────────────────────────────────────
                if frame_bytes != self._last_frame_bytes:
                    self._last_frame_bytes = frame_bytes
                    self._last_frame_pixels = frame_pixels
                    if self._gemini_live_client:
                        self.page.run_thread(
                            self._gemini_live_client.send_video_frame, frame_bytes
                        )

                # Adaptive sleep: more change = faster. Never exceed 1 FPS.
                if self._last_frame_pixels is not None:
                    diff = cv2.absdiff(frame_pixels, self._last_frame_pixels)
                    changed = float(np.count_nonzero(diff)) / frame_pixels.size
                    if changed > 0.30:
                        sleep = 1.0
                    elif changed > 0.10:
                        sleep = 1.5
                    else:
                        sleep = 1.0
                else:
                    sleep = 1.0
                time.sleep(max(sleep, MIN_INTERVAL))

        except Exception:
            pass
        finally:
            if sct:
                try: sct.close()
                except Exception: pass
            self._is_sharing_screen = False
            self._last_frame_bytes = None
            self._last_frame_pixels = None
            # Always reset button UI when the capture thread exits
            try:
                self.page.run_thread(lambda: self._update_video_btn_ui(False))
            except Exception:
                pass

    def _open_settings(self, _event) -> None:
        self._register_widget_interaction()
        if self._settings_opening: return
        if self._settings_process and self._settings_process.poll() is None: return
        self._settings_opening = True

        def _launch() -> None:
            try:
                if getattr(sys, "frozen", False): cmd = [sys.executable, "--settings"]
                else:
                    app_script = Path(__file__).with_name("app.py")
                    cmd = [sys.executable, str(app_script), "--settings"]
                kwargs = {}
                if sys.platform.startswith("win"): kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self._settings_process = subprocess.Popen(cmd, **kwargs)
                threading.Thread(target=self._wait_for_settings_process, args=(self._settings_process,), daemon=True).start()
            except Exception as exc: self.page.run_thread(self._show_settings_error, f"Unable to open settings: {exc}")
            finally: self._settings_opening = False
        threading.Thread(target=_launch, daemon=True).start()

    def _wait_for_settings_process(self, process: subprocess.Popen) -> None:
        try: process.wait()
        except Exception: return
        try: self.page.run_thread(self._sync_settings_from_disk)
        except RuntimeError: return

    def _sync_settings_from_disk(self) -> None:
        previous_theme = self._theme_name
        self.cfg = config.load()
        self._device_id = self._get_or_create_device_id()
        self._config_mtime = self._get_config_mtime()
        theme_changed = self._apply_theme_globals()
        self.page.window.always_on_top = bool(self.cfg.get("always_on_top", True))
        if theme_changed or previous_theme != self._theme_name: self._apply_theme_to_controls()
        self._set_mode_badge()
        if self._auto_minimize_enabled(): self._schedule_auto_minimize_timer()
        else:
            self._cancel_auto_minimize_timer()
            if self._is_minimized: self._set_minimized(False)
        self._set_status("Ready", MUTED)
        self.page.update()

    def _show_settings_error(self, message: str) -> None:
        self.page.snack_bar = ft.SnackBar(content=ft.Text(message, color=TEXT), bgcolor=CARD_SOFT, open=True)
        self.page.update()

    def _get_config_mtime(self) -> float:
        try: return float(config.CONFIG_FILE.stat().st_mtime)
        except Exception: return 0.0

    def _watch_config_changes(self) -> None:
        while True:
            time.sleep(1.0)
            latest_mtime = self._get_config_mtime()
            if latest_mtime <= 0.0 or latest_mtime == self._config_mtime: continue
            self._config_mtime = latest_mtime
            try: self.page.run_thread(self._sync_settings_from_disk)
            except RuntimeError: return

    def _get_or_create_device_id(self) -> str:
        device_id = (self.cfg.get("device_id") or "").strip()
        if device_id: return device_id
        device_id = uuid.uuid4().hex
        latest = config.load(); latest["device_id"] = device_id
        config.save(latest); self.cfg = latest
        return device_id

    def _load_cached_license_state(self) -> None:
        state = license_cache.load_state()
        self._license_token = (state.get("token") or "").strip()
        raw_entitlement = state.get("entitlement")
        if isinstance(raw_entitlement, dict) and (raw_entitlement.get("licenseId") or raw_entitlement.get("license_id")):
            try:
                self._license_entitlement = website_client.LicenseEntitlement(
                    license_id=(raw_entitlement.get("licenseId") or raw_entitlement.get("license_id") or "").strip(),
                    status=(raw_entitlement.get("status") or "").strip().lower(),
                    plan=(raw_entitlement.get("plan") or "starter").strip().lower(),
                    billing_cycle=(raw_entitlement.get("billingCycle") or raw_entitlement.get("billing_cycle") or "").strip().lower(),
                    quota_chars=int(raw_entitlement.get("quotaChars") or raw_entitlement.get("quota_chars") or 0),
                    bonus_chars=int(raw_entitlement.get("bonusChars") or raw_entitlement.get("bonus_chars") or 0),
                    used_chars=int(raw_entitlement.get("usedChars") or raw_entitlement.get("used_chars") or 0),
                    used_words=int(raw_entitlement.get("usedWords") or raw_entitlement.get("used_words") or 0),
                    remaining_chars=int(raw_entitlement.get("remainingChars") or raw_entitlement.get("remaining_chars") or 0),
                    seat_limit=int(raw_entitlement.get("seatLimit") or raw_entitlement.get("seat_limit") or 1),
                    active_seats=int(raw_entitlement.get("activeSeats") or raw_entitlement.get("active_seats") or 0),
                    is_subscription=bool(raw_entitlement.get("isSubscription") or raw_entitlement.get("is_subscription") or False),
                    can_transcribe=bool(raw_entitlement.get("canTranscribe") or raw_entitlement.get("can_transcribe") or False),
                )
            except Exception: self._license_entitlement = None

    def _load_runtime_config(self, force: bool = False) -> website_client.RuntimeConfig | None:
        now = time.time()
        if not force and self._runtime_config is not None and (now - self._runtime_config_last_fetch) < self._runtime_config_ttl_sec: return self._runtime_config
        try:
            runtime_cfg = website_client.get_runtime_config(channel=(self.cfg.get("runtime_channel") or "stable").strip().lower(), platform=app_info.APP_PLATFORM, device_id=self._device_id)
            self._runtime_config = runtime_cfg; self._runtime_config_last_fetch = now
        except Exception: pass
        return self._runtime_config

    def _warmup_startup(self) -> None:
        try: _get_recorder().list_input_devices()
        except Exception: pass
        try: self._load_cached_license_state()
        except Exception: pass
        self._load_runtime_config(force=True)
        try:
            self._refresh_license_session(force=False)
            self._get_runtime_api_key()
            self._get_gemini_api_key()
        except Exception: pass

    def _refresh_license_session(self, force: bool = False) -> website_client.LicenseEntitlement:
        now = time.time()
        with self._license_lock:
            if not force and self._license_entitlement is not None and self._license_token and now < self._license_refresh_at: return self._license_entitlement
        state = license_cache.load_state()
        token = (state.get("token") or self._license_token or "").strip()
        if not token: raise website_client.WebsiteAPIError("License required. Activate in Settings.")
        try: session_data = website_client.refresh_license(token=token, device_id=self._device_id, device_name=f"{app_info.APP_NAME}-{app_info.APP_PLATFORM}", license_key=(state.get("licenseKey") or "").strip())
        except Exception:
            if not (state.get("licenseKey") or "").strip(): raise
            session_data = website_client.activate_license(license_key=(state.get("licenseKey") or "").strip(), device_id=self._device_id, device_name=f"{app_info.APP_NAME}-{app_info.APP_PLATFORM}")
        with self._license_lock:
            self._license_token = session_data.token; self._license_entitlement = session_data.entitlement
            self._license_refresh_at = time.time() + 1800
            self._runtime_api_key = session_data.live_api_key or self._runtime_api_key
            if session_data.live_api_key: self._api_last_check_at = time.time()
        license_cache.save_state(token=session_data.token, license_key=(state.get("licenseKey") or "").strip(), entitlement=self._serialize_entitlement(session_data.entitlement))
        return session_data.entitlement

    def _get_runtime_api_key(self) -> str:
        now = time.time()
        with self._api_lock:
            cfg_key = config.get("api_key", "").strip()
            if cfg_key:
                logger.info("Using private API key from config")
                self._runtime_api_key = cfg_key
                return cfg_key
            if self._runtime_api_key and (now - self._api_last_check_at) < self._api_cache_ttl_sec:
                return self._runtime_api_key
        
        logger.info("Fetching gated runtime API key from website")
        bootstrap = website_client.get_desktop_bootstrap(token=self._license_token, device_id=self._device_id)
        with self._api_lock:
            self._runtime_api_key = bootstrap.api_key.strip()
            self._api_last_check_at = time.time()
        return self._runtime_api_key

    def _get_gemini_api_key(self) -> str:
        now = time.time()
        if self._gemini_api_key and (now - self._gemini_api_last_check) < 300: return self._gemini_api_key
        bootstrap = website_client.get_desktop_bootstrap(token=self._license_token, device_id=self._device_id)
        self._gemini_api_key = bootstrap.gemini_api_key.strip(); self._gemini_api_last_check = now
        return self._gemini_api_key

    def _get_gemini_ephemeral_token(self) -> website_client.EphemeralTokenResult:
        return website_client.get_ephemeral_token(
            token=self._license_token,
            device_id=self._device_id,
        )

    def _on_action_click(self, _event) -> None:
        logger.info("Action button clicked")
        self._register_widget_interaction()
        if self._is_chatting:
            logger.info("Action blocked: chat mode active")
            self.page.snack_bar = ft.SnackBar(content=ft.Text("Stop chat mode first", color=TEXT), bgcolor=CARD_SOFT, open=True)
            self.page.update(); return
        if self._is_recording:
            logger.info("Stopping recording via button")
            self._stop_recording(); return
        if self._waiting_click:
            logger.info("Cancelling target selection via button")
            self._cancel_target_selection(); return
        if not self._api_check_in_flight:
            logger.info("Starting transcription flow")
            self._ask_for_target_click()

    def _ask_for_target_click(self) -> None:
        self._session_id = _import_reliability().new_session_id(); self._api_check_in_flight = True
        logger.info(f"Session {self._session_id}: checking license")
        self._set_status("Checking license", MUTED)
        threading.Thread(target=self._check_api_and_prepare_target, daemon=True).start()

    def _check_api_and_prepare_target(self) -> None:
        try:
            logger.info("Refreshing license session")
            ent = self._refresh_license_session(force=False)
            if not ent.can_transcribe:
                logger.warning("License quota exhausted")
                raise website_client.WebsiteAPIError("Quota reached.")
            logger.info(f"License OK: plan={ent.plan}, remaining={ent.remaining_chars}")
            if self._is_live_mode():
                logger.info("Fetching live runtime API key")
                self._get_runtime_api_key()
            self.page.run_thread(self._begin_target_selection)
        except Exception as exc:
            logger.error(f"API check failed: {exc}")
            self.page.run_thread(self._on_api_check_failed, str(exc))

    def _begin_target_selection(self) -> None:
        self._api_check_in_flight = False; self._waiting_click = True
        self._set_status("Click a target", MUTED)
        self._set_action("Cancel", CARD_SOFT, DANGER, DANGER, self._cancel_target_selection)
        self.page.window.opacity = 0.85
        self.page.update()
        threading.Thread(target=self._listen_for_target_click, daemon=True).start()

    def _on_api_check_failed(self, message: str) -> None:
        self._api_check_in_flight = False; self._waiting_click = False
        self.page.window.opacity = 1; self._stop_target_listener(); self._reset_to_ready()
        self.page.snack_bar = ft.SnackBar(content=ft.Text(message, color=TEXT), bgcolor=CARD_SOFT, open=True)
        self.page.update()

    def _listen_for_target_click(self) -> None:
        logger.info("Listening for target click")
        try: from pynput import mouse as pmouse
        except ImportError:
            logger.error("pynput not installed")
            self.page.run_thread(self._on_api_check_failed, "pynput library required for click-to-type. Install with: pip install pynput")
            return
        def on_click(x, y, button, pressed):
            if not pressed or button != pmouse.Button.left: return
            wx = int(self.page.window.left or 0)
            wy = int(self.page.window.top or 0)
            ww = int(self.page.window.width or 320)
            wh = int(self.page.window.height or 170)
            if wx <= x <= wx + ww and wy <= y <= wy + wh:
                logger.debug("Click on self, ignoring")
                return
            if not self._waiting_click: return True
            logger.info(f"Target clicked at ({x}, {y})")
            try: listener.stop()
            except Exception: pass
            self.page.run_thread(self._on_target_selected)
        try:
            listener = pmouse.Listener(on_click=on_click); self._click_listener = listener
            logger.info("Mouse listener started")
            listener.start(); listener.join()
        except Exception as exc:
            logger.error(f"Mouse listener failed: {exc}")
            self.page.run_thread(self._on_api_check_failed, f"Mouse capture failed: {exc}")

    def _cancel_target_selection(self) -> None:
        logger.info("Target selection cancelled")
        self._waiting_click = False; self.page.window.opacity = 1; self._stop_target_listener(); self._reset_to_ready()

    def _on_target_selected(self) -> None:
        if not self._waiting_click: return
        logger.info("Target selected, starting recording")
        self._waiting_click = False; self.page.window.opacity = 1; self._stop_target_listener()
        self._set_status("Target selected", SUCCESS)
        self._start_recording()

    def _stop_target_listener(self) -> None:
        if self._click_listener:
            try: self._click_listener.stop()
            except Exception: pass
            self._click_listener = None

    def _start_recording(self) -> None:
        self.cfg = config.load()
        self._load_runtime_config(force=False)
        self._stopping = False
        self._set_aux_chip("", False)
        self._set_action("Preparing...", CARD_SOFT, BORDER, MUTED, lambda _e: None)
        mode = "Live" if self._is_live_mode() else "Batch"
        logger.info(f"Starting {mode} mode recording, source={self.cfg.get('source', 'mic')}, sample_rate={self.cfg.get('sample_rate', 16000)}")
        if self._is_live_mode(): self._start_live_mode()
        else: self._start_batch_mode()

    def _start_batch_mode(self) -> None:
        sample_rate = int(self.cfg.get("sample_rate", 16000))
        preferred = self.cfg.get("source", "mic")
        runtime_cfg = self._runtime_config or self._load_runtime_config(force=False)
        endpointing_mode = self.cfg.get("reliability_mode", "balanced")
        if runtime_cfg and runtime_cfg.in_rollout:
            endpointing_mode = runtime_cfg.endpointing_mode or endpointing_mode
        if preferred == "system":
            supported, reason = _get_recorder().system_audio_support_status()
            if not supported:
                logger.warning(f"System audio unsupported: {reason}, falling back to mic")
                preferred = "mic"
        sources = [preferred]
        alt = "system" if preferred == "mic" else "mic"
        if alt not in sources: sources.append(alt)
        logger.info(f"Batch sources: {sources}, endpointing={endpointing_mode}")
        last_exc = None
        for source in sources:
            logger.info(f"Trying recorder source={source}")
            self._recorder = _get_recorder().Recorder(source=source, sample_rate=sample_rate, silence_trim_enabled=self._silence_trim_enabled(), reliability_mode=endpointing_mode)
            try:
                self._recorder.start()
                self._is_recording = True
                logger.info(f"Recording started on source={source}")
                self._set_status("Recording", TEXT)
                self._set_action("Stop recording", CARD_SOFT, DANGER, DANGER, self._on_action_click)
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Source {source} failed: {exc}")
                self._recorder = None
        logger.error(f"All sources failed: {last_exc}")
        self._on_transcription_error(str(last_exc or "Unable to start recording source."))

    def _start_live_mode(self) -> None:
        api_key = self._get_runtime_api_key()
        if not api_key:
            logger.error("Live mode: API key is empty")
            self._on_transcription_error("Live mode key is unavailable. Refresh license from Settings and retry.")
            return
        preferred = self.cfg.get("source", "mic")
        if preferred == "system":
            supported, reason = _get_recorder().system_audio_support_status()
            if not supported:
                logger.warning(f"System audio unsupported: {reason}, falling back to mic")
                preferred = "mic"
        logger.info(f"Starting live mode, source={preferred}")
        self._live_raw_text = ""; self._live_segments = []; self._live_processed_marker = 0; self._is_recording = True
        try:
            selected_model = self.cfg.get("model", "voxtral-mini-2602").strip().lower()
            live_model = LIVE_MODEL_MAP.get(selected_model, "voxtral-mini-transcribe-realtime-2602")
            logger.info(f"Live mode model: {live_model} (from config: {selected_model})")
            raw_mic_dev = self.cfg.get("mic_device") or ""
            mic_dev: int | str | None = None
            if raw_mic_dev.strip():
                try:
                    mic_dev = int(raw_mic_dev.strip())
                except ValueError:
                    mic_dev = raw_mic_dev.strip()
            self._rt_transcriber = _import_realtime_transcriber().RealtimeTranscriber(
                api_key=api_key, model=live_model,
                sample_rate=int(self.cfg.get("sample_rate", 16000)),
                source=preferred, mic_device=mic_dev,
                on_delta=lambda t: self.page.run_thread(self._type_live_delta, t),
                on_segment=lambda t: self.page.run_thread(self._on_segment, t),
                on_status=lambda s: self.page.run_thread(self._set_status, s, MUTED),
                on_done=lambda: self.page.run_thread(self._on_realtime_done),
                on_error=lambda e: self.page.run_thread(self._on_transcription_error, e),
            )
            self._rt_transcriber.start()
            logger.info("Live transcriber started")
            # Avoid overwriting the status from rt_transcriber (e.g. "Connecting...")
            # self._set_status("Listening", SUCCESS)
            self._set_action("Stop recording", CARD_SOFT, DANGER, DANGER, self._on_action_click)
        except Exception as exc:
            logger.error(f"Live mode failed: {exc}")
            self._on_transcription_error(str(exc))

    def _stop_recording(self) -> None:
        logger.info("Stop recording requested")
        self._stopping = True
        if self._is_live_mode(): self._stop_realtime()
        else:
            self._is_recording = False
            logger.info("Starting batch transcription worker")
            threading.Thread(target=self._transcribe_batch_worker, daemon=True).start()

    def _transcribe_batch_worker(self) -> None:
        wav_path = None
        try:
            wav_path = self._recorder.stop()
            logger.info(f"Audio captured to {wav_path}")
            client = _import_transcriber().TranscriptionClient(api_key=self._runtime_api_key, model=self._selected_batch_model(), license_token=self._license_token, device_id=self._device_id)
            raw_text = client.transcribe(wav_path, language=self.cfg.get("language") or None)
            logger.info(f"Transcription received: {len(raw_text)} chars")
            processed = self._process_transcript(raw_text)
            output_handler.type_text(processed.text)
            self.page.run_thread(self._on_transcription_done, processed.text)
        except Exception as exc:
            logger.error(f"Batch transcription error: {exc}")
            self.page.run_thread(self._on_transcription_error, str(exc))
        finally:
            if wav_path:
                _import_transcriber().cleanup_temp(wav_path)
                logger.debug(f"Temp file cleaned: {wav_path}")

    def _on_transcription_done(self, text: str) -> None:
        logger.info(f"Transcription done: {len(text)} chars typed")
        self._stopping = False; self._reset_to_ready()

    def _on_transcription_error(self, err: str) -> None:
        logger.error(f"Transcription error: {err}")
        self._stopping = False; self._reset_to_ready()
        if self._is_chatting: self._apply_theme_to_controls()
        self.page.snack_bar = ft.SnackBar(content=ft.Text(err, color=TEXT), bgcolor=CARD_SOFT, open=True)
        self.page.update()

    def _set_status(self, text: str, color: str = MUTED, animate: bool = False) -> None:
        self.status_text.value = text; self.status_text.color = color; self.page.update()

    def _reset_to_ready(self) -> None:
        self._is_recording = False
        self._api_check_in_flight = False
        if self._is_chatting: self._set_status("Ask me anything", SUCCESS)
        else: self._set_status("Ready", MUTED)
        self._set_action("Start", ACCENT, ACCENT, TEXT, self._on_action_click)

    def _on_segment(self, segment_text: str) -> None:
        """Replace accumulated raw text with the authoritative segment text.

        ``TranscriptionStreamSegmentDelta`` carries complete, correctly-spaced
        text for each segment (sentence/clause).  We rebuild the accumulated
        raw text from the segment history so that spacing is always correct.
        """
        logger.info(f"_on_segment: '{segment_text[:60]}' (len={len(segment_text)})")
        with _live_lock:
            self._live_segments.append(segment_text)
            self._live_raw_text = " ".join(self._live_segments)
        # Immediately flush so the corrected text appears without delay.
        if self._live_paste_job: self._live_paste_job.cancel()
        self._flush_live_buffer()

    def _stop_realtime(self) -> None:
        logger.info("_stop_realtime called")
        self._is_recording = False
        # Flush any remaining text in the buffer
        self._flush_live_buffer()
        if self._rt_transcriber: self._rt_transcriber.stop(); self._rt_transcriber = None
        self._finalize_stop()

    def _finalize_stop(self) -> None:
        self._stopping = False; self._reset_to_ready()

    def _type_live_delta(self, delta: str) -> None:
        """Handle a raw text delta from the realtime API.

        Deltas are incremental fragments (sub-word partials, partial words,
        or words with/without leading spaces).  We accumulate them *as-is*
        for instant responsiveness, then let ``_on_segment`` correct the
        full text when a complete segment arrives.
        """
        logger.info(f"_type_live_delta: '{delta[:60]}' (raw_len={len(delta)})")
        with _live_lock:
            self._live_raw_text += delta
        logger.debug(f"_type_live_delta: raw_text now {len(self._live_raw_text)} chars")
        if self._live_paste_job: self._live_paste_job.cancel()
        # Short debounce – text appears quickly; segments correct it shortly after.
        timer = threading.Timer(0.15, self._flush_live_buffer); self._live_paste_job = timer; timer.daemon = True; timer.start()

    def _flush_live_buffer(self) -> None:
        with _live_lock:
            raw = self._live_raw_text
        processed = self._process_transcript(raw)
        with _live_lock:
            full = processed.text
            old = self._live_processed_marker
            if len(full) <= old:
                logger.debug(f"_flush_live_buffer: nothing new to type (processed len={len(full)}, marker={old})")
                return
            text_to_type = full[old:]
            self._live_processed_marker = len(full)
        logger.info(f"_flush_live_buffer: new_text='{text_to_type[:80]}' (len={len(text_to_type)})")
        if text_to_type:
            result = output_handler.type_text(text_to_type, interval=0.01)
            if result:
                logger.info(f"_flush_live_buffer: type_text succeeded for '{text_to_type[:60]}'")
            else:
                logger.error(f"_flush_live_buffer: type_text FAILED for '{text_to_type[:60]}'")

    def _on_realtime_done(self) -> None:
        logger.info("_on_realtime_done called")
        self._flush_live_buffer()
        self._finalize_stop()

    def _stop_any_active_work(self) -> None:
        if self._rt_transcriber:
            try: self._rt_transcriber.stop()
            except Exception: pass
            self._rt_transcriber = None
        if self._gemini_live_client:
            try: self._gemini_live_client.stop()
            except Exception: pass
            self._gemini_live_client = None

    def _serialize_entitlement(self, ent: website_client.LicenseEntitlement | None) -> dict:
        if not ent: return {}
        return {"licenseId": ent.license_id, "status": ent.status, "plan": ent.plan, "billingCycle": ent.billing_cycle, "quotaChars": ent.quota_chars, "bonusChars": ent.bonus_chars, "usedChars": ent.used_chars, "usedWords": ent.used_words, "remainingChars": ent.remaining_chars, "seatLimit": ent.seat_limit, "activeSeats": ent.active_seats, "isSubscription": ent.is_subscription, "canTranscribe": ent.can_transcribe}

    def _process_transcript(self, raw_text: str) -> dictation_features.ProcessedTranscript:
        return dictation_features.process_transcript(raw_text=raw_text, profile=self.cfg.get("dictation_profile", "notes"), replacements=self.cfg.get("text_replacements", {}), personal_dictionary=self.cfg.get("personal_dictionary", []), voice_commands_enabled=bool(self.cfg.get("voice_commands_enabled", True)), command_prefix=self.cfg.get("command_prefix", "command"))

    def _close_app(self, _event) -> None:
        logger.info("Close requested")
        if self._settings_process and self._settings_process.poll() is None:
            try:
                import ctypes
                hwnd = ctypes.windll.user32.FindWindowW(None, "Voxify Settings")
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 5)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Close the Settings window first.", color=TEXT),
                bgcolor=ft.Colors.with_opacity(0.9, DANGER), open=True,
            )
            self.page.update()
            return
        self._stop_any_active_work()
        self.page.run_task(self._close_app_async)

    async def _close_app_async(self) -> None:
        logger.info("Destroying window")
        await self.page.window.destroy()
        logger.info("Window destroyed")

    def _silence_trim_enabled(self) -> bool: return bool(self.cfg.get("silence_trim_enabled", True))

def main(page: ft.Page) -> None:
    VoxifyApp(page)

if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP)
