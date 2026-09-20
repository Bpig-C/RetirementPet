"""The pet window: input, position, display and painting only.

The window never decides actions - interactions are emitted as callbacks and
the app turns them into ActionController requests (design section 4).
"""

from __future__ import annotations

import inspect
import logging
from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QPainter, QRegion
from PySide6.QtWidgets import QMenu, QWidget

from retirement_pet.character_layout import (
    BodyLayout,
    CharacterGeometry,
    compute_body_layout,
    widget_rect_for_mask,
)
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
        self._body_accepts_layout = self._probe_body_layout(renderer)
        self._overlay_renderer = overlay_renderer or EngineOverlayRenderer()
        self._overlay_accepts_origin = self._probe_render_origin(
            self._overlay_renderer)
        self._panel = panel or CountdownPanel()
        self._menu_builder = menu_builder
        self._render_snap: RenderSnapshot | None = None
        self._countdown_snap: CountdownSnapshot | None = None
        self._active_layout: BodyLayout | None = None
        # V12-08 downshift: content key of the last composed frame
        self._last_content_key = None
        self._mask_applied: QRegion | None = None
        self._first_paint_done = False
        self._panel_expanded = panel_expanded
        # V12-06: resolved layout × policy view model.  ``_countdown_mode``
        # comes from the engine matrix (hidden/badge/standard/hover/summary);
        # ``_panel_expanded`` stays the user's preference inside standard.
        self._countdown_mode = "standard"
        self._hover_expanded = False
        self._heading_text: str | None = None
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
        # The new body may declare different geometry: recompute the layout
        # and re-derive the input mask on the next composed frame.  Legacy
        # injected bodies keep their 3-argument render_body signature.
        self._body_accepts_layout = self._probe_body_layout(renderer)
        self._active_layout = None
        self._mask_applied = None

    @staticmethod
    def _probe_body_layout(renderer) -> bool:
        return PetWindow._probe_signature(getattr(renderer, "render_body", None), 4)

    @staticmethod
    def _probe_render_origin(overlay) -> bool:
        return PetWindow._probe_signature(getattr(overlay, "render", None), 4)

    @staticmethod
    def _probe_signature(fn, min_positional: int) -> bool:
        if not callable(fn):
            return False
        try:
            positional = [
                parameter for parameter
                in inspect.signature(fn).parameters.values()
                if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                      inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                      inspect.Parameter.VAR_POSITIONAL)
            ]
        except (TypeError, ValueError):
            return False
        return len(positional) >= min_positional

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
        if self._countdown_mode == "hidden":
            return  # the matrix decides: no panel exists to toggle
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

    # -- layout × policy view model (V12-06) ---------------------------------

    def apply_layout_view_model(self, view_model: dict) -> None:
        """Apply the engine-resolved layout/policy combination.

        ``view_model`` is the validated dict from
        :func:`retirement_pet.layout_policy.resolve_layout`; the countdown
        entry (hidden/badge/standard/hover/summary) decides how the panel
        area behaves and whether it exists at all.
        """
        self._countdown_mode = str(view_model.get("countdown", "standard"))
        decorations = str(view_model.get("decorations", "full"))
        self._overlay_renderer.set_decorations(decorations)
        self._apply_size()

    def set_countdown_heading(self, text: str | None) -> None:
        """Rendered user countdown template; None keeps the stage text."""
        self._heading_text = text

    def _panel_visible(self) -> bool:
        return self._countdown_mode != "hidden"

    def _panel_expanded_now(self) -> bool:
        if self._countdown_mode == "standard":
            return self._panel_expanded
        if self._countdown_mode == "hover":
            return self._hover_expanded
        return False  # badge / summary stay collapsed

    def _apply_size(self) -> None:
        if not self._panel_visible():
            self.setFixedSize(WINDOW_WIDTH, CAT_AREA_HEIGHT)
            return
        panel_h = PANEL_EXPANDED_HEIGHT if self._panel_expanded_now() \
            else PANEL_COLLAPSED_HEIGHT
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
        # V12-08 downshift: a static visual (parts profile, no sequence
        # advancing) draws identical pixels while nothing else changes -
        # skip the repaint instead of re-composing every tick.  Animated
        # visuals advance `frame` (sequence) or `time_ms` (engine cat
        # breath), so their key keeps moving and they repaint as before.
        animates = True
        probe = getattr(self._renderer, "is_animated_for", None)
        if callable(probe):
            try:
                animates = bool(probe(render_snap.action))
            except Exception:  # noqa: BLE001 - renderer media is untrusted
                animates = True
        key = (
            render_snap.action, render_snap.stage, render_snap.frame,
            render_snap.overlay.blink,
            round(render_snap.overlay.gaze_x, 2),
            round(render_snap.overlay.gaze_y, 2),
            render_snap.overlay.effects,
            render_snap.overlay.bubble_text,
            render_snap.assets_available,
            countdown_snap.days, countdown_snap.hours,
            countdown_snap.minutes, countdown_snap.seconds,
            countdown_snap.is_past,
        )
        self._render_snap = render_snap
        self._countdown_snap = countdown_snap
        if not animates and key == self._last_content_key:
            return
        self._last_content_key = key
        self._refresh_layout()
        self._update_input_mask(render_snap)
        self.update()

    # -- character layout (V12-01) ---------------------------------------------

    def _character_geometry(self) -> CharacterGeometry | None:
        getter = getattr(self._renderer, "character_geometry", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:  # noqa: BLE001 - renderer media is untrusted
            logger.exception("character geometry lookup failed")
            return None

    def current_foot_point(self) -> tuple[float, float] | None:
        """The real BodyLayout.foot_point in window DIP coordinates.

        Read-only diagnostic observation for harnesses (V12-08 R08-03):
        this is where base_anchor actually lands - including foot
        margins and motion-bounds clamping - not the window's bottom
        edge.
        """
        layout = self._active_layout
        if layout is None:
            return None
        return [float(layout.foot_point.x()), float(layout.foot_point.y())]

    def current_foot_point_global(self) -> tuple[float, float] | None:
        """The layout foot anchor in SCREEN pixels (Qt-mapped).

        The comparison basis for foot stability: unlike the window-DIP
        local point, this is unambiguous across window moves and DPI
        scaling (R08-03).
        """
        layout = self._active_layout
        if layout is None:
            return None
        point = self.mapToGlobal(layout.foot_point.toPoint())
        return [float(point.x()), float(point.y())]

    def current_window_dpi(self) -> int | None:
        dpi = self.screen().devicePixelRatio() if self.screen() else None
        return int(round(dpi * 96)) if dpi else None

    def _refresh_layout(self) -> None:
        """Recompute the shared body transform for the current renderer."""
        geometry = self._character_geometry()
        if geometry is None:
            self._active_layout = None
            return
        self._active_layout = compute_body_layout(
            QRectF(0, 0, WINDOW_WIDTH, CAT_AREA_HEIGHT), geometry)

    def _update_input_mask(self, snap: RenderSnapshot) -> None:
        """Keep desktop clicks passing through transparent whitespace.

        The window mask covers the mapped action motion bounds (a superset
        of every frame), the countdown panel, and — only while present —
        the bubble and transient effect decorations.  Renderers without
        geometry keep the legacy full-surface behaviour.
        """
        region = QRegion()
        if self._panel_visible():
            region = region.united(QRegion(widget_rect_for_mask(QRectF(
                0, CAT_AREA_HEIGHT, WINDOW_WIDTH,
                max(0.0, self.height() - CAT_AREA_HEIGHT)))))
        layout = self._active_layout
        if layout is None or layout.motion_rect is None:
            region = region.united(QRegion(QRect(
                0, 0, WINDOW_WIDTH, CAT_AREA_HEIGHT)))
        else:
            region = region.united(
                QRegion(widget_rect_for_mask(layout.motion_rect)))
        cat_rect = QRectF(0, 0, WINDOW_WIDTH, CAT_AREA_HEIGHT)
        origin = layout.bubble_origin if layout is not None else None
        bubble = self._overlay_renderer.bubble_rect(cat_rect, snap, origin)
        if bubble is not None:
            region = region.united(QRegion(widget_rect_for_mask(bubble)))
        if snap.overlay.effects:
            region = region.united(QRegion(widget_rect_for_mask(
                self._overlay_renderer.effects_rect(cat_rect))))
        if region == self._mask_applied:
            return
        self.setMask(region)
        self._mask_applied = region

    def hit_accepts(self, point: QPointF) -> bool:
        """True when a click at window DIP ``point`` hits the character."""
        layout = self._active_layout
        if layout is None:
            return True
        return layout.hit_contains(point)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._render_snap is None or self._countdown_snap is None:
            return
        painter = QPainter(self)
        cat_rect = QRectF(0, 0, WINDOW_WIDTH, CAT_AREA_HEIGHT)
        panel_rect = QRectF(
            0, CAT_AREA_HEIGHT, WINDOW_WIDTH, self.height() - CAT_AREA_HEIGHT
        )
        layout = self._active_layout
        body_renderer = getattr(self._renderer, "render_body", None)
        if callable(body_renderer) and self._body_accepts_layout:
            body_renderer(painter, cat_rect, self._render_snap, layout)
        elif callable(body_renderer):
            body_renderer(painter, cat_rect, self._render_snap)
        else:  # compatibility with a legacy injected body renderer
            self._renderer.render(painter, cat_rect, self._render_snap)
        if self._overlay_accepts_origin:
            self._overlay_renderer.render(
                painter, cat_rect, self._render_snap,
                layout.bubble_origin if layout is not None else None)
        else:
            self._overlay_renderer.render(
                painter, cat_rect, self._render_snap)
        if self._panel_visible():
            if self._panel_expanded_now():
                self._panel.render_expanded(
                    painter, panel_rect, self._countdown_snap,
                    heading=self._heading_text)
            else:
                # badge shows the days count only; summary/hover keep clock
                self._panel.render_collapsed(
                    painter, panel_rect, self._countdown_snap,
                    show_clock=self._countdown_mode != "badge")
        painter.end()
        if not self._first_paint_done:
            self._first_paint_done = True
            self.first_paint.emit()

    # -- input ------------------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: N802
        # hover_expand layout: the panel unfolds while the cursor is over
        # the pet and folds back on leave; the cat stays anchored.
        if self._countdown_mode == "hover" and not self._hover_expanded:
            old_bottom = self.geometry().bottom()
            self._hover_expanded = True
            self._apply_size()
            self.move(self.x(), old_bottom - self.height() + 1)
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_expanded:
            old_bottom = self.geometry().bottom()
            self._hover_expanded = False
            self._apply_size()
            self.move(self.x(), old_bottom - self.height() + 1)
        event.accept()

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
            elif self.hit_accepts(event.position()):
                self._click_timer.start()
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._click_timer.stop()  # single click swallowed by double click
            if self.hit_accepts(event.position()):
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
