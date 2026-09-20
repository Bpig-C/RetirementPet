"""PetWindow behaviors: panel toggle, click-through, position save, clamping."""

from __future__ import annotations

import pytest

from retirement_pet.ui.pet_window import (
    CAT_AREA_HEIGHT,
    PANEL_COLLAPSED_HEIGHT,
    PANEL_EXPANDED_HEIGHT,
    PetWindow,
)


@pytest.fixture()
def window(qt_application):
    win = PetWindow(
        renderer=_dummy_renderer(),
        always_on_top=False,
        panel_expanded=True,
    )
    yield win
    win.close()


def _dummy_renderer():
    from retirement_pet.ui.renderer import CatRenderer

    return CatRenderer()


def test_initial_sizes(qt_application):
    win = PetWindow(_dummy_renderer(), always_on_top=False, panel_expanded=True)
    assert win.height() == CAT_AREA_HEIGHT + PANEL_EXPANDED_HEIGHT
    win.close()

    win2 = PetWindow(_dummy_renderer(), always_on_top=False, panel_expanded=False)
    assert win2.height() == CAT_AREA_HEIGHT + PANEL_COLLAPSED_HEIGHT
    win2.close()


def test_toggle_panel_grows_upwards(qt_application):
    win = PetWindow(_dummy_renderer(), always_on_top=False, panel_expanded=True)
    win.move(100, 100)
    bottom = win.frameGeometry().bottom()
    win.toggle_panel()
    assert not win.panel_expanded
    assert win.height() == CAT_AREA_HEIGHT + PANEL_COLLAPSED_HEIGHT
    assert win.frameGeometry().bottom() <= bottom + 1  # anchored near same bottom
    win.close()


def test_click_through_flags(qt_application):
    from PySide6.QtCore import Qt

    win = PetWindow(_dummy_renderer(), always_on_top=False)
    assert not win.testAttribute(Qt.WA_TransparentForMouseEvents)
    win.set_click_through(True)
    assert win.testAttribute(Qt.WA_TransparentForMouseEvents)
    win.set_click_through(False)
    assert not win.testAttribute(Qt.WA_TransparentForMouseEvents)
    win.close()


def test_always_on_top_flag(qt_application):
    from PySide6.QtCore import Qt

    win = PetWindow(_dummy_renderer(), always_on_top=False)
    win.set_always_on_top(True)
    assert bool(win.windowFlags() & Qt.WindowStaysOnTopHint)
    win.set_always_on_top(False)
    assert not bool(win.windowFlags() & Qt.WindowStaysOnTopHint)
    win.close()


def test_desktop_pet_window_contract(qt_application):
    """Frameless + Tool + translucent must hold at init (original behavior)."""
    from PySide6.QtCore import Qt

    win = PetWindow(_dummy_renderer(), always_on_top=True)
    flags = win.windowFlags()
    assert bool(flags & Qt.FramelessWindowHint), "missing FramelessWindowHint"
    assert bool(flags & Qt.Tool), "missing Tool (taskbar would show the pet)"
    assert bool(flags & Qt.WindowStaysOnTopHint)
    assert win.testAttribute(Qt.WA_TranslucentBackground)
    win.close()

    win = PetWindow(_dummy_renderer(), always_on_top=False)
    assert bool(win.windowFlags() & Qt.FramelessWindowHint)
    assert bool(win.windowFlags() & Qt.Tool)
    assert not bool(win.windowFlags() & Qt.WindowStaysOnTopHint)
    win.close()


def test_flag_toggles_preserve_window_contract(qt_application):
    """Switching on-top / click-through must never drop the base flags."""
    from PySide6.QtCore import Qt

    def assert_contract(win):
        flags = win.windowFlags()
        assert bool(flags & Qt.FramelessWindowHint)
        assert bool(flags & Qt.Tool)
        assert win.testAttribute(Qt.WA_TranslucentBackground)

    win = PetWindow(_dummy_renderer(), always_on_top=True)
    for on_top in (False, True, False):
        win.set_always_on_top(on_top)
        assert bool(win.windowFlags() & Qt.WindowStaysOnTopHint) is on_top
        assert_contract(win)
    for click_through in (True, False, True):
        win.set_click_through(click_through)
        assert (
            bool(win.windowFlags() & Qt.WindowTransparentForInput) is click_through
        )
        assert_contract(win)
    win.close()


def test_position_saved_signal(qt_application):
    from PySide6.QtCore import QPoint

    win = PetWindow(_dummy_renderer(), always_on_top=False)
    saved = []
    win.position_saved.connect(lambda p: saved.append(p))
    win.move(50, 60)
    win.position_saved.emit(win.pos())
    assert saved == [QPoint(50, 60)]
    win.close()


def test_ensure_visible_clamps(qt_application):
    from PySide6.QtGui import QGuiApplication

    win = PetWindow(_dummy_renderer(), always_on_top=False)
    screen = QGuiApplication.primaryScreen().availableGeometry()
    win.move(screen.right() + 500, screen.bottom() + 500)  # far off-screen
    moved = win.ensure_visible_on_screen()
    assert moved
    geo = win.frameGeometry()
    assert screen.contains(geo.topLeft())
    win.close()


def test_double_click_emits_once(qt_application):
    win = PetWindow(_dummy_renderer(), always_on_top=False)
    doubles = []
    win.double_clicked.connect(lambda: doubles.append(1))
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF, QEvent, Qt

    event = QMouseEvent(
        QEvent.MouseButtonDblClick,
        QPointF(50, 50),
        QPointF(50, 50),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    win.mouseDoubleClickEvent(event)
    assert doubles == [1]
    win.close()


def test_paint_order_is_body_overlay_countdown_exactly_once(
        qt_application, monkeypatch):
    """The compatibility composite must not duplicate the live overlay."""
    from datetime import datetime

    from retirement_pet.countdown import compute_countdown
    from retirement_pet.models import ActionId, LifeStage, RenderSnapshot
    import retirement_pet.ui.pet_window as window_module

    calls = []

    class Body:
        def render_body(self, _painter, _rect, _snapshot):
            calls.append("body")

        def render(self, *_args):
            raise AssertionError("PetWindow must use the body-only path")

    class Overlay:
        def render(self, _painter, _rect, _snapshot):
            calls.append("overlay")

    class Panel:
        def render_expanded(self, _painter, _rect, _snapshot, heading=None):
            calls.append("countdown")

    class Painter:
        def __init__(self, _widget):
            pass

        def end(self):
            pass

    monkeypatch.setattr(window_module, "QPainter", Painter)
    win = PetWindow(
        Body(), panel=Panel(), overlay_renderer=Overlay(),
        always_on_top=False, panel_expanded=True,
    )
    win._render_snap = RenderSnapshot(
        action=ActionId.WORK, stage=LifeStage.YOUNG,
        elapsed_ms=0, frame=0, time_ms=0,
    )
    win._countdown_snap = compute_countdown(
        datetime(2026, 9, 1), datetime(2060, 1, 1))
    win.paintEvent(None)
    assert calls == ["body", "overlay", "countdown"]
    win.close()
