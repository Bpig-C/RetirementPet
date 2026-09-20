"""App-to-Runtime idle playback proof (V12-01 acceptance).

A synthetic three-frame pack with distinct colors and deliberately
different frame durations is installed and activated through the REAL
application, then snapshots composed by ``PetApplication`` are rendered
through the live PackCharacterRuntime.  Frame time comes only from the
app's idle timeline driven by the injected clock - never hand-filled into
the Runtime.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("qt_application")


@pytest.fixture(autouse=True)
def isolate_startup_registry(monkeypatch):
    from retirement_pet.startup import MemoryBackend

    monkeypatch.setattr("retirement_pet.app.WinRegBackend", MemoryBackend)


FRAME_MS = (500, 700, 1100)  # deliberately unequal; total 2300
FRAME_COLORS = ((255, 0, 0, 255), (0, 180, 0, 255), (0, 0, 255, 255))
WORK_COLOR = (255, 220, 0, 255)


def _png(width: int, height: int, rgba) -> bytes:
    import struct
    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(rgba) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _sequence_manifest() -> dict:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_petpack import make_manifest

    return make_manifest(
        assets=[
            {"id": "thumb", "path": "assets/thumb.png",
             "media_type": "image/png", "byte_size": 0, "sha256": "",
             "rights_ref": "rights.original",
             "source_ref": "source.original"},
        ] + [
            {"id": f"idle{index}", "path": f"assets/idle_{index}.png",
             "media_type": "image/png", "byte_size": 0, "sha256": "",
             "rights_ref": "rights.original",
             "source_ref": "source.original"}
            for index in range(3)
        ] + [
            {"id": "work0", "path": "assets/work_0.png",
             "media_type": "image/png", "byte_size": 0, "sha256": "",
             "rights_ref": "rights.original",
             "source_ref": "source.original"},
        ],
        actions=[
            {"id": "action.idle", "semantic": "core.idle",
             "lifecycle": {"loop": {"renderer": {"type": "sequence",
                                                 "frames": [
                 {"asset": f"idle{index}", "duration_ms": FRAME_MS[index]}
                 for index in range(3)]}}}},
            {"id": "action.work", "semantic": "core.work",
             "lifecycle": {"loop": {"renderer": {
                 "type": "static", "asset": "work0"}}}},
        ],
        characters=[
            {"id": "demo", "display_name": {"zh-CN": "三帧示例"},
             "thumbnail_asset": "thumb",
             "geometry": {
                 "logical_canvas": {"width": 512, "height": 512},
                 "content_bounds": {"x": 96, "y": 60,
                                    "width": 320, "height": 400},
                 "motion_bounds": {"x": 66, "y": 30,
                                   "width": 380, "height": 450},
                 "base_anchor": {"x": 256, "y": 460},
                 "bubble_anchor": {"x": 256, "y": 90},
                 "reference_height": 400,
                 "hit_regions": [
                     {"shape": "rect", "x": 96, "y": 60,
                      "width": 320, "height": 400},
                 ]},
             "actions": {"core.idle": "action.idle",
                         "core.work": "action.work"}},
        ],
    )


@pytest.fixture()
def playback_app(qt_application, tmp_path, monkeypatch):
    from pathlib import Path

    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    app = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-idle-{tmp_path.name}",
    )
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_petpack import build_pack

    files = {"assets/thumb.png": _png(8, 8, (128, 128, 128, 255))}
    for index, color in enumerate(FRAME_COLORS):
        files[f"assets/idle_{index}.png"] = _png(512, 512, color)
    files["assets/work_0.png"] = _png(512, 512, WORK_COLOR)
    pack_path = tmp_path / "sequence-demo.petpack"
    pack_path.write_bytes(build_pack(_sequence_manifest(), files))
    app.library.install(pack_path)
    entry = next(e for e in app.catalog.entries() if e.character_id == "demo")
    assert app._switch_character(entry) is True
    yield app
    app.shutdown()


def _render_center_pixel(app, now_ms):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter

    snapshot = app._compose_snapshot(now_ms)
    layout = app.window._active_layout
    if layout is None:
        from retirement_pet.character_layout import (
            CharacterGeometry, compute_body_layout)

        geometry = app.window._renderer.character_geometry()
        assert isinstance(geometry, CharacterGeometry)
        layout = compute_body_layout(QRectF(0, 0, 232, 236), geometry)
    image = QImage(232, 236, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    try:
        app.window._renderer.render_body(painter, QRectF(0, 0, 232, 236),
                                         snapshot, layout)
    finally:
        painter.end()
    center = layout.body_rect.center().toPoint()
    return snapshot, QColor(image.pixel(center))


def _assert_frame_color(app, now_ms, frame_index):
    snapshot, color = _render_center_pixel(app, now_ms)
    expected = FRAME_COLORS[frame_index]
    assert snapshot.elapsed_ms == app._idle_timeline.elapsed_ms(now_ms)
    assert (color.red(), color.green(), color.blue(), color.alpha()) \
        == expected, f"t={now_ms}: expected frame {frame_index}, got {color}"


def test_app_idle_advances_frames_loops_and_restarts_after_action(
        playback_app):
    from retirement_pet.models import ActionId

    app = playback_app
    app.visual_clock.set_visible(True)

    # t=200: inside frame 1 (durations 500/700/1100).
    app.clock.advance_ms(200)
    app.service_clock.pump()
    _assert_frame_color(app, app.clock.monotonic_ms(), 0)
    # t=800: frame 2.  t=1500: frame 3.
    app.clock.advance_ms(600)
    _assert_frame_color(app, app.clock.monotonic_ms(), 1)
    app.clock.advance_ms(700)
    _assert_frame_color(app, app.clock.monotonic_ms(), 2)
    # t=2300/2400: loop boundary wraps back to frame 1.
    app.clock.advance_ms(800)
    _assert_frame_color(app, app.clock.monotonic_ms(), 0)
    app.clock.advance_ms(100)
    _assert_frame_color(app, app.clock.monotonic_ms(), 0)

    # An explicit action replaces idle rendering; ending it restarts the
    # idle loop from its FIRST frame (a fresh timeline, not a jump).
    assert app._service_port.request(ActionId.WORK, "rhythm")
    assert app.controller.current_action() is ActionId.WORK
    _, work_color = _render_center_pixel(app, app.clock.monotonic_ms())
    assert (work_color.red(), work_color.green(), work_color.blue()) == \
        (WORK_COLOR[0], WORK_COLOR[1], WORK_COLOR[2]), \
        "the renderable core.work action must paint its own frame"
    # The rhythm fact ends: the resolver must end the performance and the
    # idle loop restarts from its FIRST frame (a fresh timeline, not a jump).
    from retirement_pet.runtime_state import ContextId

    app.contexts.clear(ContextId.WORKING, "rhythm")
    app.bridge.apply("test_work_over")
    assert app.controller.current_action() is None
    app.clock.advance_ms(200)
    _assert_frame_color(app, app.clock.monotonic_ms(), 0)


def test_app_idle_timeline_freezes_while_hidden(playback_app):
    app = playback_app
    app.run()  # shows the window and starts the visual clock
    app.clock.advance_ms(300)
    app._hide_window()
    frozen = app._idle_timeline.elapsed_ms()
    app.clock.advance_ms(500_000)
    assert app._idle_timeline.elapsed_ms() == frozen, \
        "hidden time must neither replay nor advance the idle loop"
    app._show_window()
    app.clock.advance_ms(120)
    assert app._idle_timeline.elapsed_ms() == frozen + 120


def test_app_hidden_window_stays_paused_after_sleep_wake(playback_app):
    """CR-A04: waking from sleep while STILL hidden must not resume the
    timeline - one pause reason must not erase the other."""
    app = playback_app
    app.run()
    app.clock.advance_ms(300)
    app._hide_window()
    frozen = app._idle_timeline.elapsed_ms()
    app._on_session_suspended(True)  # sleep
    app._on_session_suspended(False)  # wake; the window is still hidden
    app.clock.advance_ms(1000)
    assert app._idle_timeline.elapsed_ms() == frozen, \
        "wake while hidden must leave the idle timeline paused"
    app._show_window()
    app.clock.advance_ms(120)
    assert app._idle_timeline.elapsed_ms() == frozen + 120


def test_app_show_during_suspension_stays_paused(playback_app):
    """CR-A04: showing the window while the session is suspended must not
    start the loop; only the wake itself may resume it."""
    app = playback_app
    app.run()
    app.clock.advance_ms(300)
    app._on_session_suspended(True)  # sleep with the window visible
    app._show_window()  # summoned while the system is still suspended
    app.clock.advance_ms(800)
    frozen = app._idle_timeline.elapsed_ms()
    assert app._idle_timeline.elapsed_ms() == frozen, \
        "visible-but-suspended must stay paused"
    app._on_session_suspended(False)  # wake with the window visible
    app.clock.advance_ms(150)
    assert app._idle_timeline.elapsed_ms() == frozen + 150


def test_app_window_uses_layout_mask_and_hit_regions(playback_app):
    from PySide6.QtCore import QPointF

    app = playback_app
    pump_visible_frames(app)
    layout = app.window._active_layout
    assert layout is not None, "geometry pack must produce a real layout"
    assert app.window._character_geometry() is not None
    # Clicks land inside the declared hit region, not in transparent margins.
    assert app.window.hit_accepts(layout.body_rect.center())
    assert not app.window.hit_accepts(QPointF(1, 1))
    mask = app.window._mask_applied
    assert mask is not None
    from PySide6.QtCore import QRect

    assert mask.boundingRect().height() >= 236  # panel + character area
    # The countdown panel must stay interactive under the mask.
    assert mask.intersects(QRect(0, 236, 232, 124))


def pump_visible_frames(app, frames=3, step_ms=100):
    from retirement_pet.clock import FakeClock

    assert isinstance(app.clock, FakeClock)
    app.visual_clock.set_visible(True)
    for _ in range(frames):
        app.clock.advance_ms(step_ms)
        app.visual_clock.pump()
        app.service_clock.pump()
