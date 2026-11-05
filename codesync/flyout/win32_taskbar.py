"""Windows taskbar auto-hide helpers for tray-adjacent popup placement."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QRect

ABM_GETAUTOHIDEBAREX = 0x0000000B
ABM_GETSTATE = 0x00000004
ABS_AUTOHIDE = 0x1
ABE_LEFT = 0
ABE_TOP = 1
ABE_RIGHT = 2
ABE_BOTTOM = 3
MONITOR_DEFAULTTONEAREST = 2
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", RECT),
        ("lParam", wintypes.LPARAM),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


if sys.platform == "win32":
    _shell32 = ctypes.windll.shell32
    _user32 = ctypes.windll.user32
    _shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]
    _shell32.SHAppBarMessage.restype = ctypes.c_void_p
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.FindWindowExW.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
    ]
    _user32.FindWindowExW.restype = wintypes.HWND
    _get_window_rect = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, ctypes.POINTER(RECT)
    )(("GetWindowRect", _user32))
    _set_window_pos = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    )(("SetWindowPos", _user32))
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    _user32.MonitorFromPoint.restype = wintypes.HMONITOR
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    _user32.GetMonitorInfoW.restype = wintypes.BOOL
else:
    _shell32 = None
    _user32 = None
    _get_window_rect = None
    _set_window_pos = None


def _native_monitor_handle(screen) -> int | None:
    """HMONITOR for a real Windows QScreen. Offscreen screens have none."""
    if screen is None:
        return None
    try:
        native = screen.nativeInterface()
        if native is None:
            return None
        handle = int(native.handle() or 0)
    except Exception:
        return None
    return handle or None


def _screen_monitor_handle(screen) -> int | None:
    native = _native_monitor_handle(screen)
    if native:
        return native
    if screen is None or _user32 is None:
        return None
    sg = screen.geometry()
    cx = sg.left() + (sg.width() // 2)
    cy = sg.top() + (sg.height() // 2)
    pt = wintypes.POINT(cx, cy)
    try:
        mh = _user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
        return int(mh) if mh else None
    except Exception:
        return None


def map_physical_rect(
    physical: tuple[int, int, int, int],
    monitor: tuple[int, int, int, int],
    screen: QRect,
) -> QRect | None:
    """Map an exclusive physical rect into a QScreen's Qt geometry.

    Qt's virtual desktop is not the Win32 pixel desktop when monitors use
    different scales. A taskbar at physical x=1920 can be the left edge of the
    secondary Qt screen, and treating 1920 as a Qt coordinate drops the flyout
    on the other monitor.
    """
    mon_left, mon_top, mon_right, mon_bottom = (int(value) for value in monitor)
    span_x = mon_right - mon_left
    span_y = mon_bottom - mon_top
    if span_x <= 0 or span_y <= 0 or screen.width() <= 0 or screen.height() <= 0:
        return None
    left, top, right, bottom = (int(value) for value in physical)

    def scale_x(value: int) -> int:
        return int(round(screen.left() + (value - mon_left) * screen.width() / span_x))

    def scale_y(value: int) -> int:
        return int(round(screen.top() + (value - mon_top) * screen.height() / span_y))

    mapped_left, mapped_top = scale_x(left), scale_y(top)
    mapped_right, mapped_bottom = scale_x(right), scale_y(bottom)
    if mapped_right <= mapped_left or mapped_bottom <= mapped_top:
        return None
    return QRect(mapped_left, mapped_top, mapped_right - mapped_left, mapped_bottom - mapped_top)


def _logical_taskbar_rect(screen, monitor: int, physical: RECT) -> QRect | None:
    """Map a taskbar's physical pixels into this QScreen's Qt geometry."""
    if _user32 is None or monitor <= 0:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    try:
        ok = _user32.GetMonitorInfoW(wintypes.HMONITOR(monitor), ctypes.byref(info))
    except Exception:
        return None
    if not ok:
        return None
    mon = info.rcMonitor
    return map_physical_rect(
        (int(physical.left), int(physical.top), int(physical.right), int(physical.bottom)),
        (int(mon.left), int(mon.top), int(mon.right), int(mon.bottom)),
        screen.geometry(),
    )


def _edge_has_autohide_taskbar_on_screen(screen, edge: int) -> bool:
    if _shell32 is None or _user32 is None or screen is None:
        return False
    sg = screen.geometry()
    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(APPBARDATA)
    abd.uEdge = edge
    abd.rc.left = int(sg.left())
    abd.rc.top = int(sg.top())
    abd.rc.right = int(sg.right()) + 1
    abd.rc.bottom = int(sg.bottom()) + 1
    try:
        hwnd = _shell32.SHAppBarMessage(ABM_GETAUTOHIDEBAREX, ctypes.byref(abd))
        if not hwnd:
            return False
        taskbar_monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        screen_monitor = _screen_monitor_handle(screen)
        return bool(taskbar_monitor) and screen_monitor is not None and int(taskbar_monitor) == int(screen_monitor)
    except Exception:
        return False


def _default_taskbar_thickness_px(screen) -> int:
    try:
        dpi = float(screen.logicalDotsPerInch()) if screen is not None else 96.0
    except Exception:
        dpi = 96.0
    scale = max(1.0, dpi / 96.0)
    return max(30, int(round(40 * scale)))


def _taskbar_windows() -> list[tuple[int, RECT]]:
    if _user32 is None:
        return []
    found: list[tuple[int, RECT]] = []
    for class_name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        after = wintypes.HWND()
        while True:
            try:
                hwnd = _user32.FindWindowExW(None, after, class_name, None)
            except Exception:
                break
            if not hwnd:
                break
            rect = RECT()
            if _get_window_rect is not None and _get_window_rect(hwnd, ctypes.byref(rect)):
                found.append((int(hwnd), rect))
            after = hwnd
    return found


def _taskbar_window_rects() -> list[RECT]:
    return [rect for _, rect in _taskbar_windows()]


def _reserve_taskbar_rect(safe: QRect, sg, left: int, top: int, right: int, bottom: int) -> bool:
    left = max(int(left), sg.left())
    top = max(int(top), sg.top())
    right = min(int(right), sg.right() + 1)
    bottom = min(int(bottom), sg.bottom() + 1)
    if right <= left or bottom <= top:
        return False
    if (
        right <= safe.left()
        or left > safe.right()
        or bottom <= safe.top()
        or top > safe.bottom()
    ):
        return False

    width = right - left
    height = bottom - top
    if width >= height:
        if top <= sg.top() + height:
            safe.setTop(max(safe.top(), bottom))
        else:
            safe.setBottom(min(safe.bottom(), top - 1))
    elif left <= sg.left() + width:
        safe.setLeft(max(safe.left(), right))
    else:
        safe.setRight(min(safe.right(), left - 1))
    return True


def _reserve_visible_taskbar(screen, safe: QRect) -> bool:
    if _user32 is None or screen is None:
        return False

    sg = screen.geometry()
    native = _native_monitor_handle(screen)
    reserved = False
    for hwnd, rect in _taskbar_windows():
        if native:
            try:
                taskbar_monitor = int(_user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) or 0)
            except Exception:
                continue
            if taskbar_monitor != native:
                continue
            logical = _logical_taskbar_rect(screen, native, rect)
            if logical is None:
                continue
            reserved = _reserve_taskbar_rect(
                safe, sg, logical.left(), logical.top(), logical.right() + 1, logical.bottom() + 1,
            ) or reserved
            continue
        reserved = _reserve_taskbar_rect(
            safe, sg, rect.left, rect.top, rect.right, rect.bottom,
        ) or reserved
    return reserved


def place_window_above_taskbar(hwnd: int, gap_px: int = 10) -> bool:
    if not hwnd or _get_window_rect is None or _set_window_pos is None:
        return False
    window = RECT()
    if not _get_window_rect(hwnd, ctypes.byref(window)):
        return False

    candidates = []
    for taskbar in _taskbar_window_rects():
        taskbar_width = int(taskbar.right) - int(taskbar.left)
        taskbar_height = int(taskbar.bottom) - int(taskbar.top)
        if taskbar_width < taskbar_height:
            continue
        overlap = max(
            0,
            min(int(window.right), int(taskbar.right))
            - max(int(window.left), int(taskbar.left)),
        )
        if overlap:
            candidates.append((overlap, taskbar))
    if not candidates:
        return False

    _, taskbar = max(candidates, key=lambda item: item[0])
    window_height = max(1, int(window.bottom) - int(window.top))
    target_y = int(taskbar.top) - max(0, int(gap_px)) - window_height
    return bool(
        _set_window_pos(
            hwnd,
            None,
            int(window.left),
            target_y,
            0,
            0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
        )
    )


def taskbar_safe_available_geometry(screen, fallback: QRect, gap_px: int = 10) -> QRect:
    if sys.platform != "win32" or screen is None:
        return QRect(fallback)
    safe = QRect(fallback)
    visible_taskbar_reserved = _reserve_visible_taskbar(screen, safe)
    reserve = max(0, _default_taskbar_thickness_px(screen) + int(max(0, gap_px)))
    if reserve == 0:
        return safe
    has_bottom = _edge_has_autohide_taskbar_on_screen(screen, ABE_BOTTOM)
    has_top = _edge_has_autohide_taskbar_on_screen(screen, ABE_TOP)
    has_left = _edge_has_autohide_taskbar_on_screen(screen, ABE_LEFT)
    has_right = _edge_has_autohide_taskbar_on_screen(screen, ABE_RIGHT)
    if visible_taskbar_reserved:
        pass
    elif has_bottom:
        safe.setBottom(safe.bottom() - reserve)
    elif has_top:
        safe.setTop(safe.top() + reserve)
    elif has_left:
        safe.setLeft(safe.left() + reserve)
    elif has_right:
        safe.setRight(safe.right() - reserve)
    else:
        try:
            abd = APPBARDATA()
            abd.cbSize = ctypes.sizeof(APPBARDATA)
            state = int(_shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(abd)) or 0)
            if state & ABS_AUTOHIDE:
                safe.setBottom(safe.bottom() - reserve)
        except Exception:
            pass
    return safe
