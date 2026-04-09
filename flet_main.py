from __future__ import annotations

import math
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import flet as ft

import config
import app_info
import dictation_features
import output_handler
import recorder as rec_module
import reliability
import realtime_transcriber as rt_module
import transcriber as tr_module
import website_client

APP_TITLE = "SONUS"
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
BATCH_MODEL = "voxtral-mini-2507"
LIVE_MODELS = {"voxtral-mini-2507", "voxtral-small-2507"}
WIDGET_FULL_WIDTH = 452
WIDGET_FULL_HEIGHT = 126
WIDGET_MINI_WIDTH = 64
WIDGET_MINI_HEIGHT = 64


class SonusApp:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.cfg = config.load()

        self._recorder: Optional[rec_module.Recorder] = None
        self._rt_transcriber: Optional[rt_module.RealtimeTranscriber] = None
        self._click_listener = None
        self._waiting_click = False
        self._is_recording = False
        self._stopping = False
        self._live_text_buffer: list[str] = []
        self._live_paste_job = None
        self._runtime_api_key = ""
        self._api_last_check_at = 0.0
        self._api_cache_ttl_sec = 120.0
        self._api_check_in_flight = False
        self._api_lock = threading.Lock()
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

        self.status_anim_running = False
        self.status_anim_step = 0
        self.status_anim_base = "Ready"
        self.status_anim_thread: Optional[threading.Thread] = None
        self._wave_anim_running = False
        self._wave_anim_thread: Optional[threading.Thread] = None
        self._settings_process: Optional[subprocess.Popen] = None
        self._settings_opening = False
        self._config_mtime = self._get_config_mtime()

        self._setup_page()
        self._build_ui()
        threading.Thread(target=self._warmup_startup, daemon=True).start()
        threading.Thread(target=self._watch_config_changes, daemon=True).start()

    def _setup_page(self) -> None:
        self.page.title = APP_TITLE
        self.page.bgcolor = BG
        self.page.padding = 0
        self.page.spacing = 0
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.window.bgcolor = BG
        self.page.window.width = WIDGET_FULL_WIDTH
        self.page.window.height = WIDGET_FULL_HEIGHT
        self.page.window.min_width = WIDGET_FULL_WIDTH
        self.page.window.max_width = WIDGET_FULL_WIDTH
        self.page.window.min_height = WIDGET_FULL_HEIGHT
        self.page.window.max_height = WIDGET_FULL_HEIGHT
        self.page.window.resizable = False
        self.page.window.shadow = True
        self.page.window.title_bar_hidden = True
        self.page.window.title_bar_buttons_hidden = True
        self.page.window.frameless = True
        self.page.window.always_on_top = bool(self.cfg.get("always_on_top", True))
        self.page.window.movable = True

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

        if minimized:
            self.page.window.width = WIDGET_MINI_WIDTH
            self.page.window.height = WIDGET_MINI_HEIGHT
            self.page.window.min_width = WIDGET_MINI_WIDTH
            self.page.window.max_width = WIDGET_MINI_WIDTH
            self.page.window.min_height = WIDGET_MINI_HEIGHT
            self.page.window.max_height = WIDGET_MINI_HEIGHT
            self._cancel_auto_minimize_timer()
        else:
            self.page.window.width = WIDGET_FULL_WIDTH
            self.page.window.height = WIDGET_FULL_HEIGHT
            self.page.window.min_width = WIDGET_FULL_WIDTH
            self.page.window.max_width = WIDGET_FULL_WIDTH
            self.page.window.min_height = WIDGET_FULL_HEIGHT
            self.page.window.max_height = WIDGET_FULL_HEIGHT
            self._schedule_auto_minimize_timer()

        self.page.update()

    def _build_ui(self) -> None:
        self.title_text = ft.Text(
            "SONUS",
            size=11,
            weight=ft.FontWeight.W_900,
            color=TEXT,
        )
        self.status_text = ft.Text(
            "Standby",
            size=8,
            weight=ft.FontWeight.W_700,
            color=MUTED,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        self.mode_badge_text = ft.Text(
            "LIVE",
            size=7,
            weight=ft.FontWeight.W_800,
            color=ACCENT_GLOW,
        )
        self.mode_badge = ft.Container(
            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
            border_radius=999,
            bgcolor=ft.Colors.with_opacity(0.15, ACCENT),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.4, ACCENT)),
            content=self.mode_badge_text,
        )

        self.aux_chip_text = ft.Text(
            "",
            size=7,
            weight=ft.FontWeight.W_700,
            color=TEXT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.aux_chip = ft.Container(
            visible=False,
            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
            border_radius=999,
            bgcolor=ft.Colors.with_opacity(0.28, "#B45309"),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.55, "#FB923C")),
            content=self.aux_chip_text,
        )

        self.pulse_ring = ft.Container(
            width=10,
            height=10,
            border_radius=5,
            bgcolor=ft.Colors.with_opacity(0.3, INACTIVE_DOT),
            alignment=ft.Alignment(0, 0),
            animate=120,
        )
        self.pulse_core = ft.Container(
            width=7,
            height=7,
            border_radius=3.5,
            bgcolor=INACTIVE_DOT,
            animate=120,
        )
        indicator = ft.Container(
            width=16,
            height=16,
            alignment=ft.Alignment(0, 0),
            content=ft.Stack(
                width=16,
                height=16,
                controls=[
                    ft.Container(alignment=ft.Alignment(0, 0), content=self.pulse_ring),
                    ft.Container(alignment=ft.Alignment(0, 0), content=self.pulse_core),
                ],
            ),
        )

        self.wave_bars: list[ft.Container] = []
        for _ in range(5):
            bar = ft.Container(
                width=3,
                height=6,
                border_radius=3,
                bgcolor=BAR_INACTIVE,
                animate=120,
            )
            self.wave_bars.append(bar)

        waveform = ft.Container(
            width=54,
            height=24,
            alignment=ft.Alignment(0, 0),
            content=ft.Row(
                spacing=3,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.END,
                controls=self.wave_bars,
            ),
        )

        self.action_label = ft.Text("Start", color=TEXT)
        self.action_icon = ft.Icon(ft.Icons.MIC_OFF_ROUNDED, size=18, color=ACCENT)
        self.action_button = ft.Container(
            width=40,
            height=40,
            border_radius=20,
            alignment=ft.Alignment(0, 0),
            bgcolor=CARD_ACTIVE,
            border=ft.Border.all(1, ft.Colors.with_opacity(0.45, ACCENT)),
            content=self.action_icon,
            on_click=self._on_action_click,
            animate=160,
            shadow=ft.BoxShadow(
                blur_radius=14,
                spread_radius=0,
                color=ft.Colors.with_opacity(0.22, ACCENT),
            ),
        )

        self.minimized_action_icon = ft.Icon(ft.Icons.MIC_OFF_ROUNDED, size=22, color=ACCENT)
        self.minimized_action_button = ft.Container(
            width=52,
            height=52,
            border_radius=26,
            alignment=ft.Alignment(0, 0),
            bgcolor=CARD_ACTIVE,
            border=ft.Border.all(1, ft.Colors.with_opacity(0.45, ACCENT)),
            content=self.minimized_action_icon,
            on_click=self._on_action_click,
            animate=160,
            shadow=ft.BoxShadow(
                blur_radius=16,
                spread_radius=0,
                color=ft.Colors.with_opacity(0.24, ACCENT),
            ),
        )

        settings_icon = ft.Icon(ft.Icons.SETTINGS_ROUNDED, size=16, color=ft.Colors.with_opacity(0.8, "#60A5FA"))
        self.settings_btn = ft.Container(
            width=32,
            height=32,
            border_radius=16,
            alignment=ft.Alignment(0, 0),
            bgcolor=ft.Colors.with_opacity(0.2, CARD_ACTIVE),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.2, BORDER)),
            content=settings_icon,
            on_click=self._open_settings,
            ink=True,
        )

        close_icon = ft.Icon(ft.Icons.CLOSE_ROUNDED, size=16, color=ft.Colors.with_opacity(0.9, "#93A8C6"))
        self.close_btn = ft.Container(
            width=32,
            height=32,
            border_radius=16,
            alignment=ft.Alignment(0, 0),
            bgcolor=ft.Colors.with_opacity(0.2, CARD_ACTIVE),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.2, BORDER)),
            content=close_icon,
            on_click=self._close_app,
            ink=True,
        )

        brand_block = ft.Column(
            spacing=1,
            tight=True,
            controls=[
                ft.Row(
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[self.title_text, self.mode_badge],
                ),
                self.status_text,
                self.aux_chip,
            ],
        )

        controls_group = ft.Container(
            margin=ft.Margin.only(left=4),
            padding=ft.Padding.only(left=10),
            border=ft.Border.only(left=ft.BorderSide(1, ft.Colors.with_opacity(0.3, ACCENT))),
            content=ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[self.action_button, self.settings_btn, self.close_btn],
            ),
        )

        self.full_mode_container = ft.Container(
            visible=True,
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[indicator, brand_block],
                    ),
                    waveform,
                    controls_group,
                ],
            ),
        )

        self.minimized_mode_container = ft.Container(
            visible=False,
            alignment=ft.Alignment(0, 0),
            content=self.minimized_action_button,
        )

        widget_shell = ft.Container(
            expand=True,
            margin=ft.Margin.all(8),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=999,
            gradient=ft.LinearGradient(
                begin=ft.Alignment(-1, -1),
                end=ft.Alignment(1, 1),
                colors=[CARD, "#0B1A32"],
            ),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.55, ACCENT)),
            shadow=ft.BoxShadow(
                blur_radius=24,
                spread_radius=1,
                color=ft.Colors.with_opacity(0.42, "#020617"),
            ),
            on_hover=self._on_widget_hover,
            content=ft.Stack(
                expand=True,
                controls=[
                    self.full_mode_container,
                    self.minimized_mode_container,
                ],
            ),
        )

        root = ft.WindowDragArea(maximizable=False, content=widget_shell)
        self.page.add(root)

        self._set_mode_badge()
        self._set_action("Start", ACCENT, ACCENT, TEXT, self._on_action_click)
        self._set_status("Ready", MUTED)
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
            self.mode_badge.bgcolor = ft.Colors.with_opacity(0.16, "#1D4ED8")
            self.mode_badge.border = ft.Border.all(1, ft.Colors.with_opacity(0.4, "#60A5FA"))
            self.mode_badge_text.color = "#93C5FD"
        self.page.update()

    def _is_live_mode(self) -> bool:
        return (self.cfg.get("mode") or "Live").strip().lower() == "live"

    def _selected_batch_model(self) -> str:
        selected = (self.cfg.get("model") or BATCH_MODEL).strip().lower()
        if selected in LIVE_MODELS:
            return selected
        return BATCH_MODEL

    def _set_action(
        self,
        label: str,
        fg: str,
        border: str,
        text_color: str,
        on_click,
    ) -> None:
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

        icon_name = ft.Icons.MIC_OFF_ROUNDED
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
            button_gradient = ft.LinearGradient(
                begin=ft.Alignment(-1, 0),
                end=ft.Alignment(1, 0),
                colors=[ACCENT, ACCENT_GLOW],
            )
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
        elif is_busy:
            icon_name = ft.Icons.HOURGLASS_TOP_ROUNDED
            icon_color = MUTED
            button_bg = CARD_SOFT
            button_border = ft.Colors.with_opacity(0.45, BORDER)
            glow_color = ft.Colors.with_opacity(0.0, ACCENT)
        elif "start" in label:
            icon_name = ft.Icons.MIC_OFF_ROUNDED
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
        self.action_icon.color = icon_color
        self.action_button.bgcolor = button_bg
        self.action_button.gradient = button_gradient
        self.action_button.border = ft.Border.all(1, button_border)
        self.action_button.shadow = ft.BoxShadow(
            blur_radius=16,
            spread_radius=0,
            color=glow_color,
        )

        self.minimized_action_icon.name = icon_name
        self.minimized_action_icon.color = icon_color
        self.minimized_action_button.bgcolor = button_bg
        self.minimized_action_button.gradient = button_gradient
        self.minimized_action_button.border = ft.Border.all(1, button_border)
        self.minimized_action_button.shadow = ft.BoxShadow(
            blur_radius=16,
            spread_radius=0,
            color=glow_color,
        )

    def _set_health_chip(self, text: str = "", active: bool = False) -> None:
        # Health chip is intentionally hidden in the compact modern UI.
        return

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
        wave_heights: tuple[tuple[float, ...], ...] = (
            (0.2, 0.6, 0.3, 0.8, 0.2),
            (0.2, 0.8, 0.4, 1.0, 0.2),
            (0.2, 0.5, 0.9, 0.4, 0.2),
            (0.2, 1.0, 0.5, 0.7, 0.2),
            (0.2, 0.7, 0.3, 0.9, 0.2),
        )

        def _animate() -> None:
            step = 0
            while self._wave_anim_running:
                try:
                    active = self._is_visual_active()
                    pattern = wave_heights[step % len(wave_heights)] if active else wave_heights[0]
                    pulse = (math.sin(step * 0.6) + 1.0) / 2.0

                    for index, bar in enumerate(self.wave_bars):
                        bar.height = int(5 + pattern[index] * 16)
                        bar.bgcolor = BAR_ACTIVE if active else BAR_INACTIVE

                    ring_size = int(10 + (6 * pulse if active else 2 * pulse))
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

        self._wave_anim_thread = threading.Thread(target=_animate, daemon=True)
        self._wave_anim_thread.start()

    def _open_settings(self, _event) -> None:
        self._register_widget_interaction()
        if self._settings_opening:
            return
        if self._settings_process and self._settings_process.poll() is None:
            return

        self._settings_opening = True

        def _launch() -> None:
            try:
                if getattr(sys, "frozen", False):
                    cmd = [sys.executable, "--settings"]
                    launch_cwd = str(Path(sys.executable).parent)
                else:
                    app_script = Path(__file__).with_name("app.py")
                    if not app_script.exists():
                        raise FileNotFoundError("app.py not found next to flet_main.py")
                    cmd = [sys.executable, str(app_script), "--settings"]
                    launch_cwd = str(app_script.parent)

                kwargs = {"cwd": launch_cwd}
                if sys.platform.startswith("win"):
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self._settings_process = subprocess.Popen(cmd, **kwargs)
                threading.Thread(
                    target=self._wait_for_settings_process,
                    args=(self._settings_process,),
                    daemon=True,
                ).start()
            except Exception as exc:
                self.page.run_thread(self._show_settings_error, f"Unable to open settings: {exc}")
            finally:
                self._settings_opening = False

        threading.Thread(target=_launch, daemon=True).start()

    def _wait_for_settings_process(self, process: subprocess.Popen) -> None:
        try:
            process.wait()
        except Exception:
            return
        try:
            self.page.run_thread(self._sync_settings_from_disk)
        except RuntimeError:
            return

    def _sync_settings_from_disk(self) -> None:
        previous_mode = (self.cfg.get("mode") or "Batch").strip()
        previous_channel = (self.cfg.get("runtime_channel") or "stable").strip().lower()
        previous_auto_minimize = self._auto_minimize_enabled()

        self.cfg = config.load()
        self._config_mtime = self._get_config_mtime()
        self.page.window.always_on_top = bool(self.cfg.get("always_on_top", True))
        self._set_mode_badge()

        if self._auto_minimize_enabled():
            self._schedule_auto_minimize_timer()
        else:
            self._cancel_auto_minimize_timer()
            if self._is_minimized:
                self._set_minimized(False)

        current_channel = (self.cfg.get("runtime_channel") or "stable").strip().lower()
        if current_channel != previous_channel:
            self._runtime_config = None
            self._runtime_config_last_fetch = 0.0

        if not self._is_recording and not self._waiting_click and not self._api_check_in_flight:
            if previous_mode != (self.cfg.get("mode") or "Batch").strip():
                self._set_status("Settings updated", SUCCESS)
            else:
                self._set_status("Ready", MUTED)

        self.page.update()

    def _show_settings_error(self, message: str) -> None:
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(message, color=TEXT),
            bgcolor=CARD_SOFT,
            open=True,
        )
        self.page.update()

    def _get_config_mtime(self) -> float:
        try:
            return float(config.CONFIG_FILE.stat().st_mtime)
        except Exception:
            return 0.0

    def _watch_config_changes(self) -> None:
        while True:
            time.sleep(0.7)
            latest_mtime = self._get_config_mtime()
            if latest_mtime <= 0.0:
                continue
            if latest_mtime == self._config_mtime:
                continue
            self._config_mtime = latest_mtime
            try:
                self.page.run_thread(self._sync_settings_from_disk)
            except RuntimeError:
                return

    def _get_or_create_device_id(self) -> str:
        device_id = (self.cfg.get("device_id") or "").strip()
        if device_id:
            return device_id
        device_id = uuid.uuid4().hex
        latest = config.load()
        latest["device_id"] = device_id
        config.save(latest)
        self.cfg = latest
        return device_id

    def _load_runtime_config(self, force: bool = False) -> website_client.RuntimeConfig | None:
        now = time.time()
        if (
            not force
            and self._runtime_config is not None
            and (now - self._runtime_config_last_fetch) < self._runtime_config_ttl_sec
        ):
            return self._runtime_config

        device_id = self._get_or_create_device_id()
        runtime_channel = (self.cfg.get("runtime_channel") or "stable").strip().lower()
        try:
            runtime_cfg = website_client.get_runtime_config(
                channel=runtime_channel,
                platform=app_info.APP_PLATFORM,
                device_id=device_id,
            )
        except Exception:
            return self._runtime_config

        self._runtime_config = runtime_cfg
        self._runtime_config_last_fetch = now
        return runtime_cfg

    def _warmup_startup(self) -> None:
        try:
            rec_module.list_input_devices()
        except Exception:
            pass
        self._load_runtime_config(force=True)
        try:
            self._get_runtime_api_key()
            self.page.run_thread(self._set_health_chip, "Connected", True)
        except Exception:
            pass

    def _current_feature_flag(self, key: str, fallback: bool) -> bool:
        runtime_cfg = self._runtime_config
        if runtime_cfg and runtime_cfg.in_rollout:
            value = runtime_cfg.feature_flags.get(key)
            if isinstance(value, bool):
                return value
        return fallback

    def _effective_live_retry_limit(self) -> int:
        base = int(self.cfg.get("live_retry_limit", 2))
        runtime_cfg = self._runtime_config
        if runtime_cfg and runtime_cfg.in_rollout:
            return max(0, min(10, int(runtime_cfg.live_retry_limit)))
        return max(0, min(10, base))

    def _silence_trim_enabled(self) -> bool:
        local_value = bool(self.cfg.get("silence_trim_enabled", True))
        runtime_cfg = self._runtime_config
        if runtime_cfg and runtime_cfg.in_rollout:
            return bool(runtime_cfg.silence_trim_enabled)
        return local_value

    def _log_reliability_event(
        self,
        event_type: str,
        latency_ms: int | None = None,
        error_code: str = "",
        detail: str = "",
    ) -> None:
        if not self._session_id:
            return
        event = reliability.build_event(
            session_id=self._session_id,
            mode=self.cfg.get("mode", "Live"),
            source=self._active_source,
            event_type=event_type,
            latency_ms=latency_ms,
            error_code=error_code,
            detail=detail,
        )
        send_remote = bool(self.cfg.get("send_reliability_events", False)) and self._current_feature_flag(
            "reliabilityEvents", False
        )
        reliability.log_event_async(event, send_remote=send_remote)

    def _process_transcript(self, raw_text: str) -> dictation_features.ProcessedTranscript:
        return dictation_features.process_transcript(
            raw_text=raw_text,
            profile=self.cfg.get("dictation_profile", "notes"),
            replacements=self.cfg.get("text_replacements", {}),
            personal_dictionary=self.cfg.get("personal_dictionary", []),
            voice_commands_enabled=bool(self.cfg.get("voice_commands_enabled", True))
            and self._current_feature_flag("voiceCommands", True),
            command_prefix=self.cfg.get("command_prefix", "command"),
        )

    def _execute_voice_actions(self, actions: tuple[str, ...]) -> None:
        for action in actions:
            if action == "undo_last":
                output_handler.send_shortcut("ctrl", "z")

    def _handle_typing_failure(self, raw_text: str) -> None:
        self._last_raw_transcript = raw_text
        self._typing_failed_pending = True
        self._log_reliability_event("typing_failed", error_code="typing_failed")
        output_handler.copy_to_clipboard(raw_text)
        self._set_aux_chip("Typing delayed", True)
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Typing failed. Raw transcript copied.", color=TEXT),
            bgcolor=CARD_SOFT,
            open=True,
        )
        self.page.update()
        if not self._is_recording and not self._waiting_click:
            self._set_action("Copy raw", CARD_SOFT, ACCENT, TEXT, self._copy_raw_transcript)

    def _copy_raw_transcript(self, _event) -> None:
        if not self._last_raw_transcript:
            return
        output_handler.copy_to_clipboard(self._last_raw_transcript)
        self._typing_failed_pending = False
        self._set_aux_chip("", False)
        self._reset_to_ready()

    def _close_app(self, _event) -> None:
        self.page.run_task(self._close_app_async)

    async def _close_app_async(self) -> None:
        self._stop_any_active_work()
        await self.page.window.close()

    def _on_action_click(self, _event) -> None:
        self._register_widget_interaction()
        if self._is_recording:
            self._stop_recording()
            return
        if self._waiting_click:
            self._cancel_target_selection()
            return
        if self._api_check_in_flight:
            return
        self._ask_for_target_click()

    def _ask_for_target_click(self) -> None:
        self.cfg = config.load()
        self._session_id = reliability.new_session_id()
        self._last_raw_transcript = ""
        self._typing_failed_pending = False
        self._api_check_in_flight = True
        self._set_status("Checking API", MUTED)
        self._set_action("Checking...", CARD_SOFT, BORDER, MUTED, lambda _e: None)
        threading.Thread(target=self._check_api_and_prepare_target, daemon=True).start()

    def _check_api_and_prepare_target(self) -> None:
        try:
            self._load_runtime_config(force=False)
            self._get_runtime_api_key()
        except website_client.WebsiteAPIError as exc:
            self.page.run_thread(self._on_api_check_failed, str(exc))
            return
        except Exception as exc:
            self.page.run_thread(self._on_api_check_failed, f"Unable to initialize API: {exc}")
            return
        self.page.run_thread(self._begin_target_selection)

    def _begin_target_selection(self) -> None:
        self._api_check_in_flight = False
        self._set_health_chip("Connected", True)
        self._log_reliability_event("api_check_ok")
        self._waiting_click = True
        self._set_status("Click a target", MUTED)
        self._set_action("Cancel", CARD_SOFT, DANGER, DANGER, self._cancel_target_selection)
        self.page.window.opacity = 0.85
        self.page.update()
        threading.Thread(target=self._listen_for_target_click, daemon=True).start()

    def _on_api_check_failed(self, message: str) -> None:
        self._api_check_in_flight = False
        self._runtime_api_key = ""
        self._set_health_chip("", False)
        self._log_reliability_event("api_check_failed", error_code=reliability.normalize_error_code(message), detail=message)
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(message, color=TEXT),
            bgcolor=CARD_SOFT,
            open=True,
        )
        self.page.update()
        self._reset_to_ready()

    def _get_runtime_api_key(self) -> str:
        now = time.time()
        with self._api_lock:
            if self._runtime_api_key and (now - self._api_last_check_at) < self._api_cache_ttl_sec:
                return self._runtime_api_key

        bootstrap = website_client.get_desktop_bootstrap()
        key = bootstrap.api_key.strip()
        if not key:
            raise website_client.WebsiteAPIError("The website did not return an API key.")

        with self._api_lock:
            self._runtime_api_key = key
            self._api_last_check_at = time.time()
        return key

    def _listen_for_target_click(self) -> None:
        try:
            from pynput import mouse as pmouse
        except ImportError:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("pynput is required for click-to-type.", color=TEXT),
                bgcolor=CARD_SOFT,
                open=True,
            )
            self.page.update()
            self.page.window.opacity = 1
            self._reset_to_ready()
            return

        def on_click(x, y, button, pressed):
            if not pressed or button != pmouse.Button.left:
                return

            wx = int(self.page.window.left or 0)
            wy = int(self.page.window.top or 0)
            ww = int(self.page.window.width or 320)
            wh = int(self.page.window.height or 170)
            if wx <= x <= wx + ww and wy <= y <= wy + wh:
                return

            if not self._waiting_click:
                return True
            try:
                listener.stop()
            except Exception:
                pass
            self.page.run_thread(self._on_target_selected)

        listener = pmouse.Listener(on_click=on_click)
        self._click_listener = listener
        listener.start()
        listener.join()

    def _cancel_target_selection(self) -> None:
        self._waiting_click = False
        self.page.window.opacity = 1
        self._stop_target_listener()
        self._reset_to_ready()

    def _on_target_selected(self) -> None:
        if not self._waiting_click:
            return
        self._waiting_click = False
        self.page.window.opacity = 1
        self._stop_target_listener()
        self._set_status("Target selected", SUCCESS)
        self._log_reliability_event("target_selected")
        self._start_recording()

    def _stop_target_listener(self) -> None:
        if self._click_listener:
            try:
                self._click_listener.stop()
            except Exception:
                pass
            self._click_listener = None

    def _start_recording(self) -> None:
        self.cfg = config.load()
        self._load_runtime_config(force=False)
        self._stopping = False
        self._set_aux_chip("", False)
        self._set_action("Preparing...", CARD_SOFT, BORDER, MUTED, lambda _e: None)

        if self._is_live_mode():
            self._start_live_mode()
            return

        self._start_batch_mode()

    def _start_batch_mode(self) -> None:
        preferred_source = self.cfg.get("source", "mic")
        sample_rate = int(self.cfg.get("sample_rate", 16000))
        runtime_cfg = self._runtime_config or self._load_runtime_config(force=False)
        endpointing_mode = self.cfg.get("reliability_mode", "balanced")
        if runtime_cfg and runtime_cfg.in_rollout:
            endpointing_mode = runtime_cfg.endpointing_mode or endpointing_mode
        candidates = [preferred_source]
        if bool(self.cfg.get("auto_fallback_enabled", True)) and self._current_feature_flag("autoFallback", True):
            alt = "system" if preferred_source == "mic" else "mic"
            if alt not in candidates:
                candidates.append(alt)

        last_exc: Optional[Exception] = None
        for idx, source in enumerate(candidates):
            self._recorder = rec_module.Recorder(
                source=source,
                sample_rate=sample_rate,
                silence_trim_enabled=self._silence_trim_enabled(),
                reliability_mode=endpointing_mode,
            )
            try:
                self._recorder.start()
                self._active_source = source
                if idx > 0:
                    self._set_aux_chip("Fallback active", True)
                    self._log_reliability_event("fallback_used", detail=f"batch:{source}")
                break
            except Exception as exc:
                last_exc = exc
                self._recorder = None

        if not self._recorder:
            message = str(last_exc or "Unable to start recording source.")
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(message, color=TEXT),
                bgcolor=CARD_SOFT,
                open=True,
            )
            self.page.update()
            self._log_reliability_event(
                "recording_failed",
                error_code=reliability.normalize_error_code(message),
                detail=message,
            )
            self._reset_to_ready()
            return

        self._is_recording = True
        self._log_reliability_event("recording_started")
        self._set_status("Recording", TEXT)
        self._set_action("Stop recording", CARD_SOFT, DANGER, DANGER, self._on_action_click)

    def _start_live_mode(self) -> None:
        api_key = self._runtime_api_key.strip()
        if not api_key:
            self._on_transcription_error("Runtime API key is missing. Press Start to re-check website API.")
            return
        preferred_source = self.cfg.get("source", "mic")
        self._live_text_buffer.clear()
        self._live_retry_count = 0
        self._last_live_typed_char = ""
        self._live_source_candidates = [preferred_source]
        if bool(self.cfg.get("auto_fallback_enabled", True)) and self._current_feature_flag("autoFallback", True):
            alt = "system" if preferred_source == "mic" else "mic"
            if alt not in self._live_source_candidates:
                self._live_source_candidates.append(alt)
        self._live_source_index = 0
        self._is_recording = True
        self._start_live_stream_with_current_source()
        self._log_reliability_event("live_started")
        self._set_status("Listening", SUCCESS)
        self._set_health_chip("Connected", True)
        self._set_action("Stop recording", CARD_SOFT, DANGER, DANGER, self._on_action_click)

    def _start_live_stream_with_current_source(self) -> None:
        if self._stopping or not self._is_recording:
            return
        api_key = self._runtime_api_key.strip()
        sample_rate = int(self.cfg.get("sample_rate", 16000))
        source = self._live_source_candidates[self._live_source_index]
        self._active_source = source
        self._set_aux_chip("Fallback active", self._live_source_index > 0)
        if self._live_source_index > 0:
            self._log_reliability_event("fallback_used", detail=f"live:{source}")

        try:
            self._rt_transcriber = rt_module.RealtimeTranscriber(
                api_key=api_key,
                sample_rate=sample_rate,
                source=source,
                on_delta=lambda t: self.page.run_thread(self._type_live_delta, t),
                on_status=lambda s: self.page.run_thread(self._set_status, self._normalize_live_status(s), MUTED),
                on_done=lambda: self.page.run_thread(self._on_realtime_done),
                on_error=lambda e: self.page.run_thread(self._on_live_stream_error, e),
            )
            self._rt_transcriber.start()
        except Exception as exc:
            self._on_live_stream_error(str(exc))

    def _on_live_stream_error(self, err: str) -> None:
        if self._stopping or not self._is_recording:
            self._on_transcription_error(err)
            return

        self._log_reliability_event(
            "live_error",
            error_code=reliability.normalize_error_code(err),
            detail=err,
        )
        retry_limit = self._effective_live_retry_limit()
        can_retry = self._live_retry_count < retry_limit
        if can_retry:
            self._live_retry_count += 1
            if self._live_source_index < len(self._live_source_candidates) - 1:
                self._live_source_index += 1
            self._set_health_chip("Recovering", True)
            self._set_status(f"Recovering ({self._live_retry_count}/{retry_limit})", MUTED)
            if self._rt_transcriber:
                try:
                    self._rt_transcriber.stop()
                except Exception:
                    pass
                self._rt_transcriber = None
            delay = min(2.5, 0.7 * self._live_retry_count)
            timer = threading.Timer(delay, lambda: self.page.run_thread(self._start_live_stream_with_current_source))
            timer.daemon = True
            timer.start()
            return

        self._on_transcription_error(err)

    def _normalize_live_status(self, status: str) -> str:
        normalized = status.lower().strip()
        if "connecting" in normalized:
            self._set_health_chip("Recovering", True)
            return "Connecting"
        if "live" in normalized or "speak now" in normalized:
            self._set_health_chip("Connected", True)
            return "Listening"
        return status

    def _stop_recording(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._log_reliability_event("recording_stopping")
        self._set_action("Stopping...", CARD_SOFT, BORDER, MUTED, lambda _e: None)
        self._set_status("Stopping...", MUTED)

        if self._is_live_mode():
            self._stop_realtime()
            return

        if not self._recorder:
            self._reset_to_ready()
            return

        self._is_recording = False
        threading.Thread(target=self._transcribe_batch_worker, daemon=True).start()

    def _transcribe_batch_worker(self) -> None:
        wav_path: Optional[str] = None
        started_at = time.perf_counter()
        try:
            wav_path = self._recorder.stop()
            prompt = None
            dictionary_terms = self.cfg.get("personal_dictionary", [])
            if dictionary_terms:
                prompt = "Prefer these terms exactly: " + ", ".join(dictionary_terms[:80])
            client = tr_module.TranscriptionClient(
                api_key=self._runtime_api_key,
                model=self._selected_batch_model(),
            )
            raw_text = client.transcribe(
                wav_path,
                language=self.cfg.get("language") or None,
                prompt=prompt,
            )
            processed = self._process_transcript(raw_text)
            self._execute_voice_actions(processed.actions)

            typed_ok = True
            if processed.text.strip():
                typed_ok = output_handler.type_text(processed.text)
                if not typed_ok:
                    self.page.run_thread(self._handle_typing_failure, raw_text)

            latency_ms = int((time.perf_counter() - started_at) * 1000)
            self._log_reliability_event("batch_transcribed", latency_ms=latency_ms)
            self.page.run_thread(self._on_transcription_done, processed.text if processed.text else raw_text)
        except Exception as exc:
            self._log_reliability_event(
                "batch_error",
                error_code=reliability.normalize_error_code(str(exc)),
                detail=str(exc),
            )
            self.page.run_thread(self._on_transcription_error, str(exc))
        finally:
            if wav_path:
                tr_module.cleanup_temp(wav_path)

    def _on_transcription_done(self, text: str) -> None:
        self._stopping = False
        if not text.strip():
            self._set_status("No speech detected", MUTED)
        else:
            self._set_status(f"Typed {len(text)} chars", SUCCESS)
        self._reset_to_ready()
        if self._typing_failed_pending and self._last_raw_transcript:
            self._set_aux_chip("Typing delayed", True)
            self._set_action("Copy raw", CARD_SOFT, ACCENT, TEXT, self._copy_raw_transcript)

    def _on_transcription_error(self, err: str) -> None:
        self._stopping = False
        self._set_health_chip("", False)
        self._log_reliability_event(
            "transcription_error",
            error_code=reliability.normalize_error_code(err),
            detail=err,
        )
        self.page.snack_bar = ft.SnackBar(content=ft.Text(err, color=TEXT), bgcolor=CARD_SOFT, open=True)
        self.page.update()
        self._reset_to_ready()

    def _start_status_animation(self, base: str) -> None:
        self.status_anim_base = base
        if self.status_anim_running:
            return
        self.status_anim_running = True

        def _animate() -> None:
            while self.status_anim_running:
                dots = "." * (self.status_anim_step % 4)
                self.status_text.value = f"{self.status_anim_base}{dots}"
                self.page.update()
                self.status_anim_step += 1
                time.sleep(0.25)

        self.status_anim_thread = threading.Thread(target=_animate, daemon=True)
        self.status_anim_thread.start()

    def _stop_status_animation(self) -> None:
        self.status_anim_running = False
        self.status_anim_step = 0
        self.status_text.value = self.status_anim_base
        self.page.update()

    def _set_status(self, text: str, color: str = MUTED, animate: bool = False) -> None:
        self._stop_status_animation()
        self.status_text.value = text
        self.status_text.color = color
        if animate:
            self._start_status_animation(text)
        self.page.update()

    def _reset_to_ready(self) -> None:
        self._stopping = False
        self._api_check_in_flight = False
        self._is_recording = False
        self._stop_status_animation()
        self._live_text_buffer.clear()
        self._set_aux_chip("", False)
        self._set_status("Ready", MUTED)
        self._set_action("Start", ACCENT, ACCENT, TEXT, self._on_action_click)

    def _stop_realtime(self) -> None:
        self._is_recording = False
        self._set_health_chip("", False)
        if self._live_paste_job:
            try:
                self._live_paste_job.cancel()
            except Exception:
                pass
            self._live_paste_job = None
        self._flush_live_buffer()
        if self._rt_transcriber:
            self._rt_transcriber.stop()
            self._rt_transcriber = None
        self._finalize_stop()

    def _finalize_stop(self) -> None:
        self._stopping = False
        self._last_live_typed_char = ""
        self._set_status("Completed", SUCCESS)
        self._reset_to_ready()
        if self._typing_failed_pending and self._last_raw_transcript:
            self._set_aux_chip("Typing delayed", True)
            self._set_action("Copy raw", CARD_SOFT, ACCENT, TEXT, self._copy_raw_transcript)

    def _type_live_delta(self, delta: str) -> None:
        self._live_text_buffer.append(delta)
        if self._live_paste_job:
            try:
                self._live_paste_job.cancel()
            except Exception:
                pass
        delay = 0.05 if len(delta) > 10 else 0.25
        timer = threading.Timer(delay, self._flush_live_buffer)
        self._live_paste_job = timer
        timer.daemon = True
        timer.start()

    def _prepare_live_chunk_for_typing(self, text: str) -> str:
        if not text:
            return text

        previous = self._last_live_typed_char
        first = text[0]
        if previous and (not previous.isspace()) and (not first.isspace()):
            if (previous.isalnum() and first.isalnum()) or (previous in ",.;:!?" and first.isalnum()):
                return " " + text
        return text

    def _flush_live_buffer(self) -> None:
        if not self._live_text_buffer:
            return
        raw_chunk = "".join(self._live_text_buffer)
        self._live_text_buffer.clear()
        processed = self._process_transcript(raw_chunk)
        self._execute_voice_actions(processed.actions)

        def _type() -> None:
            payload = processed.text
            if not payload.strip():
                return

            with self._live_type_lock:
                payload = self._prepare_live_chunk_for_typing(payload)
                ok = output_handler.type_text(payload, interval=0.01)
                if ok and payload:
                    self._last_live_typed_char = payload[-1]

            if not ok:
                self.page.run_thread(self._handle_typing_failure, raw_chunk)
                self._log_reliability_event("typing_failed", error_code="typing_failed", detail="live_chunk")

        threading.Thread(target=_type, daemon=True).start()
        self._live_paste_job = None

    def _on_realtime_done(self) -> None:
        self._is_recording = False
        self._log_reliability_event("live_completed")
        self._flush_live_buffer()
        if self._rt_transcriber:
            self._rt_transcriber = None
        self._finalize_stop()

    def _stop_any_active_work(self) -> None:
        self.status_anim_running = False
        self._wave_anim_running = False
        self._cancel_auto_minimize_timer()
        self._stop_target_listener()
        if self._rt_transcriber:
            try:
                self._rt_transcriber.stop()
            except Exception:
                pass
            self._rt_transcriber = None
        if self._recorder:
            self._recorder = None
        if self._live_paste_job:
            try:
                self._live_paste_job.cancel()
            except Exception:
                pass
            self._live_paste_job = None


def main(page: ft.Page) -> None:
    SonusApp(page)


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP)
