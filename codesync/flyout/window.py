"""Remote Islands tray panel, with CodeSync pages inside it."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    Property,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..paths import home_dir, is_frozen
from ..sync.node import Node
from .constants import (
    APP_DISPLAY_NAME,
    FLYOUT_PANEL_FIXED_WIDTH,
    TRAY_FLYOUT_SLIDE_CLOSE_DURATION_MS,
    TRAY_FLYOUT_SLIDE_CLOSE_EASING,
    TRAY_FLYOUT_SLIDE_FADE_IN_MS,
    TRAY_FLYOUT_SLIDE_FADE_OUT_MS,
    TRAY_FLYOUT_SLIDE_OFFSET_MIN_PX,
    TRAY_FLYOUT_SLIDE_OFFSET_SCALE_PX,
    TRAY_FLYOUT_SLIDE_OPEN_DURATION_MS,
    TRAY_FLYOUT_SLIDE_OPEN_EASING,
    TRAY_PEEK_SLIDE_CLOSE_DURATION_MS,
    TRAY_PEEK_SLIDE_CLOSE_EASING,
    TRAY_PEEK_SLIDE_FADE_IN_MS,
    TRAY_PEEK_SLIDE_FADE_OUT_MS,
    TRAY_PEEK_SLIDE_OFFSET_MIN_PX,
    TRAY_PEEK_SLIDE_OFFSET_SCALE_PX,
    TRAY_PEEK_SLIDE_OPEN_DURATION_MS,
    TRAY_PEEK_SLIDE_OPEN_EASING,
    flyout_panel_font,
)
from .slide_fade import close_slide_parallel_group, open_slide_parallel_group
from .styles import (
    flyout_acrylic_accent_overlay,
    flyout_acrylic_overlay_fill,
    flyout_panel_border_color,
    flyout_panel_fill_color,
    flyout_stylesheet,
    is_dark_theme,
)
from .win32_dwm import try_apply_flyout_acrylic
from .win32_taskbar import taskbar_safe_available_geometry


def _sheet(dark: bool) -> str:
    extra_dark = """
        QLineEdit {
            color: #f3f3f3;
            background: rgba(255,255,255,0.08);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 6px;
            padding: 6px;
        }
        QScrollArea { background: transparent; border: none; }
        QScrollArea > QWidget > QWidget { background: transparent; }
    """
    extra_light = """
        QLineEdit {
            color: #1a1a1a;
            background: rgba(0,0,0,0.04);
            border: 1px solid rgba(0,0,0,0.10);
            border-radius: 6px;
            padding: 6px;
        }
        QScrollArea { background: transparent; border: none; }
        QScrollArea > QWidget > QWidget { background: transparent; }
    """
    return flyout_stylesheet(dark) + (extra_dark if dark else extra_light)


class StudioFlyoutPanel(QWidget):
    """Shared tray panel styling, positioning, focus handling and animation."""

    windowAnchorPosition = Property(QPoint, QWidget.windowAnchorPosition, QWidget.setWindowAnchorPosition)

    _open_ms = (
        TRAY_FLYOUT_SLIDE_OPEN_DURATION_MS,
        TRAY_FLYOUT_SLIDE_OPEN_EASING,
        TRAY_FLYOUT_SLIDE_FADE_IN_MS,
        TRAY_FLYOUT_SLIDE_OFFSET_MIN_PX,
        TRAY_FLYOUT_SLIDE_OFFSET_SCALE_PX,
    )
    _close_ms = (
        TRAY_FLYOUT_SLIDE_CLOSE_DURATION_MS,
        TRAY_FLYOUT_SLIDE_CLOSE_EASING,
        TRAY_FLYOUT_SLIDE_FADE_OUT_MS,
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._win11_acrylic_backdrop = False
        self._deactivate_close_after = 0.0
        self._flyout_open_slide_scheduled = False
        self._flyout_slide_anim_state: str | None = None
        self._flyout_open_anim = None
        self._flyout_close_anim = None
        self._suspend = False
        self.setObjectName("FlyoutRoot")
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        if (
            self.metaObject().indexOfProperty("windowAnchorPosition") < 0
            or not self.setProperty("windowAnchor", QPointF(1, 1))
        ):
            raise RuntimeError("CodeSync needs its patched Qt runtime. Run build.bat to build it.")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFixedWidth(FLYOUT_PANEL_FIXED_WIDTH)
        self.setFont(flyout_panel_font())
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._title = QLabel(APP_DISPLAY_NAME)
        self._title.setObjectName("Title")
        self._title.setMinimumWidth(0)
        version = QLabel(__version__)
        version.setObjectName("Version")
        self._version = version
        header.addWidget(self._title, 1)
        header.addWidget(version, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)
        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFrameShape(QFrame.Shape.NoFrame)
        divider.setFixedHeight(1)
        root.addWidget(divider)
        self._apply_theme()
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)

    def _apply_theme(self) -> None:
        self.setStyleSheet(_sheet(is_dark_theme()))
        self.update()
        if self.isVisible():
            self._sync_win11_backdrop()

    def _sync_win11_backdrop(self) -> None:
        self._win11_acrylic_backdrop = try_apply_flyout_acrylic(self, is_dark_theme())
        self.update()

    def paintEvent(self, _event) -> None:
        radius = 10.0
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        dark = is_dark_theme()
        if self._win11_acrylic_backdrop:
            painter.fillPath(path, flyout_acrylic_overlay_fill(dark))
            accent = flyout_acrylic_accent_overlay(dark)
            if accent is not None:
                painter.fillPath(path, accent)
        else:
            painter.fillPath(path, flyout_panel_fill_color(dark))
        painter.setPen(QPen(flyout_panel_border_color(dark)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _slide_offset_px(self) -> int:
        _, _, _, minimum, scale = self._open_ms
        screen = self.screen()
        ratio = float(screen.devicePixelRatio()) if screen is not None else 1.0
        return max(minimum, int(round(scale * ratio)))

    def _stop_slide_animations(self) -> None:
        for name in ("_flyout_open_anim", "_flyout_close_anim"):
            anim = getattr(self, name, None)
            if anim is None:
                continue
            try:
                anim.stop()
                anim.deleteLater()
            except RuntimeError:
                pass
            setattr(self, name, None)

    def _tray_anchor(self) -> QPoint:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return self.property("windowAnchorPosition")
        available = taskbar_safe_available_geometry(screen, screen.availableGeometry())
        margin = 10
        right = max(available.left() + margin + self.width(), available.right() + 1 - margin)
        return QPoint(right, available.bottom() + 1 - margin)

    def showEvent(self, event) -> None:
        self._refresh_version()
        self._flyout_open_slide_scheduled = True
        self.setWindowOpacity(0.0)
        self._apply_theme()
        self._deactivate_close_after = time.monotonic() + 0.5
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_win11_backdrop)
        QTimer.singleShot(0, self._after_show_slide)

    def _refresh_version(self) -> None:
        from ..sync.update import read_version

        self._version.setText(read_version())

    def _after_show_slide(self) -> None:
        if not self._flyout_open_slide_scheduled or self.isHidden():
            self.setWindowOpacity(1.0)
            return
        self._flyout_slide_anim_state = "opening"
        self._flyout_open_slide_scheduled = False
        self.adjustSize()
        self._stop_slide_animations()
        end = self._tray_anchor()
        start = end + QPoint(0, self._slide_offset_px())
        self.setProperty("windowAnchorPosition", start)
        duration, easing, fade, _, _ = self._open_ms
        group = open_slide_parallel_group(
            self,
            start=start,
            end=end,
            slide_duration_ms=duration,
            slide_easing=easing,
            fade_in_ms=fade,
        )
        group.finished.connect(self._open_slide_finished)
        self._flyout_open_anim = group
        group.start()

    def _open_slide_finished(self) -> None:
        self._flyout_open_anim = None
        self._flyout_slide_anim_state = None
        self.setWindowOpacity(1.0)

    def _start_close_slide(self) -> None:
        self._stop_slide_animations()
        self._flyout_slide_anim_state = "closing"
        current = self.property("windowAnchorPosition")
        end = current + QPoint(0, self._slide_offset_px())
        duration, easing, fade = self._close_ms
        group = close_slide_parallel_group(
            self,
            start=current,
            end=end,
            slide_duration_ms=duration,
            slide_easing=easing,
            fade_out_ms=fade,
        )
        group.finished.connect(self._close_slide_finished)
        self._flyout_close_anim = group
        group.start()

    def _close_slide_finished(self) -> None:
        self._flyout_close_anim = None
        self._flyout_slide_anim_state = None
        QWidget.hide(self)

    def hide(self) -> None:
        if not self.isVisible():
            return
        self._flyout_open_slide_scheduled = False
        state = self._flyout_slide_anim_state
        if state == "closing":
            return
        if state == "opening":
            self._stop_slide_animations()
            self._flyout_slide_anim_state = None
        self._start_close_slide()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() != QEvent.Type.ActivationChange or self._suspend:
            return
        if self.isActiveWindow():
            return
        active = QApplication.activeWindow()
        if QApplication.activePopupWidget() is not None or (active is not None and self.isAncestorOf(active)):
            return
        if time.monotonic() < self._deactivate_close_after:
            return
        if self.isVisible():
            self.hide()

    @Slot()
    def toggle_focused(self) -> None:
        if self.isVisible():
            self.hide()
            return
        self.adjustSize()
        self.show()
        self.raise_()
        self.activateWindow()

    @Slot()
    def dismiss(self) -> None:
        self._flyout_open_slide_scheduled = False
        self._stop_slide_animations()
        self._flyout_slide_anim_state = None
        QWidget.hide(self)


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("FlyoutSectionTitle")
    return label


class _ElidedLabel(QLabel):
    """One-line label that shrinks and elides instead of widening the panel."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if text:
            self.setText(text)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(0)
        return hint

    def setText(self, text: str) -> None:
        self._full = text
        self._elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._elide()

    def _elide(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            shown = self._full
        else:
            shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, width)
        if self.text() != shown:
            QLabel.setText(self, shown)
        self.setToolTip(self._full if shown != self._full else "")


class _WrappingLabel(QLabel):
    """Wraps long paths on separators without reporting a huge minimum width."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setWordWrap(True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if text:
            self.setText(text)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        # A zero width during adjustSize would wrap one character per line.
        width = width if width >= 80 else 240
        return self.fontMetrics().boundingRect(0, 0, width, 10_000, int(Qt.TextFlag.TextWordWrap), self.text()).height()

    def setText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        shown = text.replace("\\", "\\\u200b").replace("/", "/\u200b")
        QLabel.setText(self, shown)


def _peer_names(share: dict) -> list[str]:
    return [str(name) for name in share.get("peers") or [] if name]


def _compact_summary(share: dict) -> str:
    peers = _peer_names(share)
    if peers:
        count = len(peers)
        return "1 connected" if count == 1 else f"{count} connected"
    detail = str(share.get("detail") or "")
    if detail == "Waiting for someone to open the link":
        return "Waiting"
    if detail:
        return detail
    return str(share.get("root") or "")


def _peek_summary(share: dict) -> str:
    summary = _compact_summary(share)
    if ":\\" in summary or summary.startswith("\\\\") or summary.startswith("/"):
        return str(share.get("status") or "")
    return summary


_IGNORE_PAGE = 3
_TREE_PAGE_LIMIT = 2000
_TREE_CHILDREN = Qt.ItemDataRole.UserRole


class _TextBridge(QObject):
    progress = Signal(str)
    done = Signal(str)


class _IgnoreBridge(QObject):
    ready = Signal(str, object)


def _nest_ignored(entries: tuple) -> dict:
    root: dict = {}
    for rel, is_dir in entries:
        cursor = root
        parts = str(rel).split("/")
        for index, part in enumerate(parts):
            child = cursor.get(part)
            if child is None:
                child = {"dir": True, "children": {}}
                cursor[part] = child
            if index == len(parts) - 1:
                child["dir"] = bool(is_dir)
            cursor = child["children"]
    return root


class _IgnoreRow(QFrame):
    def __init__(self, on_open) -> None:
        super().__init__()
        self.setObjectName("BackendRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(0)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel("Gitignore")
        name.setObjectName("BackendName")
        self.count = QLabel("")
        self.count.setObjectName("BackendDetail")
        text.addWidget(name)
        text.addWidget(self.count)
        layout.addLayout(text, 1)
        self._on_open = on_open

    def mousePressEvent(self, event) -> None:
        self._on_open()
        event.accept()


class _ClientRow(QFrame):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.setObjectName("ClientRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)
        dot = QFrame()
        dot.setObjectName("ClientDot")
        dot.setFixedSize(8, 8)
        label = _ElidedLabel(name)
        label.setObjectName("BackendName")
        layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(label, 1)
        self._name = name


def _pill_colors(status: str, dark: bool) -> tuple[QColor, QColor]:
    if dark:
        table = {
            "running": (QColor("#8ee0a8"), QColor(80, 180, 110, 56)),
            "starting": (QColor("#f0d48a"), QColor(200, 160, 50, 56)),
            "stopped": (QColor(255, 255, 255, 158), QColor(255, 255, 255, 22)),
            "error": (QColor("#f0a0a0"), QColor(200, 70, 70, 56)),
        }
    else:
        table = {
            "running": (QColor("#1f7a3a"), QColor(80, 180, 110, 46)),
            "starting": (QColor("#8a6a10"), QColor(200, 160, 50, 51)),
            "stopped": (QColor(0, 0, 0, 140), QColor(0, 0, 0, 16)),
            "error": (QColor("#a03030"), QColor(200, 70, 70, 41)),
        }
    return table.get(status, table["stopped"])


class _StatusPill(QLabel):
    """Capsule status chip. Painted explicitly so the ends stay round."""

    def __init__(self, text: str, status: str) -> None:
        super().__init__(text)
        self._status = status
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)
        self.setContentsMargins(10, 3, 10, 3)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if rect.width() <= 1 or rect.height() <= 1:
            return
        fg, bg = _pill_colors(self._status, is_dark_theme())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        painter.setPen(fg)
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self.text())


class _FolderScrollBar(QScrollBar):
    """Thin overlay thumb. The scroll area's own bar stays hidden."""

    _WIDTH = 14

    def __init__(self, scroll: QScrollArea) -> None:
        super().__init__(Qt.Orientation.Vertical, scroll)
        self._scroll = scroll
        self._hover = False
        self._dragging = False
        self.setFixedWidth(self._WIDTH)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.hide()
        inner = scroll.verticalScrollBar()
        inner.valueChanged.connect(self._pull)
        inner.rangeChanged.connect(self._sync_range)
        self.valueChanged.connect(self._push)

    def dragging(self) -> bool:
        return self._dragging

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self.releaseMouse()
        super().hideEvent(event)

    def wheelEvent(self, event) -> None:
        self._scroll.wheelEvent(event)

    def _pull(self, value: int) -> None:
        if self.value() == value:
            self.update()
            return
        self.blockSignals(True)
        self.setValue(value)
        self.blockSignals(False)
        self.update()

    def _push(self, value: int) -> None:
        inner = self._scroll.verticalScrollBar()
        if inner.value() != value:
            inner.setValue(value)

    def _sync_range(self, low: int, high: int) -> None:
        inner = self._scroll.verticalScrollBar()
        self.blockSignals(True)
        self.setRange(low, high)
        self.setPageStep(inner.pageStep())
        self.setSingleStep(inner.singleStep())
        self.setValue(inner.value())
        self.blockSignals(False)
        self.setVisible(high > low)
        self.place()
        self.update()

    def place(self) -> None:
        host = self._scroll
        if host.width() <= 0 or host.height() <= 0:
            return
        top = 8
        self.setGeometry(host.width() - self._WIDTH - 2, top, self._WIDTH, max(0, host.height() - top * 2))
        if self.isVisible():
            self.raise_()

    def _metrics(self) -> tuple[int, int]:
        extent = self.height()
        page = self.pageStep()
        span = (self.maximum() - self.minimum()) + page
        if extent <= 0 or span <= 0:
            return 0, 0
        handle = int(round(extent * page / span)) if page else 36
        handle = min(extent, max(36, handle))
        return handle, max(0, extent - handle)

    def paintEvent(self, _event) -> None:
        handle, _travel = self._metrics()
        if handle <= 0 or self.maximum() <= self.minimum():
            return
        offset = 0
        travel = self.height() - handle
        if travel > 0 and self.maximum() > self.minimum():
            offset = int(round(travel * (self.value() - self.minimum()) / (self.maximum() - self.minimum())))
        active = self._hover or self._dragging
        thickness = 8.0 if active else 6.0
        if is_dark_theme():
            alpha = 235 if self._dragging else 210 if active else 170
            color = QColor(255, 255, 255, alpha)
        else:
            alpha = 210 if self._dragging else 170 if active else 120
            color = QColor(0, 0, 0, alpha)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        rect = QRectF((self.width() - thickness) / 2.0, float(offset), thickness, float(handle))
        painter.drawRoundedRect(rect, thickness / 2.0, thickness / 2.0)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.grabMouse()
            self._drag_to(event.position().y())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._drag_to(event.position().y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_dragging = self._dragging
        self._dragging = False
        if was_dragging:
            self.releaseMouse()
        self.update()
        snap = getattr(self._scroll, "snap", None)
        if was_dragging and snap is not None:
            snap()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _drag_to(self, y: float) -> None:
        handle, travel = self._metrics()
        if handle <= 0:
            return
        offset = min(max(0.0, y - handle / 2.0), float(travel))
        if travel <= 0 or self.maximum() == self.minimum():
            self.setValue(self.minimum())
            return
        span = self.maximum() - self.minimum()
        self.setValue(int(round(self.minimum() + (offset / travel) * span)))


class _FolderList(QScrollArea):
    """Folder rows. The viewport stays a whole number of cards, with an overlay thumb."""

    _CAP = 360
    _GUTTER = 18

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("FolderList")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setMaximumHeight(self._CAP)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        viewport = self.viewport()
        viewport.setAutoFillBackground(False)
        viewport.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        host = QWidget()
        host.setObjectName("FolderListHost")
        host.setMinimumWidth(0)
        host.setAutoFillBackground(False)
        self._rows = QVBoxLayout(host)
        self._rows.setContentsMargins(0, 2, 1, 6)
        self._rows.setSpacing(6)
        self.setWidget(host)
        self._bar = _FolderScrollBar(self)
        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.timeout.connect(self.snap)
        inner = self.verticalScrollBar()
        inner.rangeChanged.connect(self._apply_gutter)
        inner.setSingleStep(24)

    @property
    def rows(self) -> QVBoxLayout:
        return self._rows

    def wheelEvent(self, event) -> None:
        step, _last = self._row_metrics()
        notches = int(event.angleDelta().y() / 120) if step else 0
        if notches:
            bar = self.verticalScrollBar()
            target = bar.value() - notches * step
            bar.setValue(max(bar.minimum(), min(bar.maximum(), target)))
            event.accept()
            return
        super().wheelEvent(event)
        self._snap_timer.start(160)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._bar.place()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.fit_rows()
        self._bar.place()

    def _row_metrics(self) -> tuple[int, int]:
        layout = self._rows
        spacing = layout.spacing()
        top = layout.contentsMargins().top()
        height = 0
        count = 0
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if isinstance(widget, _ShareRow):
                hint = widget.sizeHint().height()
                height = max(height, hint)
                count += 1
        if count == 0 or height <= 0:
            return 0, 0
        step = height + spacing
        last_bottom = top + count * height + max(0, count - 1) * spacing
        return step, last_bottom

    def fit_rows(self) -> None:
        step, last_bottom = self._row_metrics()
        if step <= 0 or last_bottom <= self._CAP:
            hint = self.widget().sizeHint().height() if self.widget() is not None else 0
            extra = min(24, max(0, hint - last_bottom))
            needed = last_bottom + extra
            cap = needed if needed > self._CAP else self._CAP
            if self.minimumHeight() != 0 or self.maximumHeight() != cap:
                self.setMinimumHeight(0)
                self.setMaximumHeight(cap)
            self._bar.place()
            return
        top = self._rows.contentsMargins().top()
        visible = max(1, (self._CAP - top) // step)
        height = top + visible * step
        if height > self._CAP:
            height = self._CAP
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)
        self.snap()
        self._bar.place()

    def snap(self) -> None:
        if self._bar.dragging():
            return
        step, _last = self._row_metrics()
        bar = self.verticalScrollBar()
        if step <= 0 or bar.maximum() <= bar.minimum():
            return
        value = int((bar.value() + step // 2) // step) * step
        value = min(bar.maximum(), max(bar.minimum(), value))
        if value != bar.value():
            bar.setValue(value)

    def _apply_gutter(self, _low: int, high: int) -> None:
        right = self._GUTTER if high > 0 else 1
        current = self._rows.contentsMargins()
        if current.right() != right:
            self._rows.setContentsMargins(current.left(), current.top(), right, current.bottom())
        self._bar.place()


class _ShareRow(QFrame):
    _RADIUS = 12.0

    def __init__(self, share: dict, on_open) -> None:
        super().__init__()
        self.setObjectName("FolderCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(0)
        self._hover = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = _ElidedLabel(share["name"])
        name.setObjectName("BackendName")
        summary = _compact_summary(share)
        detail = _ElidedLabel(summary)
        detail.setObjectName("ConnectedCount" if summary.endswith(" connected") else "BackendDetail")
        text.addWidget(name)
        text.addWidget(detail)
        layout.addLayout(text, 1)
        layout.addWidget(_StatusPill(share["status"], share["pill"]), 0, Qt.AlignmentFlag.AlignVCenter)
        self._on_open = on_open
        self._share_id = share["id"]

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(1.0, 1.0, max(0.0, self.width() - 2.0), max(0.0, self.height() - 2.0))
        if rect.width() <= 2 or rect.height() <= 2:
            painter.end()
            return
        dark = is_dark_theme()
        if dark:
            fill = QColor(255, 255, 255, 22 if self._hover else 12)
            border = QColor(255, 255, 255, 40 if self._hover else 24)
        else:
            fill = QColor(0, 0, 0, 18 if self._hover else 10)
            border = QColor(0, 0, 0, 36 if self._hover else 22)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)

    def mousePressEvent(self, event) -> None:
        self._on_open(self._share_id)
        event.accept()


class MainFlyout(StudioFlyoutPanel):
    def __init__(self, node: Node) -> None:
        super().__init__()
        self._node = node
        self._selected = ""
        self._signature: tuple = ()
        self._client_signature: tuple = ()
        self._join_folder = ""
        self._ignore_running: set[str] = set()
        self._ignore_token: tuple = ()
        self._ignore_bridge = _IgnoreBridge(self)
        self._ignore_bridge.ready.connect(self._ignore_ready)
        stack = QStackedWidget()
        self._stack = stack
        stack.addWidget(self._build_list())
        stack.addWidget(self._build_join())
        stack.addWidget(self._build_detail())
        stack.addWidget(self._build_ignored())
        self.layout().addWidget(stack)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(400)
        self.refresh()

    def _build_list(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(_section("Folders"))
        self._empty = QLabel("No synced folders yet")
        self._empty.setObjectName("BackendDetail")
        self._empty.setWordWrap(True)
        scroll = _FolderList()
        self._list = scroll
        self._rows = scroll.rows
        self._rows.addWidget(self._empty)
        self._rows.addStretch(1)
        layout.addWidget(scroll)
        self._list_error = QLabel("")
        self._list_error.setObjectName("BackendDetail")
        self._list_error.setWordWrap(True)
        self._list_error.setMinimumWidth(0)
        self._list_error.hide()
        layout.addWidget(self._list_error)
        sync = QPushButton("Sync a folder")
        sync.setObjectName("FlyoutPrimaryBtn")
        sync.clicked.connect(self._choose_folder)
        join = QPushButton("Join with link")
        join.clicked.connect(self._show_join)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(sync, 1)
        actions.addWidget(join, 1)
        layout.addLayout(actions)
        return page

    def _build_join(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        back = QPushButton("Back")
        back.setObjectName("FlyoutBackBtn")
        back.clicked.connect(self._show_list)
        self._join_link = QLineEdit()
        self._join_link.setPlaceholderText("codesync://…")
        self._join_link.setMinimumWidth(0)
        self._join_path = _WrappingLabel("No folder selected")
        self._join_path.setObjectName("BackendDetail")
        browse = QPushButton("Choose local folder")
        browse.clicked.connect(self._choose_join_folder)
        start = QPushButton("Start syncing")
        start.setObjectName("FlyoutPrimaryBtn")
        start.clicked.connect(self._start_join)
        self._join_error = _WrappingLabel("")
        self._join_error.setObjectName("BackendDetail")
        self._join_error.hide()
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(_section("Link"))
        layout.addWidget(self._join_link)
        layout.addWidget(_section("Folder on this PC"))
        layout.addWidget(self._join_path)
        layout.addWidget(browse)
        layout.addWidget(start)
        layout.addWidget(self._join_error)
        return page

    def _build_detail(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        back = QPushButton("Back")
        back.setObjectName("FlyoutBackBtn")
        back.clicked.connect(self._show_list)
        self._detail_status = QLabel("")
        self._detail_status.setObjectName("BackendStatus")
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        header.addStretch(1)
        header.addWidget(self._detail_status, 0, Qt.AlignmentFlag.AlignRight)
        self._detail_path = _WrappingLabel("")
        self._detail_path.setObjectName("BackendDetail")
        self._detail_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._clients_box = QWidget()
        self._clients = QVBoxLayout(self._clients_box)
        self._clients.setContentsMargins(0, 0, 0, 0)
        self._clients.setSpacing(4)
        self._detail_text = _WrappingLabel("")
        self._detail_text.setObjectName("BackendDetail")
        self._detail_link = QLineEdit()
        self._detail_link.setReadOnly(True)
        self._detail_link.setMinimumWidth(0)
        copy = QPushButton("Copy")
        copy.setObjectName("FlyoutRestartBtn")
        copy.clicked.connect(self._copy_link)
        link_row = QHBoxLayout()
        link_row.setSpacing(6)
        link_row.addWidget(self._detail_link, 1)
        link_row.addWidget(copy)
        self._trust_card = QFrame()
        self._trust_card.setObjectName("NoticeCard")
        trust_layout = QVBoxLayout(self._trust_card)
        trust_layout.setContentsMargins(10, 8, 10, 8)
        trust_layout.setSpacing(6)
        trust_layout.addWidget(_section("Fingerprint"))
        self._fingerprint = QLabel("")
        self._fingerprint.setObjectName("BackendDetail")
        self._fingerprint.setWordWrap(True)
        self._fingerprint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._trust = QPushButton("Trust this PC")
        self._trust.setObjectName("FlyoutPrimaryBtn")
        self._trust.clicked.connect(self._accept)
        trust_layout.addWidget(self._fingerprint)
        trust_layout.addWidget(self._trust)
        self._conflict_card = QFrame()
        self._conflict_card.setObjectName("NoticeCard")
        self._conflict_card.setProperty("kind", "danger")
        self._conflict_card.style().unpolish(self._conflict_card)
        self._conflict_card.style().polish(self._conflict_card)
        conflict_layout = QVBoxLayout(self._conflict_card)
        conflict_layout.setContentsMargins(10, 8, 10, 8)
        conflict_layout.setSpacing(6)
        conflict_layout.addWidget(_section("Kept both copies"))
        self._conflicts = _WrappingLabel("")
        self._conflicts.setObjectName("BackendDetail")
        dismiss = QPushButton("Dismiss")
        dismiss.clicked.connect(self._dismiss)
        conflict_layout.addWidget(self._conflicts)
        conflict_layout.addWidget(dismiss)
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(self._open_folder)
        self._pause = QPushButton("Pause")
        self._pause.clicked.connect(self._toggle_pause)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addWidget(open_folder)
        actions.addWidget(self._pause)
        another = QPushButton("Sync another folder")
        another.clicked.connect(self._sync_another)
        stop = QPushButton("Stop sharing")
        stop.setObjectName("FlyoutDestructiveBtn")
        stop.clicked.connect(self._stop_share)
        layout.addLayout(header)
        layout.addWidget(_section("Folder"))
        layout.addWidget(self._detail_path)
        self._ignore_row = _IgnoreRow(self._show_ignored)
        self._ignore_row.hide()
        layout.addWidget(self._ignore_row)
        layout.addWidget(_section("Connected"))
        layout.addWidget(self._clients_box)
        layout.addWidget(self._detail_text)
        layout.addWidget(self._trust_card)
        layout.addWidget(self._conflict_card)
        layout.addWidget(_section("Share link"))
        layout.addLayout(link_row)
        layout.addLayout(actions)
        layout.addWidget(another)
        layout.addSpacing(4)
        layout.addWidget(stop)
        self._trust_card.hide()
        self._conflict_card.hide()
        self._clients_box.hide()
        return page

    def _build_ignored(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        back = QPushButton("Back")
        back.setObjectName("FlyoutBackBtn")
        back.clicked.connect(self._show_detail_page)
        self._ignore_status = QLabel("")
        self._ignore_status.setObjectName("BackendDetail")
        self._ignore_status.setWordWrap(True)
        self._ignore_tree = QTreeWidget()
        self._ignore_tree.setObjectName("IgnoreTree")
        self._ignore_tree.setHeaderHidden(True)
        self._ignore_tree.setUniformRowHeights(True)
        self._ignore_tree.setAnimated(False)
        self._ignore_tree.setIndentation(14)
        self._ignore_tree.setMinimumWidth(0)
        self._ignore_tree.setMinimumHeight(140)
        self._ignore_tree.setMaximumHeight(320)
        self._ignore_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._ignore_tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._ignore_tree.itemExpanded.connect(self._ignore_expand)
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._ignore_status)
        layout.addWidget(self._ignore_tree)
        return page

    def prefill_join(self, link: str) -> None:
        self._join_link.setText(link)
        self._set_join_error("")
        self._show_join()
        if not self.isVisible():
            self.show()
            self.raise_()
            self.activateWindow()
        else:
            self.raise_()
            self.activateWindow()

    def refresh(self) -> None:
        try:
            shares = self._node.list_shares()
        except Exception:
            return
        signature = tuple(
            (item["id"], item["status"], item["detail"], item["name"], tuple(item.get("peers") or []))
            for item in shares
        )
        if signature != self._signature:
            self._signature = signature
            self._rebuild(shares)
        if self._stack.currentIndex() == 2:
            self._fill_detail(shares)
        elif self._stack.currentIndex() == _IGNORE_PAGE:
            self._refresh_ignore_page()

    def _rebuild(self, shares: list[dict]) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._empty = QLabel("No synced folders yet")
        self._empty.setObjectName("BackendDetail")
        self._empty.setWordWrap(True)
        self._empty.setVisible(not shares)
        self._rows.addWidget(self._empty)
        for share in shares:
            self._rows.addWidget(_ShareRow(share, self._open_detail))
        self._rows.addStretch(1)
        self._list.fit_rows()
        self.adjustSize()

    def _fill_detail(self, shares: list[dict]) -> None:
        share = next((item for item in shares if item["id"] == self._selected), None)
        if share is None:
            self._show_list()
            return
        self._title.setText(share["name"])
        self._detail_status.setText(share["status"])
        self._detail_status.setProperty("status", share["pill"])
        self._detail_status.style().unpolish(self._detail_status)
        self._detail_status.style().polish(self._detail_status)
        self._detail_path.setText(share["root"])
        peers = _peer_names(share)
        self._render_clients(peers)
        if peers or share["pending_fp"]:
            self._detail_text.hide()
        else:
            waiting = share["detail"] or "No other PCs connected"
            if waiting.endswith(" connected"):
                waiting = "No other PCs connected"
            self._detail_text.setText(waiting)
            self._detail_text.show()
        if self._detail_link.text() != share["link"]:
            self._detail_link.setText(share["link"])
        if share["pending_fp"]:
            self._fingerprint.setText(share["pending_fp"])
            self._trust_card.show()
        else:
            self._trust_card.hide()
        if share["conflicts"]:
            self._conflicts.setText("\n".join(share["conflicts"]))
            self._conflict_card.show()
        else:
            self._conflict_card.hide()
        self._pause.setText("Resume" if share["paused"] else "Pause")
        self._refresh_ignore_row(share["id"])

    def _refresh_ignore_row(self, share_id: str) -> None:
        cached = self._node.ignore_cached(share_id)
        if cached is None:
            self._ignore_row.hide()
            self._schedule_ignore(share_id)
            return
        self._apply_ignore_row(cached)

    def _refresh_ignore_page(self) -> None:
        if not self._selected:
            return
        cached = self._node.ignore_cached(self._selected)
        if cached is None:
            self._schedule_ignore(self._selected)
            return
        self._render_ignore_tree(cached)

    def _schedule_ignore(self, share_id: str) -> None:
        if not share_id or share_id in self._ignore_running:
            return
        if self._node.ignore_cached(share_id) is not None:
            return
        self._ignore_running.add(share_id)

        def work() -> None:
            try:
                result = self._node.ignore_listing(share_id)
            except Exception:
                result = None
            self._ignore_bridge.ready.emit(share_id, result)

        threading.Thread(target=work, name="codesync-ignore", daemon=True).start()

    def _ignore_ready(self, share_id: str, result) -> None:
        self._ignore_running.discard(share_id)
        if share_id != self._selected:
            return
        page = self._stack.currentIndex()
        if page == 2:
            self._apply_ignore_row(result)
        elif page == _IGNORE_PAGE:
            self._render_ignore_tree(result)

    def _apply_ignore_row(self, result: dict | None) -> None:
        show = bool(result and result.get("detected"))
        if show and result is not None:
            count = int(result["count"])
            if count == 0:
                text = "Nothing ignored"
            elif count == 1:
                text = "1 ignored"
            else:
                text = f"{count} ignored"
            changed = self._ignore_row.isHidden() or self._ignore_row.count.text() != text
            self._ignore_row.count.setText(text)
            self._ignore_row.show()
        else:
            changed = not self._ignore_row.isHidden()
            self._ignore_row.hide()
        if changed and self.isVisible() and self._stack.currentIndex() == 2:
            self.adjustSize()

    def _show_ignored(self) -> None:
        self._title.setText("Ignored")
        self._stack.setCurrentIndex(_IGNORE_PAGE)
        cached = self._node.ignore_cached(self._selected)
        if cached is None:
            self._ignore_token = ()
            self._ignore_status.setText("Reading…")
            self._ignore_tree.hide()
            self._schedule_ignore(self._selected)
        else:
            self._render_ignore_tree(cached)
        self.adjustSize()

    def _show_detail_page(self) -> None:
        self._stack.setCurrentIndex(2)
        self.refresh()
        self.adjustSize()

    def _render_ignore_tree(self, result: dict | None) -> None:
        if not result or not result.get("detected"):
            self._show_detail_page()
            return
        entries = result.get("entries") or ()
        token = (self._selected, id(entries))
        count = int(result.get("count") or 0)
        if count == 0:
            self._ignore_status.setText("Nothing matches these rules")
        elif count == 1:
            self._ignore_status.setText("1 ignored path")
        else:
            self._ignore_status.setText(f"{count} ignored paths")
        self._ignore_tree.setVisible(count > 0)
        if token == self._ignore_token:
            return
        self._ignore_token = token
        self._ignore_tree.setUpdatesEnabled(False)
        self._ignore_tree.clear()
        if count:
            self._populate_ignore(None, _nest_ignored(entries))
        self._ignore_tree.setUpdatesEnabled(True)
        if self.isVisible():
            self.adjustSize()

    def _populate_ignore(self, parent: QTreeWidgetItem | None, children: dict) -> None:
        names = sorted(children, key=lambda name: (not children[name]["dir"], name.casefold()))
        extra = 0
        if len(names) > _TREE_PAGE_LIMIT:
            extra = len(names) - _TREE_PAGE_LIMIT
            names = names[:_TREE_PAGE_LIMIT]
        items: list[QTreeWidgetItem] = []
        for name in names:
            node = children[name]
            item = QTreeWidgetItem([name])
            kids = node["children"]
            if kids:
                item.setData(0, _TREE_CHILDREN, kids)
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            items.append(item)
        if extra:
            more = QTreeWidgetItem([f"+{extra} more"])
            more.setDisabled(True)
            items.append(more)
        if parent is None:
            self._ignore_tree.addTopLevelItems(items)
        else:
            parent.addChildren(items)

    def _ignore_expand(self, item: QTreeWidgetItem) -> None:
        kids = item.data(0, _TREE_CHILDREN)
        if not kids or item.childCount():
            return
        self._ignore_tree.setUpdatesEnabled(False)
        self._populate_ignore(item, kids)
        item.setData(0, _TREE_CHILDREN, None)
        self._ignore_tree.setUpdatesEnabled(True)

    def _render_clients(self, peers: list[str]) -> None:
        wanted = list(peers)
        signature = tuple(wanted)
        if signature == self._client_signature:
            self._clients_box.setVisible(bool(wanted))
            return
        self._client_signature = signature
        self._clients_box.setUpdatesEnabled(False)
        try:
            buckets: dict[str, list[_ClientRow]] = {}
            for index in range(self._clients.count()):
                widget = self._clients.itemAt(index).widget()
                if isinstance(widget, _ClientRow):
                    buckets.setdefault(widget._name, []).append(widget)
            ordered: list[_ClientRow] = []
            for name in wanted:
                found = buckets.get(name)
                if found:
                    ordered.append(found.pop())
                else:
                    ordered.append(_ClientRow(name))
            for leftover in buckets.values():
                for row in leftover:
                    self._clients.removeWidget(row)
                    row.setParent(None)
                    row.deleteLater()
            for index, row in enumerate(ordered):
                if self._clients.indexOf(row) != index:
                    self._clients.removeWidget(row)
                    self._clients.insertWidget(index, row)
            self._clients_box.setVisible(bool(wanted))
        finally:
            self._clients_box.setUpdatesEnabled(True)
        if self.isVisible():
            self.adjustSize()

    def _show_list(self) -> None:
        self._title.setText(APP_DISPLAY_NAME)
        self._stack.setCurrentIndex(0)
        self.adjustSize()

    def _show_join(self) -> None:
        self._title.setText("Join")
        self._stack.setCurrentIndex(1)
        self.adjustSize()

    def _open_detail(self, share_id: str) -> None:
        self._selected = share_id
        self._client_signature = ()
        self._ignore_token = ()
        self._stack.setCurrentIndex(2)
        self.refresh()
        self.adjustSize()

    def _choose_folder(self) -> None:
        folder = self._pick_folder("Sync a folder")
        if not folder:
            return
        try:
            info = self._node.create_share(Path(folder))
        except Exception as exc:
            self._list_error.setText(str(exc))
            self._list_error.show()
            return
        self._list_error.hide()
        self._signature = ()
        self._open_detail(info["id"])

    def _choose_join_folder(self) -> None:
        folder = self._pick_folder("Folder on this PC")
        if not folder:
            return
        self._join_folder = folder
        self._join_path.setText(folder)

    def _pick_folder(self, title: str) -> str:
        self._suspend = True
        try:
            return QFileDialog.getExistingDirectory(self, title)
        finally:
            self._suspend = False
            self.raise_()
            self.activateWindow()

    def _set_join_error(self, text: str) -> None:
        self._join_error.setText(text)
        self._join_error.setVisible(bool(text))

    def _start_join(self) -> None:
        self._set_join_error("")
        if not self._join_folder:
            self._set_join_error("Choose a local folder first.")
            return
        try:
            info = self._node.join_share(self._join_link.text(), Path(self._join_folder))
        except Exception as exc:
            self._set_join_error(str(exc))
            return
        self._signature = ()
        self._open_detail(info["id"])

    def _selected_share(self) -> dict | None:
        for share in self._node.list_shares():
            if share["id"] == self._selected:
                return share
        return None

    def _sync_another(self) -> None:
        share = self._selected_share()
        if share is None:
            return
        self._join_link.setText(share["link"])
        self._join_folder = ""
        self._join_path.setText("No folder selected")
        self._set_join_error("")
        self._show_join()

    def _copy_link(self) -> None:
        QApplication.clipboard().setText(self._detail_link.text())

    def _open_folder(self) -> None:
        share = self._selected_share()
        if share:
            os.startfile(share["root"])

    def _toggle_pause(self) -> None:
        share = self._selected_share()
        if share:
            self._node.set_paused(share["id"], not share["paused"])
            self._signature = ()
            self.refresh()

    def _dismiss(self) -> None:
        if self._selected:
            self._node.dismiss_conflicts(self._selected)
            self._signature = ()
            self.refresh()

    def _accept(self) -> None:
        if self._selected:
            self._node.accept_fingerprint(self._selected)
            self.refresh()

    def _stop_share(self) -> None:
        if not self._selected:
            return
        self._node.stop_share(self._selected)
        self._selected = ""
        self._signature = ()
        self._show_list()
        self.refresh()


class ControlsFlyout(StudioFlyoutPanel):
    def __init__(self, node: Node, *, on_exit, launch_command: str) -> None:
        super().__init__()
        self._node = node
        self._on_exit = on_exit
        self._command = launch_command
        self._sending = False
        self._startup = QPushButton("")
        self._startup.clicked.connect(self._toggle_startup)
        self._update: QPushButton | None = None
        self._update_status: QLabel | None = None
        if not is_frozen():
            self._update_bridge = _TextBridge(self)
            self._update_bridge.progress.connect(self._update_progress)
            self._update_bridge.done.connect(self._update_finished)
            update = QPushButton("Update connected clients")
            update.clicked.connect(self._send_update)
            self._update = update
            self._update_status = QLabel("")
            self._update_status.setObjectName("BackendDetail")
            self._update_status.setWordWrap(True)
            self._update_status.hide()
        data = QPushButton("Open CodeSync folder")
        data.clicked.connect(lambda: os.startfile(home_dir()))
        exit_button = QPushButton("Exit")
        exit_button.setObjectName("FlyoutDestructiveBtn")
        exit_button.clicked.connect(self._on_exit)
        self.layout().addWidget(self._startup)
        if self._update is not None and self._update_status is not None:
            self.layout().addWidget(self._update)
            self.layout().addWidget(self._update_status)
        self.layout().addWidget(data)
        self.layout().addWidget(exit_button)
        self._refresh_startup()

    def _send_update(self) -> None:
        if is_frozen() or self._update is None or self._update_status is None or self._sending:
            return
        self._sending = True
        self._update.setEnabled(False)
        self._update_status.setText("Building…")
        self._update_status.show()

        def work() -> None:
            try:
                message = self._node.broadcast_upgrade(on_status=self._update_bridge.progress.emit)
            except Exception as exc:
                message = str(exc)
            self._update_bridge.done.emit(message)

        threading.Thread(target=work, name="codesync-upgrade", daemon=True).start()

    def _update_progress(self, message: str) -> None:
        self._refresh_version()
        if self._update_status is None:
            return
        self._update_status.setText(message)
        self._update_status.show()

    def _update_finished(self, message: str) -> None:
        self._sending = False
        self._refresh_version()
        if self._update is None or self._update_status is None:
            return
        self._update.setEnabled(True)
        self._update_status.setText(message)
        self._update_status.show()

    def _run_value(self) -> str:
        return self._command

    def _refresh_startup(self) -> None:
        enabled = _startup_enabled(self._run_value())
        self._startup.setText("Start with Windows: On" if enabled else "Start with Windows: Off")

    def _toggle_startup(self) -> None:
        _set_startup(not _startup_enabled(self._run_value()), self._run_value())
        self._refresh_startup()

    @Slot()
    def toggle_focused(self) -> None:
        self._refresh_startup()
        super().toggle_focused()


class PeekFlyout(StudioFlyoutPanel):
    _open_ms = (
        TRAY_PEEK_SLIDE_OPEN_DURATION_MS,
        TRAY_PEEK_SLIDE_OPEN_EASING,
        TRAY_PEEK_SLIDE_FADE_IN_MS,
        TRAY_PEEK_SLIDE_OFFSET_MIN_PX,
        TRAY_PEEK_SLIDE_OFFSET_SCALE_PX,
    )
    _close_ms = (
        TRAY_PEEK_SLIDE_CLOSE_DURATION_MS,
        TRAY_PEEK_SLIDE_CLOSE_EASING,
        TRAY_PEEK_SLIDE_FADE_OUT_MS,
    )

    def __init__(self, node: Node) -> None:
        super().__init__()
        self._node = node
        self.setFixedWidth(280)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._line = QLabel("In sync")
        self._line.setObjectName("BackendDetail")
        self._line.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._line.setWordWrap(False)
        self._line.setMinimumWidth(0)
        self.layout().addWidget(self._line)
        self._hover_until = 0.0
        timer = QTimer(self)
        timer.timeout.connect(self._poll)
        timer.start(200)

    def changeEvent(self, event) -> None:
        QWidget.changeEvent(self, event)

    def _peek_text(self, shares: list[dict]) -> str:
        if not shares:
            return "No synced folders"
        width = max(self.width(), self.minimumWidth(), 280) - 28
        metrics = self._line.fontMetrics()
        lines = []
        shown = shares[:6]
        for item in shown:
            summary = _peek_summary(item) or str(item.get("status") or "")
            line = f"{item['name']} · {summary}" if summary else str(item["name"])
            lines.append(metrics.elidedText(line, Qt.TextElideMode.ElideRight, width))
        extra = len(shares) - len(shown)
        if extra:
            lines.append(f"+{extra} more")
        return "\n".join(lines)

    @Slot()
    def notify_hover(self) -> None:
        self._hover_until = time.monotonic() + 0.8
        shares = self._node.list_shares()
        self._line.setText(self._peek_text(shares))
        self._title.setText(APP_DISPLAY_NAME)
        self.adjustSize()
        if self.isHidden():
            self.show()
            self.raise_()

    def _poll(self) -> None:
        if not self.isVisible():
            return
        from .win32_dwm import cursor_on_window

        if cursor_on_window(self) or time.monotonic() < self._hover_until:
            return
        self.hide()


def _startup_enabled(command: str) -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
        value, _kind = winreg.QueryValueEx(key, "CodeSync")
    except OSError:
        return False
    return str(value).strip('"').lower() in command.lower() or str(value) == command


def _set_startup(enabled: bool, command: str) -> None:
    if os.name != "nt":
        return
    import winreg

    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    )
    if enabled:
        winreg.SetValueEx(key, "CodeSync", 0, winreg.REG_SZ, command)
    else:
        try:
            winreg.DeleteValue(key, "CodeSync")
        except OSError:
            pass
