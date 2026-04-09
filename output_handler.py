"""
Text output handler — delivers transcribed text to the user.

Modes
-----
clipboard  : copies text with pyperclip (instant, user pastes manually)
auto_type  : waits for a countdown, then types text directly at the active
             cursor position without touching the clipboard.
"""

import sys
import time
import threading
from typing import Callable, Literal, Optional

try:
    import pyperclip
    _CLIP_OK = True
except ImportError:
    _CLIP_OK = False

try:
    import pyautogui
    _GUI_OK = True
except ImportError:
    _GUI_OK = False

try:
    import ctypes
    from ctypes import wintypes
    _WIN_OK = sys.platform.startswith("win")
except Exception:
    _WIN_OK = False


if _WIN_OK:
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [
            ("type", wintypes.DWORD),
            ("u", _INPUTUNION),
        ]

    _SendInput = ctypes.windll.user32.SendInput
    _SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
    _SendInput.restype = ctypes.c_uint


OutputMode = Literal["clipboard", "auto_type"]


# ── Public helpers ──────────────────────────────────────────────────────────

def copy_to_clipboard(text: str) -> bool:
    """Copy *text* to the system clipboard. Returns True on success."""
    if not _CLIP_OK:
        return False
    try:
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def _iter_utf16_units(text: str):
    """Yield UTF-16 code units for Windows Unicode input."""
    encoded = text.encode("utf-16-le")
    for index in range(0, len(encoded), 2):
        yield int.from_bytes(encoded[index:index + 2], "little")


def _send_key(vk_code: int) -> bool:
    if not _WIN_OK:
        return False

    inputs = (INPUT * 2)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].ki = KEYBDINPUT(vk_code, 0, 0, 0, 0)
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].ki = KEYBDINPUT(vk_code, 0, KEYEVENTF_KEYUP, 0, 0)
    return _SendInput(2, inputs, ctypes.sizeof(INPUT)) == 2


def _send_unicode_unit(unit: int) -> bool:
    if not _WIN_OK:
        return False

    inputs = (INPUT * 2)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].ki = KEYBDINPUT(0, unit, KEYEVENTF_UNICODE, 0, 0)
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].ki = KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
    return _SendInput(2, inputs, ctypes.sizeof(INPUT)) == 2


def _type_text_windows(text: str, interval: float = 0.03) -> bool:
    if not _WIN_OK:
        return False

    normalized = text.replace("\r\n", "\n")
    try:
        for char in normalized:
            if char in ("\n", "\r"):
                if not _send_key(0x0D):
                    return False
            elif char == "\t":
                if not _send_key(0x09):
                    return False
            else:
                for unit in _iter_utf16_units(char):
                    if not _send_unicode_unit(unit):
                        return False
            if interval:
                time.sleep(interval)
        return True
    except Exception:
        return False


def _type_text_pyautogui(text: str, interval: float = 0.03) -> bool:
    if not _GUI_OK:
        return False
    try:
        pyautogui.write(text, interval=interval)
        return True
    except Exception:
        return False


def type_text(text: str, interval: float = 0.03) -> bool:
    """Type *text* directly into the active window without using clipboard."""
    if not text:
        return True

    if _type_text_windows(text, interval=interval):
        return True

    if _type_text_pyautogui(text, interval=interval):
        return True

    if not _WIN_OK:
        return copy_to_clipboard(text)

    return False


def auto_type(
    text: str,
    delay_seconds: int = 3,
    on_countdown: Optional[Callable[[int], None]] = None,
    on_done: Optional[Callable[[], None]] = None,
    interval: float = 0.03,
) -> threading.Thread:
    """
    After a *delay_seconds* countdown, type *text* at the current cursor.

    Runs in a background thread so the UI stays responsive.

    Parameters
    ----------
    text         : text to type
    delay_seconds: seconds to wait (gives user time to click the target field)
    on_countdown : callback(seconds_remaining) fired each countdown tick
    on_done      : callback fired when typing is complete
    interval     : seconds between each character (affects typing speed)

    Returns
    -------
    threading.Thread  — already started; join() if you need to wait.
    """
    def _run():
        for remaining in range(delay_seconds, 0, -1):
            if on_countdown:
                on_countdown(remaining)
            time.sleep(1)

        if on_countdown:
            on_countdown(0)

        type_text(text)

        if on_done:
            on_done()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def deliver(
    text: str,
    mode: OutputMode = "clipboard",
    delay_seconds: int = 3,
    on_countdown: Optional[Callable[[int], None]] = None,
    on_done: Optional[Callable[[], None]] = None,
) -> None:
    """
    Unified delivery dispatch.

    mode="clipboard" → instant copy, triggers on_done immediately.
    mode="auto_type" → countdown then type, triggers on_done when complete.
    """
    if mode == "clipboard":
        copy_to_clipboard(text)
        if on_done:
            on_done()
    else:
        auto_type(
            text,
            delay_seconds=delay_seconds,
            on_countdown=on_countdown,
            on_done=on_done,
        )


def _type_text(text: str, interval: float = 0.03) -> bool:
    """Backward-compatible alias for direct typing."""
    return type_text(text, interval=interval)


def send_shortcut(*keys: str) -> bool:
    """Send a keyboard shortcut such as ('ctrl', 'z')."""
    if not keys:
        return False
    if _GUI_OK:
        try:
            pyautogui.hotkey(*keys)
            return True
        except Exception:
            return False
    return False
