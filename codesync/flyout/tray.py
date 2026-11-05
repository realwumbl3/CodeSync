"""pystray icon. Left click toggles the flyout, right click opens controls, hover peeks."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import pystray

from .constants import APP_DISPLAY_NAME
from .icons import load_tray_icon

logger = logging.getLogger(__name__)

_last_hover = 0.0
_last_click = 0.0


def _install_win32_notify_hook() -> None:
    try:
        from pystray._win32 import Icon as Win32Icon
    except (ImportError, AttributeError):
        return
    if getattr(Win32Icon, "_codesync_notify_patched", False):
        return
    original = Win32Icon._on_notify

    def _patched(self, wparam, lparam):
        global _last_hover, _last_click
        if lparam == 0x0205:
            _last_click = time.time()
            callback = getattr(self, "_codesync_on_secondary_click", None)
            if callback is not None:
                try:
                    callback()
                except Exception:
                    logger.exception("tray right-click failed")
                return
        if lparam == 0x0200:
            now = time.time()
            if now - _last_click < 1.0 or now - _last_hover < 0.4:
                return original(self, wparam, lparam)
            _last_hover = now
            callback = getattr(self, "_codesync_on_hover", None)
            if callback is not None:
                try:
                    callback()
                except Exception:
                    logger.exception("tray hover failed")
        return original(self, wparam, lparam)

    Win32Icon._on_notify = _patched
    Win32Icon._codesync_notify_patched = True


class TrayIcon(threading.Thread):
    def __init__(self, *, on_toggle, on_secondary_click, on_hover) -> None:
        super().__init__(name="codesync-tray", daemon=True)
        self._on_toggle = on_toggle
        _install_win32_notify_hook()
        self._icon = pystray.Icon(
            APP_DISPLAY_NAME,
            load_tray_icon(),
            APP_DISPLAY_NAME,
            menu=pystray.Menu(pystray.MenuItem("", self._on_primary, default=True, visible=False)),
        )
        self._icon._codesync_on_secondary_click = on_secondary_click
        self._icon._codesync_on_hover = on_hover

    def _on_primary(self, _icon, _item) -> None:
        global _last_click
        _last_click = time.time()
        try:
            self._on_toggle()
        except Exception:
            logger.exception("tray left-click failed")

    def run(self) -> None:
        self._icon.run()
