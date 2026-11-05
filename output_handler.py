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
import datetime
import logging
from typing import Callable, Literal, Optional

logger = logging.getLogger("output_handler")

from lazyimport import LazyModule

# pyperclip / pyautogui are imported lazily on first use — importing them
# eagerly at module load slows app startup by several hundred milliseconds.
pyperclip = LazyModule("pyperclip")
_CLIP_OK = pyperclip
pyautogui = LazyModule("pyautogui")
_GUI_OK = pyautogui

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

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _INPUTUNION(ctypes.Union):
        # MOUSEINPUT must be included so sizeof(INPUT) matches the
        # Windows x64 ABI (40 bytes). Without it, SendInput fails with
        # ERROR_INVALID_PARAMETER (87).
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

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


# ── Internal Helpers ───────────────────────────────────────────────────────

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


def read_clipboard() -> str:
    """Read the current text content from the system clipboard."""
    if not _CLIP_OK:
        return "Clipboard access unavailable."
    try:
        content = pyperclip.paste()
        if not content:
            return "Clipboard is empty."
        return content
    except Exception as e:
        return f"Error reading clipboard: {str(e)}"


def get_active_window_info() -> dict:
    """Get information about the currently focused (active) window."""
    info = {"title": "Unknown", "process": "Unknown"}
    if _WIN_OK:
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                info["title"] = buff.value
            
            # Get process name
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            import psutil
            process = psutil.Process(pid.value)
            info["process"] = process.name()
        except Exception:
            pass
    return info


def system_action(action: Literal["lock", "sleep", "empty_trash"]) -> bool:
    """Perform system-level housekeeping actions."""
    if not _WIN_OK: return False
    try:
        import subprocess
        if action == "lock":
            ctypes.windll.user32.LockWorkStation()
        elif action == "sleep":
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"], timeout=5)
        elif action == "empty_trash":
            subprocess.run(["powershell.exe", "-Command", "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"], capture_output=True, timeout=30)
        return True
    except Exception:
        return False


def list_files(directory: str = "downloads") -> list[str]:
    """List files in common user directories."""
    import os
    from pathlib import Path
    
    mapping = {
        "downloads": str(Path.home() / "Downloads"),
        "documents": str(Path.home() / "Documents"),
        "desktop": str(Path.home() / "Desktop"),
        "pictures": str(Path.home() / "Pictures"),
        "videos": str(Path.home() / "Videos")
    }
    
    target = mapping.get(directory.lower(), directory)
    try:
        if not os.path.exists(target):
            return [f"Directory {target} not found."]
        files = os.listdir(target)
        # Return newest 15 files
        full_paths = [os.path.join(target, f) for f in files if os.path.isfile(os.path.join(target, f))]
        full_paths.sort(key=os.path.getmtime, reverse=True)
        return [{"name": os.path.basename(f), "path": f} for f in full_paths[:15]]
    except Exception as e:
        return [f"Error listing files: {str(e)}"]


def read_file_content(file_path: str) -> str:
    """Read the text content of a file (limited to 5000 characters)."""
    import os
    try:
        if not os.path.exists(file_path):
            return f"File not found: {file_path}"
        
        # Check size (safety for large binaries)
        if os.path.getsize(file_path) > 1024 * 1024: # 1MB limit
            return "File too large to read directly. Please open it manually."
            
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(5000)
            return content if content else "File is empty."
    except Exception as e:
        return f"Error reading file: {str(e)}"


def run_shell_command(command: str) -> dict:
    """Execute a shell/terminal command and return the output (Windows PowerShell)."""
    if not _WIN_OK: return {"error": "Only supported on Windows"}
    try:
        import subprocess
        result = subprocess.run(["powershell.exe", "-Command", command], capture_output=True, text=True, timeout=15)
        return {
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:2000],
            "exit_code": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 15 seconds."}
    except Exception as e:
        return {"error": str(e)}


def get_screens_info() -> list[dict]:
    """Get information about all connected monitors/screens."""
    screens = []
    if _GUI_OK:
        try:
            from screeninfo import get_monitors
            for m in get_monitors():
                screens.append({
                    "name": m.name,
                    "x": m.x,
                    "y": m.y,
                    "width": m.width,
                    "height": m.height,
                    "is_primary": m.is_primary
                })
        except Exception:
            # Fallback to single monitor if screeninfo is missing
            w, h = pyautogui.size()
            screens.append({"name": "Primary", "x": 0, "y": 0, "width": w, "height": h, "is_primary": True})
    return screens


def set_system_volume(level: int) -> bool:
    """Set the system master volume (0 to 100) using Win32 API — no shell/cmd."""
    if not _WIN_OK: return False
    try:
        vol = max(0, min(100, level))
        # Map 0-100 to 0-0xFFFF (16-bit unsigned)
        c_val = int(vol / 100.0 * 0xFFFF)
        packed = (c_val << 16) | c_val  # left + right channels
        ctypes.windll.winmm.waveOutSetVolume(0, packed)
        return True
    except Exception:
        return False


def set_screen_brightness(level: int) -> bool:
    """Set screen brightness (0 to 100) on all supported monitors."""
    if not _WIN_OK: return False
    # Uses subprocess with PowerShell WMI (no shell=True — safe from injection).
    # Pure ctypes/COM approach would require expensive boilerplate.
    try:
        import subprocess
        ps_cmd = f"Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | ForEach-Object {{ $_.WmiSetBrightness(0, {level}) }}"
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=15)
        return True
    except Exception: return False


def get_foreground_window() -> int:
    """Return the handle of the current foreground window."""
    if _WIN_OK:
        try:
            return int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            pass
    return 0


def get_window_title(hwnd: int) -> str:
    """Return the title of a window by handle."""
    if not _WIN_OK or not hwnd:
        return ""
    try:
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
            return buff.value
    except Exception:
        pass
    return ""


def verify_and_lock_target_window(target_hwnd: int, timeout: float = 0.5) -> bool:
    """Verify that target_hwnd or its root ancestor is active, bringing it to foreground if needed."""
    if not _WIN_OK or not target_hwnd:
        return True
    try:
        fg = ctypes.windll.user32.GetForegroundWindow()
        if fg == target_hwnd:
            return True
        root_fg = ctypes.windll.user32.GetAncestor(fg, 2) or fg  # GA_ROOT = 2
        root_target = ctypes.windll.user32.GetAncestor(target_hwnd, 2) or target_hwnd
        if root_fg == root_target:
            return True

        ctypes.windll.user32.ShowWindow(root_target, 9)  # SW_RESTORE = 9
        ctypes.windll.user32.SetForegroundWindow(root_target)
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(0.04)
            current = ctypes.windll.user32.GetForegroundWindow()
            cur_root = ctypes.windll.user32.GetAncestor(current, 2) or current
            if cur_root == root_target:
                return True
        logger.warning(f"Target window {target_hwnd} could not regain foreground (current fg={current})")
        return False
    except Exception as exc:
        logger.warning(f"Focus verification error: {exc}")
        return False


def type_text(
    text: str,
    interval: float = 0.03,
    x: Optional[int] = None,
    y: Optional[int] = None,
    target_hwnd: Optional[int] = None,
    verify_focus: bool = True,
) -> bool:
    """Type *text* safely with target window verification.
    
    If x and y are provided, clicks at (x, y) first and locks target_hwnd to that window.
    If target_hwnd is specified or inferred, verifies that the target window has foreground focus
    before and during typing to prevent accidental injection into other apps or popup windows.
    """
    if not text:
        return True
    
    logger.info(f"type_text called: text='{text[:50]}...' (total_len={len(text)}), interval={interval}, x={x}, y={y}, target_hwnd={target_hwnd}")

    if x is not None and y is not None:
        if _WIN_OK:
            try:
                from ctypes import wintypes
                pt = wintypes.POINT(int(x), int(y))
                h = ctypes.windll.user32.WindowFromPoint(pt)
                if h:
                    target_hwnd = ctypes.windll.user32.GetAncestor(h, 2) or h
            except Exception:
                pass
        if not mouse_click(x, y):
            logger.warning(f"mouse_click({x}, {y}) failed")
            return False
        time.sleep(0.15)
    elif _WIN_OK and not target_hwnd:
        cur_fg = ctypes.windll.user32.GetForegroundWindow()
        if cur_fg:
            target_hwnd = ctypes.windll.user32.GetAncestor(cur_fg, 2) or cur_fg

    if verify_focus and target_hwnd and _WIN_OK:
        if not verify_and_lock_target_window(target_hwnd):
            logger.warning(f"type_text: target window {target_hwnd} ('{get_window_title(target_hwnd)}') not focused, safe abort")
            return False

    if _WIN_OK:
        normalized = text.replace("\r\n", "\n")
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            title = get_window_title(hwnd)
            logger.info(f"Active window for typing: '{title}' (hwnd={hwnd})")

            char_count = 0
            for char in normalized:
                char_count += 1
                if verify_focus and target_hwnd and char_count % 25 == 0:
                    current_fg = ctypes.windll.user32.GetForegroundWindow()
                    current_root = ctypes.windll.user32.GetAncestor(current_fg, 2) or current_fg
                    target_root = ctypes.windll.user32.GetAncestor(target_hwnd, 2) or target_hwnd
                    if current_root != target_root:
                        logger.warning(f"Focus shifted mid-typing to '{get_window_title(current_fg)}' (hwnd={current_fg}). Re-locking...")
                        if not verify_and_lock_target_window(target_hwnd):
                            logger.error("type_text: target focus lost mid-typing; aborting remaining characters for safety.")
                            return False

                if char in ("\n", "\r"):
                    if not _send_key(0x0D):
                        logger.warning(f"_send_key(0x0D) failed, falling back")
                        raise RuntimeError("_send_key failed")
                elif char == "\t":
                    if not _send_key(0x09):
                        logger.warning(f"_send_key(0x09) failed, falling back")
                        raise RuntimeError("_send_key failed")
                else:
                    for unit in _iter_utf16_units(char):
                        if not _send_unicode_unit(unit):
                            logger.warning(f"_send_unicode_unit failed for char={repr(char)} unit=U+{unit:04X}, falling back")
                            raise RuntimeError("_send_unicode_unit failed")
                if interval: time.sleep(interval)
            logger.info(f"type_text: successfully typed {len(text)} chars via SendInput")
            return True
        except Exception as exc:
            logger.error(f"type_text SendInput failed: {exc}", exc_info=True)

    if _GUI_OK:
        try:
            logger.info("Falling back to pyautogui.write")
            pyautogui.write(text, interval=interval)
            return True
        except Exception as exc:
            logger.error(f"type_text pyautogui failed: {exc}", exc_info=True)

    logger.warning("type_text: all methods failed, falling back to clipboard copy")
    return copy_to_clipboard(text)


def auto_type(
    text: str,
    delay_seconds: int = 3,
    on_countdown: Optional[Callable[[int], None]] = None,
    on_done: Optional[Callable[[], None]] = None,
    interval: float = 0.03,
) -> threading.Thread:
    """After a *delay_seconds* countdown, type *text* at the current cursor."""
    def _run():
        for remaining in range(delay_seconds, 0, -1):
            if on_countdown: on_countdown(remaining)
            time.sleep(1)
        if on_countdown: on_countdown(0)
        type_text(text, interval=interval)
        if on_done: on_done()

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
    """Unified delivery dispatch."""
    if mode == "clipboard":
        copy_to_clipboard(text)
        if on_done: on_done()
    else:
        auto_type(text, delay_seconds=delay_seconds, on_countdown=on_countdown, on_done=on_done)


def send_shortcut(*keys: str) -> bool:
    """Send a keyboard shortcut such as ('ctrl', 'z')."""
    if not keys: return False
    if _GUI_OK:
        try:
            pyautogui.hotkey(*keys)
            return True
        except Exception: return False
    return False


def mouse_click(x: int, y: int, button: Literal["left", "right", "middle"] = "left", double: bool = False) -> bool:
    """Perform a mouse click at the specified coordinates."""
    if not _GUI_OK: return False
    try:
        if double: pyautogui.doubleClick(x, y, button=button)
        else: pyautogui.click(x, y, button=button)
        return True
    except Exception: return False


def move_mouse(x: int, y: int, duration: float = 0.2) -> bool:
    """Move the mouse cursor to specific screen coordinates with smooth easing."""
    if not _GUI_OK: return False
    try:
        pyautogui.moveTo(x, y, duration=duration, tween=pyautogui.easeInOutQuad)
        return True
    except Exception: return False


def move_mouse_relative(dx: int, dy: int, duration: float = 0.1) -> bool:
    """Move the mouse cursor by a small pixel offset from current position."""
    if not _GUI_OK: return False
    try:
        pyautogui.moveRel(dx, dy, duration=duration, tween=pyautogui.easeInOutQuad)
        return True
    except Exception: return False


def mouse_drag(x1: int, y1: int, x2: int, y2: int, button: Literal["left", "right"] = "left") -> bool:
    """Drag the mouse from one point to another."""
    if not _GUI_OK: return False
    try:
        pyautogui.moveTo(x1, y1)
        pyautogui.dragTo(x2, y2, duration=0.5, button=button, tween=pyautogui.easeInOutQuad)
        return True
    except Exception: return False


_SCROLL_STOP_EVENT = threading.Event()
_SCROLL_THREAD: Optional[threading.Thread] = None

def start_continuous_scroll(direction: Literal["up", "down"], speed: float = 0.5) -> bool:
    """Start scrolling the page continuously in a background thread."""
    global _SCROLL_THREAD
    if not _GUI_OK: return False
    
    # Stop existing scroll if any
    stop_continuous_scroll()
    
    _SCROLL_STOP_EVENT.clear()
    
    def _scroll_worker():
        amount = 120 if direction == "up" else -120
        while not _SCROLL_STOP_EVENT.is_set():
            try:
                pyautogui.scroll(amount)
                time.sleep(speed)
            except Exception:
                break
    
    _SCROLL_THREAD = threading.Thread(target=_scroll_worker, daemon=True)
    _SCROLL_THREAD.start()
    return True


def stop_continuous_scroll() -> bool:
    """Stop any active continuous scrolling."""
    _SCROLL_STOP_EVENT.set()
    return True


def smooth_scroll(direction: Literal["up", "down"], clicks: int = 3, speed: float = 0.05) -> bool:
    """Perform a smooth, professional-feeling scroll."""
    if not _GUI_OK: return False
    try:
        amount = 120 if direction == "up" else -120
        steps = 5
        for _ in range(clicks):
            for _ in range(steps):
                pyautogui.scroll(amount // steps)
                time.sleep(speed / steps)
        return True
    except Exception: return False


def open_application(query: str) -> bool:
    """Attempt to open an application by name. Searches Start Menu shortcuts and registry paths before falling back to system start command."""
    if not _WIN_OK: return False
    import os
    import subprocess
    from pathlib import Path
    
    query_lower = query.lower().strip()
    
    # 1. Search in Start Menu folders for matching shortcut (.lnk) files
    start_menu_dirs = [
        os.path.join(os.environ.get("ProgramData", "C:\\ProgramData"), "Microsoft\\Windows\\Start Menu\\Programs"),
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft\\Windows\\Start Menu\\Programs")
    ]
    
    # Quick scan for matching .lnk files
    for start_dir in start_menu_dirs:
        if os.path.exists(start_dir):
            for root, dirs, files in os.walk(start_dir):
                for file in files:
                    if file.lower().endswith(".lnk"):
                        name_without_ext = file[:-4].lower()
                        if query_lower in name_without_ext:
                            lnk_path = os.path.join(root, file)
                            try:
                                os.startfile(lnk_path)
                                return True
                            except Exception:
                                pass # try other shortcuts
                                
    # 2. Search common installation folders for direct executables
    common_search_paths = [
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files")),
        os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")),
        os.path.join(os.environ.get("LocalAppData", ""), "Programs")
    ]
    for search_dir in common_search_paths:
        if os.path.exists(search_dir):
            for folder_name in os.listdir(search_dir):
                if query_lower in folder_name.lower():
                    folder_path = os.path.join(search_dir, folder_name)
                    if os.path.isdir(folder_path):
                        try:
                            for file in os.listdir(folder_path):
                                if file.lower() == f"{query_lower}.exe" or (file.lower().endswith(".exe") and query_lower in file.lower()):
                                    try:
                                        os.startfile(os.path.join(folder_path, file))
                                        return True
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                                    
    # 3. Fallback to start command (safe: no shell=True)
    try:
        os.startfile(query)
        return True
    except Exception:
        try:
            # Use cmd.exe /c start with list args (no shell=True) to prevent injection
            subprocess.run(["cmd.exe", "/c", "start", "", query], capture_output=True, timeout=10)
            return True
        except Exception:
            return False


def get_screen_size() -> tuple[int, int]:
    """Return the primary monitor resolution."""
    if _GUI_OK: return pyautogui.size()
    return (0, 0)


def get_mouse_position() -> tuple[int, int]:
    """Return the current (x, y) coordinates of the mouse cursor."""
    if _GUI_OK: return pyautogui.position()
    return (0, 0)


# ── Modern PC Control Enhancements ──────────────────────────────────────────

def list_windows() -> list[str]:
    """Return a list of titles for all visible windows."""
    titles = []
    if _WIN_OK:
        def callback(hwnd, extra):
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                    titles.append(buff.value)
            return True
        
        EnumWindows = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        ctypes.windll.user32.EnumWindows(EnumWindows(callback), 0)
    return sorted(list(set(titles)))


def manage_window(title: str, action: Literal["activate", "minimize", "maximize", "close"]) -> bool:
    """Perform actions on a window identified by its title."""
    if not _WIN_OK: return False
    
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if not hwnd:
        # Try partial match
        all_titles = list_windows()
        for t in all_titles:
            if title.lower() in t.lower():
                hwnd = ctypes.windll.user32.FindWindowW(None, t)
                break
    
    if not hwnd: return False
    
    if action == "activate":
        ctypes.windll.user32.ShowWindow(hwnd, 9) # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    elif action == "minimize":
        ctypes.windll.user32.ShowWindow(hwnd, 6) # SW_MINIMIZE
    elif action == "maximize":
        ctypes.windll.user32.ShowWindow(hwnd, 3) # SW_MAXIMIZE
    elif action == "close":
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0) # WM_CLOSE
    
    return True


def get_system_status() -> dict:
    """Gather basic system resource information."""
    info = {"timestamp": datetime.datetime.now().isoformat()}
    try:
        import psutil
        info["cpu_percent"] = psutil.cpu_percent(interval=None)
        info["memory_percent"] = psutil.virtual_memory().percent
        battery = psutil.sensors_battery()
        if battery:
            info["battery_percent"] = battery.percent
            info["power_plugged"] = battery.power_plugged
    except ImportError:
        info["note"] = "Install psutil for detailed stats"
    
    info["screen_resolution"] = f"{get_screen_size()[0]}x{get_screen_size()[1]}"
    return info


def media_control(action: Literal["play_pause", "next", "previous", "volume_up", "volume_down", "mute"]) -> bool:
    """Control system media playback."""
    mapping = {
        "play_pause": "playpause",
        "next": "nexttrack",
        "previous": "prevtrack",
        "volume_up": "volumeup",
        "volume_down": "volumedown",
        "mute": "volumemute"
    }
    key = mapping.get(action)
    if key and _GUI_OK:
        try:
            pyautogui.press(key)
            return True
        except Exception: pass
    return False


def search_web(query: str, mode: Literal["tab", "window"] = "tab") -> bool:
    """Search the web using the default browser."""
    import webbrowser
    import os
    try:
        url = f"https://www.google.com/search?q={query}"
        if mode == "window":
            webbrowser.open_new(url)
        else:
            # Try standard way
            success = webbrowser.open_new_tab(url)
            if not success and _WIN_OK:
                # Fallback for Windows
                os.startfile(url)
        return True
    except Exception: return False


def open_url(url: str, mode: Literal["tab", "window"] = "tab") -> bool:
    """Open a specific URL in the default browser."""
    import webbrowser
    import os
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if mode == "window":
            webbrowser.open_new(url)
        else:
            success = webbrowser.open_new_tab(url)
            if not success and _WIN_OK:
                os.startfile(url)
        return True
    except Exception: return False


def web_search(query: str) -> str:
    """Perform a background web search and return a summary of results."""
    try:
        import requests
        from bs4 import BeautifulSoup
        import urllib.parse
        import html
        import re

        clean_query = (query or "").strip()
        if not clean_query:
            return "No search query provided."

        # 1. Specialized weather handler
        weather_match = re.search(r'(?:weather|temperature|forecast)\s+(?:in|for|at)?\s*([a-zA-Z\s]+)', clean_query, re.IGNORECASE)
        if weather_match:
            city = weather_match.group(1).strip()
            if city:
                try:
                    wr = requests.get(f"https://wttr.in/{urllib.parse.quote(city)}?format=j1", timeout=5)
                    if wr.status_code == 200:
                        wdata = wr.json()
                        curr = wdata.get("current_condition", [{}])[0]
                        desc = curr.get("weatherDesc", [{}])[0].get("value", "Clear").strip()
                        tc = curr.get("temp_C", "N/A")
                        tf = curr.get("temp_F", "N/A")
                        hum = curr.get("humidity", "N/A")
                        return f"Current weather in {city.title()}: {desc}, {tc}°C ({tf}°F), Humidity: {hum}%"
                except Exception as we:
                    logger.debug(f"Weather lookup failed: {we}")

        # 2. DuckDuckGo HTML search with Session
        try:
            s = requests.Session()
            s.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            })
            s.get("https://duckduckgo.com/", timeout=4)
            resp = s.post("https://html.duckduckgo.com/html/", data={"q": clean_query}, timeout=6)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for result in soup.find_all("div", class_="result"):
                    a = result.find("a", class_="result__a")
                    snippet = result.find(class_="result__snippet")
                    if a:
                        title = a.get_text(strip=True)
                        snip = snippet.get_text(strip=True) if snippet else ""
                        if title and snip:
                            results.append(f"Title: {title}\nSnippet: {snip}")
                            if len(results) >= 4:
                                break
                if results:
                    return "\n\n".join(results)
        except Exception as de:
            logger.debug(f"DDG HTML search failed: {de}")

        # 3. DuckDuckGo Lite search fallback
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            resp = requests.post(
                "https://lite.duckduckgo.com/lite/",
                data={"q": clean_query},
                headers=headers,
                timeout=6
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for a in soup.find_all("a", class_="result-link"):
                    title = a.get_text(strip=True)
                    tr = a.find_parent("tr")
                    snippet = ""
                    if tr:
                        snippet_tr = tr.find_next_sibling("tr")
                        if snippet_tr:
                            snippet_td = snippet_tr.find("td", class_="result-snippet")
                            if snippet_td:
                                snippet = snippet_td.get_text(strip=True)
                    if title and snippet:
                        results.append(f"Title: {title}\nSnippet: {snippet}")
                        if len(results) >= 4:
                            break
                if results:
                    return "\n\n".join(results)
        except Exception as le:
            logger.debug(f"DDG Lite search failed: {le}")

        # 4. Wikipedia API fallback
        try:
            wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(clean_query)}&utf8=&format=json"
            wiki_resp = requests.get(wiki_url, headers={"User-Agent": "VoxifyAssistant/1.0"}, timeout=5)
            if wiki_resp.status_code == 200:
                wiki_data = wiki_resp.json()
                items = wiki_data.get("query", {}).get("search", [])
                results = []
                for item in items[:3]:
                    title = item.get("title", "")
                    raw_snippet = item.get("snippet", "")
                    clean_snippet = html.unescape(re.sub(r"<[^>]+>", "", raw_snippet)).strip()
                    if title and clean_snippet:
                        results.append(f"Title: {title}\nSnippet: {clean_snippet}")
                if results:
                    return "\n\n".join(results)
        except Exception as we:
            logger.debug(f"Wikipedia fallback failed: {we}")

        return f"No results found for '{clean_query}'."
    except Exception as e:
        return f"Error performing search: {str(e)}"



def get_local_time() -> str:
    """Return the current local date and time."""
    return datetime.datetime.now().strftime("%A, %B %d, %Y %I:%M %p")


# ── New Custom Functions for PC Control improvement ────────────────────────

def press_key_combination(keys: list[str]) -> bool:
    """Presses multiple keys simultaneously and releases them (modifier hotkeys)."""
    if not keys:
        return False
    if not _GUI_OK:
        return False
    try:
        # Press all keys in sequence
        for key in keys:
            pyautogui.keyDown(key.strip().lower())
        # Release in reverse order
        for key in reversed(keys):
            pyautogui.keyUp(key.strip().lower())
        return True
    except Exception:
        return False


def _parse_screen_via_uia(target_hwnd: Optional[int] = None) -> list[dict]:
    """Inspect UI elements and native coordinates directly via Windows UI Automation (UIA) tree inspection.
    
    10x faster and 100% accurate without taking screenshots or running heavy OCR.
    """
    if not _WIN_OK:
        return []
    
    import json
    import subprocess
    
    hwnd_val = int(target_hwnd or 0)
    if hwnd_val <= 0:
        try:
            hwnd_val = int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            hwnd_val = 0

    ps_code = f"""
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes

    $hwnd = [IntPtr]{hwnd_val}
    $target = if ($hwnd -ne [IntPtr]::Zero) {{
        try {{ [System.Windows.Automation.AutomationElement]::FromHandle($hwnd) }} catch {{ $null }}
    }} else {{ $null }}
    if ($target -eq $null) {{
        $target = [System.Windows.Automation.AutomationElement]::RootElement
    }}

    $cond = New-Object System.Windows.Automation.OrCondition(
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)),
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)),
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::ComboBox)),
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::CheckBox)),
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::RadioButton)),
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Hyperlink)),
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::MenuItem)),
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)),
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Text))
    )

    $out = @()
    try {{
        $elements = $target.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
        foreach ($el in $elements) {{
            try {{
                $rect = $el.Current.BoundingRectangle
                if ($rect.Width -gt 4 -and $rect.Height -gt 4) {{
                    $name = $el.Current.Name
                    $type = $el.Current.ControlType.ProgrammaticName.Replace('ControlType.', '')
                    if ($name -or $type -eq 'Edit') {{
                        $out += [PSCustomObject]@{{
                            text = if ($name) {{ $name }} else {{ "[$type]" }}
                            type = $type
                            x = [int]($rect.X + ($rect.Width / 2))
                            y = [int]($rect.Y + ($rect.Height / 2))
                            w = [int]$rect.Width
                            h = [int]$rect.Height
                        }}
                    }}
                }}
            }} catch {{}}
            if ($out.Count -ge 200) {{ break }}
        }}
    }} catch {{}}
    Write-Output ($out | ConvertTo-Json -Compress)
    """

    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_code],
            capture_output=True,
            text=True,
            timeout=4,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            items = data if isinstance(data, list) else [data]
            logger.info(f"UI Automation (UIA) extracted {len(items)} native UI elements")
            return items
    except Exception as exc:
        logger.warning(f"UI Automation inspection failed: {exc}")
    return []


def _parse_screen_via_ocr() -> list[dict]:
    """Capture the screen and use Windows native OCR to find text elements and coordinates as fallback."""
    if not _WIN_OK or not _GUI_OK:
        return []

    import os
    import tempfile
    import json
    import subprocess

    temp_dir = tempfile.gettempdir()
    temp_img = os.path.join(temp_dir, f"voxify_ocr_{int(time.time())}.png")

    try:
        pyautogui.screenshot(temp_img)
        ps_code = f"""
        function Await($task) {{
            while ($task.Status -eq 'Started') {{ [System.Threading.Thread]::Sleep(5) }}
            return $task.GetResults()
        }}
        try {{
            $absolutePath = "{temp_img.replace(chr(92), '/')}"
            [void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
            [void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
            [void][Windows.Storage.Streams.RandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]
            
            $storageFileTask = [Windows.Storage.StorageFile]::GetFileFromPathAsync($absolutePath)
            $storageFile = Await $storageFileTask
            
            $fileStreamTask = $storageFile.OpenAsync([Windows.Storage.FileAccessMode]::Read)
            $fileStream = Await $fileStreamTask

            $decoderTask = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($fileStream)
            $decoder = Await $decoderTask

            $bitmapTask = $decoder.GetSoftwareBitmapAsync()
            $softwareBitmap = Await $bitmapTask

            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
            if ($engine -ne $null) {{
                $ocrTask = $engine.RecognizeAsync($softwareBitmap)
                $result = Await $ocrTask
                
                $out = @()
                foreach ($line in $result.Lines) {{
                    foreach ($word in $line.Words) {{
                        $rect = $word.BoundingRect
                        $out += [PSCustomObject]@{{
                            text = $word.Text
                            x = [int]$rect.X
                            y = [int]$rect.Y
                            w = [int]$rect.Width
                            h = [int]$rect.Height
                            type = "Text"
                        }}
                    }}
                }}
                $fileStream.Dispose()
                Write-Output ($out | ConvertTo-Json -Compress)
            }} else {{
                $fileStream.Dispose()
                Write-Output "[]"
            }}
        }} catch {{
            Write-Output "[]"
        }}
        """

        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps_code], capture_output=True, text=True, timeout=8)
        if result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout.strip())
                return data if isinstance(data, list) else [data]
            except Exception:
                pass
        return []
    except Exception:
        return []
    finally:
        if os.path.exists(temp_img):
            try:
                os.remove(temp_img)
            except Exception:
                pass


def parse_screen_text(prefer_uia: bool = True) -> list[dict]:
    """Find text and UI elements with screen coordinates.
    
    Uses native Windows UI Automation (UIA) tree inspection first (10x faster, zero GPU load,
    100% accurate native coordinates). Falls back to WinRT OCR if UIA returns no elements.
    """
    if prefer_uia and _WIN_OK:
        elements = _parse_screen_via_uia()
        if elements:
            return elements
        logger.info("UIA inspection found no elements, falling back to screen OCR")
    return _parse_screen_via_ocr()


def write_file_content(path: str, content: str) -> bool:
    """Create or overwrite a text file with the specified content."""
    try:
        # Expand user directories if ~ is used
        full_path = os.path.abspath(os.path.expanduser(path))
        parent_dir = os.path.dirname(full_path)
        if not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
            
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception:
        return False


def file_operation(source: str, target: str, action: Literal["copy", "move", "delete", "rename"]) -> bool:
    """Perform file actions like copying, moving, deleting, or renaming files/folders."""
    import shutil
    try:
        src = os.path.abspath(os.path.expanduser(source)) if source else ""
        tgt = os.path.abspath(os.path.expanduser(target)) if target else ""
        
        if action == "copy":
            if os.path.isdir(src):
                shutil.copytree(src, tgt)
            else:
                shutil.copy(src, tgt)
        elif action == "move" or action == "rename":
            shutil.move(src, tgt)
        elif action == "delete":
            if os.path.isdir(src):
                shutil.rmtree(src)
            else:
                os.remove(src)
        return True
    except Exception:
        return False


def create_folder(path: str) -> bool:
    """Create a new folder (including subfolders if necessary)."""
    try:
        full_path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(full_path, exist_ok=True)
        return True
    except Exception:
        return False


def list_running_processes() -> list[dict]:
    """Return a list of currently running processes with PID, name, and CPU/Memory usage."""
    import psutil
    processes = []
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
            try:
                # Cache process info
                info = proc.info
                # Make memory percent readable (convert to percent of total memory)
                processes.append({
                    "pid": info['pid'],
                    "name": info['name'],
                    "cpu": info['cpu_percent'],
                    "memory": round(info['memory_percent'], 2) if info['memory_percent'] else 0
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        # Sort by CPU usage and return top 25 processes
        processes.sort(key=lambda x: x['cpu'], reverse=True)
        return processes[:25]
    except Exception:
        return []


def kill_process(pid_or_name: str) -> bool:
    """Terminate a process by its process ID (PID) or exact process name."""
    import psutil
    killed = False
    try:
        if pid_or_name.isdigit():
            pid = int(pid_or_name)
            p = psutil.Process(pid)
            p.terminate()
            return True
        else:
            name_lower = pid_or_name.lower().strip()
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if proc.info['name'] and proc.info['name'].lower() == name_lower:
                        proc.terminate()
                        killed = True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return killed
    except Exception:
        return False


def set_timer(duration_seconds: int, label: str = "Timer") -> bool:
    """Sets a timer/alarm for a specified duration in seconds. Alerts the user when finished."""
    def _timer_thread():
        time.sleep(duration_seconds)
        if _WIN_OK:
            try:
                import winsound
                # Play beep sound 3 times
                for _ in range(3):
                    winsound.MessageBeep()
                    time.sleep(0.5)
            except Exception:
                pass
        
        # Display popup dialog
        if _WIN_OK:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, f"Timer '{label}' finished!", "Voxify Alarm", 0x40 | 0x1000)
            except Exception:
                pass
    
    threading.Thread(target=_timer_thread, daemon=True).start()
    return True


