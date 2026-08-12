"""
Voxify - Floating Speech-to-Text Desktop Tool
=============================================
A compact, always-on-top floating desktop widget that:
    - records mic or system audio
    - transcribes speech in batch or live mode
    - delivers text by auto-typing or clipboard copy
"""

import atexit
import os
import sys
import ctypes
import importlib
import tempfile
from pathlib import Path

# ── Logging Setup ──────────────────────────────────────────────────────────
import logging
_log_file = Path(__file__).with_name("voxify_debug.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    filename=str(_log_file),
    filemode="w",
)
logger = logging.getLogger("app")
logger.info("=" * 60)
logger.info("Voxify starting up")
logger.info("=" * 60)
# Also log to console via print for immediate feedback
def _print_log_tail():
    try:
        with open(_log_file) as f:
            tail = f.readlines()[-20:]
        print("".join(tail), flush=True)
    except Exception:
        pass
atexit.register(_print_log_tail)
# ───────────────────────────────────────────────────────────────────────────

APP_NAME = "Voxify"


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


def _ensure_flet_runtime() -> None:
    missing_packages: list[str] = []
    required = (("flet", "flet"), ("flet-desktop", "flet_desktop"))
    for package_name, module_name in required:
        try:
            importlib.import_module(module_name)
        except Exception:
            missing_packages.append(package_name)

    if missing_packages:
        joined = ", ".join(missing_packages)
        raise RuntimeError(
            f"Missing required desktop packages: {joined}. "
            "Run: python -m pip install -r requirements.txt"
        )


def main() -> None:
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
        _ensure_flet_runtime()
        import flet as ft

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
        if os.getenv("VOXIFY_RERAISE_STARTUP_ERRORS", "").strip() == "1":
            raise
        return
    finally:
        if not is_settings_mode:
            _MAIN_INSTANCE_LOCK.release()


if __name__ == "__main__":
    main()
