"""The pet window: input, position, display and painting only.

The window never decides actions - interactions are emitted as callbacks and
the app turns them into ActionController requests (design section 4).
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QMenu, QWidget

from retirement_pet.countdown import CountdownSnapshot
from retirement_pet.models import OverlaySnapshot, RenderSnapshot
from retirement_pet.ui.countdown_panel import CountdownPanel
from retirement_pet.ui.overlay_renderer import EngineOverlayRenderer
from retirement_pet.ui.renderer import CatRenderer

WINDOW_WIDTH = 232
CAT_AREA_HEIGHT = 236
PANEL_EXPANDED_HEIGHT = 124
PANEL_COLLAPSED_HEIGHT = 42

GAZE_RANGE_PX = 130.0

logger = logging.getLogger(__name__)


class PetWindow(QWidget):
    #: Interactions surfaced to the app layer.
    clicked = Signal()
    double_clicked = Signal()
    wheel_steps = Signal(int)  # positive = up
    position_saved = Signal(QPoint)
    #: Emitted once, on the first PAINTED frame (valid snapshot rendered).
    first_paint = Signal()

    def __init__(
        self,
        renderer: CatRenderer,
        panel: CountdownPanel | None = None,
        menu_builder: Callable[[], QMenu | None] | None = None,
        always_on_top: bool = True,
        click_through: bool = False,
        panel_expanded: bool = True,
        overlay_renderer: EngineOverlayRenderer | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._renderer = renderer
        self._overlay_renderer = overlay_renderer or EngineOverlayRenderer()
        self._panel = panel or CountdownPanel()
        self._menu_builder = menu_builder
        self._render_snap: RenderSnapshot | None = None
        self._countdown_snap: CountdownSnapshot | None = None
        self._first_paint_done = False
        self._panel_expanded = panel_expanded
        self._press_offset = QPoint()
        self._dragging = False
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(280)
        self._click_timer.timeout.connect(self._emit_click)

        self.setWindowTitle("退休倒计时")
        # Desktop-pet window semantics: no title bar / border (Frameless),
        # no taskbar entry (Tool); translucency alone does NOT remove the
        # native frame.  All toggles recompute the full flag set so the base
        # contract can never be dropped.
        self._always_on_top = bool(always_on_top)
        self._click_through = bool(click_through)
        self._apply_window_flags()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._apply_size()

    # -- appearance -----------------------------------------------------------

    def _apply_window_flags(self) -> None:
        """Recompute and apply the complete window flag set.

        Order matters: WA_TransparentForMouseEvents must be set before
        setWindowFlags, otherwise Qt re-applies the input-transparency flag
        and WindowTransparentForInput can never be cleared again.

        Visibility must be sampled BEFORE setWindowFlags: on the real
        ``windows`` platform setWindowFlags re-parents and HIDES the window,
        so checking isVisible() afterwards would never re-show it and every
        穿透/置顶 toggle would make the pet vanish (found by the native
        tier, M0; offscreen tests cannot see this).
        """
        was_visible = self.isVisible()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self._click_through)
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self._always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        if self._click_through:
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def set_always_on_top(self, on: bool) -> None:
        self._always_on_top = bool(on)
        self._apply_window_flags()

    def set_click_through(self, on: bool) -> None:
        self._click_through = bool(on)
        self._apply_window_flags()

    @property
    def is_click_through(self) -> bool:
        return self._click_through

    def set_renderer(self, renderer) -> None:
        """Swap the body renderer for the NEXT frame (atomic switch support).

        Any object with ``render(painter, rect, snapshot)`` works; the two
        known implementations are the built-in CatRenderer (engine safe
        body) and a validated PackCharacterRuntime.  The first-paint
        marker stays process-global and is not reset by body swaps.
        """
        self._renderer = renderer

    # -- SwitchSurface protocol (switcher.SwitchSurface) -----------------------

    @property
    def renderer(self):
        return self._renderer

    def swap_renderer(self, renderer) -> None:
        self.set_renderer(renderer)

    def paint_first_frame(self, renderer) -> bool:
        """Synchronously paint one frame with ``renderer`` offscreen.

        The switch transaction calls this AFTER swapping: only a renderer
        that actually completes a first frame may commit its selection.
        """
        try:
            from PySide6.QtGui import QColor, QImage, QPainter

            from retirement_pet.models import ActionId, LifeStage, RenderSnapshot

            image = QImage(96, 96, QImage.Format.Format_ARGB32)
            if image.isNull():
                return False
            image.fill(QColor(0, 0, 0, 0))
            painter = QPainter(image)
            try:
                snapshot = self._render_snap or RenderSnapshot(
                    action=ActionId.IDLE, stage=LifeStage.YOUNG,
                    elapsed_ms=0, frame=0, time_ms=0)
                renderer.render(painter, QRectF(0, 0, 96, 96), snapshot)
            finally:
                painter.end()
            return not image.isNull()
        except Exception:  # noqa: BLE001 - renderer media is untrusted
            logger.exception("first-frame paint failed during switch")
            return False

    def set_panel_expanded(self, expanded: bool) -> None:
        if expanded == self._panel_expanded:
            return
        old_bottom = self.geometry().bottom()
        self._panel_expanded = expanded
        self._apply_size()
        # Keep the cat anchored: grow/shrink upwards.
        self.move(self.x(), old_bottom - self.height() + 1)
        self.position_saved.emit(self.pos())

    @property
    def panel_expanded(self) -> bool:
        return self._panel_expanded

    def toggle_panel(self) -> None:
        self.set_panel_expanded(not self._panel_expanded)

    def _apply_size(self) -> None:
        panel_h = PANEL_EXPANDED_HEIGHT if self._panel_expanded else PANEL_COLLAPSED_HEIGHT
        self.setFixedSize(WINDOW_WIDTH, CAT_AREA_HEIGHT + panel_h)

    # -- snapshot & painting ----------------------------------------------------

    def update_snapshot(self, render_snap: RenderSnapshot, countdown_snap: CountdownSnapshot) -> None:
        # Sample gaze from the live cursor even without hover events.
        center = self.frameGeometry().center()
        cursor = QCursor.pos()
        dx = (cursor.x() - center.x()) / GAZE_RANGE_PX
        dy = (cursor.y() - center.y()) / GAZE_RANGE_PX
        overlay = render_snap.overlay
        render_snap = RenderSnapshot(
            action=render_snap.action,
            stage=render_snap.stage,
            elapsed_ms=render_snap.elapsed_ms,
            frame=render_snap.frame,
            time_ms=render_snap.time_ms,
            overlay=OverlaySnapshot(
                blink=overlay.blink,
                gaze_x=max(-1.0, min(1.0, dx)),
                gaze_y=max(-1.0, min(1.0, dy)),
                effects=overlay.effects,
                bubble_text=overlay.bubble_text,
            ),
            assets_available=render_snap.assets_available,
        )
        self._render_snap = render_snap
        self._countdown_snap = countdown_snap
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._render_snap is None or self._countdown_snap is None:
            return
        painter = QPainter(self)
        cat_rect = QRectF(0, 0, WINDOW_WIDTH, CAT_AREA_HEIGHT)
        panel_rect = QRectF(
            0, CAT_AREA_HEIGHT, WINDOW_WIDTH, self.height() - CAT_AREA_HEIGHT
        )
        body_renderer = getattr(self._renderer, "render_body", None)
        if callable(body_renderer):
            body_renderer(painter, cat_rect, self._render_snap)
        else:  # compatibility with a legacy injected body renderer
            self._renderer.render(painter, cat_rect, self._render_snap)
        self._overlay_renderer.render(painter, cat_rect, self._render_snap)
        if self._panel_expanded:
            self._panel.render_expanded(painter, panel_rect, self._countdown_snap)
        else:
            self._panel.render_collapsed(painter, panel_rect, self._countdown_snap)
        painter.end()
        if not self._first_paint_done:
            self._first_paint_done = True
            self.first_paint.emit()

    # -- input ------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self._dragging = True
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._press_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            moved = (
                event.globalPosition().toPoint() - self._press_offset
            ).manhattanLength() > 6
            self._dragging = False
            if moved:
                self.position_saved.emit(self.pos())
            else:
                self._click_timer.start()
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._click_timer.stop()  # single click swallowed by double click
            self.double_clicked.emit()
            event.accept()

    def _emit_click(self) -> None:
        self.clicked.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        steps = 1 if event.angleDelta().y() > 0 else -1
        self.wheel_steps.emit(steps)
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if self._menu_builder is None:
            return
        menu = self._menu_builder()
        if menu is not None:
            menu.exec(event.globalPos())

    # -- geometry safety -------------------------------------------------------

    def ensure_visible_on_screen(self) -> bool:
        """Clamp back into the nearest screen after display changes."""
        rect = self.frameGeometry()
        from PySide6.QtGui import QGuiApplication

        containing = None
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().intersects(rect):
                containing = screen
                break
        if containing is None:
            containing = QGuiApplication.primaryScreen()
        if containing is None:
            return False
        available = containing.availableGeometry()
        x = max(available.left(), min(rect.x(), available.right() - self.width()))
        y = max(available.top(), min(rect.y(), available.bottom() - self.height()))
        target = QPoint(x, y)
        if target != rect.topLeft():
            self.move(target)
            self.position_saved.emit(self.pos())
            return True
        return False

    def place_default(self) -> None:
        """Bottom-right of the primary screen (first launch)."""
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        margin = 24
        self.move(
            available.right() - self.width() - margin,
            available.bottom() - self.height() - margin,
        )

    def restore_to_primary_screen(self) -> bool:
        """Tray recovery command: bring the pet back onto the primary screen.

        Keeps the current spot when it already intersects the primary work
        area (only clamping fully inside); otherwise falls back to the
        default bottom-right placement.
        """
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return False
        available = screen.availableGeometry()
        if not self.frameGeometry().intersects(available):
            self.place_default()
            self.position_saved.emit(self.pos())
            return True
        x = max(available.left(), min(self.x(), available.right() - self.width()))
        y = max(available.top(), min(self.y(), available.bottom() - self.height()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)
            self.position_saved.emit(self.pos())
            return True
        return False


__all__ = ["PetWindow", "WINDOW_WIDTH", "CAT_AREA_HEIGHT"]
