"""Engine-owned, plain-text overlays shared by every character renderer.

PetPack media contains character pixels only.  Captions, speech bubbles and
the small, allowlisted action effects are painted here at runtime so they do
not inherit a pack builder's font environment or bitmap resolution.
"""

from __future__ import annotations

from functools import lru_cache
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QPolygonF,
)

from retirement_pet.models import ActionId, RenderSnapshot

CANVAS = 256.0

# Keep the Chinese mapping public for compatibility with callers that used the
# v1 renderer's constant.  ASCII is a deterministic last resort: a missing CJK
# font must never turn a predefined engine caption into tofu boxes.
ACTION_CAPTIONS = {
    ActionId.IDLE: "待机",
    ActionId.WORK: "工作中",
    ActionId.REST: "休息中",
    ActionId.EAT: "干饭",
    ActionId.EXERCISE: "健身",
    ActionId.MEETING: "会议中",
    ActionId.MUSIC: "听歌",
    ActionId.STRETCH: "伸懒腰",
    ActionId.LICK_PAW: "舔爪爪",
    ActionId.LOOK_AROUND: "张望",
    ActionId.YAWN: "打哈欠",
    ActionId.INTERACT: "开心！",
}

ASCII_CAPTIONS = {
    ActionId.IDLE: "Idle",
    ActionId.WORK: "Working",
    ActionId.REST: "Resting",
    ActionId.EAT: "Meal",
    ActionId.EXERCISE: "Exercise",
    ActionId.MEETING: "Meeting",
    ActionId.MUSIC: "Music",
    ActionId.STRETCH: "Stretch",
    ActionId.LICK_PAW: "Grooming",
    ActionId.LOOK_AROUND: "Looking",
    ActionId.YAWN: "Yawning",
    ActionId.INTERACT: "Happy!",
}

_FONT_FAMILIES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "SimSun",
    "Segoe UI",
)

CAPTION_BG = QColor(25, 27, 31, 215)
CAPTION_FG = QColor(235, 235, 238)


def _supports_text(font: QFont, text: str) -> bool:
    """Return whether Qt can resolve a real glyph for every visible scalar."""
    metrics = QFontMetrics(font)
    return all(
        char.isspace() or not char.isprintable()
        or metrics.inFontUcs4(ord(char))
        for char in text
    )


@lru_cache(maxsize=32)
def _find_font(text: str, point_size: int, bold: bool) -> QFont | None:
    """Bounded glyph lookup; arbitrary bubble text cannot grow a cache."""
    weight = QFont.Weight.DemiBold if bold else QFont.Weight.Normal
    for family in _FONT_FAMILIES:
        font = QFont(family, point_size, weight)
        if _supports_text(font, text):
            return font
    return None


def resolved_caption(action: ActionId) -> tuple[str, QFont]:
    """Use Chinese only when the active Qt font stack has every glyph."""
    chinese = ACTION_CAPTIONS.get(action, "")
    if chinese:
        font = _find_font(chinese, 8, True)
        if font is not None:
            return chinese, font
    fallback = ASCII_CAPTIONS.get(action, "")
    font = _find_font(fallback, 8, True)
    return fallback, font or QFont("Segoe UI", 8, QFont.Weight.DemiBold)


def _plain_bubble_text(text: str) -> tuple[str, QFont]:
    """Choose a glyph-complete font or replace missing glyphs with ``?``.

    QPainter's plain-text overload is used throughout; HTML/markup is never
    parsed, even when the bubble originated in user data.
    """
    font = _find_font(text, 8, False)
    if font is not None:
        return text, font
    fallback = _find_font("ASCII", 8, False) or QFont("Segoe UI", 8)
    metrics = QFontMetrics(fallback)
    safe = "".join(
        char if (char.isspace() or not char.isprintable()
                 or metrics.inFontUcs4(ord(char))) else "?"
        for char in text
    )
    return safe, fallback


def _bubble_geometry(rect: QRectF, snap: RenderSnapshot,
                     bubble_origin: QPointF | None = None):
    """Shared bubble geometry: tail tip, tail base y, box and text layout.

    With ``bubble_origin`` (window DIP, from the character layout) the tail
    tip sits just above the anchor, clamped into the viewport; without it
    the position matches the legacy canvas-mapped bubble exactly.
    """
    text, font = _plain_bubble_text(snap.overlay.bubble_text)
    metrics = QFontMetrics(font)
    lines = text.split("\n")
    line_height = metrics.height()
    width = min(max(metrics.horizontalAdvance(line) for line in lines) + 22,
                max(24.0, rect.width() - 4.0))
    height = line_height * len(lines) + 12
    scale = min(rect.width(), rect.height()) / CANVAS
    if bubble_origin is None:
        tail_tip = QPointF(
            rect.center().x() + (126.0 - CANVAS / 2.0) * scale,
            rect.center().y() + (44.0 - CANVAS / 2.0) * scale)
    else:
        tail_tip = QPointF(
            max(rect.left() + width / 2.0 + 2.0,
                min(rect.right() - width / 2.0 - 2.0, bubble_origin.x())),
            max(rect.top() + height + 2.0, bubble_origin.y() - 4.0))
    tail_base_y = tail_tip.y() - 10.0
    box = QRectF(tail_tip.x() - width / 2.0, tail_base_y - height,
                 width, height)
    if box.left() < rect.left() + 2.0:
        box.moveLeft(rect.left() + 2.0)
    elif box.right() > rect.right() - 2.0:
        box.moveRight(rect.right() - 2.0)
    if box.top() < rect.top() + 2.0:
        box.moveTop(rect.top() + 2.0)
    return tail_tip, tail_base_y, box, width, height, lines, line_height


class EngineOverlayRenderer:
    """Paint the engine layer once, after a body and before the countdown."""

    def __init__(self) -> None:
        # V12-06 visibility policy gate: full | reduced | hidden
        self._decorations = "full"

    def set_decorations(self, mode: str) -> None:
        self._decorations = mode \
            if mode in ("full", "reduced", "hidden") else "full"

    def render(self, painter: QPainter, rect: QRectF,
               snap: RenderSnapshot,
               bubble_origin: QPointF | None = None) -> None:
        """``bubble_origin`` places the bubble tail tip in window DIP.

        It comes from the shared character layout (manifest
        ``bubble_anchor``); None keeps the legacy canvas-mapped position.
        The bubble is painted outside the character canvas transform so a
        geometry-placed anchor is exact, not rescaled through the canvas.
        """
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            scale = min(rect.width(), rect.height()) / CANVAS
            painter.translate(rect.center())
            painter.scale(scale, scale)
            painter.translate(-CANVAS / 2, -CANVAS / 2)
            self._draw_effects(painter, snap)
            self._draw_caption(painter, snap)
        finally:
            painter.restore()
        if snap.overlay.bubble_text:
            painter.save()
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                self._draw_bubble(painter, rect, snap, bubble_origin)
            finally:
                painter.restore()

    @staticmethod
    def bubble_rect(rect: QRectF, snap: RenderSnapshot,
                    bubble_origin: QPointF | None = None) -> QRectF | None:
        """Bounding rect (box + tail) the bubble will occupy, for masks."""
        if not snap.overlay.bubble_text:
            return None
        tail_tip, tail_base_y, box, _width, _height, _lines, _lh = \
            _bubble_geometry(rect, snap, bubble_origin)
        tail_bounds = QRectF(
            QPointF(tail_tip.x() - 6, tail_base_y),
            QPointF(tail_tip.x() + 6, tail_tip.y()))
        return box.united(tail_bounds)

    @staticmethod
    def effects_rect(rect: QRectF) -> QRectF:
        """Viewport area where transient effect decorations may draw."""
        scale = min(rect.width(), rect.height()) / CANVAS
        offset = QPointF(rect.center().x() - CANVAS * scale / 2.0,
                         rect.center().y() - CANVAS * scale / 2.0)
        canvas_area = QRectF(50, 80, 165, 90)
        return QRectF(canvas_area.x() * scale + offset.x(),
                      canvas_area.y() * scale + offset.y(),
                      canvas_area.width() * scale,
                      canvas_area.height() * scale)

    def _draw_effects(self, painter: QPainter, snap: RenderSnapshot) -> None:
        if self._decorations == "hidden":
            return
        # reduced: at most one decoration kind stays visible
        effects = snap.overlay.effects \
            if self._decorations != "reduced" \
            else snap.overlay.effects[:1]
        t = snap.time_ms / 1000.0
        painter.setPen(Qt.PenStyle.NoPen)
        for kind in effects:
            if kind == "zzz":
                for index in range(3):
                    drift = (t * 14 + index * 10) % 30
                    alpha = max(0.0, 1.0 - drift / 30.0)
                    painter.setPen(QPen(
                        QColor(160, 165, 180, int(255 * alpha)), 2))
                    font = QFont("Segoe UI", 9 + index)
                    font.setBold(True)
                    painter.setFont(font)
                    painter.drawText(
                        QPointF(178 + index * 9 + drift * 0.3,
                                150 - drift - index * 12),
                        "Z",
                    )
                    painter.setPen(Qt.PenStyle.NoPen)
            elif kind == "notes":
                # Vector notes avoid relying on musical-symbol font glyphs.
                for index in range(3):
                    drift = (t * 20 + index * 14) % 36
                    alpha = max(0.0, 1.0 - drift / 36.0)
                    x = 72 + index * 34 + math.sin(t * 2 + index) * 6
                    y = 130 - drift
                    painter.setPen(QPen(
                        QColor(129, 140, 248, int(255 * alpha)), 2))
                    painter.setBrush(QColor(
                        129, 140, 248, int(255 * alpha)))
                    painter.drawEllipse(QPointF(x, y), 3.2, 2.5)
                    painter.drawLine(QPointF(x + 3, y),
                                     QPointF(x + 3, y - 13))
                    if index % 2:
                        painter.drawLine(QPointF(x + 3, y - 13),
                                         QPointF(x + 9, y - 10))
            elif kind == "sweat":
                painter.setPen(Qt.PenStyle.NoPen)
                for index in range(2):
                    drift = (t * 26 + index * 16) % 26
                    painter.setBrush(QColor(120, 170, 235, 210))
                    painter.drawEllipse(
                        QPointF(88 + index * 86, 92 + drift), 3.2, 4.6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    @staticmethod
    def _draw_caption(painter: QPainter, snap: RenderSnapshot) -> None:
        if snap.action is ActionId.IDLE:
            return
        text, font = resolved_caption(snap.action)
        if not text:
            return
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(text) + 18
        rect = QRectF(128 - width / 2, 4, width, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(CAPTION_BG)
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(QPen(CAPTION_FG, 1))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(Qt.PenStyle.NoPen)

    @staticmethod
    def _draw_bubble(painter: QPainter, rect: QRectF, snap: RenderSnapshot,
                     bubble_origin: QPointF | None = None) -> None:
        if not snap.overlay.bubble_text:
            return
        (_tail_tip, tail_base_y, box,
         width, _height, lines, line_height) = _bubble_geometry(
            rect, snap, bubble_origin)
        tail = QPolygonF([
            QPointF(_tail_tip.x() - 6, tail_base_y),
            QPointF(_tail_tip.x() + 6, tail_base_y),
            _tail_tip,
        ])
        painter.setPen(QPen(QColor(210, 212, 218), 1))
        painter.setBrush(QColor(250, 250, 252, 244))
        painter.drawRoundedRect(box, 10, 10)
        painter.drawPolygon(tail)
        painter.setPen(QPen(QColor(60, 62, 70), 1))
        for index, line in enumerate(lines):
            painter.drawText(
                QRectF(box.left() + 8,
                       box.top() + 4 + index * line_height,
                       width - 16, line_height),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                line,
            )
        painter.setPen(Qt.PenStyle.NoPen)


__all__ = [
    "ACTION_CAPTIONS",
    "ASCII_CAPTIONS",
    "EngineOverlayRenderer",
    "resolved_caption",
]
