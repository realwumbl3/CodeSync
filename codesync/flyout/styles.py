"""Qt stylesheets and color tokens for the tray flyout."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication

from .win32_dwm import get_windows_accent_rgb


def is_dark_theme() -> bool:
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    return False


def _scrollbar_qss(dark: bool) -> str:
    if dark:
        handle, hover, pressed = (
            "rgba(255,255,255,0.34)",
            "rgba(255,255,255,0.55)",
            "rgba(255,255,255,0.72)",
        )
    else:
        handle, hover, pressed = (
            "rgba(0,0,0,0.28)",
            "rgba(0,0,0,0.45)",
            "rgba(0,0,0,0.58)",
        )
    return f"""
            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 6px 2px 6px 2px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {handle};
                min-height: 28px;
                border-radius: 3px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {hover}; }}
            QScrollBar::handle:vertical:pressed {{ background: {pressed}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                width: 0px;
                background: none;
                border: none;
                subcontrol-origin: margin;
            }}
            QScrollBar::add-line:vertical {{ subcontrol-position: bottom; }}
            QScrollBar::sub-line:vertical {{ subcontrol-position: top; }}
            QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
                border: none;
                width: 0px;
                height: 0px;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 10px;
                margin: 2px 6px 2px 6px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {handle};
                min-width: 28px;
                border-radius: 3px;
                margin: 0px;
            }}
            QScrollBar::handle:horizontal:hover {{ background: {hover}; }}
            QScrollBar::handle:horizontal:pressed {{ background: {pressed}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                height: 0px;
                width: 0px;
                background: none;
                border: none;
                subcontrol-origin: margin;
            }}
            QScrollBar::add-line:horizontal {{ subcontrol-position: right; }}
            QScrollBar::sub-line:horizontal {{ subcontrol-position: left; }}
            QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal,
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
                background: none;
                border: none;
                width: 0px;
                height: 0px;
            }}
        """


def flyout_stylesheet(dark: bool) -> str:
    return _theme_stylesheet(dark) + _scrollbar_qss(dark)


def _theme_stylesheet(dark: bool) -> str:
    if dark:
        return """
            QWidget#FlyoutRoot {
                background-color: transparent;
                border: none;
            }
            QWidget#FlyoutMachinePage { background: transparent; }
            QComboBox {
                color: #f3f3f3;
                background: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 6px;
                padding: 6px;
            }
            QComboBox QAbstractItemView { color: #f3f3f3; background: #2d2d2d; }
            QLabel#Title {
                color: #f3f3f3;
                font-size: 15px;
                font-weight: 600;
            }
            QLabel#Version {
                color: #a0a0a0;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#FlyoutSectionTitle {
                color: #a0a0a0;
                font-size: 11px;
                font-weight: 600;
                margin: 0px;
                padding: 0px;
            }
            QFrame#Divider {
                background-color: rgba(255,255,255,0.10);
                max-height: 1px;
                min-height: 1px;
            }
            QFrame#BackendRow {
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }
            QFrame#BackendRow:hover {
                background-color: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.14);
            }
            QFrame#ClientRow {
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 8px;
            }
            QFrame#ClientDot {
                background-color: #3ddc84;
                border: none;
                border-radius: 4px;
            }
            QLabel#ConnectedCount {
                color: #8ee0a8;
                font-size: 11px;
                font-weight: 600;
            }
            QFrame#NoticeCard {
                background-color: rgba(200, 160, 50, 0.14);
                border: 1px solid rgba(200, 160, 50, 0.32);
                border-radius: 8px;
            }
            QFrame#NoticeCard[kind="danger"] {
                background-color: rgba(200, 70, 70, 0.14);
                border: 1px solid rgba(200, 70, 70, 0.32);
            }
            QTreeWidget#IgnoreTree {
                background-color: rgba(255,255,255,0.04);
                color: #f3f3f3;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                outline: none;
            }
            QTreeWidget#IgnoreTree::item {
                padding: 2px 4px;
            }
            QTreeWidget#IgnoreTree::item:hover {
                background: rgba(255,255,255,0.06);
            }
            QTreeWidget#IgnoreTree::item:selected {
                background: rgba(255,255,255,0.10);
                color: #f3f3f3;
            }
            QListWidget#DiscoveryMachines, QScrollArea#DiscoveryList {
                background-color: rgba(255,255,255,0.04);
                color: #f3f3f3;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                outline: none;
            }
            QWidget#DiscoveryListHost { background: transparent; }
            QListWidget#DiscoveryMachines::item { padding: 10px; }
            QListWidget#DiscoveryMachines::item:selected {
                background-color: rgba(255,255,255,0.12);
            }
            QScrollArea#DeviceList, QWidget#DeviceListHost {
                background: transparent;
                border: none;
            }
            QFrame#DeviceRow {
                background: transparent;
                border: none;
                border-radius: 6px;
            }
            QFrame#DeviceRow[selected="true"] {
                background-color: rgba(255,255,255,0.12);
            }
            QLabel#PairingCode {
                color: #f3f3f3;
                font-size: 20px;
                font-weight: 600;
                padding: 4px 0px;
            }
            QPushButton#FlyoutDestructiveBtn {
                color: #ffb4ab;
            }
            QPushButton#FlyoutDestructiveBtn:disabled {
                color: rgba(255,255,255,0.38);
            }
            QLabel#BackendName {
                color: #f3f3f3;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#BackendReloadTime {
                color: #a0a0a0;
                font-size: 10px;
                font-weight: 600;
            }
            QLabel#BackendDetail {
                color: #a0a0a0;
                font-size: 11px;
            }
            QLabel#BackendDetail a {
                color: #9ec4ff;
                text-decoration: none;
            }
            QLabel#BackendDetail a:hover {
                text-decoration: underline;
            }
            QLabel#BackendStatus {
                font-size: 11px;
                font-weight: 600;
                padding: 3px 10px;
                border-radius: 999px;
            }
            QLabel#BackendStatus[status="running"] {
                color: #8ee0a8;
                background-color: rgba(80, 180, 110, 0.22);
            }
            QLabel#BackendStatus[status="starting"] {
                color: #f0d48a;
                background-color: rgba(200, 160, 50, 0.22);
            }
            QLabel#BackendStatus[status="stopped"] {
                color: rgba(255,255,255,0.62);
                background-color: rgba(255,255,255,0.08);
            }
            QLabel#BackendStatus[status="error"] {
                color: #f0a0a0;
                background-color: rgba(200, 70, 70, 0.22);
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: #f3f3f3;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                padding: 6px 10px;
                min-height: 18px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                border: 1px solid rgba(255, 255, 255, 0.12);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.04);
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.58);
            }
            QPushButton#FlyoutRestartBtn {
                min-width: 72px;
                max-width: 88px;
                padding: 4px 8px;
            }
            QPushButton#FlyoutDebugBtn {
                min-height: 22px;
                padding: 6px 10px;
            }
            QPushButton#FlyoutNavBtn {
                min-height: 22px;
                padding: 8px 10px;
                text-align: left;
            }
            QPushButton#FlyoutBackBtn {
                min-width: 56px;
                max-width: 72px;
                padding: 4px 8px;
                background-color: transparent;
                border: 1px solid transparent;
            }
            QPushButton#FlyoutBackBtn:hover {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.08);
            }
            QPushButton#FlyoutPrimaryBtn {
                background-color: #007aff;
                border: 1px solid #007aff;
                color: white;
                font-weight: 600;
            }
            QPushButton#FlyoutPrimaryBtn:hover { background-color: #3395ff; }
            QPushButton#FlyoutPrimaryBtn:pressed { background-color: #0063cc; }
            QPushButton#FlyoutSecondaryBtn {
                min-width: 32px;
                max-width: 32px;
                min-height: 28px;
                max-height: 28px;
                padding: 0px;
                background-color: transparent;
                border: 1px solid transparent;
            }
            QPushButton#FlyoutSecondaryBtn:hover {
                background-color: rgba(255, 255, 255, 0.10);
                border: 1px solid rgba(255, 255, 255, 0.08);
            }
            QPushButton#FlyoutSecondaryBtn:checked {
                background-color: rgba(255, 255, 255, 0.16);
                border: 1px solid rgba(255, 255, 255, 0.12);
            }
            QPushButton#FlyoutSecondaryBtn:disabled {
                background-color: transparent;
                border: 1px solid transparent;
            }
            QPushButton#UacApprove {
                background-color: #007aff;
                border: 1px solid #007aff;
                color: white;
            }
            QPushButton#UacApprove:hover { background-color: #3395ff; }
            QPushButton#UacApprove:disabled {
                background-color: rgba(0, 122, 255, 0.28);
                color: rgba(255, 255, 255, 0.58);
            }
        """
    return """
            QWidget#FlyoutRoot {
                background-color: transparent;
                border: none;
            }
            QWidget#FlyoutMachinePage { background: transparent; }
            QComboBox {
                color: #1a1a1a;
                background: rgba(0,0,0,0.04);
                border: 1px solid rgba(0,0,0,0.10);
                border-radius: 6px;
                padding: 6px;
            }
            QComboBox QAbstractItemView { color: #1a1a1a; background: #ffffff; }
            QLabel#Title {
                color: #1a1a1a;
                font-size: 15px;
                font-weight: 600;
            }
            QLabel#Version {
                color: #6b6b6b;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#FlyoutSectionTitle {
                color: #6b6b6b;
                font-size: 11px;
                font-weight: 600;
                margin: 0px;
                padding: 0px;
            }
            QFrame#Divider {
                background-color: rgba(0,0,0,0.10);
                max-height: 1px;
                min-height: 1px;
            }
            QFrame#BackendRow {
                background-color: rgba(0,0,0,0.03);
                border: 1px solid rgba(0,0,0,0.08);
                border-radius: 12px;
            }
            QFrame#BackendRow:hover {
                background-color: rgba(0,0,0,0.06);
                border: 1px solid rgba(0,0,0,0.12);
            }
            QFrame#ClientRow {
                background-color: rgba(0,0,0,0.03);
                border: 1px solid rgba(0,0,0,0.06);
                border-radius: 8px;
            }
            QFrame#ClientDot {
                background-color: #1f7a3a;
                border: none;
                border-radius: 4px;
            }
            QLabel#ConnectedCount {
                color: #1f7a3a;
                font-size: 11px;
                font-weight: 600;
            }
            QFrame#NoticeCard {
                background-color: rgba(200, 160, 50, 0.16);
                border: 1px solid rgba(160, 120, 20, 0.28);
                border-radius: 8px;
            }
            QFrame#NoticeCard[kind="danger"] {
                background-color: rgba(200, 70, 70, 0.12);
                border: 1px solid rgba(160, 40, 40, 0.24);
                border-radius: 8px;
            }
            QTreeWidget#IgnoreTree {
                background-color: rgba(0,0,0,0.03);
                color: #1a1a1a;
                border: 1px solid rgba(0,0,0,0.08);
                border-radius: 8px;
                outline: none;
            }
            QTreeWidget#IgnoreTree::item {
                padding: 2px 4px;
            }
            QTreeWidget#IgnoreTree::item:hover {
                background: rgba(0,0,0,0.04);
            }
            QTreeWidget#IgnoreTree::item:selected {
                background: rgba(0,0,0,0.08);
                color: #1a1a1a;
            }
            QListWidget#DiscoveryMachines, QScrollArea#DiscoveryList {
                background-color: rgba(0,0,0,0.03);
                color: #1a1a1a;
                border: 1px solid rgba(0,0,0,0.08);
                border-radius: 8px;
                outline: none;
            }
            QWidget#DiscoveryListHost { background: transparent; }
            QListWidget#DiscoveryMachines::item { padding: 10px; }
            QListWidget#DiscoveryMachines::item:selected {
                background-color: rgba(0,0,0,0.08);
            }
            QScrollArea#DeviceList, QWidget#DeviceListHost {
                background: transparent;
                border: none;
            }
            QFrame#DeviceRow {
                background: transparent;
                border: none;
                border-radius: 6px;
            }
            QFrame#DeviceRow[selected="true"] {
                background-color: rgba(0,0,0,0.08);
            }
            QLabel#PairingCode {
                color: #1a1a1a;
                font-size: 20px;
                font-weight: 600;
                padding: 4px 0px;
            }
            QPushButton#FlyoutDestructiveBtn {
                color: #a4262c;
            }
            QPushButton#FlyoutDestructiveBtn:disabled {
                color: rgba(0,0,0,0.38);
            }
            QLabel#BackendName {
                color: #1a1a1a;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#BackendReloadTime {
                color: #6b6b6b;
                font-size: 10px;
                font-weight: 600;
            }
            QLabel#BackendDetail {
                color: #6b6b6b;
                font-size: 11px;
            }
            QLabel#BackendDetail a {
                color: #0b57d0;
                text-decoration: none;
            }
            QLabel#BackendDetail a:hover {
                text-decoration: underline;
            }
            QLabel#BackendStatus {
                font-size: 11px;
                font-weight: 600;
                padding: 3px 10px;
                border-radius: 999px;
            }
            QLabel#BackendStatus[status="running"] {
                color: #1f7a3a;
                background-color: rgba(80, 180, 110, 0.18);
            }
            QLabel#BackendStatus[status="starting"] {
                color: #8a6a10;
                background-color: rgba(200, 160, 50, 0.20);
            }
            QLabel#BackendStatus[status="stopped"] {
                color: rgba(0,0,0,0.55);
                background-color: rgba(0,0,0,0.06);
            }
            QLabel#BackendStatus[status="error"] {
                color: #a03030;
                background-color: rgba(200, 70, 70, 0.16);
            }
            QPushButton {
                background-color: rgba(0, 0, 0, 0.04);
                color: #1a1a1a;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 6px;
                padding: 6px 10px;
                min-height: 18px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.08);
            }
            QPushButton:pressed {
                background-color: rgba(0, 0, 0, 0.04);
            }
            QPushButton:disabled {
                color: rgba(0, 0, 0, 0.40);
            }
            QPushButton#FlyoutRestartBtn {
                min-width: 72px;
                max-width: 88px;
                padding: 4px 8px;
            }
            QPushButton#FlyoutDebugBtn {
                min-height: 22px;
                padding: 6px 10px;
            }
            QPushButton#FlyoutNavBtn {
                min-height: 22px;
                padding: 8px 10px;
                text-align: left;
            }
            QPushButton#FlyoutBackBtn {
                min-width: 56px;
                max-width: 72px;
                padding: 4px 8px;
                background-color: transparent;
                border: 1px solid transparent;
            }
            QPushButton#FlyoutBackBtn:hover {
                background-color: rgba(0, 0, 0, 0.06);
                border: 1px solid rgba(0, 0, 0, 0.08);
            }
            QPushButton#FlyoutPrimaryBtn {
                background-color: #007aff;
                border: 1px solid #007aff;
                color: white;
                font-weight: 600;
            }
            QPushButton#FlyoutPrimaryBtn:hover { background-color: #3395ff; }
            QPushButton#FlyoutPrimaryBtn:pressed { background-color: #0063cc; }
            QPushButton#FlyoutSecondaryBtn {
                min-width: 32px;
                max-width: 32px;
                min-height: 28px;
                max-height: 28px;
                padding: 0px;
                background-color: transparent;
                border: 1px solid transparent;
            }
            QPushButton#FlyoutSecondaryBtn:hover {
                background-color: rgba(0, 0, 0, 0.06);
                border: 1px solid rgba(0, 0, 0, 0.08);
            }
            QPushButton#FlyoutSecondaryBtn:checked {
                background-color: rgba(0, 0, 0, 0.10);
                border: 1px solid rgba(0, 0, 0, 0.12);
            }
            QPushButton#FlyoutSecondaryBtn:disabled {
                background-color: transparent;
                border: 1px solid transparent;
            }
            QPushButton#UacApprove {
                background-color: #007aff;
                border: 1px solid #007aff;
                color: white;
            }
            QPushButton#UacApprove:hover { background-color: #3395ff; }
            QPushButton#UacApprove:disabled {
                background-color: rgba(0, 122, 255, 0.28);
                color: rgba(0, 0, 0, 0.45);
            }
        """


def flyout_panel_fill_color(dark: bool) -> QColor:
    return QColor("#2d2d2d") if dark else QColor("#ffffff")


def flyout_acrylic_overlay_fill(dark: bool) -> QColor:
    if dark:
        return QColor(32, 32, 32, 88)
    return QColor(252, 252, 252, 110)


def _flyout_accent_vivid(r: int, g: int, b: int, alpha: int) -> QColor:
    c = QColor(r, g, b)
    h, s, v, _a = c.getHsvF()
    if s > 0.0:
        s = min(1.0, s * 1.62)
        v = min(1.0, v * 1.06)
        c.setHsvF(h, s, v, alpha / 255.0)
    else:
        c.setAlpha(alpha)
    return c


def flyout_acrylic_accent_overlay(dark: bool) -> QColor | None:
    rgb = get_windows_accent_rgb()
    if rgb is None:
        return None
    r, g, b = rgb
    a = 76 if dark else 92
    return _flyout_accent_vivid(r, g, b, a)


def flyout_panel_border_color(dark: bool) -> QColor:
    return QColor(255, 255, 255, 23) if dark else QColor(0, 0, 0, 20)
