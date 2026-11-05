"""Windows 11 DWM: system Acrylic backdrop + rounded corners for the tray flyout."""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

_backdrop_rejection_logged = False
_accent_rgb_cache: tuple[tuple[int, int, int] | None, float] | None = None
_ACCENT_CACHE_TTL_SEC = 8.0

if sys.platform == "win32":
    _dwmapi = ctypes.windll.dwmapi
    _dwmapi.DwmSetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_int),
        wintypes.DWORD,
    ]
    _dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

    class _MARGINS(ctypes.Structure):
        _fields_ = [
            ("cxLeftWidth", ctypes.c_int),
            ("cxRightWidth", ctypes.c_int),
            ("cyTopHeight", ctypes.c_int),
            ("cyBottomHeight", ctypes.c_int),
        ]

    _dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(_MARGINS)]
    _dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
    _user32 = ctypes.windll.user32
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.WindowFromPoint.argtypes = [wintypes.POINT]
    _user32.WindowFromPoint.restype = wintypes.HWND
    _user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetAncestor.restype = wintypes.HWND
else:
    _dwmapi = None
    _user32 = None
    _MARGINS = None


def get_windows_accent_rgb() -> tuple[int, int, int] | None:
    global _accent_rgb_cache
    now = time.monotonic()
    if _accent_rgb_cache is not None:
        cached, t0 = _accent_rgb_cache
        if now - t0 < _ACCENT_CACHE_TTL_SEC:
            return cached
    if sys.platform != "win32":
        _accent_rgb_cache = (None, now)
        return None
    rgb: tuple[int, int, int] | None
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\DWM",
            0,
            winreg.KEY_READ,
        )
        try:
            colorization: int | None = None
            accent_color: int | None = None
            try:
                raw, _ = winreg.QueryValueEx(key, "ColorizationColor")
                if isinstance(raw, int):
                    colorization = raw
            except OSError:
                pass
            try:
                raw, _ = winreg.QueryValueEx(key, "AccentColor")
                if isinstance(raw, int):
                    accent_color = raw
            except OSError:
                pass
        finally:
            winreg.CloseKey(key)
    except OSError:
        rgb = None
        _accent_rgb_cache = (rgb, now)
        return None

    rgb = None
    if colorization is not None:
        r = (colorization >> 16) & 0xFF
        g = (colorization >> 8) & 0xFF
        b = colorization & 0xFF
        if (r | g | b) != 0:
            rgb = (r, g, b)
    if rgb is None and accent_color is not None:
        r = accent_color & 0xFF
        g = (accent_color >> 8) & 0xFF
        b = (accent_color >> 16) & 0xFF
        if (r | g | b) != 0:
            rgb = (r, g, b)

    _accent_rgb_cache = (rgb, now)
    return rgb


def suppress_window_chrome(widget, color: tuple[int, int, int] | None = None) -> bool:
    """Drop Win11's system corner. A transparent border reads as lighter corners."""
    if sys.platform != "win32" or _dwmapi is None:
        return False
    try:
        wid = int(widget.winId())
    except (RuntimeError, TypeError, ValueError):
        return False
    if not wid:
        return False

    DWMWA_TRANSITIONS_FORCEDISABLED = 3
    DWMWA_WINDOW_CORNER_PREFERENCE = 33
    DWMWA_BORDER_COLOR = 34
    DWMWA_SYSTEMBACKDROP_TYPE = 38
    DWMWCP_ROUND = 2
    DWMSBT_NONE = 1
    red, green, blue = color or (26, 26, 28)
    border = (int(blue) & 0xFF) << 16 | (int(green) & 0xFF) << 8 | (int(red) & 0xFF)

    def _dwm_set(hwnd: int, attr: int, value: int, kind) -> int:
        held = kind(value)
        return int(
            _dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(attr),
                ctypes.byref(held),
                wintypes.DWORD(ctypes.sizeof(held)),
            )
        )

    try:
        # Acrylic on a translucent window tints the corners and floods any extra area.
        # Transitions play the window in from its owner, which reads as a snap.
        _dwm_set(wid, DWMWA_TRANSITIONS_FORCEDISABLED, 1, ctypes.c_int)
        _dwm_set(wid, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_NONE, ctypes.c_int)
        _dwm_set(wid, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND, ctypes.c_int)
        _dwm_set(wid, DWMWA_BORDER_COLOR, border, ctypes.c_uint)
    except Exception:
        return False
    return True


def commit_tool_window_rect(widget, x: int, y: int, width: int, height: int) -> None:
    """Move a tool window to its final rect before Windows paints another one."""
    widget.setGeometry(int(x), int(y), int(width), int(height))
    suppress_window_chrome(widget)


def hide_tool_window(widget) -> None:
    """Hide a tool window. Qt's hide() can leave the native window on screen."""
    hwnd = 0
    if sys.platform == "win32" and _user32 is not None:
        try:
            hwnd = int(widget.winId())
        except (RuntimeError, TypeError, ValueError):
            hwnd = 0
    widget.hide()
    if not hwnd:
        return
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    SWP_HIDEWINDOW = 0x0080
    try:
        _user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(0),
            0,
            0,
            0,
            0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_HIDEWINDOW,
        )
    except Exception:
        return


def cursor_on_window(widget) -> bool:
    """True when the pointer is on this window, including its native children."""
    if sys.platform != "win32" or widget is None:
        return False
    try:
        if widget.isHidden():
            return False
        target = int(widget.winId())
    except (RuntimeError, TypeError, ValueError):
        return False
    if not target:
        return False
    point = wintypes.POINT()
    try:
        if not _user32.GetCursorPos(ctypes.byref(point)):
            return False
        hit = int(_user32.WindowFromPoint(point) or 0)
    except Exception:
        return False
    if not hit:
        return False
    GA_ROOT = 2
    try:
        root = int(_user32.GetAncestor(wintypes.HWND(hit), GA_ROOT) or 0)
    except Exception:
        root = 0
    return hit == target or root == target


def apply_transient_acrylic(hwnd: int, dark: bool) -> bool:
    """The flyout's backdrop: Win11 transient acrylic, dark mode, rounded corners."""
    if sys.platform != "win32" or _dwmapi is None or not hwnd:
        return False
    global _backdrop_rejection_logged
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    DWMWA_WINDOW_CORNER_PREFERENCE = 33
    DWMWA_SYSTEMBACKDROP_TYPE = 38
    DWMSBT_TRANSIENTWINDOW = 3
    DWMWCP_ROUND = 2

    def _dwm_set_int(attr: int, value: int) -> int:
        held = ctypes.c_int(value)
        return int(
            _dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(attr),
                ctypes.byref(held),
                wintypes.DWORD(ctypes.sizeof(held)),
            )
        )

    try:
        hr = _dwm_set_int(DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_TRANSIENTWINDOW)
        if hr != 0:
            if not _backdrop_rejection_logged:
                _backdrop_rejection_logged = True
                logger.info(
                    "DWM system backdrop (Acrylic) not applied for flyout; "
                    "using opaque panel (HRESULT=0x%08X).",
                    hr & 0xFFFFFFFF,
                )
            return False
        _dwm_set_int(DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
        _dwm_set_int(DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
    except Exception:
        if not _backdrop_rejection_logged:
            _backdrop_rejection_logged = True
            logger.info(
                "DwmSetWindowAttribute failed for flyout acrylic; using opaque panel.",
                exc_info=True,
            )
        return False
    return True


def extend_client_frame(hwnd: int) -> bool:
    """Let the system backdrop fill the client instead of stopping at a frame."""
    if sys.platform != "win32" or _dwmapi is None or not hwnd:
        return False
    margins = _MARGINS(-1, -1, -1, -1)
    try:
        hr = int(_dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins)))
    except Exception:
        logger.debug("DwmExtendFrameIntoClientArea failed", exc_info=True)
        return False
    return hr == 0


def prepare_splash_acrylic(hwnd: int, dark: bool) -> bool:
    """Acrylic popup with the flyout's backdrop and no extra system border."""
    if not apply_transient_acrylic(hwnd, dark) or not extend_client_frame(hwnd):
        return False
    DWMWA_TRANSITIONS_FORCEDISABLED = 3
    DWMWA_BORDER_COLOR = 34
    # DWMWA_COLOR_NONE is 0xFFFFFFFE. The shared attribute call takes a signed int.
    DWMWA_COLOR_NONE = -2

    def _dwm_set(attr: int, value: int) -> None:
        held = ctypes.c_int(value)
        _dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(attr),
            ctypes.byref(held),
            wintypes.DWORD(ctypes.sizeof(held)),
        )

    try:
        _dwm_set(DWMWA_TRANSITIONS_FORCEDISABLED, 1)
        _dwm_set(DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE)
    except Exception:
        logger.debug("Host splash chrome adjust failed", exc_info=True)
    return True


def try_apply_flyout_acrylic(widget, dark: bool) -> bool:
    if sys.platform != "win32" or _dwmapi is None:
        return False
    try:
        wid = int(widget.winId())
    except (RuntimeError, TypeError, ValueError):
        return False
    if not wid:
        return False
    return apply_transient_acrylic(wid, dark)
