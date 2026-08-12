"""Tiny helper for lazy (deferred) module imports.

Some third-party modules (requests, pyautogui, pyperclip, ...) are only needed
at runtime, yet importing them eagerly at module load can add hundreds of
milliseconds to app startup. ``LazyModule`` defers the import until the first
attribute access or truthiness check.
"""

from __future__ import annotations

import importlib
from typing import Any


class LazyModule:
    """Import a module on first use.

    Behaves like the module itself for attribute access (``mod.func()``) and
    like a boolean for availability checks (``if mod: ...``), so it can be
    used as a drop-in replacement for an eager ``import``.
    """

    def __init__(self, module_name: str) -> None:
        self._module_name = module_name
        self._module: Any = None
        self._attempted = False

    def _ensure(self) -> None:
        if self._attempted:
            return
        self._attempted = True
        try:
            self._module = importlib.import_module(self._module_name)
        except Exception:
            self._module = None

    def __bool__(self) -> bool:
        self._ensure()
        return self._module is not None

    def __getattr__(self, name: str) -> Any:
        self._ensure()
        if self._module is None:
            raise ImportError(f"{self._module_name} is not available")
        return getattr(self._module, name)
