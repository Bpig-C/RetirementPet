"""Countdown panel: expanded card (like the original) or collapsed pill."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter

from retirement_pet.countdown import CountdownSnapshot

CARD_BG = QColor(25, 27, 31, 225)
TEXT_MAIN = QColor(235, 235, 238)
TEXT_SUB = QColor(165, 168, 176)
TEXT_DIM = QColor(115, 118, 126)
TEXT_DAYS = QColor(248, 248, 250)


class CountdownPanel:
    def render_expanded(self, painter: QPainter, rect: QRectF,
                        snap: CountdownSnapshot,
                        heading: str | None = None) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        card = QRectF(rect.left() + 4, rect.top() + 4, rect.width() - 8, rect.height() - 8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(CARD_BG)
        painter.drawRoundedRect(card, 18, 18)

        left = card.left() + 16
        top = card.top()

        painter.setPen(TEXT_MAIN)
        painter.setFont(QFont("Microsoft YaHei UI", 11, QFont.DemiBold))
        # V12-06: the heading line renders the user's countdown template;
        # None keeps the built-in stage text (identical by default).
        painter.drawText(QPointF(left, top + 26),
                         heading if heading is not None else snap.stage_text)

        painter.setPen(TEXT_SUB)
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.drawText(QPointF(left, top + 45), snap.stage_subtitle)

        if snap.is_past:
            painter.setPen(QColor(245, 245, 247))
            painter.setFont(QFont("Microsoft YaHei UI", 19, QFont.Bold))
            painter.drawText(
                QRectF(left, top + 52, card.width() - 32, 40),
                Qt.AlignLeft | Qt.AlignVCenter,
                "退休快乐",
            )
            painter.setPen(QColor(170, 173, 180))
            painter.setFont(QFont("Microsoft YaHei UI", 8))
            painter.drawText(
                QPointF(left, card.bottom() - 12), snap.target.strftime("%Y.%m.%d  %H:%M")
            )
            return

        painter.setPen(TEXT_DAYS)
        painter.setFont(QFont("Segoe UI", 21, QFont.Bold))
        painter.drawText(
            QRectF(left, top + 50, card.width() - 32, 34),
            Qt.AlignLeft | Qt.AlignVCenter,
            snap.days_text,
        )

        painter.setPen(QColor(185, 188, 195))
        painter.setFont(QFont("Consolas", 11))
        painter.drawText(
            QRectF(left, top + 84, card.width() - 32, 20),
            Qt.AlignLeft | Qt.AlignVCenter,
            snap.clock_text,
        )

        painter.setPen(TEXT_DIM)
        painter.setFont(QFont("Microsoft YaHei UI", 7))
        painter.drawText(
            QPointF(left, card.bottom() - 10),
            f"目标  {snap.target.strftime('%Y.%m.%d  %H:%M')}",
        )

    def render_collapsed(self, painter: QPainter, rect: QRectF,
                         snap: CountdownSnapshot,
                         show_clock: bool = True) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        pill = QRectF(rect.left() + 4, rect.top() + 2, rect.width() - 8, rect.height() - 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(CARD_BG)
        painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)

        if snap.is_past:
            text, sub = "退休快乐", ""
        else:
            # V12-06: badge layout shows the days count only; summary and
            # hover layouts keep the clock alongside.
            text = snap.days_text
            sub = snap.clock_text if show_clock else ""
        painter.setPen(TEXT_DAYS)
        painter.setFont(QFont("Segoe UI", 12, QFont.Bold))
        painter.drawText(
            QRectF(pill.left() + 14, pill.top(), pill.width() - 28, pill.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            text,
        )
        if sub:
            painter.setPen(QColor(185, 188, 195))
            painter.setFont(QFont("Consolas", 9))
            painter.drawText(
                QRectF(pill.left() + 14, pill.top(), pill.width() - 28, pill.height()),
                Qt.AlignVCenter | Qt.AlignRight,
                sub,
            )
