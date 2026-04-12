from __future__ import annotations

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
self.colors['accent'] = "#0A84FF"         # Professional Sapphire Blue
ACCENT_HOVER = "#0070E0"
self.colors['bg'] = "#0F0F0F"            # Solid Dark Background
self.colors['card'] = "#1A1A1A"          # Solid Surface
self.colors['card_soft'] = "#242424"
self.colors['card_active'] = "#2C2C2C"
self.colors['border'] = "#333333"
self.colors['text'] = "#FFFFFF"
self.colors['muted'] = "#999999"
self.colors['muted_soft'] = "#666666"
self.colors['danger'] = "#FF453A"
DANGER_HOVER = "#E03B32"
self.colors['success'] = "#32D74B"
BATCH_MODEL = "voxtral-mini-2507"
LIVE_MODELS = {"voxtral-mini-2507", "voxtral-small-2507"}


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

        self.status_anim_running = False
        self.status_anim_step = 0
        self.status_anim_base = "Ready"
        self.status_anim_thread: Optional[threading.Thread] = None
        self._settings_process: Optional[subprocess.Popen] = None
        self._settings_opening = False
        self._config_mtime = self._get_config_mtime()

        self._setup_page()
        self._build_ui()
        threading.Thread(target=self._warmup_startup, daemon=True).start()
        threading.Thread(target=self._watch_config_changes, daemon=True).start()

    def _setup_page(self) -> None:
        self.page.title = APP_TITLE
        self.page.bgcolor = self.colors['bg']
        self.page.padding = 0
        self.page.spacing = 0
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.window.bgcolor = self.colors['bg']
        self.page.window.width = 280
        self.page.window.height = 160
        self.page.window.min_width = 268
        self.page.window.max_width = 268
        self.page.window.min_height = 150
        self.page.window.max_height = 150
        self.page.window.resizable = False
        self.page.window.shadow = True
        self.page.window.title_bar_hidden = True
        self.page.window.title_bar_buttons_hidden = True
        self.page.window.frameless = True
        self.page.window.always_on_top = bool(self.cfg.get("always_on_top", True))
        self.page.window.movable = True

    def _build_ui(self) -> None:
        # custom title bar
        self.title_text = ft.Text("SONUS", size=13, weight=ft.FontWeight.W_900, color=self.colors['text'])
        self.subtitle_text = ft.Text("PRIVATE VOICE AI", size=7, color=self.colors['muted'], weight=ft.FontWeight.BOLD)
        
        self.settings_btn = ft.IconButton(
            icon=ft.Icons.SETTINGS_ROUNDED,
            icon_color=self.colors['muted'],
            icon_size=16,
            tooltip="Settings",
            on_click=self._open_settings,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=10),
                overlay_color=ft.Colors.with_opacity(0.1, self.colors['text']),
            ),
        )
        self.close_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_color=self.colors['danger'],
            icon_size=16,
            tooltip="Close",
            on_click=self._close_app,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=10),
                overlay_color=ft.Colors.with_opacity(0.1, self.colors['danger']),
            ),
        )

        title_bar = ft.Container(
            padding=ft.padding.only(left=14, right=8, top=4, bottom=4),
            bgcolor=self.colors['card'],
            border=ft.border.only(bottom=ft.BorderSide(1, self.colors['border'])),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        controls=[
                            ft.Container(width=8, height=8, bgcolor=self.colors['accent'], border_radius=4),
                            ft.Column(spacing=-2, tight=True, controls=[self.title_text, self.subtitle_text])
                        ]
                    ),
                    ft.Row(
                        spacing=0,
                        controls=[self.settings_btn, self.close_btn],
                    ),
                ],
            ),
        )

        self.status_text = ft.Text("SYSTEM READY", size=10, weight=ft.FontWeight.W_800, color=self.colors['muted'])
        self.mode_badge = ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
            border_radius=6,
            bgcolor=self.colors['card_active'],
            border=ft.Border.all(1, self.colors['border']),
            content=ft.Text(
                self.cfg.get("mode", "Live").upper(),
                size=9,
                weight=ft.FontWeight.W_900,
                color=self.colors['accent'],
            ),
        )

        self.action_label = ft.Text(
            "START ENGINE",
            size=12,
            weight=ft.FontWeight.W_900,
            color=self.colors['text'],
        )
        self.action_button = ft.Container(
            height=40,
            padding=0,
            border_radius=10,
            bgcolor=self.colors['accent'],
            alignment=ft.Alignment(0, 0),
            animate=150,
            border=ft.Border.all(1, ft.Colors.with_opacity(0.1, self.colors['text'])),
            content=self.action_label,
            on_click=self._on_action_click,
            shadow=ft.BoxShadow(
                spread_radius=1,
                blur_radius=10,
                color=ft.Colors.with_opacity(0.15, self.colors['accent']),
            )
        )

        self.aux_chip_text = ft.Text("", size=8, color=self.colors['text'], weight=ft.FontWeight.W_700)
        self.aux_chip = ft.Container(
            visible=False,
            padding=ft.Padding(10, 4, 10, 4),
            border_radius=6,
            bgcolor="#7C2D12",
            border=ft.Border.all(1, self.colors['danger']),
            content=self.aux_chip_text,
        )

        body = ft.Container(
            expand=True,
            padding=14,
            bgcolor=self.colors['bg'],
            content=ft.Column(
                spacing=14,
                tight=True,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self.status_text,
                            self.mode_badge,
                        ],
                    ),
                    self.aux_chip,
                    self.action_button,
                ],
            ),
        )

        main_col = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                title_bar,
                body,
            ]
        )

        root = ft.WindowDragArea(maximizable=False, content=main_col)

        self.page.add(root)
        self.page.update()

    def _set_mode_badge(self) -> None:
        mode_value = (self.cfg.get("mode") or "Batch").strip().lower()
        mode_label = "Live" if mode_value == "live" else "Batch"
        self.mode_badge.content = ft.Text(
            mode_label.upper(),
            size=9,
            weight=ft.FontWeight.W_900,
            color=self.colors['accent'],
        )
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
        self.action_button.bgcolor = fg
        self.action_button.border = ft.Border.all(1, border)
        self.action_button.on_click = on_click
        self.page.update()

    def _set_health_chip(self, text: str = "", active: bool = False) -> None:
        # Health chip is intentionally hidden in the compact modern UI.
        return

    def _set_aux_chip(self, text: str = "", active: bool = False) -> None:
        self.aux_chip.visible = active
        if active:
            self.aux_chip_text.value = text
        self.page.update()

    def _open_settings(self, _event) -> None:
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
        self.page.run_thread(self._sync_settings_from_disk)

    def _sync_settings_from_disk(self) -> None:
        previous_mode = (self.cfg.get("mode") or "Batch").strip()
        previous_channel = (self.cfg.get("runtime_channel") or "stable").strip().lower()

        self.cfg = config.load()
        self._config_mtime = self._get_config_mtime()
        self.page.window.always_on_top = bool(self.cfg.get("always_on_top", True))
        self._set_mode_badge()

        current_channel = (self.cfg.get("runtime_channel") or "stable").strip().lower()
        if current_channel != previous_channel:
            self._runtime_config = None
            self._runtime_config_last_fetch = 0.0

        if not self._is_recording and not self._waiting_click and not self._api_check_in_flight:
            if previous_mode != (self.cfg.get("mode") or "Batch").strip():
                self._set_status("Settings updated", self.colors['success'])
            else:
                self._set_status("Ready", self.colors['muted'])

        self.page.update()

    def _show_settings_error(self, message: str) -> None:
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(message, color=self.colors['text']),
            bgcolor=self.colors['card_soft'],
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
            self.page.run_thread(self._sync_settings_from_disk)

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
                modifier = "command" if sys.platform == "darwin" else "ctrl"
                output_handler.send_shortcut(modifier, "z")

    def _handle_typing_failure(self, raw_text: str) -> None:
        self._last_raw_transcript = raw_text
        self._typing_failed_pending = True
        self._log_reliability_event("typing_failed", error_code="typing_failed")
        output_handler.copy_to_clipboard(raw_text)
        self._set_aux_chip("Typing delayed", True)
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Typing failed. Raw transcript copied.", color=self.colors['text']),
            bgcolor=self.colors['card_soft'],
            open=True,
        )
        self.page.update()
        if not self._is_recording and not self._waiting_click:
            self._set_action("Copy raw", self.colors['card_soft'], self.colors['accent'], self.colors['text'], self._copy_raw_transcript)

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
        self._set_status("Checking API", self.colors['muted'])
        self._set_action("Checking...", self.colors['card_soft'], self.colors['border'], self.colors['muted'], lambda _e: None)
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
        self._set_status("Click a target", self.colors['muted'])
        self._set_action("Cancel", self.colors['card_soft'], self.colors['danger'], self.colors['danger'], self._cancel_target_selection)
        self.page.window.opacity = 0.85
        self.page.update()
        threading.Thread(target=self._listen_for_target_click, daemon=True).start()

    def _on_api_check_failed(self, message: str) -> None:
        self._api_check_in_flight = False
        self._runtime_api_key = ""
        self._set_health_chip("", False)
        self._log_reliability_event("api_check_failed", error_code=reliability.normalize_error_code(message), detail=message)
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(message, color=self.colors['text']),
            bgcolor=self.colors['card_soft'],
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
                content=ft.Text("pynput is required for click-to-type.", color=self.colors['text']),
                bgcolor=self.colors['card_soft'],
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
        self._set_status("Target selected", self.colors['success'])
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
        self._set_action("Preparing...", self.colors['card_soft'], self.colors['border'], self.colors['muted'], lambda _e: None)

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
                content=ft.Text(message, color=self.colors['text']),
                bgcolor=self.colors['card_soft'],
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
        self._set_status("Recording", self.colors['text'])
        self._set_action("Stop recording", self.colors['card_soft'], self.colors['danger'], self.colors['danger'], self._on_action_click)

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
        self._set_status("Listening", self.colors['success'])
        self._set_health_chip("Connected", True)
        self._set_action("Stop recording", self.colors['card_soft'], self.colors['danger'], self.colors['danger'], self._on_action_click)

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
                on_status=lambda s: self.page.run_thread(self._set_status, self._normalize_live_status(s), self.colors['muted']),
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
            self._set_status(f"Recovering ({self._live_retry_count}/{retry_limit})", self.colors['muted'])
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
        self._set_action("Stopping...", self.colors['card_soft'], self.colors['border'], self.colors['muted'], lambda _e: None)
        self._set_status("Stopping...", self.colors['muted'])

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
            self._set_status("No speech detected", self.colors['muted'])
        else:
            self._set_status(f"Typed {len(text)} chars", self.colors['success'])
        self._reset_to_ready()
        if self._typing_failed_pending and self._last_raw_transcript:
            self._set_aux_chip("Typing delayed", True)
            self._set_action("Copy raw", self.colors['card_soft'], self.colors['accent'], self.colors['text'], self._copy_raw_transcript)

    def _on_transcription_error(self, err: str) -> None:
        self._stopping = False
        self._set_health_chip("", False)
        self._log_reliability_event(
            "transcription_error",
            error_code=reliability.normalize_error_code(err),
            detail=err,
        )
        self.page.snack_bar = ft.SnackBar(content=ft.Text(err, color=self.colors['text']), bgcolor=self.colors['card_soft'], open=True)
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

    def _set_status(self, text: str, color: str = self.colors['muted'], animate: bool = False) -> None:
        self._stop_status_animation()
        self.status_text.value = text.upper()
        self.status_text.color = color
        if animate:
            self._start_status_animation(text.upper())
        self.page.update()

    def _reset_to_ready(self) -> None:
        self._stopping = False
        self._api_check_in_flight = False
        self._is_recording = False
        self._stop_status_animation()
        self._live_text_buffer.clear()
        self._set_aux_chip("", False)
        self._set_status("Ready", self.colors['muted'])
        self._set_action("Start", self.colors['accent'], self.colors['accent'], self.colors['text'], self._on_action_click)

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
        self._set_status("Completed", self.colors['success'])
        self._reset_to_ready()
        if self._typing_failed_pending and self._last_raw_transcript:
            self._set_aux_chip("Typing delayed", True)
            self._set_action("Copy raw", self.colors['card_soft'], self.colors['accent'], self.colors['text'], self._copy_raw_transcript)

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
