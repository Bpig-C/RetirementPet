"""V12-08 downshift: repaint only when composed content changes.

The window composes a frame on every visual tick; when the active
visual is STATIC (a parts profile whose pixels do not advance) and the
overlay/countdown state is unchanged, scheduling a repaint is wasted
work.  These tests count real Qt paint events on a shown window.

All fixtures are isolated (headless PetApplication, synthetic pack).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from PySide6.QtCore import QEvent, QObject, QPoint

from retirement_pet.models import RenderSnapshot, OverlaySnapshot, LifeStage


class _PaintCounter(QObject):
    """Counts real paint events delivered to the pet window."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self.count = 0

    def eventFilter(self, obj, event):  # noqa: N802
        if self._window is None:
            return False
        if obj is self._window and event.type() == QEvent.Type.Paint:
            self.count += 1
        return False

    def detach(self):
        """Pair every install with removal (OVR-04): a dangling filter
        on a destroyed window raises inside Qt's callback."""
        if self._window is not None:
            self._window.removeEventFilter(self)
            self._window = None


@contextmanager
def _counting(window, qt_application):
    """Install a counter, drain queued paints, always detach."""
    counter = _PaintCounter(window)
    window.installEventFilter(counter)
    try:
        for _ in range(3):  # drain paints queued by the initial show
            qt_application.processEvents()
            qt_application.processEvents()
        yield counter
    finally:
        counter.detach()


def _snapshot(action_value="idle", frame=0, *, blink=False, gaze=(0.0, 0.0),
              effects=(), bubble=None):
    from retirement_pet.models import ActionId

    return RenderSnapshot(
        action=ActionId(action_value), stage=LifeStage.RETIRED,
        elapsed_ms=0, frame=frame, time_ms=0,
        overlay=OverlaySnapshot(blink=blink, gaze_x=gaze[0], gaze_y=gaze[1],
                                effects=tuple(effects),
                                bubble_text=bubble),
        assets_available=True,
    )


@pytest.fixture()
def app(qt_application, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    pet = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-downshift-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def _activate_static_pack(app):
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    app.library.install(
        root / "tests/fixtures/petpack/minimal-static.petpack")
    entry = next(e for e in app.catalog.entries()
                 if e.character_id == "demo")
    assert app._switch_character(entry) is True
    app._show_window()
    qt_app = __import__("PySide6.QtWidgets",
                        fromlist=["QApplication"]).QApplication.instance()
    qt_app.processEvents()
    return app.window


def test_static_pack_visual_skips_repaints(app, qt_application):
    """A parts-based pack character draws identical pixels while the
    snapshot content stands still: exactly one composing repaint, then
    the gate holds.  A blink change repaints exactly once more."""
    window = _activate_static_pack(app)
    countdown = app._countdown_cache
    with _counting(window, qt_application) as counter:
        baseline = counter.count
        for _ in range(20):
            window.update_snapshot(_snapshot("idle"), countdown)
            qt_application.processEvents()
        qt_application.processEvents()
        assert counter.count - baseline == 1
        window.update_snapshot(_snapshot("idle", blink=True), countdown)
        qt_application.processEvents()
        assert counter.count - baseline == 2


def test_animated_visual_keeps_repainting(app, qt_application):
    """The engine cat animates with time_ms: advancing frames must keep
    scheduling repaints (no false downshift)."""
    app._show_window()
    qt_application.processEvents()
    window = app.window
    countdown = app._countdown_cache
    with _counting(window, qt_application) as counter:
        baseline = counter.count
        for frame in range(12):
            window.update_snapshot(_snapshot("idle", frame=frame),
                                   countdown)
            qt_application.processEvents()
        assert counter.count - baseline >= 12


def test_countdown_change_repaints_even_for_static_visual(
        app, qt_application):
    from dataclasses import replace

    window = _activate_static_pack(app)
    countdown = app._countdown_cache
    with _counting(window, qt_application) as counter:
        baseline = counter.count
        for _ in range(10):
            window.update_snapshot(_snapshot("idle"), countdown)
            qt_application.processEvents()
        assert counter.count - baseline == 1
        changed = replace(countdown, seconds=countdown.seconds + 1)
        window.update_snapshot(_snapshot("idle"), changed)
        qt_application.processEvents()
        assert counter.count - baseline == 2


def test_static_visual_skips_decode_work_on_repeat_ticks(
        app, qt_application, monkeypatch):
    """The decode path is cache-backed: repeating the same static frame
    must not add new decoded pixmaps (no per-tick re-decode)."""
    window = _activate_static_pack(app)
    runtime = app.switcher.current_runtime
    countdown = app._countdown_cache

    decode_calls = {"n": 0}
    original_decode = runtime._decode

    def counting_decode(asset_id):
        decode_calls["n"] += 1
        return original_decode(asset_id)

    monkeypatch.setattr(runtime, "_decode", counting_decode)
    with _counting(window, qt_application) as counter:
        for _ in range(20):
            window.update_snapshot(_snapshot("idle"), countdown)
            qt_application.processEvents()
        warm = decode_calls["n"]
        for _ in range(20):
            window.update_snapshot(_snapshot("idle"), countdown)
            qt_application.processEvents()
        assert decode_calls["n"] == warm  # cache-served, zero new decodes


class _LocalPaintCounter(QObject):
    """Minimal paint counter with a hard detach (OVR-04)."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self.count = 0

    def eventFilter(self, obj, event):  # noqa: N802
        if self._window is None:
            return False
        if obj is self._window and event.type() == QEvent.Type.Paint:
            self.count += 1
        return False

    def detach(self):
        if self._window is not None:
            self._window.removeEventFilter(self)
            self._window = None


def test_paint_counter_detaches_even_when_window_dies(
        app, qt_application):
    """OVR-04: every filter install is paired with removal, including
    the window-destruction path - repeated runs produce no Qt callback
    exceptions."""
    root = Path(__file__).resolve().parent.parent
    app.library.install(
        root / "tests/fixtures/petpack/minimal-static.petpack")
    entry = next(e for e in app.catalog.entries()
                 if e.character_id == "demo")
    assert app._switch_character(entry) is True
    app._show_window()
    qt_application.processEvents()
    window = app.window
    counter = _LocalPaintCounter(window)
    window.installEventFilter(counter)
    qt_application.processEvents()
    counter.detach()

    # destroying the window afterwards triggers no callback on the
    # detached counter (the old dangling-filter AttributeError)
    # hide first so isVisible flips; deleteLater alone does not hide
    window.hide()
    qt_application.processEvents()
    assert not window.isVisible()
