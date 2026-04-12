"""
Voxify - Floating Speech-to-Text Desktop Tool
=============================================
A compact, always-on-top CustomTkinter window that:
    - records mic or system audio
    - transcribes speech in batch or live mode
    - delivers text by auto-typing or clipboard copy
"""

import atexit
import os
import sys
import threading
import time
import ctypes
import importlib.util
import subprocess
import tempfile
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from typing import Optional

try:
    import customtkinter as ctk
except ImportError as exc:
    raise RuntimeError("customtkinter is required. Install dependencies with pip install -r requirements.txt") from exc

import config
import output_handler
import recorder as rec_module
import realtime_transcriber as rt_module
import transcriber as tr_module

APP_NAME = "Voxify"
WIN_W = 286
WIN_H = 120
CORNER_R = 18


class _SingleInstanceLock:
    def __init__(self, lock_name: str) -> None:
        self._path = Path(tempfile.gettempdir()) / lock_name
        self._file = None
        self._locked = False

    def acquire(self) -> bool:
        try:
            self._file = open(self._path, "a+", encoding="utf-8")
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                self._file.write("0")
                self._file.flush()
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            self._file.seek(0)
            self._file.truncate()
            self._file.write(str(os.getpid()))
            self._file.flush()
            self._locked = True
            return True
        except Exception:
            self.release()
            return False

    def release(self) -> None:
        if not self._file:
            return
        try:
            if self._locked:
                if os.name == "nt":
                    import msvcrt

                    self._file.seek(0)
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._file.close()
        except Exception:
            pass
        self._file = None
        self._locked = False


_MAIN_INSTANCE_LOCK = _SingleInstanceLock("voxify-main.lock")

COLORS = {
    "bg_main": "#06111F",
    "bg_card": "#0B1A30",
    "bg_hover": "#123152",
    "border": "#24466F",
    "border_soft": "#17314E",
    "text_main": "#F5F9FF",
    "text_dim": "#9BB2CF",
    "accent": "#4D9CFF",
    "accent_hover": "#3287F0",
    "accent_soft": "#1A3F73",
    "record": "#FF6B7A",
    "record_hover": "#E85A6A",
}

if sys.platform.startswith("win"):
    _WINDOWS_ROUNDED_CORNERS_AVAILABLE = True
    _DWMWA_WINDOW_CORNER_PREFERENCE = 33
    _DWMWCP_ROUND = 2
    _dwmapi = ctypes.windll.dwmapi
else:
    _WINDOWS_ROUNDED_CORNERS_AVAILABLE = False
    _DWMWA_WINDOW_CORNER_PREFERENCE = 33
    _DWMWCP_ROUND = 2
    _dwmapi = None


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent: ctk.CTk) -> None:
        super().__init__(parent)
        self.title("Settings")
        self.geometry("300x420")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_main"])
        self.grab_set()
        self.focus()
        self.attributes("-topmost", True)

        cfg = config.load()

        main_frame = ctk.CTkFrame(
            self,
            corner_radius=18,
            fg_color=COLORS["bg_card"],
            border_width=1,
            border_color=COLORS["border_soft"],
        )
        main_frame.pack(fill="both", expand=True, padx=12, pady=12)

        ctk.CTkLabel(
            main_frame,
            text="Voxify Settings",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=COLORS["text_main"],
        ).pack(anchor="w", pady=(0, 12))

        ctk.CTkLabel(
            main_frame,
            text="Private desktop speech control",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_dim"],
        ).pack(anchor="w", pady=(0, 10))

        scroll = ctk.CTkScrollableFrame(
            main_frame,
            fg_color="transparent",
            label_text="",
            height=250,
        )
        scroll.pack(fill="both", expand=True, pady=(0, 10))

        self._section_label(scroll, "API KEY")
        self._key_var = tk.StringVar(value=cfg.get("api_key", ""))
        self._key_entry = ctk.CTkEntry(
            scroll,
            textvariable=self._key_var,
            show="•",
            height=32,
            corner_radius=9,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border"],
            border_width=1,
            placeholder_text="Enter your API key...",
        )
        self._key_entry.pack(fill="x", pady=(0, 6))

        self._show_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            scroll,
            text="Show key",
            variable=self._show_var,
            command=self._toggle_show,
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_dim"],
            checkbox_width=18,
            checkbox_height=18,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
        ).pack(anchor="w", pady=(0, 12))

        self._section_label(scroll, "MODEL")
        self._model_map = {
            "Mini": "voxtral-mini-2507",
            "Small": "voxtral-small-2507",
        }
        self._model_reverse = {model_id: label for label, model_id in self._model_map.items()}
        saved_model = cfg.get("model", "voxtral-mini-2507")
        self._model_var = tk.StringVar(value=self._model_reverse.get(saved_model, "Mini"))
        ctk.CTkComboBox(
            scroll,
            values=list(self._model_map.keys()),
            variable=self._model_var,
            height=32,
            corner_radius=9,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_hover"],
            dropdown_text_color=COLORS["text_main"],
        ).pack(fill="x", pady=(0, 12))

        self._section_label(scroll, "LANGUAGE")
        self._lang_map = {
            "Auto-detect": "",
            "English": "en",
            "Urdu": "ur",
            "Korean": "ko",
            "Japanese": "ja",
            "Chinese (Mandarin)": "zh",
            "German": "de",
            "French": "fr",
            "Spanish": "es",
            "Arabic": "ar",
            "Hindi": "hi",
            "Custom...": "custom",
        }
        self._code_to_lang = {code: label for label, code in self._lang_map.items() if code != "custom"}
        saved_lang = cfg.get("language", "").lower().strip()
        self._custom_lang_code = ""

        if saved_lang in self._code_to_lang:
            initial_value = self._code_to_lang[saved_lang]
        elif saved_lang:
            initial_value = "Custom..."
            self._custom_lang_code = saved_lang
        else:
            initial_value = "Auto-detect"

        self._lang_var = tk.StringVar(value=initial_value)
        self._lang_combo = ctk.CTkComboBox(
            scroll,
            values=list(self._lang_map.keys()),
            variable=self._lang_var,
            command=self._on_language_change,
            height=32,
            corner_radius=9,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_hover"],
            dropdown_text_color=COLORS["text_main"],
        )
        self._lang_combo.pack(fill="x", pady=(0, 6))

        self._custom_lang_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._custom_lang_var = tk.StringVar(value=self._custom_lang_code)
        self._custom_lang_entry = ctk.CTkEntry(
            self._custom_lang_frame,
            textvariable=self._custom_lang_var,
            height=30,
            corner_radius=8,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border"],
            placeholder_text="e.g. ko, ur, ar",
        )
        self._custom_lang_entry.pack(fill="x")

        if initial_value == "Custom...":
            self._custom_lang_frame.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            scroll,
            text="ISO code (e.g. en, fr, de)",
            font=ctk.CTkFont(size=9),
            text_color=COLORS["text_dim"],
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        self._section_label(scroll, "AUTO-TYPE DELAY")
        self._delay_var = tk.IntVar(value=cfg.get("auto_type_delay", 3))
        delay_container = ctk.CTkFrame(scroll, fg_color="transparent")
        delay_container.pack(fill="x", pady=(0, 12))

        ctk.CTkSlider(
            delay_container,
            from_=1,
            to=10,
            number_of_steps=9,
            variable=self._delay_var,
            height=12,
            fg_color=COLORS["border"],
            progress_color=COLORS["accent"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        ).pack(side="left", fill="x", expand=True, padx=(0, 10))

        ctk.CTkLabel(
            delay_container,
            textvariable=self._delay_var,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["accent"],
            width=20,
        ).pack(side="right")

        self._section_label(scroll, "MODE")
        self._mode_var = tk.StringVar(value=cfg.get("mode", "Live"))
        ctk.CTkComboBox(
            scroll,
            values=["Batch", "Live"],
            variable=self._mode_var,
            height=32,
            corner_radius=9,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_hover"],
            dropdown_text_color=COLORS["text_main"],
        ).pack(fill="x", pady=(0, 12))

        self._section_label(scroll, "SOURCE")
        self._source_var = tk.StringVar(value=cfg.get("source", "mic").capitalize())
        ctk.CTkComboBox(
            scroll,
            values=["Mic", "System"],
            variable=self._source_var,
            height=32,
            corner_radius=9,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_hover"],
            dropdown_text_color=COLORS["text_main"],
        ).pack(fill="x", pady=(0, 12))

        ctk.CTkButton(
            main_frame,
            text="Save Changes",
            command=self._save,
            height=34,
            corner_radius=11,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
        ).pack(fill="x", side="bottom")

    def _section_label(self, parent, text):
        ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["text_dim"],
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

    def _toggle_show(self) -> None:
        self._key_entry.configure(show="" if self._show_var.get() else "•")

    def _on_language_change(self, choice: str) -> None:
        """Show or hide the custom language entry."""
        if choice == "Custom...":
            self._custom_lang_frame.pack(fill="x", pady=(0, 12))
            self._custom_lang_entry.focus()
        else:
            self._custom_lang_frame.pack_forget()

    def _save(self) -> None:
        cfg = config.load()
        cfg["api_key"] = self._key_var.get().strip()
        cfg["model"] = self._model_map.get(self._model_var.get().strip(), "voxtral-mini-2507")

        selected_lang = self._lang_var.get()
        if selected_lang == "Custom...":
            lang_code = self._custom_lang_var.get().strip().lower()
        else:
            lang_code = self._lang_map.get(selected_lang, "").lower()

        cfg["language"] = lang_code
        cfg["auto_type_delay"] = int(self._delay_var.get())
        cfg["mode"] = self._mode_var.get()
        cfg["source"] = self._source_var.get().lower()
        config.save(cfg)
        self.destroy()
#  Main floating window
# ════════════════════════════════════════════════════════════════════════════
#  Main floating window
# ════════════════════════════════════════════════════════════════════════════

class FloatingApp(ctk.CTk):
    """Compact always-on-top floating transcription widget."""

    def __init__(self) -> None:
        super().__init__()

        # ── Window chrome ────────────────────────────────────────────────────
        self.title(APP_NAME)
        cfg = config.load()
        x, y = cfg.get("window_x", 100), cfg.get("window_y", 100)
        self.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")
        self.resizable(False, False)
        self.overrideredirect(True)           # remove OS title bar
        self.attributes("-topmost", cfg.get("always_on_top", True))
        self.attributes("-alpha", 0.0)
        self.configure(fg_color=COLORS["bg_main"])

        # ── State ────────────────────────────────────────────────────────────
        self._recorder:   Optional[rec_module.Recorder] = None
        self._is_recording    = False
        self._pulse_idx       = 0
        self._pulse_job       = None
        self._alpha_job       = None
        self._drag_x = self._drag_y = 0
        self._countdown_job   = None
        self._waiting_click   = False
        self._click_listener  = None
        self._rt_transcriber  = None    # RealtimeTranscriber instance
        self._target_selected = False   # True after user clicks target field
        self._live_text_buffer = []     # accumulates live deltas before typing
        self._live_paste_job   = None   # scheduled paste timer for live mode
        self._stopping         = False  # guard flag to prevent double-stop

        # ── Build UI ─────────────────────────────────────────────────────────
        self._build_ui()
        self.bind("<ButtonPress-1>",   self._drag_start)
        self.bind("<B1-Motion>",       self._drag_move)
        self.bind("<ButtonRelease-1>", self._drag_end)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.attributes("-alpha", 0.0)

        # ── Apply rounded corners (Windows 10/11) ───────────────────────────
        self.after(100, self._apply_rounded_corners)
        self.after(60, lambda: self._fade_to_alpha(0.98))

    def _apply_rounded_corners(self) -> None:
        """Apply rounded corners to the window using Windows DWM API."""
        if not _WINDOWS_ROUNDED_CORNERS_AVAILABLE:
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            preference = ctypes.c_int(_DWMWCP_ROUND)
            _dwmapi.DwmSetWindowAttribute(
                hwnd,
                _DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(preference),
                ctypes.sizeof(preference)
            )
        except Exception:
            pass  # Silently fail if DWM API not available

    def _fade_to_alpha(self, target: float, step: float = 0.08) -> None:
        if self._alpha_job is not None:
            try:
                self.after_cancel(self._alpha_job)
            except Exception:
                pass
            self._alpha_job = None

        def _tick() -> None:
            try:
                current = float(self.attributes("-alpha"))
            except Exception:
                self._alpha_job = None
                return

            if abs(current - target) <= 0.01:
                self.attributes("-alpha", target)
                self._alpha_job = None
                return

            if current < target:
                next_alpha = min(current + step, target)
            else:
                next_alpha = max(current - step, target)

            self.attributes("-alpha", next_alpha)
            self._alpha_job = self.after(16, _tick)

        _tick()

    def _build_ui(self) -> None:
        self._root_frame = ctk.CTkFrame(
            self,
            corner_radius=CORNER_R,
            fg_color=COLORS["bg_main"],
            border_width=1,
            border_color=COLORS["border_soft"],
        )
        self._root_frame.pack(fill="both", expand=True, padx=0, pady=0)

        title_bar = ctk.CTkFrame(self._root_frame, height=24, fg_color="transparent")
        title_bar.pack(fill="x", padx=9, pady=(3, 0))
        title_bar.pack_propagate(False)

        title_bar.bind("<ButtonPress-1>", self._drag_start)
        title_bar.bind("<B1-Motion>", self._drag_move)

        ctk.CTkLabel(
            title_bar,
            text="Voxify",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_main"],
        ).pack(side="left")

        ctk.CTkLabel(
            title_bar,
            text="private desktop AI",
            font=ctk.CTkFont(size=9),
            text_color=COLORS["text_dim"],
        ).pack(side="left", padx=(6, 0))

        ctk.CTkButton(
            title_bar,
            text="×",
            width=20,
            height=20,
            corner_radius=6,
            fg_color="transparent",
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_dim"],
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._on_close,
        ).pack(side="right", padx=1)

        ctk.CTkButton(
            title_bar,
            text="⋯",
            width=20,
            height=20,
            corner_radius=6,
            fg_color="transparent",
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_dim"],
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._open_settings,
        ).pack(side="right", padx=1)

        content = ctk.CTkFrame(self._root_frame, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=9, pady=(2, 7))

        status_row = ctk.CTkFrame(content, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 4))

        self._status_var = tk.StringVar(value="Ready")
        self._status_lbl = ctk.CTkLabel(
            status_row,
            textvariable=self._status_var,
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=COLORS["text_dim"],
            anchor="w",
        )
        self._status_lbl.pack(side="left", fill="x", expand=True)

        self._mode_badge = ctk.CTkFrame(
            status_row,
            corner_radius=999,
            fg_color=COLORS["accent_soft"],
            border_width=1,
            border_color=COLORS["border"],
        )
        self._mode_badge.pack(side="right")
        self._mode_badge_label = ctk.CTkLabel(
            self._mode_badge,
            text=config.load().get("mode", "Live"),
            font=ctk.CTkFont(size=8, weight="bold"),
            text_color=COLORS["text_main"],
        )
        self._mode_badge_label.pack(padx=7, pady=1)

        self._progress = ctk.CTkProgressBar(
            content,
            height=2,
            corner_radius=1,
            fg_color=COLORS["border_soft"],
            progress_color=COLORS["accent"],
        )
        self._progress.set(0)
        self._progress.pack_forget()

        self._action_btn = ctk.CTkButton(
            content,
            text="Start",
            height=34,
            corner_radius=12,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_start_click,
        )
        self._action_btn.pack(fill="x")

        self._set_action_style(
            text="Start",
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_main"],
            border_width=0,
            border_color=COLORS["accent"],
        )

        self._status_anim_job = None
        self._status_anim_step = 0
        self._status_anim_base = "Ready"

    def _set_action_style(
        self,
        text: str,
        fg_color: str,
        hover_color: str,
        text_color: str,
        border_width: int,
        border_color: str,
        command=None,
        state: str = "normal",
    ) -> None:
        self._action_btn.configure(
            text=text,
            fg_color=fg_color,
            hover_color=hover_color,
            text_color=text_color,
            border_width=border_width,
            border_color=border_color,
            state=state,
            command=command if command is not None else self._action_btn.cget("command"),
        )

    def _set_status_message(self, text: str, animated: bool = False) -> None:
        if self._status_anim_job is not None:
            try:
                self.after_cancel(self._status_anim_job)
            except Exception:
                pass
            self._status_anim_job = None
        self._status_anim_base = text
        self._status_anim_step = 0

        if not animated:
            self._status_var.set(text)
            return

        def _tick() -> None:
            dots = "." * (self._status_anim_step % 4)
            self._status_var.set(f"{self._status_anim_base}{dots}")
            self._status_anim_step += 1
            self._status_anim_job = self.after(240, _tick)

        _tick()

    def _stop_status_animation(self) -> None:
        if self._status_anim_job is not None:
            try:
                self.after_cancel(self._status_anim_job)
            except Exception:
                pass
            self._status_anim_job = None
        self._status_var.set(self._status_anim_base)

    # ── Recording logic ───────────────────────────────────────────────────────

    def _on_start_click(self) -> None:
        """Handle Start button click."""
        if not self._is_recording and not self._waiting_click:
            self._ask_for_target_click()

    def _on_stop_click(self) -> None:
        """Handle Stop button click."""
        if self._is_recording:
            self._stop_recording()

    def _ask_for_target_click(self) -> None:
        """Dim window and wait for user to click the typing target."""
        cfg = config.load()
        api_key = cfg.get("api_key", "").strip()
        if not api_key:
            messagebox.showwarning("API Key Missing", "Please add your API key in Settings.")
            return

        self._waiting_click = True
        self._target_selected = False
        self._set_action_style(
            text="Cancel",
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["record"],
            border_width=1,
            border_color=COLORS["record"],
            command=self._cancel_target_selection,
        )
        self._status_var.set("Click a target")
        self._fade_to_alpha(0.35)

        def _listen():
            try:
                from pynput import mouse as pmouse
            except ImportError:
                self.after(0, self._on_pynput_missing)
                return

            def _on_click(x, y, button, pressed):
                if not pressed or button != pmouse.Button.left:
                    return
                # Ignore clicks on our own window
                wx, wy = self.winfo_x(), self.winfo_y()
                ww, wh = self.winfo_width(), self.winfo_height()
                if wx <= x <= wx + ww and wy <= y <= wy + wh:
                    return
                if not self._waiting_click:
                    return True
                self._click_listener.stop()
                self.after(0, self._on_target_selected)

            self._click_listener = pmouse.Listener(on_click=_on_click)
            self._click_listener.start()
            self._click_listener.join()

        threading.Thread(target=_listen, daemon=True).start()

    def _cancel_target_selection(self) -> None:
        """User pressed Cancel during target selection."""
        self._waiting_click = False
        self._stopping = False
        if self._click_listener:
            try:
                self._click_listener.stop()
            except: pass
            self._click_listener = None

        self._fade_to_alpha(0.98)
        self._reset_to_ready()

    def _on_target_selected(self) -> None:
        """User clicked a target field — now start recording."""
        self._waiting_click = False
        self._target_selected = True
        self._click_listener = None
        self._fade_to_alpha(0.98)
        self._status_var.set("Target selected")
        self._start_recording()

    def _on_pynput_missing(self) -> None:
        """pynput not installed."""
        self._waiting_click = False
        self._fade_to_alpha(0.98)
        self._reset_to_ready()
        messagebox.showerror("Missing Dependency", "pynput is required for click-to-type.")

    def _start_recording(self) -> None:
        """Called after target is selected — actually start recording."""
        cfg = config.load()
        api_key = cfg.get("api_key", "").strip()
        self._stopping = False

        self._set_action_style(
            text="Preparing...",
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_dim"],
            border_width=1,
            border_color=COLORS["border"],
            state="disabled",
        )
        self._progress.set(0.35)
        self._set_status_message("Preparing")
        
        if (cfg.get("mode") or "Live").strip().lower() == "live":
            self._start_live_mode(api_key, cfg)
            return

        self._start_batch_mode(cfg)

    def _start_live_mode(self, api_key: str, cfg: dict) -> None:
        if self._stopping: return
        try:
            self._start_realtime(api_key, cfg)
        except Exception as e:
            messagebox.showerror("Live Mode Error", str(e))
            self._reset_to_ready()

    def _start_batch_mode(self, cfg: dict) -> None:
        if self._stopping: return
        source = cfg.get("source", "mic")
        self._recorder = rec_module.Recorder(source=source, sample_rate=cfg.get("sample_rate", 16000))
        try:
            self._recorder.start()
        except Exception as e:
            messagebox.showerror("Recorder Error", str(e))
            self._reset_to_ready()
            return

        self._is_recording = True
        self._progress.stop()
        self._progress.set(1.0)
        self._progress.configure(progress_color=COLORS["record"])

        self._set_action_style(
            text="Stop recording",
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["record"],
            border_width=1,
            border_color=COLORS["record"],
            command=self._on_stop_click,
        )
        self._status_var.set("Recording")
        self._set_status_message("Recording")

    def _stop_recording(self) -> None:
        if self._stopping: return
        self._stopping = True
        
        cfg = config.load()
        self._set_action_style(
            text="Stopping...",
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_dim"],
            border_width=1,
            border_color=COLORS["border"],
            state="disabled",
        )
        self._status_var.set("Stopping...")
        self._set_status_message("Stopping")
        self._progress.set(0.8)
        
        if (cfg.get("mode") or "Live").strip().lower() == "live":
            self._stop_realtime()
            return
        
        if not self._recorder:
            self._stopping = False
            return
            
        self._is_recording = False
        self._status_var.set("Transcribing...")
        threading.Thread(target=self._transcribe_thread, daemon=True).start()

    def _transcribe_thread(self) -> None:
        wav_path: Optional[str] = None
        try:
            wav_path = self._recorder.stop()
            cfg = config.load()
            client = tr_module.TranscriptionClient(api_key=cfg["api_key"], model=cfg.get("model", "voxtral-mini-2507"))
            text = client.transcribe(wav_path, language=cfg.get("language") or None)
            self.after(0, self._on_transcription_done, text)
        except Exception as e:
            self.after(0, self._on_transcription_error, str(e))
        finally:
            if wav_path: tr_module.cleanup_temp(wav_path)

    def _on_transcription_done(self, text: str) -> None:
        self._stopping = False
        self._reset_to_ready()
        
        if not text.strip():
            self._status_var.set("No speech detected")
            return

        self._status_var.set("Typing...")
        def _type_batch():
            time.sleep(0.1)
            output_handler.type_text(text)
            self.after(0, lambda: self._status_var.set(f"Typed {len(text)} chars"))
        threading.Thread(target=_type_batch, daemon=True).start()

    def _reset_to_ready(self) -> None:
        self._stopping = False
        self._is_recording = False
        self._stop_status_animation()
        self._progress.stop()
        self._progress.set(0)
        self._progress.configure(progress_color=COLORS["accent"])

        self._set_action_style(
            text="Start",
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_main"],
            border_width=0,
            border_color=COLORS["accent"],
            command=self._on_start_click,
            state="normal",
        )
        self._target_selected = False
        self._sync_mode_badge()
        self._set_status_message("Ready")

    def _sync_mode_badge(self) -> None:
        try:
            self._mode_badge_label.configure(text=config.load().get("mode", "Live"))
        except Exception:
            pass

    def _on_transcription_error(self, err: str) -> None:
        self._reset_to_ready()
        messagebox.showerror("Transcription Error", err)

    def _start_realtime(self, api_key: str, cfg: dict) -> None:
        self._live_text_buffer.clear()
        source = cfg.get("source", "mic")
        
        self._rt_transcriber = rt_module.RealtimeTranscriber(
            api_key=api_key,
            sample_rate=cfg.get("sample_rate", 16000),
            source=source,
            on_delta =lambda t: self.after(0, self._type_live_delta, t),
            on_status=lambda s: self.after(0, self._set_live_status, s),
            on_done  =lambda:   self.after(0, self._on_realtime_done),
            on_error =lambda e: self.after(0, self._on_transcription_error, e),
        )
        self._rt_transcriber.start()
        self._is_recording = True
        self._progress.configure(progress_color=COLORS["accent"])
        self._progress.start()

        self._set_status_message("Listening", animated=True)

        self._set_action_style(
            text="Stop recording",
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["record"],
            border_width=1,
            border_color=COLORS["record"],
            command=self._on_stop_click,
        )
        self._status_var.set("Listening")

    def _set_live_status(self, status: str) -> None:
        normalized = status.lower().strip()
        if "connecting" in normalized:
            self._set_status_message("Connecting", animated=True)
            return

        if "live" in normalized or "speak now" in normalized:
            self._set_status_message("Listening", animated=True)
            return

        self._set_status_message(status)

    def _stop_realtime(self) -> None:
        self._is_recording = False
        self._progress.stop()
        
        if self._live_paste_job:
            self.after_cancel(self._live_paste_job)
            self._live_paste_job = None
        self._flush_live_buffer()
        
        if self._rt_transcriber:
            self._rt_transcriber.stop()
            self._rt_transcriber = None
        
        self.after(400, self._finalize_stop)

    def _finalize_stop(self) -> None:
        self._reset_to_ready()
        self._status_var.set("Completed")
        self._set_status_message("Completed")

    def _type_live_delta(self, delta: str) -> None:
        self._live_text_buffer.append(delta)
        if self._live_paste_job:
            self.after_cancel(self._live_paste_job)
        delay_ms = 50 if len(delta) > 10 else 300
        self._live_paste_job = self.after(delay_ms, self._flush_live_buffer)

    def _flush_live_buffer(self) -> None:
        if not self._live_text_buffer: return
        text_chunk = "".join(self._live_text_buffer)
        self._live_text_buffer.clear()
        threading.Thread(
            target=lambda: output_handler.type_text(text_chunk, interval=0.01),
            daemon=True,
        ).start()
        self._live_paste_job = None

    def _on_realtime_done(self) -> None:
        self._is_recording = False
        self._progress.stop()
        self._flush_live_buffer()
        self._rt_transcriber = None
        self._reset_to_ready()

    def _drag_start(self, event: tk.Event) -> None:
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _drag_move(self, event: tk.Event) -> None:
        nx, ny = event.x_root - self._drag_x, event.y_root - self._drag_y
        self.geometry(f"+{nx}+{ny}")

    def _drag_end(self, event: tk.Event) -> None:
        config.set_value("window_x", self.winfo_x())
        config.set_value("window_y", self.winfo_y())

    def _open_settings(self) -> None:
        settings_script = Path(__file__).with_name("flet_settings.py")

        if settings_script.exists() and importlib.util.find_spec("flet") is not None:
            try:
                subprocess.Popen([sys.executable, str(settings_script)], cwd=str(settings_script.parent))
                return
            except Exception as exc:
                messagebox.showerror("Settings Error", f"Unable to open the Flet settings window:\n{exc}")

        SettingsDialog(self)

    def _on_close(self) -> None:
        if self._rt_transcriber: self._rt_transcriber.stop()
        config.set_value("window_x", self.winfo_x())
        config.set_value("window_y", self.winfo_y())
        self.destroy()

def main() -> None:
    import flet as ft
    is_settings_mode = "--settings" in sys.argv
    if not is_settings_mode:
        if not _MAIN_INSTANCE_LOCK.acquire():
            notice = "Voxify is already running. Close the existing window first."
            if sys.platform.startswith("win"):
                try:
                    ctypes.windll.user32.MessageBoxW(None, notice, APP_NAME, 0x40)
                except Exception:
                    print(notice)
            else:
                print(notice)
            return
        atexit.register(_MAIN_INSTANCE_LOCK.release)

    try:
        if is_settings_mode:
            import flet_settings

            ft.run(flet_settings.main, view=ft.AppView.FLET_APP)
            return

        import flet_main

        ft.run(flet_main.main, view=ft.AppView.FLET_APP)
    except Exception as exc:
        message = f"Unable to start Voxify: {exc}"
        if sys.platform.startswith("win"):
            try:
                ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
            except Exception:
                print(message)
        else:
            print(message)
        raise
    finally:
        if not is_settings_mode:
            _MAIN_INSTANCE_LOCK.release()

if __name__ == "__main__":
    main()
