"""Parallel slide + window opacity animations for tray flyouts."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
)
from PySide6.QtWidgets import QWidget


def open_slide_parallel_group(
    widget: QWidget,
    *,
    start: QPoint,
    end: QPoint,
    slide_duration_ms: int,
    slide_easing,
    fade_in_ms: int,
) -> QParallelAnimationGroup:
    group = QParallelAnimationGroup(widget)

    pos = QPropertyAnimation(widget, b"windowAnchorPosition", widget)
    pos.setDuration(max(1, slide_duration_ms))
    pos.setStartValue(start)
    pos.setEndValue(end)
    pos.setEasingCurve(slide_easing)
    group.addAnimation(pos)

    fin = max(0, int(fade_in_ms))
    if fin <= 0:
        widget.setWindowOpacity(1.0)
    else:
        eff = min(fin, max(1, slide_duration_ms))
        widget.setWindowOpacity(0.0)
        op = QPropertyAnimation(widget, b"windowOpacity", widget)
        op.setDuration(eff)
        op.setStartValue(0.0)
        op.setEndValue(1.0)
        op.setEasingCurve(QEasingCurve.Type.Linear)
        group.addAnimation(op)

    return group


def close_slide_parallel_group(
    widget: QWidget,
    *,
    start: QPoint,
    end: QPoint,
    slide_duration_ms: int,
    slide_easing,
    fade_out_ms: int,
) -> QParallelAnimationGroup:
    group = QParallelAnimationGroup(widget)

    pos = QPropertyAnimation(widget, b"windowAnchorPosition", widget)
    pos.setDuration(max(1, slide_duration_ms))
    pos.setStartValue(start)
    pos.setEndValue(end)
    pos.setEasingCurve(slide_easing)
    group.addAnimation(pos)

    fout = max(0, int(fade_out_ms))
    if fout <= 0:
        widget.setWindowOpacity(1.0)
    else:
        eff = min(fout, max(1, slide_duration_ms))
        widget.setWindowOpacity(1.0)
        op = QPropertyAnimation(widget, b"windowOpacity", widget)
        op.setDuration(eff)
        op.setStartValue(1.0)
        op.setEndValue(0.0)
        op.setEasingCurve(QEasingCurve.Type.Linear)
        group.addAnimation(op)

    return group
