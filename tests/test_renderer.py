"""Renderer smoke tests: every action/stage paints without assets."""

from __future__ import annotations

import pytest

from retirement_pet.models import ActionId, LifeStage, OverlaySnapshot, RenderSnapshot


def render_to_image(qt_application, action, stage, overlay=None):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from retirement_pet.ui.renderer import CatRenderer

    image = QImage(232, 236, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    snap = RenderSnapshot(
        action=action,
        stage=stage,
        elapsed_ms=750,
        frame=3,
        time_ms=12_345,
        overlay=overlay or OverlaySnapshot(),
    )
    CatRenderer().render(painter, QRectF(0, 0, 232, 236), snap)
    painter.end()
    return image


def test_all_actions_render_without_assets(qt_application):
    for action in ActionId:
        if action is ActionId.BLINK:
            continue
        image = render_to_image(qt_application, action, LifeStage.MIDDLE)
        assert not image.isNull()


def test_all_stages_render(qt_application):
    for stage in LifeStage:
        image = render_to_image(qt_application, ActionId.IDLE, stage)
        assert not image.isNull()


def test_nonblank_output(qt_application):
    image = render_to_image(qt_application, ActionId.IDLE, LifeStage.YOUNG)
    # Count non-transparent pixels - the cat must actually be there.
    opaque = 0
    for y in range(0, image.height(), 4):
        for x in range(0, image.width(), 4):
            if image.pixel(x, y) >> 24 != 0:
                opaque += 1
    assert opaque > 150


def test_bubble_and_effects_render(qt_application):
    overlay = OverlaySnapshot(
        blink=True, gaze_x=0.5, gaze_y=-0.3,
        effects=("zzz", "notes", "sweat"), bubble_text="干饭啦",
    )
    image = render_to_image(qt_application, ActionId.REST, LifeStage.OLD, overlay)
    assert not image.isNull()


def test_body_only_keeps_caption_band_transparent(qt_application):
    """Generated pack media must not contain an engine-owned caption."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from retirement_pet.ui.renderer import CatRenderer

    image = QImage(512, 512, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    CatRenderer().render_body(
        painter, QRectF(0, 0, 512, 512),
        RenderSnapshot(
            action=ActionId.WORK,
            stage=LifeStage.YOUNG,
            elapsed_ms=0,
            frame=0,
            time_ms=0,
        ),
    )
    painter.end()
    assert all(
        image.pixelColor(x, y).alpha() == 0
        for y in range(32) for x in range(image.width())
    )


def test_predefined_caption_uses_chinese_only_with_real_glyphs(
        qt_application, monkeypatch):
    from PySide6.QtGui import QFont

    import retirement_pet.ui.overlay_renderer as overlay_module

    monkeypatch.setattr(
        overlay_module,
        "_find_font",
        lambda text, _size, _bold: (
            None if any(ord(char) > 127 for char in text)
            else QFont("Segoe UI", 8)
        ),
    )
    text, _font = overlay_module.resolved_caption(ActionId.WORK)
    assert text == "Working"

    monkeypatch.setattr(
        overlay_module,
        "_find_font",
        lambda _text, _size, _bold: QFont("Microsoft YaHei UI", 8),
    )
    text, _font = overlay_module.resolved_caption(ActionId.WORK)
    assert text == "工作中"


def test_font_lookup_cache_is_bounded(qt_application):
    from retirement_pet.ui.overlay_renderer import _find_font

    _find_font.cache_clear()
    for index in range(100):
        _find_font(f"bubble-{index}", 8, False)
    assert _find_font.cache_info().maxsize == 32
    assert _find_font.cache_info().currsize <= 32
    _find_font.cache_clear()


def test_countdown_panel_expanded_and_collapsed(qt_application):
    from datetime import datetime

    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from retirement_pet.countdown import compute_countdown
    from retirement_pet.ui.countdown_panel import CountdownPanel

    snap = compute_countdown(datetime(2026, 8, 23), datetime(2060, 7, 7, 21, 32))
    panel = CountdownPanel()
    for painter_rect, method in (
        (QRectF(0, 0, 232, 124), "render_expanded"),
        (QRectF(0, 0, 232, 42), "render_collapsed"),
    ):
        image = QImage(int(painter_rect.width()), int(painter_rect.height()),
                       QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        getattr(panel, method)(painter, painter_rect, snap)
        painter.end()
        assert not image.isNull()
