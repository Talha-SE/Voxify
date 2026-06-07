from __future__ import annotations

import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog

import flet as ft
import numpy as np
from scipy.io import wavfile

import app_info
import branding
import config
import license_cache
import recorder as rec_module
import website_client

IMAGE_FIT_CONTAIN = getattr(
    getattr(ft, "ImageFit", None),
    "CONTAIN",
    getattr(getattr(ft, "BoxFit", None), "CONTAIN", "contain"),
)


def _logo_image(logo_base64: str, size: int) -> ft.Image:
    try:
        return ft.Image(
            src_base64=logo_base64,
            width=size,
            height=size,
            fit=IMAGE_FIT_CONTAIN,
        )
    except TypeError:
        return ft.Image(
            src=f"data:image/png;base64,{logo_base64}",
            width=size,
            height=size,
            fit=IMAGE_FIT_CONTAIN,
        )

APP_TITLE = "Voxify Settings"
ACCENT = "#00B3FF"
ACCENT_ALT = "#0066FF"
BG = "#030712"
SURFACE = "#0B101E"
CARD = "#0F172A"
CARD_SOFT = "#131C31"
BORDER = "#1E293B"
TEXT = "#F8FBFF"
MUTED = "#94A3B8"
MUTED_SOFT = "#64748B"
SUCCESS = "#22C55E"
WARNING = "#F59E0B"

DEFAULT_MEMBERSHIP_URL = "https://brevios.gumroad.com/l/voxify-membership"
DEFAULT_ONETIME_URL = "https://brevios.gumroad.com/l/voxify"
DEFAULT_GUMROAD_URL = "https://brevios.gumroad.com"
DEFAULT_WEBSITE_URL = "https://voxify.brevios.com"

THEME_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "ACCENT": "#00B3FF",
        "ACCENT_ALT": "#0066FF",
        "BG": "#030712",
        "SURFACE": "#0B101E",
        "CARD": "#0F172A",
        "CARD_SOFT": "#131C31",
        "BORDER": "#1E293B",
        "TEXT": "#F8FBFF",
        "MUTED": "#94A3B8",
        "MUTED_SOFT": "#64748B",
        "SUCCESS": "#22C55E",
    },
    "light": {
        "ACCENT": "#2563EB",
        "ACCENT_ALT": "#1D4ED8",
        "BG": "#EAF4FF",
        "SURFACE": "#F8FCFF",
        "CARD": "#E3EEFD",
        "CARD_SOFT": "#D6E7FD",
        "BORDER": "#A7C1EA",
        "TEXT": "#102542",
        "MUTED": "#3E5D88",
        "MUTED_SOFT": "#5F7EA9",
        "SUCCESS": "#15803D",
    },
}


def _apply_theme_constants(theme_name: str) -> None:
    global ACCENT, ACCENT_ALT, BG, SURFACE, CARD, CARD_SOFT
    global BORDER, TEXT, MUTED, MUTED_SOFT, SUCCESS

    selected = theme_name if theme_name in THEME_PALETTES else "dark"
    palette = THEME_PALETTES[selected]
    ACCENT = palette["ACCENT"]
    ACCENT_ALT = palette["ACCENT_ALT"]
    BG = palette["BG"]
    SURFACE = palette["SURFACE"]
    CARD = palette["CARD"]
    CARD_SOFT = palette["CARD_SOFT"]
    BORDER = palette["BORDER"]
    TEXT = palette["TEXT"]
    MUTED = palette["MUTED"]
    MUTED_SOFT = palette["MUTED_SOFT"]
    SUCCESS = palette["SUCCESS"]

MODEL_OPTIONS = [
    ("Core", "voxtral-mini-transcribe-2602"),
    ("Advanced", "voxtral-small-2507"),
]
BATCH_MODEL_OPTIONS = [
    ("Core", "voxtral-mini-transcribe-2602"),
]

TABS: list[tuple[str, str, str]] = [
    ("general", "General", ft.Icons.PALETTE_OUTLINED),
    ("audio", "Audio", ft.Icons.MIC),
    ("assistant", "Assistant", ft.Icons.SMART_TOY_OUTLINED),
    ("system", "System", ft.Icons.MEMORY_OUTLINED),
    ("about", "About", ft.Icons.INFO_OUTLINE),
]


def _dropdown_options(items: list[tuple[str, str]]) -> list[ft.dropdown.Option]:
    return [ft.dropdown.Option(label) for label, _ in items]


def _dropdown_value(items: list[tuple[str, str]], stored_value: str, default_label: str) -> str:
    reverse = {value: label for label, value in items}
    return reverse.get(stored_value, default_label)


def _field_style() -> dict:
    return {
        "border_radius": 10,
        "filled": True,
        "fill_color": CARD_SOFT,
        "border_color": BORDER,
        "focused_border_color": ACCENT,
        "text_style": ft.TextStyle(color=TEXT, size=12),
        "label_style": ft.TextStyle(color=MUTED, size=10, weight=ft.FontWeight.W_700),
    }


def _list_to_multiline(values: list[str]) -> str:
    return "\n".join((value or "").strip() for value in values if (value or "").strip())


def _dict_to_multiline(values: dict[str, str]) -> str:
    lines: list[str] = []
    for key, value in values.items():
        clean_k = (key or "").strip()
        clean_v = (value or "").strip()
        if clean_k and clean_v:
            lines.append(f"{clean_k} => {clean_v}")
    return "\n".join(lines)


def _parse_multiline_list(raw_text: str) -> list[str]:
    return [line.strip() for line in (raw_text or "").splitlines() if line.strip()]


def _parse_replacements(raw_text: str) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for line in (raw_text or "").splitlines():
        clean = line.strip()
        if not clean:
            continue
        if "=>" in clean:
            left, right = clean.split("=>", 1)
        elif ":" in clean:
            left, right = clean.split(":", 1)
        else:
            continue
        key = left.strip()
        value = right.strip()
        if key and value:
            replacements[key] = value
    return replacements


def _get_or_create_device_id(cfg: dict) -> str:
    device_id = (cfg.get("device_id") or "").strip()
    if device_id:
        return device_id
    device_id = uuid.uuid4().hex
    latest = config.load()
    latest["device_id"] = device_id
    config.save(latest)
    return device_id


def _fmt_chars(value: int) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def _format_billing_cycle(value: str) -> str:
    normalized = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    labels = {
        "monthly": "Monthly",
        "quarterly": "Quarterly",
        "biannual": "Every 6 months",
        "yearly": "Yearly",
        "every_two_years": "Every 2 years",
        "lifetime": "Lifetime",
        "one_time": "One-time",
    }
    if not normalized:
        return "-"
    return labels.get(normalized, normalized.replace("_", " ").title())


def _setting_row(label: str, control: ft.Control) -> ft.Row:
    return ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Text(label, color=TEXT, size=12, weight=ft.FontWeight.W_500),
            control,
        ],
    )


def _card(title: str, icon: str, controls: list[ft.Control]) -> ft.Container:
    return ft.Container(
        padding=ft.Padding(14, 14, 14, 14),
        border_radius=14,
        bgcolor=SURFACE,
        border=ft.Border.all(1, BORDER),
        content=ft.Column(
            spacing=10,
            controls=[
                ft.Row(
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Icon(icon, size=14, color=ACCENT),
                        ft.Text(title, color=TEXT, size=11, weight=ft.FontWeight.W_800),
                    ],
                ),
                *controls,
            ],
        ),
    )


def _logo_widget(logo_base64: str | None, size: int, fallback_icon: str = ft.Icons.MIC) -> ft.Control:
    if logo_base64:
        return _logo_image(logo_base64, size)
    return ft.Icon(fallback_icon, size=size, color=ACCENT)


def main(page: ft.Page) -> None:
    cfg = config.load()
    logo_base64 = branding.load_logo_base64()
    device_id = _get_or_create_device_id(cfg)
    license_state = license_cache.load_state()
    selected_theme = (cfg.get("theme", "dark") or "dark").strip().lower()
    if selected_theme not in THEME_PALETTES:
        selected_theme = "dark"
    initial_theme = selected_theme
    preview_theme = selected_theme
    theme_preview_dirty = False
    _apply_theme_constants(selected_theme)

    status_dot = ft.Container(
        width=8,
        height=8,
        border_radius=4,
        bgcolor=SUCCESS,
        animate=ft.Animation(200, "ease"),
    )
    status_text = ft.Text("All changes saved", color=TEXT, size=11, weight=ft.FontWeight.W_600)
    status_indicator = ft.Row(
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[status_dot, status_text],
    )

    def _update_status(value: str, color: str) -> None:
        status_text.value = value
        status_dot.bgcolor = color

    page.title = APP_TITLE
    page.bgcolor = BG
    page.padding = 0
    page.spacing = 0
    page.theme_mode = ft.ThemeMode.DARK if selected_theme == "dark" else ft.ThemeMode.LIGHT
    page.window.bgcolor = BG
    page.window.title_bar_hidden = True
    page.window.title_bar_buttons_hidden = True
    page.window.resizable = False
    page.window.width = 500
    page.window.height = 650
    page.window.min_width = 500
    page.window.max_width = 500
    page.window.min_height = 650
    page.window.max_height = 650
    try:
        logo_path = branding.resolve_window_icon_path()
        if logo_path is not None:
            page.window.icon = str(logo_path.resolve())
    except Exception:
        pass

    def _on_window_event(event) -> None:
        nonlocal preview_theme
        nonlocal theme_preview_dirty
        event_type = str(getattr(event, "type", "")).lower()
        event_data = str(getattr(event, "data", "")).lower()
        if "close" in event_type or event_data == "close":
            if theme_preview_dirty and preview_theme != initial_theme:
                try:
                    latest_cfg = config.load()
                    latest_cfg["theme"] = initial_theme
                    config.save(latest_cfg)
                except Exception:
                    pass

    try:
        page.window.on_event = _on_window_event
    except Exception:
        pass

    def _get_current_ui_cfg() -> dict:
        is_live_mode = (mode_field.value or "Batch").strip().lower() == "live"
        allowed_options = MODEL_OPTIONS if is_live_mode else BATCH_MODEL_OPTIONS
        model_value = next((value for label, value in allowed_options if label == model_field.value), allowed_options[0][1])
        
        try:
            retry_limit = int((live_retry_limit_field.value or "2").strip())
        except ValueError:
            retry_limit = 2

        return {
            "model": model_value,
            "gemini_model": gemini_model_field.value or "gemini-3.1-flash-live-preview",
            "gemini_voice": voice_field.value or "Puck",
            "auto_type_delay": int(delay_slider.value or 3),
            "mode": "Live" if is_live_mode else "Batch",
            "source": (source_field.value or "Mic").lower(),
            "always_on_top": bool(always_on_top_switch.value),
            "check_for_updates": bool(check_updates_switch.value),
            "theme": "dark" if theme_switch.value else "light",
            "auto_minimize": bool(auto_minimize_switch.value),
            "minimize_timeout": int(minimize_timeout_slider.value or 10),
            "pc_control_enabled": bool(pc_control_switch.value),
            "live_retry_limit": max(0, min(10, retry_limit)),
            "voice_commands_enabled": bool(voice_commands_switch.value),
            "command_prefix": (command_prefix_field.value or "command").strip().lower() or "command",
            "auto_fallback_enabled": bool(auto_fallback_switch.value),
            "silence_trim_enabled": bool(silence_trim_switch.value),
            "send_reliability_events": bool(reliability_events_switch.value),
            "auto_install_updates": bool(auto_install_updates_switch.value),
            "restart_after_update": bool(restart_after_update_switch.value),
            "personal_dictionary": _parse_multiline_list(personal_dictionary_field.value or ""),
            "text_replacements": _parse_replacements(text_replacements_field.value or ""),
            "screen_share_resolution": (screen_resolution_field.value or "medium").strip(),
            "screen_share_pause_on_idle": bool(screen_pause_on_idle_switch.value),
        }

    def _sync_dropdown_from_event(e) -> None:
        """Flet may not update Dropdown.value when on_change fires; sync from event data."""
        if e is not None and e.data and isinstance(e.control, ft.Dropdown):
            e.control.value = e.data

    def _update_save_button_state(_e=None) -> None:
        # Sync control value from event data if available (Flet 0.84 Dropdown fix)
        _sync_dropdown_from_event(_e)
        current_ui = _get_current_ui_cfg()
        
        keys_to_compare = [
            "model", "gemini_model", "gemini_voice", "auto_type_delay", "mode", "source",
            "always_on_top", "check_for_updates", "theme", "auto_minimize", "minimize_timeout", "pc_control_enabled",
            "live_retry_limit", "voice_commands_enabled", "command_prefix", "auto_fallback_enabled",
            "silence_trim_enabled", "send_reliability_events", "auto_install_updates", "restart_after_update",
            "personal_dictionary", "text_replacements",
            "screen_share_resolution", "screen_share_quality", "screen_share_pause_on_idle",
        ]
        
        is_dirty = False
        for key in keys_to_compare:
            saved_val = cfg.get(key)
            current_val = current_ui.get(key)
            
            # Normalize None values to avoid false dirty detection on missing keys
            if saved_val is None:
                if isinstance(current_val, bool):
                    saved_val = False
                elif isinstance(current_val, (int, float)):
                    saved_val = 0
                elif isinstance(current_val, (list, dict)):
                    saved_val = [] if isinstance(current_val, list) else {}
                else:
                    saved_val = ""
                    
            if current_val is None:
                if isinstance(saved_val, bool):
                    current_val = False
                elif isinstance(saved_val, (int, float)):
                    current_val = 0
                elif isinstance(saved_val, (list, dict)):
                    current_val = [] if isinstance(saved_val, list) else {}
                else:
                    current_val = ""

            # Check if they are lists or dicts
            if isinstance(saved_val, (list, dict)) or isinstance(current_val, (list, dict)):
                if saved_val != current_val:
                    is_dirty = True
                    break
            elif isinstance(saved_val, bool) or isinstance(current_val, bool):
                if bool(saved_val) != bool(current_val):
                    is_dirty = True
                    break
            elif isinstance(saved_val, (int, float)) or isinstance(current_val, (int, float)):
                try:
                    if float(saved_val) != float(current_val):
                        is_dirty = True
                        break
                except (ValueError, TypeError):
                    if str(saved_val) != str(current_val):
                        is_dirty = True
                        break
            else:
                if str(saved_val).strip().lower() != str(current_val).strip().lower():
                    is_dirty = True
                    break

        # Always keep the save button active — user can tap it anytime
        save_button.disabled = False
        save_button.style = ft.ButtonStyle(
            color=TEXT,
            bgcolor=ACCENT_ALT,
            overlay_color=ACCENT,
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.Padding(14, 10, 14, 10),
        )
        save_button.content = ft.Row(
            tight=True,
            spacing=6,
            controls=[
                ft.Icon(ft.Icons.SAVE, size=14, color=TEXT),
                ft.Text("Save Changes", weight=ft.FontWeight.W_700)
            ]
        )

        if is_dirty:
            _update_status("Changes pending...", WARNING)
        else:
            _update_status("All changes saved", SUCCESS)

        page.update()

    # Create controls without on_change in constructor
    theme_switch = ft.Switch(value=(cfg.get("theme", "dark") == "dark"), active_color=ACCENT_ALT)
    always_on_top_switch = ft.Switch(value=bool(cfg.get("always_on_top", True)), active_color=ACCENT_ALT)
    auto_minimize_switch = ft.Switch(value=bool(cfg.get("auto_minimize", True)), active_color=ACCENT_ALT)

    delay_text = ft.Text(
        f"{int(cfg.get('auto_type_delay', 3))} s",
        color=ACCENT,
        size=10,
        weight=ft.FontWeight.W_700,
    )
    delay_slider = ft.Slider(
        min=1,
        max=10,
        divisions=9,
        value=float(cfg.get("auto_type_delay", 3)),
        active_color=ACCENT_ALT,
        inactive_color=BORDER,
    )

    minimize_timeout_value = int(cfg.get("minimize_timeout", 10))
    minimize_timeout_value = max(5, min(120, minimize_timeout_value))
    minimize_timeout_text = ft.Text(
        f"{minimize_timeout_value} s",
        color=ACCENT,
        size=10,
        weight=ft.FontWeight.W_700,
    )
    minimize_timeout_slider = ft.Slider(
        min=5,
        max=120,
        divisions=23,
        value=float(minimize_timeout_value),
        active_color=ACCENT_ALT,
        inactive_color=BORDER,
    )

    mode_field = ft.Dropdown(
        label="Mode",
        value=cfg.get("mode", "Live"),
        options=[ft.dropdown.Option("Batch"), ft.dropdown.Option("Live")],
        **_field_style(),
    )
    source_field = ft.Dropdown(
        label="Source",
        value=cfg.get("source", "mic").capitalize(),
        options=[ft.dropdown.Option("Mic"), ft.dropdown.Option("System")],
        **_field_style(),
    )
    model_field = ft.Dropdown(
        label="Model",
        value=_dropdown_value(MODEL_OPTIONS, cfg.get("model", "voxtral-mini-transcribe-2602"), "Core"),
        options=_dropdown_options(MODEL_OPTIONS),
        **_field_style(),
    )
    model_hint_text = ft.Text("", color=MUTED_SOFT, size=9)

    live_retry_limit_field = ft.TextField(
        label="Live retry limit",
        value=str(int(cfg.get("live_retry_limit", 2))),
        hint_text="0 - 10",
        **_field_style(),
    )
    voice_commands_switch = ft.Switch(value=bool(cfg.get("voice_commands_enabled", True)), active_color=ACCENT_ALT)
    auto_fallback_switch = ft.Switch(value=bool(cfg.get("auto_fallback_enabled", True)), active_color=ACCENT_ALT)
    silence_trim_switch = ft.Switch(value=bool(cfg.get("silence_trim_enabled", True)), active_color=ACCENT_ALT)
    command_prefix_field = ft.TextField(
        label="Command prefix",
        value=(cfg.get("command_prefix", "command") or "command").strip(),
        hint_text="e.g. command",
        **_field_style(),
    )

    personal_dictionary_field = ft.TextField(
        label="Personal dictionary",
        value=_list_to_multiline(cfg.get("personal_dictionary", [])),
        hint_text="One term per line",
        multiline=True,
        min_lines=3,
        max_lines=5,
        **_field_style(),
    )
    text_replacements_field = ft.TextField(
        label="Text replacements",
        value=_dict_to_multiline(cfg.get("text_replacements", {})),
        hint_text="abbr => expanded text",
        multiline=True,
        min_lines=3,
        max_lines=5,
        **_field_style(),
    )

    reliability_events_switch = ft.Switch(value=bool(cfg.get("send_reliability_events", False)), active_color=ACCENT_ALT)
    check_updates_switch = ft.Switch(value=bool(cfg.get("check_for_updates", True)), active_color=ACCENT_ALT)
    auto_install_updates_switch = ft.Switch(value=bool(cfg.get("auto_install_updates", True)), active_color=ACCENT_ALT)
    restart_after_update_switch = ft.Switch(value=bool(cfg.get("restart_after_update", True)), active_color=ACCENT_ALT)

    GEMINI_MODEL_OPTIONS: list[tuple[str, str]] = [
        ("gemini-3.1-flash-live-preview", "Gemini 3.1 Flash Live"),
        ("gemini-2.5-flash-native-audio-preview-12-2025", "Gemini 2.5 Flash Native Audio"),
    ]

    gemini_model_field = ft.Dropdown(
        label="Live Chat Model",
        value=cfg.get("gemini_model", "gemini-3.1-flash-live-preview"),
        options=[ft.dropdown.Option(key, text) for key, text in GEMINI_MODEL_OPTIONS],
        **_field_style(),
    )
    pc_control_switch = ft.Switch(value=bool(cfg.get("pc_control_enabled", True)), active_color=ACCENT_ALT)

    GEMINI_VOICES: list[tuple[str, str]] = [
        ("Achernar", "female"), ("Achird", "male"), ("Algenib", "male"), ("Algieba", "male"),
        ("Alnilam", "male"), ("Aoede", "female"), ("Autonoe", "female"), ("Callirrhoe", "female"),
        ("Charon", "male"), ("Despina", "female"), ("Enceladus", "male"), ("Erinome", "female"),
        ("Fenrir", "male"), ("Gacrux", "female"), ("Iapetus", "male"), ("Kore", "female"),
        ("Laomedeia", "female"), ("Leda", "female"), ("Orus", "male"), ("Puck", "male"),
        ("Pulcherrima", "female"), ("Rasalgethi", "male"), ("Sadachbia", "male"), ("Sadaltager", "male"),
        ("Schedar", "male"), ("Sulafat", "female"), ("Umbriel", "male"), ("Vindemiatrix", "female"),
        ("Zephyr", "female"), ("Zubenelgenubi", "male"),
    ]

    GEMINI_GENDER_OPTIONS: list[tuple[str, str]] = [
        ("all", "All"), ("male", "Male"), ("female", "Female"),
    ]

    current_gender = "all"
    current_voice = cfg.get("gemini_voice", "Puck")

    def _build_voice_options(gender: str) -> list[ft.dropdown.Option]:
        filtered = GEMINI_VOICES if gender == "all" else [v for v in GEMINI_VOICES if v[1] == gender]
        return [ft.dropdown.Option(name) for name, _ in filtered]

    voice_gender_field = ft.Dropdown(
        label="Voice Gender",
        value=current_gender,
        options=[ft.dropdown.Option(key, text) for key, text in GEMINI_GENDER_OPTIONS],
        **_field_style(),
    )

    voice_field = ft.Dropdown(
        label="Voice",
        value=current_voice if any(v[0] == current_voice for v in GEMINI_VOICES) else "Puck",
        options=_build_voice_options(current_gender),
        **_field_style(),
    )

    def _on_gender_change(e) -> None:
        _sync_dropdown_from_event(e)
        gender = voice_gender_field.value
        voice_field.options = _build_voice_options(gender)
        if not any(v[0] == voice_field.value for v in GEMINI_VOICES if gender == "all" or v[1] == gender):
            voice_field.value = voice_field.options[0].key if voice_field.options else "Puck"
        _update_save_button_state()
        voice_gender_field.page.update()

    # Define missing functions before assignment
    def _sync_model_by_mode() -> None:
        selected_mode = (mode_field.value or "Batch").strip().lower()
        is_live_mode = selected_mode == "live"
        allowed_options = MODEL_OPTIONS if is_live_mode else BATCH_MODEL_OPTIONS
        allowed_labels = [label for label, _ in allowed_options]
        if model_field.value not in allowed_labels:
            model_field.value = allowed_labels[0]
        model_field.options = _dropdown_options(allowed_options)
        model_hint_text.value = "Live supports Core and Advanced." if is_live_mode else "Batch supports Core only."
        model_field.update()
        model_hint_text.update()

    def on_mode_change(_event: ft.ControlEvent) -> None:
        _sync_dropdown_from_event(_event)
        _sync_model_by_mode()

    def on_delay_change(_event: ft.ControlEvent) -> None:
        delay_text.value = f"{int(delay_slider.value)} s"
        delay_text.update()

    def on_timeout_change(_event: ft.ControlEvent) -> None:
        minimize_timeout_text.value = f"{int(minimize_timeout_slider.value)} s"
        minimize_timeout_text.update()

    def on_auto_minimize_toggle(_event: ft.ControlEvent) -> None:
        timeout_row.visible = bool(auto_minimize_switch.value)
        timeout_row.update()

    def on_updates_toggle(_event: ft.ControlEvent) -> None:
        if not check_updates_switch.value:
            update_notice.visible = False
            update_status_text.value = f"v{app_info.APP_VERSION} - Updates disabled"
            update_status_text.color = MUTED
            page.update()
            return
        _check_updates(manual=False)

    def on_theme_toggle(_event: ft.ControlEvent) -> None:
        nonlocal preview_theme, theme_preview_dirty
        new_theme = "dark" if theme_switch.value else "light"
        preview_theme = new_theme
        theme_preview_dirty = True
        _apply_theme_constants(new_theme)
        page.theme_mode = ft.ThemeMode.DARK if new_theme == "dark" else ft.ThemeMode.LIGHT
        page.bgcolor = BG
        page.window.bgcolor = BG
        page.update()

    def on_save(_event: ft.ControlEvent | None = None) -> None:
        nonlocal cfg, initial_theme, theme_preview_dirty
        try:
            _update_status("Saving...", ACCENT)
            save_button.disabled = True
            save_button.content = ft.Row(
                tight=True,
                spacing=6,
                controls=[
                    ft.ProgressRing(width=14, height=14, color=TEXT, stroke_width=2),
                    ft.Text("Saving...", weight=ft.FontWeight.W_700)
                ]
            )
            page.update()

            # Perform actual save work in a background thread so UI stays responsive
            def _on_save_success(new_full_cfg: dict) -> None:
                nonlocal cfg, initial_theme, theme_preview_dirty
                cfg = new_full_cfg
                initial_theme = cfg.get("theme", "dark")
                theme_preview_dirty = False
                page.window.always_on_top = bool(cfg.get("always_on_top", True))
                _update_save_button_state()
                _show_snack("Settings saved successfully")
                page.update()

            def _on_save_failed(err: str) -> None:
                _update_status("Save failed", WARNING)
                _show_snack(f"Save failed: {err}")
                page.update()

            def _worker() -> None:
                try:
                    new_ui_cfg = _get_current_ui_cfg()
                    full_cfg = config.load()
                    full_cfg.update(new_ui_cfg)
                    config.save(full_cfg)
                    # schedule UI update on the main page thread
                    page.run_thread(_on_save_success, full_cfg)
                except Exception as exc:
                    page.run_thread(_on_save_failed, str(exc))

            threading.Thread(target=_worker, daemon=True).start()
        except Exception as exc:
            _update_status("Save failed", WARNING)
            _show_snack(f"Save failed: {exc}")
        page.update()

    def _clear_license(_event: ft.ControlEvent | None = None) -> None:
        try:
            license_cache.clear()
            license_key_field.value = ""
            nonlocal license_state
            license_state = {}
            _render_license_entitlement(None)
            _show_snack("License cleared from cache")
        except Exception as exc:
            _show_snack(f"Failed to clear license: {exc}")
        page.update()

    def _open_update_download(_e=None):
        if _latest_update_info and _latest_update_info.download_url:
            _open_external_url(_latest_update_info.download_url)
        else:
            _show_snack("No download URL available")

    def _install_update(_e=None):
        _show_snack("Installation logic not implemented in this build")

    def _restart_app(_e=None):
        try:
            subprocess.Popen([sys.executable] + sys.argv)
            page.window.destroy()
        except Exception as exc:
            _show_snack(f"Failed to restart: {exc}")

    def _ignore_update_version(_e=None):
        if _latest_update_info:
            try:
                c = config.load()
                c["ignored_update_version"] = _latest_update_info.version
                config.save(c)
                update_notice.visible = False
                _show_snack(f"Ignoring version {_latest_update_info.version}")
            except Exception as exc:
                _show_snack(f"Error: {exc}")
        page.update()

    def _finish_update_check(info: website_client.UpdateInfo | None, manual: bool, ignored_version: str, updates_enabled: bool) -> None:
        nonlocal _update_check_in_flight, _latest_update_info
        _update_check_in_flight = False
        check_now_button.disabled = False
        
        if not info or not info.update_available:
            update_status_text.value = f"v{app_info.APP_VERSION} - Up to date"
            update_status_text.color = SUCCESS
            if manual:
                _show_snack("You have the latest version!")
            page.update()
            return

        _latest_update_info = info
        if info.version == ignored_version and not manual:
            update_status_text.value = f"v{info.version} available (ignored)"
            page.update()
            return

        update_status_text.value = f"New version: v{info.version}"
        update_status_text.color = WARNING
        latest_update_text.value = f"Voxify v{info.version} is available"
        update_note_text.value = info.release_notes or "Stability and feature improvements."
        update_notice.visible = True
        update_link_button.visible = bool(info.download_url)
        install_update_button.visible = False
        restart_app_button.visible = False
        page.update()

    def _fail_update_check(error: str, manual: bool) -> None:
        nonlocal _update_check_in_flight
        _update_check_in_flight = False
        check_now_button.disabled = False
        update_status_text.value = "Update check failed"
        update_status_text.color = WARNING
        if manual:
            _show_snack(f"Update check failed: {error}")
        page.update()

    # Assign all event handlers as properties AFTER initialization
    theme_switch.on_change = lambda e: (on_theme_toggle(e), _update_save_button_state(e))
    always_on_top_switch.on_change = lambda e: _update_save_button_state(e)
    auto_minimize_switch.on_change = lambda e: (on_auto_minimize_toggle(e), _update_save_button_state(e))
    delay_slider.on_change = lambda e: (on_delay_change(e), _update_save_button_state(e))
    minimize_timeout_slider.on_change = lambda e: (on_timeout_change(e), _update_save_button_state(e))
    mode_field.on_change = lambda e: (on_mode_change(e), _update_save_button_state(e))
    source_field.on_change = lambda e: _update_save_button_state(e)
    model_field.on_change = lambda e: _update_save_button_state(e)
    live_retry_limit_field.on_change = lambda e: _update_save_button_state(e)
    voice_commands_switch.on_change = lambda e: _update_save_button_state(e)
    auto_fallback_switch.on_change = lambda e: _update_save_button_state(e)
    silence_trim_switch.on_change = lambda e: _update_save_button_state(e)
    command_prefix_field.on_change = lambda e: _update_save_button_state(e)
    personal_dictionary_field.on_change = lambda e: _update_save_button_state(e)
    text_replacements_field.on_change = lambda e: _update_save_button_state(e)
    reliability_events_switch.on_change = lambda e: _update_save_button_state(e)
    check_updates_switch.on_change = lambda e: (on_updates_toggle(e), _update_save_button_state(e))
    auto_install_updates_switch.on_change = lambda e: _update_save_button_state(e)
    restart_after_update_switch.on_change = lambda e: _update_save_button_state(e)
    gemini_model_field.on_change = lambda e: _update_save_button_state(e)
    pc_control_switch.on_change = lambda e: _update_save_button_state(e)
    voice_gender_field.on_change = _on_gender_change
    voice_field.on_change = lambda e: _update_save_button_state(e)

    update_status_text = ft.Text(f"Current version: v{app_info.APP_VERSION}", size=10, color=MUTED)
    latest_update_text = ft.Text("", size=10, color=TEXT, weight=ft.FontWeight.W_600)
    update_note_text = ft.Text("", size=9, color=MUTED)
    update_link_button = ft.TextButton("Download update")
    install_update_button = ft.TextButton("Install now")
    restart_app_button = ft.TextButton("Restart app")
    ignore_update_button = ft.TextButton("Ignore this version")
    downloaded_update_text = ft.Text("", size=9, color=MUTED_SOFT)
    check_now_button = ft.OutlinedButton(
        "Check now",
        icon=ft.Icons.UPDATE,
        style=ft.ButtonStyle(
            color=TEXT,
            side=ft.BorderSide(1, BORDER),
            shape=ft.RoundedRectangleBorder(radius=10),
        ),
    )

    _update_check_in_flight = False
    _latest_update_info: website_client.UpdateInfo | None = None
    _downloaded_update_path = ""

    license_key_field = ft.TextField(
        label="License key",
        value=(license_state.get("licenseKey") or "").strip(),
        password=True,
        can_reveal_password=True,
        **_field_style(),
    )
    api_key_field = ft.TextField(
        label="Private API key",
        value=(cfg.get("api_key") or "").strip(),
        password=True,
        can_reveal_password=True,
        hint_text="Mistral API key (optional override)",
        **_field_style(),
    )
    api_key_field.on_change = lambda e: _update_save_button_state(e)

    def _export_api(_e):
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            file_path = filedialog.asksaveasfilename(
                parent=root,
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt")],
                initialfile="api.txt",
                title="Export API Key"
            )
            root.destroy()
            if file_path:
                key = api_key_field.value or ""
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(key)
                _show_snack(f"API key exported to {Path(file_path).name}")
        except Exception as exc:
            _show_snack(f"Export failed: {exc}")

    export_api_button = ft.OutlinedButton(
        "Export API",
        icon=ft.Icons.DOWNLOAD,
        on_click=_export_api,
        style=ft.ButtonStyle(
            color=TEXT,
            side=ft.BorderSide(1, BORDER),
            shape=ft.RoundedRectangleBorder(radius=10),
        ),
    )
    license_status_text = ft.Text("License not activated", size=11, color=MUTED, weight=ft.FontWeight.W_700)
    license_plan_text = ft.Text("", size=10, color=TEXT, weight=ft.FontWeight.W_600)
    license_cycle_text = ft.Text("", size=10, color=TEXT, weight=ft.FontWeight.W_600)
    license_quota_text = ft.Text("", size=10, color=TEXT)
    license_seat_text = ft.Text("", size=10, color=TEXT)
    activate_license_button = ft.FilledButton("Activate", icon=ft.Icons.VERIFIED_USER)
    refresh_license_button = ft.OutlinedButton("Refresh", icon=ft.Icons.REFRESH)
    clear_license_button = ft.TextButton("Clear")

    update_notice = ft.Container(
        visible=False,
        padding=ft.Padding(10, 10, 10, 10),
        border_radius=12,
        bgcolor="#173359",
        border=ft.Border.all(1, ACCENT),
        content=ft.Column(
            spacing=6,
            controls=[
                latest_update_text,
                update_note_text,
                downloaded_update_text,
                ft.Row(spacing=8, controls=[update_link_button, install_update_button, restart_app_button]),
                ft.Row(spacing=8, controls=[ignore_update_button]),
            ],
        ),
    )

    timeout_row = ft.Container(
        visible=bool(auto_minimize_switch.value),
        content=ft.Column(
            spacing=6,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text("Idle timeout", color=MUTED, size=10, weight=ft.FontWeight.W_700),
                        minimize_timeout_text,
                    ],
                ),
                minimize_timeout_slider,
            ],
        ),
    )

    api_source_note = ft.Container(
        padding=ft.Padding(10, 10, 10, 10),
        border_radius=10,
        bgcolor=CARD_SOFT,
        border=ft.Border.all(1, BORDER),
        content=ft.Text(
            "Batch transcription is proxied through your website license service. Live mode can use gated runtime keys.",
            size=10,
            color=MUTED,
        ),
    )

    audio_diag_hint = ft.Text("Run a 2-second capture test for Mic and System sources.", size=10, color=MUTED)
    audio_diag_mic_text = ft.Text("Mic: not tested", size=10, color=MUTED)
    audio_diag_system_text = ft.Text("System: not tested", size=10, color=MUTED)
    run_audio_diag_button = ft.FilledButton("Run 2s Mic + System test", icon=ft.Icons.GRAPHIC_EQ)

    membership_link_button = ft.OutlinedButton("Open Membership Plan", icon=ft.Icons.OPEN_IN_NEW)
    onetime_link_button = ft.OutlinedButton("Open One-Time Plan", icon=ft.Icons.OPEN_IN_NEW)
    gumroad_link_button = ft.OutlinedButton("Open Gumroad Store", icon=ft.Icons.OPEN_IN_NEW)
    website_link_button = ft.OutlinedButton("Open Voxify Website", icon=ft.Icons.LANGUAGE)

    link_button_style = ft.ButtonStyle(
        color=TEXT, side=ft.BorderSide(1, BORDER), shape=ft.RoundedRectangleBorder(radius=10)
    )
    membership_link_button.style = link_button_style
    onetime_link_button.style = link_button_style
    gumroad_link_button.style = link_button_style
    website_link_button.style = link_button_style

    async def _close_window_async() -> None:
        try:
            await page.window.close()
        except Exception:
            try:
                page.window.destroy()
            except Exception:
                pass

    def _revert_theme_preview_if_needed() -> None:
        nonlocal preview_theme, theme_preview_dirty
        if theme_preview_dirty and preview_theme != initial_theme:
            try:
                latest_cfg = config.load()
                latest_cfg["theme"] = initial_theme
                config.save(latest_cfg)
            except Exception:
                pass
        preview_theme = initial_theme
        theme_preview_dirty = False

    def _close_window(_event=None) -> None:
        _revert_theme_preview_if_needed()
        page.run_task(_close_window_async)

    def _render_license_entitlement(entitlement: website_client.LicenseEntitlement | None) -> None:
        if not entitlement:
            license_status_text.value = "License not activated"
            license_status_text.color = MUTED
            license_plan_text.value = "Plan: -"
            license_cycle_text.value = "Billing cycle: -"
            license_quota_text.value = "Usage: -"
            license_seat_text.value = "Seats: -"
            page.update()
            return
        quota_exhausted = (not entitlement.can_transcribe) or int(entitlement.remaining_chars) <= 0
        if quota_exhausted:
            license_status_text.value = "Status: Quota reached"
            license_status_text.color = WARNING
        else:
            license_status_text.value = f"Status: {(entitlement.status or '').capitalize()}"
            license_status_text.color = SUCCESS if entitlement.status == "active" else MUTED
        license_plan_text.value = f"Plan: {(entitlement.plan or '').title() or '-'}"
        license_cycle_text.value = f"Billing cycle: {_format_billing_cycle(entitlement.billing_cycle)}"
        license_quota_text.value = (
            f"Usage: {_fmt_chars(entitlement.used_chars)} / {_fmt_chars(entitlement.quota_chars + entitlement.bonus_chars)} chars"
            f" | {_fmt_chars(entitlement.used_words)} words"
            f" | Remaining {_fmt_chars(entitlement.remaining_chars)}"
            f"{' | Top up required' if quota_exhausted else ''}"
        )
        license_seat_text.value = f"Seats: {entitlement.active_seats} / {entitlement.seat_limit}"
        page.update()

    def _show_snack(message: str) -> None:
        page.snack_bar = ft.SnackBar(content=ft.Text(message, color=TEXT), bgcolor=CARD_SOFT, open=True)
        page.update()

    def _open_external_url(url: str) -> None:
        try:
            page.launch_url(url)
        except Exception as exc:
            _show_snack(f"Unable to open link: {exc}")

    def _diagnostic_probe(source: str, label: str) -> tuple[bool, str]:
        wav_path = ""
        try:
            sample_rate = int(config.load().get("sample_rate", 16000))
            probe = rec_module.Recorder(source=source, sample_rate=sample_rate, silence_trim_enabled=False, reliability_mode="latency")
            probe.start()
            time.sleep(2.0)
            wav_path = probe.stop()
            _, audio = wavfile.read(wav_path)
            raw = np.asarray(audio)
            if raw.size == 0: return False, f"{label}: no audio frames captured"
            source_is_int = np.issubdtype(raw.dtype, np.integer)
            if raw.ndim > 1: raw = raw.astype(np.float32).mean(axis=1)
            normalized = raw.astype(np.float32)
            if source_is_int:
                max_value = float(np.iinfo(audio.dtype).max) or 1.0
                normalized = normalized / max_value
            else:
                normalized = np.clip(normalized, -1.0, 1.0)
            rms = float(np.sqrt(np.mean(np.square(normalized)))) if normalized.size else 0.0
            peak = float(np.max(np.abs(normalized))) if normalized.size else 0.0
            verdict = "signal detected" if (rms >= 0.010 or peak >= 0.080) else "low signal"
            return True, f"{label}: RMS {rms:.4f} | Peak {peak:.4f} | {verdict}"
        except Exception as exc: return False, f"{label}: {exc}"
        finally:
            if wav_path:
                try: Path(wav_path).unlink(missing_ok=True)
                except Exception: pass

    def _set_diag_line(source: str, message: str, ok: bool) -> None:
        target = audio_diag_mic_text if source == "mic" else audio_diag_system_text
        target.value = message
        target.color = SUCCESS if ok else WARNING
        page.update()

    def _finish_diagnostic(all_signals_ok: bool) -> None:
        run_audio_diag_button.disabled = False
        _update_status("Audio diagnostic complete" if all_signals_ok else "Audio diagnostic found issues", SUCCESS if all_signals_ok else WARNING)
        page.update()

    def _run_audio_diagnostic(_event: ft.ControlEvent | None = None) -> None:
        run_audio_diag_button.disabled = True
        _update_status("Running audio diagnostic...", MUTED)
        audio_diag_mic_text.value = "Mic: testing..."
        audio_diag_mic_text.color = MUTED
        audio_diag_system_text.value = "System: testing..."
        audio_diag_system_text.color = MUTED
        page.update()
        def _worker() -> None:
            signal_ok = True
            for source, label in (("mic", "Mic"), ("system", "System")):
                ok, message = _diagnostic_probe(source, label)
                if "low signal" in message.lower() or not ok: signal_ok = False
                page.run_thread(_set_diag_line, source, message, ok)
            page.run_thread(_finish_diagnostic, signal_ok)
        threading.Thread(target=_worker, daemon=True).start()

    def _activate_license(_event: ft.ControlEvent | None = None) -> None:
        key = (license_key_field.value or "").strip()
        if not key:
            _show_snack("License key is required.")
            return
        _update_status("Activating license...", MUTED)
        page.update()
        def _worker() -> None:
            try:
                session_data = website_client.activate_license(license_key=key, device_id=device_id, device_name=f"{app_info.APP_NAME}-{app_info.APP_PLATFORM}")
                license_cache.save_state(token=session_data.token, license_key=key, entitlement=session_data.entitlement.__dict__)
                page.run_thread(_render_license_entitlement, session_data.entitlement)
                page.run_thread(_show_snack, "License activated successfully.")
            except Exception as exc: page.run_thread(_show_snack, str(exc))
        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_license(_event: ft.ControlEvent | None = None) -> None:
        state = license_cache.load_state()
        token = (state.get("token") or "").strip()
        if not token:
            _activate_license(_event)
            return
        def _worker() -> None:
            try:
                session_data = website_client.refresh_license(token=token, device_id=device_id, device_name=f"{app_info.APP_NAME}-{app_info.APP_PLATFORM}", license_key=(state.get("licenseKey") or "").strip())
                license_cache.save_state(token=session_data.token, license_key=(state.get("licenseKey") or "").strip(), entitlement=session_data.entitlement.__dict__)
                page.run_thread(_render_license_entitlement, session_data.entitlement)
            except Exception as exc: page.run_thread(_show_snack, str(exc))
        threading.Thread(target=_worker, daemon=True).start()

    def _check_updates(manual: bool = False) -> None:
        nonlocal _update_check_in_flight
        if _update_check_in_flight: return
        _update_check_in_flight = True
        check_now_button.disabled = True
        update_status_text.value = f"Current version: v{app_info.APP_VERSION} - Checking..."
        update_status_text.color = MUTED
        page.update()
        def _worker() -> None:
            updates_enabled = bool(check_updates_switch.value)
            ignored_version = (config.load().get("ignored_update_version") or "").strip()
            try:
                info = website_client.get_update_info(current_version=app_info.APP_VERSION, platform=app_info.APP_PLATFORM, channel=app_info.APP_CHANNEL)
                page.run_thread(_finish_update_check, info, manual, ignored_version, updates_enabled)
            except Exception as exc: page.run_thread(_fail_update_check, str(exc), manual)
        threading.Thread(target=_worker, daemon=True).start()

    def _on_check_now(_event: ft.ControlEvent) -> None: _check_updates(manual=True)

    update_link_button.on_click = lambda e: _open_update_download(e)
    install_update_button.on_click = lambda e: _install_update(e)
    restart_app_button.on_click = lambda e: _restart_app(e)
    ignore_update_button.on_click = lambda e: _ignore_update_version(e)
    check_now_button.on_click = _on_check_now
    activate_license_button.on_click = _activate_license
    refresh_license_button.on_click = _refresh_license
    clear_license_button.on_click = lambda e: _clear_license(e)
    run_audio_diag_button.on_click = _run_audio_diagnostic
    membership_link_button.on_click = lambda _e: _open_external_url(DEFAULT_MEMBERSHIP_URL)
    onetime_link_button.on_click = lambda _e: _open_external_url(DEFAULT_ONETIME_URL)
    gumroad_link_button.on_click = lambda _e: _open_external_url(DEFAULT_GUMROAD_URL)
    website_link_button.on_click = lambda _e: _open_external_url(DEFAULT_WEBSITE_URL)

    general_content = ft.Column(
        spacing=10,
        controls=[
            _card("Appearance", ft.Icons.PALETTE_OUTLINED, [_setting_row("Dark theme", theme_switch)]),
            _card("Typing", ft.Icons.KEYBOARD, [ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, controls=[ft.Text("Auto-type delay", color=MUTED, size=10, weight=ft.FontWeight.W_700), delay_text]), delay_slider]),
            _card("Smart Behavior", ft.Icons.AUTO_AWESOME, [_setting_row("Auto-minimize to mic", auto_minimize_switch), timeout_row]),
            _card("Text Tools", ft.Icons.AUTO_FIX_HIGH, [personal_dictionary_field, text_replacements_field]),
        ],
    )

    audio_content = ft.Column(
        spacing=10,
        controls=[
            _card("Mode & Source", ft.Icons.MIC, [ft.Row(spacing=10, controls=[ft.Container(expand=True, content=mode_field), ft.Container(expand=True, content=source_field)]), live_retry_limit_field, _setting_row("Voice commands", voice_commands_switch), command_prefix_field, _setting_row("Auto fallback", auto_fallback_switch), _setting_row("Adaptive trim", silence_trim_switch), _setting_row("Always on top", always_on_top_switch)]),
            _card("Audio Diagnostic", ft.Icons.GRAPHIC_EQ, [audio_diag_hint, run_audio_diag_button, audio_diag_mic_text, audio_diag_system_text]),
        ],
    )

    screen_resolution_field = ft.Dropdown(
        label="Share Resolution",
        value=cfg.get("screen_share_resolution", "medium"),
        options=[
            ft.dropdown.Option("low", "Low (480p)"),
            ft.dropdown.Option("medium", "Medium (768p)"),
            ft.dropdown.Option("high", "High (1024p)"),
        ],
        **_field_style(),
    )
    screen_quality_slider = ft.Slider(
        min=30, max=95, divisions=13,
        value=float(cfg.get("screen_share_quality", 70)),
        label="{value}%",
        active_color=ACCENT_ALT,
        inactive_color=ft.Colors.with_opacity(0.15, ACCENT_ALT),
    )
    screen_quality_text = ft.Text(f"{int(screen_quality_slider.value)}%", size=10, color=MUTED, width=35)
    screen_pause_on_idle_switch = ft.Switch(
        value=bool(cfg.get("screen_share_pause_on_idle", True)),
        active_color=ACCENT_ALT,
    )

    def _on_screen_quality_change(e) -> None:
        screen_quality_text.value = f"{int(screen_quality_slider.value)}%"
        screen_quality_text.update()
        _update_save_button_state(e)

    screen_quality_slider.on_change = _on_screen_quality_change
    screen_resolution_field.on_change = lambda e: _update_save_button_state(e)
    screen_pause_on_idle_switch.on_change = lambda e: _update_save_button_state(e)

    assistant_content = ft.Column(
        spacing=10,
        controls=[
            _card("Gemini Live Chat", ft.Icons.SMART_TOY_OUTLINED, [ft.Text("Select the Gemini model for real-time voice chat.", size=10, color=MUTED), gemini_model_field, _setting_row("Allow AI to control PC", pc_control_switch)]),
            _card("Voice", ft.Icons.RECORD_VOICE_OVER_OUTLINED, [ft.Text("Choose a voice for the Gemini assistant.", size=10, color=MUTED), voice_gender_field, voice_field]),
            _card("Screen Sharing", ft.Icons.MONITOR_OUTLINED, [
                ft.Text("Adjust screen capture quality and behavior.", size=10, color=MUTED),
                screen_resolution_field,
                ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[ft.Text("JPEG quality", color=MUTED, size=10, weight=ft.FontWeight.W_700), screen_quality_text]),
                screen_quality_slider,
                _setting_row("Skip idle frames", screen_pause_on_idle_switch),
            ]),
        ],
    )

    system_content = ft.Column(
        spacing=10,
        controls=[
            _card("License & Quota", ft.Icons.VERIFIED_USER, [license_key_field, ft.Row(spacing=8, controls=[activate_license_button, refresh_license_button, clear_license_button]), license_status_text, license_plan_text, license_cycle_text, license_quota_text, license_seat_text]),
            _card("Purchase & Links", ft.Icons.LINK, [ft.Text("Upgrade or manage plans quickly.", size=10, color=MUTED), membership_link_button, onetime_link_button, gumroad_link_button, website_link_button]),
            _card("Runtime", ft.Icons.MEMORY_OUTLINED, [api_source_note, api_key_field, export_api_button, model_field, model_hint_text]),
            _card("Updates & Telemetry", ft.Icons.UPDATE, [_setting_row("Check for updates", check_updates_switch), _setting_row("Auto-install updates", auto_install_updates_switch), _setting_row("Restart after install", restart_after_update_switch), _setting_row("Send reliability events", reliability_events_switch), update_status_text, check_now_button, update_notice]),
        ],
    )

    about_content = ft.Column(
        spacing=10,
        controls=[
            _card("About Voxify", ft.Icons.INFO_OUTLINE, [ft.Container(padding=ft.Padding(12, 12, 12, 12), border_radius=10, bgcolor=CARD_SOFT, border=ft.Border.all(1, BORDER), content=ft.Column(spacing=6, controls=[ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[_logo_widget(logo_base64, 22), ft.Text("Voxify", size=16, color=TEXT, weight=ft.FontWeight.W_900)]), ft.Text("Speech to Text Engine", size=10, color=MUTED, weight=ft.FontWeight.W_700), ft.Text(f"Version {app_info.APP_VERSION}", size=10, color=MUTED_SOFT)])), ft.OutlinedButton("Documentation", icon=ft.Icons.OPEN_IN_NEW, on_click=lambda _e: page.launch_url("https://brevios.com"), style=ft.ButtonStyle(color=TEXT, side=ft.BorderSide(1, BORDER), shape=ft.RoundedRectangleBorder(radius=10))), ft.OutlinedButton("Privacy Policy", icon=ft.Icons.OPEN_IN_NEW, on_click=lambda _e: page.launch_url("http://127.0.0.1:5050/privacy-policy"), style=ft.ButtonStyle(color=TEXT, side=ft.BorderSide(1, BORDER), shape=ft.RoundedRectangleBorder(radius=10))), ft.OutlinedButton("Terms of Service", icon=ft.Icons.OPEN_IN_NEW, on_click=lambda _e: page.launch_url("http://127.0.0.1:5050/terms-and-conditions"), style=ft.ButtonStyle(color=TEXT, side=ft.BorderSide(1, BORDER), shape=ft.RoundedRectangleBorder(radius=10)))]),
        ],
    )

    tab_containers: dict[str, ft.Container] = {
        "general": ft.Container(visible=True, content=general_content),
        "audio": ft.Container(visible=False, content=audio_content),
        "assistant": ft.Container(visible=False, content=assistant_content),
        "system": ft.Container(visible=False, content=system_content),
        "about": ft.Container(visible=False, content=about_content),
    }

    tab_buttons: dict[str, dict[str, ft.Control]] = {}
    active_tab_id = "general"

    def _activate_tab(tab_id: str) -> None:
        nonlocal active_tab_id
        active_tab_id = tab_id
        for item_id, refs in tab_buttons.items():
            is_active = item_id == tab_id
            refs["container"].bgcolor = CARD_SOFT if is_active else ft.Colors.TRANSPARENT
            refs["icon"].color = ACCENT if is_active else MUTED_SOFT
            refs["label"].color = TEXT if is_active else MUTED_SOFT
            tab_containers[item_id].visible = is_active
        page.update()

    segmented_buttons: list[ft.Control] = []
    for tab_id, label, icon_name in TABS:
        icon = ft.Icon(icon_name, size=14, color=MUTED_SOFT)
        text = ft.Text(label, size=9, weight=ft.FontWeight.W_700, color=MUTED_SOFT)
        
        def make_hover_handler(tid, ic, tx):
            def on_hover(e):
                if tid != active_tab_id:
                    e.control.bgcolor = CARD_SOFT if e.data == "true" else ft.Colors.TRANSPARENT
                    ic.color = TEXT if e.data == "true" else MUTED_SOFT
                    tx.color = TEXT if e.data == "true" else MUTED_SOFT
                    e.control.update()
            return on_hover

        button = ft.Container(
            expand=True,
            border_radius=10,
            padding=ft.Padding(6, 8, 6, 8),
            alignment=ft.Alignment(0, 0),
            animate=ft.Animation(200, "easeOut"),
            on_hover=make_hover_handler(tab_id, icon, text),
            on_click=lambda _e, tid=tab_id: _activate_tab(tid),
            content=ft.Column(
                spacing=3,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[icon, text]
            )
        )
        tab_buttons[tab_id] = {"container": button, "icon": icon, "label": text}
        segmented_buttons.append(button)

    _activate_tab("general")

    save_button = ft.FilledButton(
        content=ft.Row(
            tight=True,
            spacing=6,
            controls=[
                ft.Icon(ft.Icons.SAVE, size=14, color=TEXT),
                ft.Text("Save Changes", weight=ft.FontWeight.W_700)
            ]
        ),
        on_click=on_save,
        style=ft.ButtonStyle(
            color=TEXT,
            bgcolor=ACCENT_ALT,
            overlay_color=ACCENT,
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.Padding(14, 10, 14, 10)
        )
    )

    root = ft.Container(
        expand=True,
        bgcolor=BG,
        padding=ft.Padding(10, 10, 10, 10),
        content=ft.Column(
            spacing=10,
            controls=[
                ft.Container(
                    padding=ft.Padding(12, 12, 12, 12),
                    border_radius=14,
                    bgcolor=SURFACE,
                    border=ft.Border.all(1, BORDER),
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Row(
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    _logo_widget(logo_base64, 18),
                                    ft.Column(
                                        spacing=2,
                                        controls=[
                                            ft.Text("Voxify", size=16, weight=ft.FontWeight.W_900, color=TEXT),
                                            ft.Text("Command Center", size=9, color=MUTED, weight=ft.FontWeight.W_700)
                                        ]
                                    )
                                ]
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE,
                                icon_size=16,
                                icon_color=MUTED,
                                tooltip="Close",
                                on_click=_close_window
                            )
                        ]
                    )
                ),
                ft.Container(
                    padding=ft.Padding(4, 4, 4, 4),
                    border_radius=14,
                    bgcolor=CARD,
                    border=ft.Border.all(1, BORDER),
                    content=ft.Row(spacing=4, controls=segmented_buttons)
                ),
                ft.Container(
                    expand=True,
                    padding=ft.Padding(0, 2, 0, 2),
                    content=ft.Column(
                        scroll=ft.ScrollMode.AUTO,
                        spacing=10,
                        controls=[
                            tab_containers["general"],
                            tab_containers["audio"],
                            tab_containers["assistant"],
                            tab_containers["system"],
                            tab_containers["about"]
                        ]
                    )
                ),
                ft.Container(
                    padding=ft.Padding(12, 12, 12, 12),
                    border_radius=14,
                    bgcolor=SURFACE,
                    border=ft.Border.all(1, BORDER),
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[status_indicator, save_button]
                    )
                )
            ]
        )
    )
    page.add(root)
    page.update()
    _sync_model_by_mode()
    _update_save_button_state()
    _check_updates(manual=False)
    _render_license_entitlement(website_client._parse_entitlement(license_state.get("entitlement")) if license_state.get("entitlement") else None)

if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP)
