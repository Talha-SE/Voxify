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
            # Requires powercfg or rundll32
            subprocess.run("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
        elif action == "empty_trash":
            # Windows shell command for recycle bin
            subprocess.run("powershell.exe -Command \"Clear-RecycleBin -Force -ErrorAction SilentlyContinue\"", shell=True)
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
    """Set the system master volume (0 to 100)."""
    if not _WIN_OK: return False
    try:
        # Use NirCmd if available, or PowerShell fallback
        import subprocess
        # Volume scale for PS is 0.0 to 1.0 (float) or using specific shell objects
        ps_cmd = f"$obj = New-Object -ComObject WScript.Shell; for($i=0; $i -lt 50; $i++) {{ $obj.SendKeys([char]174) }}; for($i=0; $i -lt {level/2}; $i++) {{ $obj.SendKeys([char]175) }}"
        subprocess.run(["powershell.exe", "-Command", ps_cmd], capture_output=True)
        return True
    except Exception: return False


def set_screen_brightness(level: int) -> bool:
    """Set screen brightness (0 to 100) on all supported monitors."""
    if not _WIN_OK: return False
    try:
        import subprocess
        # Using Get-CimInstance which is the modern replacement for Get-WmiObject
        # We use ForEach-Object to ensure it hits all monitors that support it
        ps_cmd = f"Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | ForEach-Object {{ $_.WmiSetBrightness(0, {level}) }}"
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps_cmd], capture_output=True)
        return True
    except Exception: return False


def type_text(text: str, interval: float = 0.03, x: Optional[int] = None, y: Optional[int] = None) -> bool:
    """Type *text* directly. If x and y are provided, click there first."""
    if not text:
        return True
    
    if x is not None and y is not None:
        if not mouse_click(x, y):
            return False
        time.sleep(0.2) # Small delay to ensure focus

    if _WIN_OK:
        normalized = text.replace("\r\n", "\n")
        try:
            for char in normalized:
                if char in ("\n", "\r"):
                    if not _send_key(0x0D): return False
                elif char == "\t":
                    if not _send_key(0x09): return False
                else:
                    for unit in _iter_utf16_units(char):
                        if not _send_unicode_unit(unit): return False
                if interval: time.sleep(interval)
            return True
        except Exception: pass

    if _GUI_OK:
        try:
            pyautogui.write(text, interval=interval)
            return True
        except Exception: pass

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
    """Attempt to open an application by name."""
    if not _WIN_OK: return False
    try:
        import subprocess
        subprocess.Popen(f"start {query}", shell=True)
        return True
    except Exception: return False


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
        
        # Use DuckDuckGo HTML version for a simple, no-API-key search
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        url = f"https://html.duckduckgo.com/html/?q={query}"
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for result in soup.find_all("a", class_="result__a")[:3]:
            title = result.get_text()
            snippet = ""
            snippet_tag = result.find_parent("div").find_next_sibling("div", class_="result__snippet")
            if snippet_tag:
                snippet = snippet_tag.get_text()
            results.append(f"Title: {title}\nSnippet: {snippet}")
        
        if not results:
            return "No results found."
        return "\n\n".join(results)
    except Exception as e:
        return f"Error performing search: {str(e)}"


def get_local_time() -> str:
    """Return the current local date and time."""
    return datetime.datetime.now().strftime("%A, %B %d, %Y %I:%M %p")
